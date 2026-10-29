#!/usr/bin/env python3
"""Transcribe one MP4 with the RustASR CLI (https://github.com/askidmobile/rustasr).

The MP4 is converted to 16 kHz mono WAV with ffmpeg, then passed to
`rustasr transcribe`, which writes plain text to --out-text. Nothing is
committed here: callers store transcripts as workflow artifacts.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

DEFAULT_BINARY = "rustasr"


def to_wav_16k_mono(source, wav):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(wav)], check=True)
    if not wav.is_file() or wav.stat().st_size < 1000:
        raise RuntimeError("ffmpeg produced no usable audio")


def transcribe_media(source, model_dir, language="ru", binary=DEFAULT_BINARY,
                     device="cpu", model_type="whisper"):
    """Return the plain transcript text for a media file. Raises if it is empty."""
    source = Path(source)
    model_dir = Path(model_dir)
    if not source.is_file():
        raise RuntimeError("Missing media file for transcription")
    if not model_dir.is_dir():
        raise RuntimeError("Missing RustASR model directory: " + str(model_dir))
    with tempfile.TemporaryDirectory(prefix="rustasr-") as tmp:
        wav = Path(tmp) / "audio-16k-mono.wav"
        out_text = Path(tmp) / "transcript.txt"
        to_wav_16k_mono(source, wav)
        subprocess.run([binary, "transcribe",
                        "--model", str(model_dir),
                        "--model-type", model_type,
                        "--audio", str(wav),
                        "--device", device,
                        "--language", language,
                        "--out-text", str(out_text)], check=True)
        if not out_text.is_file():
            raise RuntimeError("RustASR did not write a transcript file")
        text = out_text.read_text(encoding="utf8").strip()
    if not text:
        raise RuntimeError("RustASR returned an empty transcript; refusing to save it")
    return text + "\n"


def main():
    parser = argparse.ArgumentParser(description="Transcribe one MP4 with RustASR")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--binary", default=DEFAULT_BINARY)
    parser.add_argument("--device", default="cpu", choices=["cpu", "metal", "cuda"])
    args = parser.parse_args()
    text = transcribe_media(args.input, args.model_dir, args.language, args.binary, args.device)
    Path(args.output).write_text(text, encoding="utf8")
    print("Wrote transcript bytes:", len(text.encode("utf8")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("RustASR transcription failed:", type(exc).__name__, str(exc)[:200], file=sys.stderr)
        raise SystemExit(1)
