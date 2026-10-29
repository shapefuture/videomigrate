#!/usr/bin/env python3
"""Save clean transcripts for UNLISTED YouTube uploads as hierarchical .txt files.

Captions are tried first and need YT_MANAGE_REFRESH_TOKEN with the
youtube.force-ssl scope. With --rustasr-fallback, a lesson without usable
captions is transcribed from the ORIGINAL Mail.ru MP4 by RustASR.

Outputs go to transcripts/ on the runner and are uploaded as a workflow
artifact. Nothing is committed back to the repository.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import unquote

from google.auth.transport.requests import AuthorizedSession
from googleapiclient.discovery import build

from subtitles_clean import captions_to_txt, plain_text_to_txt
from transcribe_rustasr import transcribe_media

ROOT = Path(__file__).resolve().parent
TRANSCRIPTS = ROOT / "transcripts"
MANIFEST = ROOT / "course_manifest.json"
UNLISTED = "unlisted"


def manager():
    from youtube_manage_auth import get_management_credentials
    creds = get_management_credentials()
    return build("youtube", "v3", credentials=creds, cache_discovery=False), AuthorizedSession(creds)


def safe_output_path(video):
    relative = video["relative_path"].split("/")
    if len(relative) < 2:
        raise ValueError("Expected course subfolder and MP4 filename")
    if not relative[-1].lower().endswith(".mp4"):
        raise ValueError("Expected original MP4 filename")
    decoded = [unquote(s) for s in relative]
    if any(not s or s in (".", "..") or "\\" in s or "/" in s or "\x00" in s
           for s in decoded):
        raise ValueError("Unsafe manifest path")
    dest = TRANSCRIPTS / Path(*decoded).with_suffix(".txt")
    if not dest.resolve().is_relative_to(TRANSCRIPTS.resolve()):
        raise ValueError("Transcript output escapes the transcripts directory")
    return dest


def owner_video_state(yt, video_id, channel_id):
    videos = yt.videos().list(part="snippet,status,processingDetails", id=video_id).execute().get("items", [])
    if len(videos) != 1:
        raise RuntimeError("Video not accessible to management account: " + video_id)
    v = videos[0]
    if v["snippet"]["channelId"] != channel_id or v["status"]["privacyStatus"] != UNLISTED:
        raise RuntimeError("Wrong YouTube channel, or video not UNLISTED: " + video_id)
    return v.get("processingDetails", {}).get("processingStatus", "unknown")


def get_youtube_captions(yt, auth_session, video_id):
    tracks = yt.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    tracks = [t for t in tracks if t.get("snippet", {}).get("status") == "serving"
              and not t.get("snippet", {}).get("isDraft")]
    tracks.sort(key=lambda t: (
        not t["snippet"].get("language", "").lower().startswith("ru"),
        t["snippet"].get("trackKind") == "ASR",
    ))
    for track in tracks:
        url = "https://www.googleapis.com/youtube/v3/captions/" + track["id"]
        response = auth_session.get(url, params={"tfmt": "srt"}, timeout=80)
        if response.status_code in (400, 403, 404):
            print("Caption track not downloadable:", track["snippet"].get("language"), response.status_code)
            continue
        response.raise_for_status()
        try:
            plain = captions_to_txt(response.content)
        except ValueError:
            print("Caption track contained no parseable SRT cues; will try another track")
            continue
        return plain, {"source": "youtube", "language": track["snippet"].get("language"),
                       "caption_track": track["id"],
                       "track_kind": track["snippet"].get("trackKind")}
    return None, None


def transcribe_original(video, model_dir, language, binary):
    """Download the original MP4 from Mail.ru and transcribe it with RustASR."""
    with tempfile.TemporaryDirectory(prefix="transcript-") as tmp:
        input_path = Path(tmp) / "original.mp4"
        subprocess.run([sys.executable, "mailru_fetch.py", "--url", video["source_url"],
                        "--output", str(input_path)], cwd=ROOT, check=True)
        raw = transcribe_media(input_path, model_dir, language=language, binary=binary)
    return plain_text_to_txt(raw), {"source": "original_mp4_rustasr",
                                    "language": language,
                                    "model": Path(model_dir).name}


def all_uploads(manifest):
    receipt_dir = ROOT / "receipts"
    items = []
    for v in manifest["videos"]:
        filename = receipt_dir / (str(v["sequence"]).zfill(4) + ".json")
        if not filename.is_file():
            continue
        r = json.loads(filename.read_text(encoding="utf8"))
        if (r.get("sequence") != v["sequence"] or r.get("source_url") != v["source_url"]
                or r.get("privacy_reported") != UNLISTED or not r.get("video_id")):
            raise RuntimeError("Receipt does not match unlisted course manifest: " + str(filename))
        items.append((v, r))
    return items


def select(uploads, sequence, limit):
    chosen = [(v, r) for v, r in uploads if sequence <= v["sequence"] < sequence + limit]
    if not chosen:
        print("No uploads yet within requested lesson range")
    return chosen


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", default="course_manifest.json")
    p.add_argument("--sequence", type=int, required=True, help="First lesson number (1-based)")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--rustasr-fallback", action="store_true",
                   help="Transcribe the original MP4 with RustASR when captions are unavailable")
    p.add_argument("--rustasr-bin", default="rustasr")
    p.add_argument("--model-dir", default="models/whisper")
    p.add_argument("--language", default="ru")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not (1 <= args.limit <= 10) or args.sequence < 1:
        raise RuntimeError("Limit must be 1 to 10 and sequence must be 1 or higher")
    manifest = json.loads((ROOT / args.manifest).read_text(encoding="utf8"))
    if manifest.get("privacy_required") != UNLISTED or manifest.get("schema_version") != 1:
        raise RuntimeError("Invalid unlisted course manifest")
    chosen = select(all_uploads(manifest), args.sequence, args.limit)
    print("Selected lessons:", [v["sequence"] for v, _ in chosen])
    if args.dry_run or not chosen:
        print("No calls to YouTube or Mail.ru; dry-run or nothing selected")
        return
    yt, sess = manager()
    channel = yt.channels().list(part="id", mine=True).execute().get("items", [])
    if len(channel) != 1:
        raise RuntimeError("Expected one authorized management channel")
    channel_id = channel[0]["id"]
    for v, rec in chosen:
        dest = safe_output_path(v)
        meta = TRANSCRIPTS / ".metadata" / (str(v["sequence"]).zfill(4) + ".json")
        if dest.is_file():
            print("SKIP existing:", dest.relative_to(ROOT))
            continue
        state = owner_video_state(yt, rec["video_id"], channel_id)
        if state != "succeeded":
            print("PENDING video processing for", v["sequence"], "state:", state)
            continue
        try:
            content, info = get_youtube_captions(yt, sess, rec["video_id"])
        except Exception as err:
            # Permission problems must not silently cause a source re-download.
            print("YouTube caption check failed for", v["sequence"], type(err).__name__, str(err)[:150])
            if not args.rustasr_fallback:
                continue
            content, info = None, None
        if content is None and args.rustasr_fallback:
            content, info = transcribe_original(v, args.model_dir, args.language, args.rustasr_bin)
        if content is None:
            print("PENDING: no usable YouTube subtitles yet for", v["sequence"])
            continue
        if not content.strip():
            raise RuntimeError("Empty transcript generated; refusing to write it")
        dest.parent.mkdir(parents=True, exist_ok=True)
        meta.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf8")
        info.update(sequence=v["sequence"], video_id=rec["video_id"],
                    relative_path=v["relative_path"], youtube_privacy=UNLISTED,
                    created_utc=datetime.now(timezone.utc).isoformat())
        meta.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        print("SAVED:", dest.relative_to(ROOT), "source:", info["source"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Transcript archive stopped:", type(exc).__name__, str(exc)[:200], file=sys.stderr)
        raise SystemExit(1)
