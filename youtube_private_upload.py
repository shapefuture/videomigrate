#!/usr/bin/env python3
"""Manual-only YouTube UNLISTED uploader. Never supports public or private."""
import json
import os
from pathlib import Path
import sys
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPE = "https://www.googleapis.com/auth/youtube.upload"

def main():
    path = Path(os.environ.get("VIDEO_PATH", "downloads/original.mp4"))
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError("Missing downloaded video")
    keys = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError("Missing GitHub secrets: " + ", ".join(missing))
    credentials = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=[SCOPE],
    )
    credentials.refresh(Request())
    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    title = (os.environ.get("VIDEO_TITLE") or "Untitled upload")[:100]
    body = {
        "snippet": {"title": title, "description": "Unlisted copy from Cloud Mail.ru", "categoryId": "22"},
        "status": {"privacyStatus": "unlisted"},
    }
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8*1024*1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        progress, response = request.next_chunk()
        if progress:
            print("Upload progress: {:.1%}".format(progress.progress()), flush=True)
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("YouTube did not return a video ID")
    receipt = {
        "video_id": video_id,
        "watch_url": "https://www.youtube.com/watch?v=" + video_id,
        "privacy_requested": "unlisted",
        "privacy_reported": response.get("status", {}).get("privacyStatus"),
        "title": title,
    }
    # Save the id before checking post-upload details: never blindly repeat videos.insert.
    Path("youtube-private-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if receipt["privacy_reported"] != "unlisted":
        raise RuntimeError("The upload response did not confirm UNLISTED status")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("## Unlisted YouTube upload\n\n- Video ID: "+video_id+"\n- Visibility: **unlisted**\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Unlisted upload failed:", str(exc), file=sys.stderr)
        raise SystemExit(1)
