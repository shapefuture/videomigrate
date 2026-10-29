#!/usr/bin/env python3
"""Convert SRT/VTT to readable Unicode text: no timecodes or cue numbers."""
import html
import re
import sys
from pathlib import Path

TIMECODE = re.compile(
    r"(?:(?:\d{1,2}:)?\d{2}:)?\d{2}[.,]\d{1,3}\s*-->\s*"
    r"(?:(?:\d{1,2}:)?\d{2}:)?\d{2}[.,]\d{1,3}"
)
TAGS = re.compile(r"<[^>]+>")
POSITION = re.compile(r"(?:^|\s)(?:align|position|line|size):[^\s]+")
CUE_INDEX = re.compile(r"^\d+$")


def strip_cue_line(line):
    line = html.unescape(TAGS.sub("", line)).strip()
    line = line.replace("\u200b", "").replace("\ufeff", "")
    line = re.sub(r"\s+", " ", line).strip()
    return line


def merge_chunks(chunks):
    """Discard repeated rolling captions without flattening intentional pauses."""
    words = []
    for item in chunks:
        new = item.split()
        if not new:
            continue
        if words:
            # ASR captions frequently repeat previously displayed words.
            best = 0
            for n in range(1, min(len(words), len(new), 24) + 1):
                if [w.casefold() for w in words[-n:]] == [w.casefold() for w in new[:n]]:
                    best = n
            new = new[best:]
        words.extend(new)
    if not words:
        raise ValueError("Caption source contained no spoken text")
    # Keep paragraphs reasonably readable without fabricating punctuation.
    paragraphs = []
    buffer = []
    for word in words:
        buffer.append(word)
        length = sum(len(w) + 1 for w in buffer)
        if length > 340 and re.search(r"[.!?…。！？]$", word):
            paragraphs.append(" ".join(buffer))
            buffer = []
        elif length > 650:
            paragraphs.append(" ".join(buffer))
            buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer))
    return "\n\n".join(paragraphs).strip() + "\n"


def captions_to_txt(data):
    if isinstance(data, bytes):
        raw = data.decode("utf-8-sig", errors="replace")
    else:
        raw = str(data).lstrip("\ufeff")
    raw = raw.replace("\r\n", "\n")
    if "WEBVTT" in raw[:30]:
        raw = re.sub(r"^WEBVTT[^\n]*", "", raw, count=1)
    blocks = re.split(r"\n\s*\n", raw)
    chunks = []
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        if lines[0].strip().startswith(("NOTE", "STYLE", "REGION")):
            continue
        cue_lines = []
        has_timecode = False
        for line in lines:
            if TIMECODE.search(line):
                has_timecode = True
                continue
            if CUE_INDEX.fullmatch(line.strip()) or not line.strip():
                continue
            cleaned = strip_cue_line(line)
            if cleaned:
                cue_lines.append(cleaned)
        if has_timecode and cue_lines:
            chunks.append(" ".join(cue_lines))
    if not chunks:
        raise ValueError("No caption cues with timecodes found")
    return merge_chunks(chunks)


def plain_text_to_txt(text):
    """Paragraph a plain ASR transcript (for example, RustASR output)."""
    return merge_chunks([text])


if __name__ == "__main__":
    source = Path(sys.argv[1])
    print(captions_to_txt(source.read_bytes()))
