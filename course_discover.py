#!/usr/bin/env python3
"""Discover video lessons recursively from a Cloud Mail.ru public course folder.

Only queries folder metadata; it does not download videos or access YouTube.
Mail.ru's v2 public API is undocumented and can change.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.parse import quote, unquote, urlsplit

import requests

API = "https://cloud.mail.ru/api/v2"
PAGE_ID = re.compile(r"""pageId['"]*:\s*['"]*([^"'\\\s,]+)""")
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpeg", ".mpg"}
USER_AGENT = {"User-Agent": "Mozilla/5.0 (compatible; mailru-course-discovery/1.0)"}


def parse_folder_url(raw):
    url = urlsplit(raw)
    if url.scheme != "https" or url.hostname != "cloud.mail.ru":
        raise ValueError("Only HTTPS cloud.mail.ru links are accepted")
    parts = [unquote(s) for s in url.path.split("/") if s]
    if len(parts) < 3 or parts[0] != "public":
        raise ValueError("Expected /public/<share>/<id>[/course/folder]")
    if any(p in (".", "..") or "/" in p or "\\" in p for p in parts):
        raise ValueError("Invalid path component")
    share = parts[1:3]
    prefix = parts[3:]
    if prefix and Path(prefix[-1]).suffix.lower() in VIDEO_EXTENSIONS:
        raise ValueError("Supply the FOLDER link, not a video file link")
    return share, prefix


def encode_path(parts):
    return "/".join(quote(part, safe="") for part in parts)


def natural_key(s):
    normalized = unicodedata.normalize("NFKC", s).casefold()
    return tuple((1, int(p)) if p.isdecimal() else (0, p)
                 for p in re.split(r"(\d+)", normalized) if p)


def file_order(item):
    return tuple(natural_key(p) for p in item["path_parts"])


def call(session, endpoint, params):
    for attempt in range(5):
        try:
            response = session.get(endpoint, params=params, timeout=(15, 50))
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(min(2 ** attempt, 8))
                continue
            response.raise_for_status()
            data = response.json()
            if data.get("status") == 403:
                raise RuntimeError("Mail.ru denied public folder access")
            return data.get("body", {})
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("Mail.ru API did not respond")


def discover(source, max_folders=5000, max_videos=15000):
    share, prefix = parse_folder_url(source)
    root_url = "https://cloud.mail.ru/public/" + encode_path(share)
    session = requests.Session()
    session.headers.update(USER_AGENT)
    response = session.get(root_url, timeout=(15, 50))
    response.raise_for_status()
    match = PAGE_ID.search(response.text)
    if not match:
        raise RuntimeError("Mail.ru pageId missing (link restricted, expired, or API changed)")
    page_id = match.group(1)
    count_folders = 0
    visited = set()
    result = []
    folders = [(prefix, [])]   # (folder path in share, relative hierarchy)
    root_folder_name = None
    while folders:
        folder_path, relative = folders.pop()
        canonical = tuple(folder_path)
        if canonical in visited:
            continue
        visited.add(canonical)
        count_folders += 1
        if count_folders > max_folders:
            raise RuntimeError("Folder traversal exceeds safety limit")
        body = call(session, API + "/folder",
                    {"weblink": "/".join(share + folder_path), "x-page-id": page_id})
        if root_folder_name is None:
            root_folder_name = body.get("name", "")
        items = body.get("list")
        if not isinstance(items, list):
            raise RuntimeError("Mail.ru returned no folder listing")
        for item in items:
            name = item.get("name")
            if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
                continue
            if item.get("type") == "folder":
                folders.append((folder_path + [name], relative + [name]))
            elif Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                full = folder_path + [name]
                result.append({
                    "path_parts": relative + [name],
                    "relative_path": "/".join(relative + [name]),
                    "source_url": root_url + "/" + encode_path(full),
                    "extension": Path(name).suffix.lower(),
                    "bytes_reported": item.get("size"),
                })
                if len(result) > max_videos:
                    raise RuntimeError("Video count exceeds safety limit")
    result.sort(key=file_order)
    for i, item in enumerate(result, 1):
        item["sequence"] = i
        item["filename"] = item["path_parts"][-1]
        clean = []
        for segment in item["path_parts"]:
            value = re.sub(r"\[SW\.BAND\]", "", Path(segment).stem if segment == item["path_parts"][-1] else segment, flags=re.I).strip()
            if value:
                clean.append(value)
        descriptive = " / ".join(clean[-3:]) or Path(item["filename"]).stem
        title = str(i).zfill(3) + ". " + descriptive
        item["title"] = title[:100]
        item["description"] = "Course archive (unlisted)\nOriginal lesson: " + item["relative_path"]
        item["uploadable"] = item["extension"] == ".mp4"
        item.pop("path_parts")
    manifest = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_folder": root_url + ("/" + encode_path(prefix) if prefix else ""),
        "root_folder_name": root_folder_name,
        "video_count": len(result),
        "mp4_ready_count": sum(x["uploadable"] for x in result),
        "folders_scanned": count_folders,
        "privacy_required": "unlisted",
        "videos": result,
    }
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Discover & sequence all videos in a Mail.ru course folder")
    parser.add_argument("--folder-url", required=True)
    parser.add_argument("--output", default="course_manifest.json")
    args = parser.parse_args()
    result = discover(args.folder_url)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Folders scanned:", result["folders_scanned"])
    print("Videos found:", result["video_count"])
    print("MP4 upload-ready:", result["mp4_ready_count"])
    print("Manifest:", output)
    for entry in result["videos"][:25]:
        print(str(entry["sequence"]).zfill(3), entry["relative_path"])
    if len(result["videos"]) > 25:
        print("... remaining paths available in manifest")
    summary = __import__("os").environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fp:
            fp.write("## Mail.ru course discovery\n\n")
            fp.write("Folders: " + str(result["folders_scanned"]) + " | Videos: " + str(result["video_count"]) + " | MP4: " + str(result["mp4_ready_count"]) + "\n\n")
            fp.write("| Sequence | Video path | MP4 upload ready |\n|---:|---|---|\n")
            for entry in result["videos"][:100]:
                safe_path = entry["relative_path"].replace("|", "\\|").replace("\n", " ")
                fp.write("| " + str(entry["sequence"]) + " | " + safe_path + " | " + str(entry["uploadable"]) + " |\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Discovery error:", str(exc), file=sys.stderr)
        raise SystemExit(1)
