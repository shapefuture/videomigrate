#!/usr/bin/env python3
"""Validate a one-time unlisted YouTube batch upload request from a git commit.

Does not access the network or read any OAuth secrets.
Only a newly added batch_requests/*.json file can trigger an upload.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


def validate(obj, filename):
    if set(obj) != {"manifest_run_id", "start", "count", "privacy", "confirm_unlisted_upload"}:
        raise ValueError("Unexpected upload-request fields")
    if obj["privacy"] != "unlisted" or obj["confirm_unlisted_upload"] is not True:
        raise ValueError("Upload requests MUST explicitly confirm unlisted-only uploads")
    for key, minimum, maximum in (("manifest_run_id", 1, 10**15), ("start", 1, 15000), ("count", 1, 10)):
        v = obj[key]
        if not isinstance(v, int) or isinstance(v, bool) or not (minimum <= v <= maximum):
            raise ValueError("Invalid " + key)
    if filename.parent != Path("batch_requests") or filename.suffix != ".json":
        raise ValueError("Upload request must be in batch_requests/*.json")
    return obj


def main():
    commit = os.getenv("GITHUB_SHA", "")
    if not commit:
        raise RuntimeError("This validator is intended for a GitHub Actions push event")
    raw = subprocess.check_output(
        ["git", "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", commit],
        text=True,
    )
    added = []
    for line in raw.splitlines():
        kind, _, name = line.partition("\t")
        if kind == "A" and name.startswith("batch_requests/") and name.endswith(".json"):
            added.append(name)
    if len(added) != 1:
        raise RuntimeError("Exactly one newly ADDED batch_requests/*.json must be in the triggering commit")
    filename = Path(added[0])
    obj = validate(json.loads(filename.read_text(encoding="utf8")), filename)
    lines = {
        "manifest_run_id": str(obj["manifest_run_id"]),
        "start": str(obj["start"]),
        "count": str(obj["count"]),
    }
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        raise RuntimeError("GITHUB_OUTPUT unavailable")
    with open(target, "a", encoding="utf8") as out:
        for k, v in lines.items():
            out.write(k + "=" + v + "\n")
    print("Authorized UNLISTED-only upload request:", filename.name,
          "lesson range", obj["start"], "to", obj["start"] + obj["count"] - 1)
    print("No YouTube authorization tokens have been read in request validation")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Request rejected:", str(exc), file=sys.stderr)
        raise SystemExit(1)
