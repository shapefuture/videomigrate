"""Offline tests for transcript cleaning and the RustASR wrapper. No network, no model."""
from pathlib import Path
import importlib.util
import os
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from subtitles_clean import captions_to_txt, plain_text_to_txt  # noqa: E402
import transcribe_rustasr  # noqa: E402

HAS_GOOGLE = importlib.util.find_spec("googleapiclient") is not None


class CaptionCleaningTests(unittest.TestCase):
    def test_srt_timecodes_and_cue_numbers_are_removed(self):
        srt = ("1\n00:00:01,000 --> 00:00:03,000\nFirst line\n\n"
               "2\n00:00:03,000 --> 00:00:05,000\nFirst line, and more\n\n"
               "3\n00:00:06,000 --> 00:00:07,000\nSecond part.\n\n")
        text = captions_to_txt(srt)
        self.assertNotIn("-->", text)
        self.assertNotIn("00:00", text)
        self.assertIn("Second part.", text)

    def test_rolling_repeats_are_deduplicated(self):
        text = plain_text_to_txt("alpha beta gamma")
        self.assertEqual(text.strip(), "alpha beta gamma")
        merged = captions_to_txt(
            "1\n00:00:01,000 --> 00:00:02,000\none two\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\ntwo three\n")
        self.assertEqual(merged.strip(), "one two three")

    def test_plain_text_is_paragraphed_without_timecodes(self):
        text = plain_text_to_txt("word " * 200 + "end.")
        self.assertNotIn("-->", text)
        self.assertTrue(text.endswith("\n"))

    def test_empty_plain_text_is_refused(self):
        with self.assertRaises(ValueError):
            plain_text_to_txt("   ")


class RustAsrWrapperTests(unittest.TestCase):
    """Runs the real wrapper against a fake `rustasr` executable written to a temp dir."""

    def _fake_binary(self, tmp, body):
        script = Path(tmp) / "fake-rustasr"
        script.write_text("#!/usr/bin/env python3\nimport sys\nargs = sys.argv[1:]\n"
                          "out = args[args.index('--out-text') + 1]\n" + body, encoding="utf8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def _media_and_model(self, tmp):
        media = Path(tmp) / "source.mp4"
        media.write_bytes(b"x")
        model = Path(tmp) / "model"
        model.mkdir()
        return media, model

    def test_wrapper_returns_text_written_by_rustasr(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._fake_binary(tmp, "open(out, 'w', encoding='utf8').write('recognised words here')\n")
            media, model = self._media_and_model(tmp)
            original = transcribe_rustasr.to_wav_16k_mono
            transcribe_rustasr.to_wav_16k_mono = lambda source, wav: wav.write_bytes(b"0" * 2000)
            try:
                text = transcribe_rustasr.transcribe_media(media, model, binary=str(binary))
            finally:
                transcribe_rustasr.to_wav_16k_mono = original
            self.assertEqual(text, "recognised words here\n")

    def test_wrapper_refuses_empty_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = self._fake_binary(tmp, "open(out, 'w', encoding='utf8').write('  \\n')\n")
            media, model = self._media_and_model(tmp)
            original = transcribe_rustasr.to_wav_16k_mono
            transcribe_rustasr.to_wav_16k_mono = lambda source, wav: wav.write_bytes(b"0" * 2000)
            try:
                with self.assertRaises(RuntimeError):
                    transcribe_rustasr.transcribe_media(media, model, binary=str(binary))
            finally:
                transcribe_rustasr.to_wav_16k_mono = original

    def test_missing_model_directory_is_refused_before_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "source.mp4"
            media.write_bytes(b"x")
            with self.assertRaises(RuntimeError):
                transcribe_rustasr.transcribe_media(media, Path(tmp) / "absent", binary="/bin/false")


@unittest.skipUnless(HAS_GOOGLE, "Google API client not installed")
class ArchivePathTests(unittest.TestCase):
    def test_output_path_stays_inside_transcripts(self):
        import transcripts_archive
        ok = {"relative_path": "Folder/Lesson.mp4"}
        self.assertEqual(transcripts_archive.safe_output_path(ok).suffix, ".txt")
        for bad in ("../Lesson.mp4", "Folder/../../Lesson.mp4", "OnlyOneSegment.mp4", "Folder/Lesson.txt"):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    transcripts_archive.safe_output_path({"relative_path": bad})

    def test_archive_requires_unlisted_manifest(self):
        import json
        import transcripts_archive
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            manifest.write_text(json.dumps({"schema_version": 1, "privacy_required": "private",
                                            "videos": []}), encoding="utf8")
            with self.assertRaises(RuntimeError):
                sys.argv = ["transcripts_archive.py", "--manifest", str(manifest), "--sequence", "1", "--dry-run"]
                original = transcripts_archive.ROOT
                transcripts_archive.ROOT = Path(tmp)
                try:
                    transcripts_archive.main()
                finally:
                    transcripts_archive.ROOT = original


if __name__ == "__main__":
    unittest.main()
