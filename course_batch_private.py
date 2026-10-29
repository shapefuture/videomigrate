#!/usr/bin/env python3
"""Manual, unlisted-only batch uploader for a previously reviewed Mail.ru manifest.

Default is preview/dry-run. The --execute flag requires a separate confirmation
environment variable. Results are committed to this repository so reruns
skip confirmed uploads, and every receipt is also saved as an Actions artifact.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

MAX_BATCH = 10
UNLISTED = "unlisted"


def validate_manifest(raw):
    if raw.get("schema_version") != 1 or raw.get("privacy_required") != UNLISTED:
        raise ValueError("Manifest has unexpected schema or privacy setting")
    folder = urlsplit(raw.get("source_folder", ""))
    if folder.scheme != "https" or folder.hostname != "cloud.mail.ru" or not folder.path.startswith("/public/"):
        raise ValueError("Manifest source is not a Mail.ru public folder")
    videos = raw.get("videos", [])
    if not isinstance(videos, list) or len(videos) != raw.get("video_count"):
        raise ValueError("Manifest count mismatch")
    for i, v in enumerate(videos, 1):
        u = urlsplit(v.get("source_url", ""))
        if (v.get("sequence") != i or u.scheme != "https" or u.hostname != "cloud.mail.ru"
                or not u.path.startswith(folder.path.rstrip("/") + "/")):
            raise ValueError("Invalid video sequence or source at item " + str(i))
    return videos


def receipt_path(seq):
    return Path("receipts") / (str(seq).zfill(4) + ".json")


def show_summary(message):
    print(message, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf8") as f:
            f.write(message + "\n")


def commit_receipt(receipt):
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    if os.environ.get("RECEIPT_GIT_PUSH") != "YES":
        raise RuntimeError("Refusing unattended uploads without persistent Git receipt storage")
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", "--", str(receipt)], check=True)
    subprocess.run(["git", "commit", "-m", "Record unlisted YouTube upload receipt " + receipt.stem], check=True)
    # The Actions token must have contents:write.
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)


def main():
    parser = argparse.ArgumentParser(description="Preview or upload a reviewed Mail.ru batch, UNLISTED only")
    parser.add_argument("--manifest", default="course_manifest.json")
    parser.add_argument("--start", type=int, default=1, help="1-based first lesson")
    parser.add_argument("--count", type=int, default=1, help="Number of lessons (max 10)")
    parser.add_argument("--execute", action="store_true", help="Actually upload, requires CONFIRM_UNLISTED_UPLOAD=YES")
    args = parser.parse_args()

    raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    videos = validate_manifest(raw)
    if args.start < 1 or args.count < 1 or args.count > MAX_BATCH:
        raise ValueError("start must be positive and count must be 1 through 10")
    selected = videos[args.start-1:args.start-1+args.count]
    if len(selected) != args.count:
        raise ValueError("Selected batch is outside manifest bounds")

    show_summary("## Mail.ru batch selection\n\nBatch: " + str(args.start) + " through " + str(args.start+args.count-1) + "\n")
    for v in selected:
        show_summary("- " + str(v["sequence"]) + ": " + v["relative_path"].replace("\n", " "))
    if not args.execute:
        show_summary("\n**DRY RUN ONLY: Nothing was uploaded to YouTube.**")
        return
    if os.environ.get("CONFIRM_UNLISTED_UPLOAD") != "YES":
        raise RuntimeError("To upload, explicitly confirm UNLISTED in workflow_dispatch")
    missing = [s for s in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not os.environ.get(s)]
    if missing:
        raise RuntimeError("Missing GitHub Actions secrets: " + ", ".join(missing))
    if os.environ.get("GITHUB_ACTIONS") and os.environ.get("RECEIPT_GIT_PUSH") != "YES":
        raise RuntimeError("Persistent receipt storage must be enabled on GitHub")

    for v in selected:
        seq = v["sequence"]
        receipt = receipt_path(seq)
        if receipt.exists():
            prior = json.loads(receipt.read_text(encoding="utf-8"))
            if prior.get("source_url") != v["source_url"]:
                raise RuntimeError("Existing receipt source differs for lesson " + str(seq) + "; manual review required")
            if prior.get("video_id"):
                if prior.get("privacy_reported") != UNLISTED:
                    raise RuntimeError("Existing receipt does not confirm UNLISTED status for lesson " + str(seq))
                show_summary("SKIP " + str(seq) + ": previously uploaded as UNLISTED (" + prior["video_id"] + ")")
                continue
            raise RuntimeError("Unresolved receipt found for lesson " + str(seq))
        if v.get("extension") != ".mp4" or not v.get("uploadable"):
            raise RuntimeError("Lesson " + str(seq) + " is not supported by the MP4 uploader")
        if Path("youtube-private-receipt.json").exists():
            Path("youtube-private-receipt.json").unlink()
        with tempfile.TemporaryDirectory(prefix="mailru-lesson-") as tmp:
            video = str(Path(tmp) / "source.mp4")
            subprocess.run(
                [sys.executable, "mailru_fetch.py", "--url", v["source_url"], "--output", video],
                check=True
            )
            env = os.environ.copy()
            env.update(VIDEO_PATH=video, VIDEO_TITLE=v["title"][:100])
            try:
                subprocess.run([sys.executable, "youtube_private_upload.py"], check=True, env=env)
            except subprocess.CalledProcessError:
                # The YouTube video may exist even if its post-upload status check failed.
                # Save the ID if present, and never blindly re-upload an uncertain completion.
                if Path("youtube-private-receipt.json").exists():
                    uploaded = json.loads(Path("youtube-private-receipt.json").read_text(encoding="utf-8"))
                    if uploaded.get("video_id"):
                        receipt.parent.mkdir(parents=True, exist_ok=True)
                        uploaded.update(source_url=v["source_url"], sequence=seq, relative_path=v["relative_path"])
                        receipt.write_text(json.dumps(uploaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        commit_receipt(receipt)
                raise RuntimeError("Upload result uncertain. Check channel and receipts before retrying lesson " + str(seq))
            uploaded = json.loads(Path("youtube-private-receipt.json").read_text(encoding="utf-8"))
            if not uploaded.get("video_id") or uploaded.get("privacy_reported") != UNLISTED:
                raise RuntimeError("UNLISTED verification failed for lesson " + str(seq) + "; stop and investigate")
            receipt.parent.mkdir(parents=True, exist_ok=True)
            uploaded.update(source_url=v["source_url"], sequence=seq, relative_path=v["relative_path"])
            receipt.write_text(json.dumps(uploaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # Persist before proceeding to the next lesson.
            commit_receipt(receipt)
            show_summary("UNLISTED uploaded: " + str(seq) + " -> " + uploaded["video_id"])
    show_summary("\n**Selected batch finished. UNLISTED only.**")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Batch stopped:", str(exc), file=sys.stderr)
        raise SystemExit(1)
