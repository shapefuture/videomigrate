"""Offline checks for the unlisted-only YouTube and Rumble pipeline. No network."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock
import base64
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import browser_state  # noqa: E402
import course_batch_private  # noqa: E402
import rumble_private_one  # noqa: E402


def manifest(privacy="unlisted", count=2):
    return {
        "schema_version": 1,
        "privacy_required": privacy,
        "source_folder": "https://cloud.mail.ru/public/abc/def",
        "video_count": count,
        "videos": [
            {"sequence": i, "source_url": "https://cloud.mail.ru/public/abc/def/" + str(i) + ".mp4",
             "extension": ".mp4", "uploadable": True, "title": "Lesson " + str(i),
             "description": "", "relative_path": str(i) + ".mp4"}
            for i in range(1, count + 1)
        ],
    }


class UnlistedManifestTests(unittest.TestCase):
    def test_unlisted_manifest_is_accepted(self):
        self.assertEqual(len(course_batch_private.validate_manifest(manifest())), 2)

    def test_private_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            course_batch_private.validate_manifest(manifest(privacy="private"))

    def test_public_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            course_batch_private.validate_manifest(manifest(privacy="public"))


class RumbleSequenceGuardTests(unittest.TestCase):
    def _lesson_with_root(self, seq):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "course_manifest.json").write_text(json.dumps(manifest()), encoding="utf8")
            with mock.patch.object(rumble_private_one, "ROOT", Path(tmp)):
                return rumble_private_one.lesson(seq)

    def test_lesson_one_is_refused_because_it_is_already_on_rumble(self):
        with self.assertRaises(RuntimeError):
            self._lesson_with_root(1)

    def test_later_lesson_is_allowed(self):
        self.assertEqual(self._lesson_with_root(2)["sequence"], 2)

    def test_rumble_uploader_requests_unlisted(self):
        source = (ROOT / "rumble_private_one.py").read_text(encoding="utf8")
        self.assertIn('"requested_visibility":"unlisted"', source)
        self.assertIn('label[for="visibility_unlisted"]', source)
        self.assertNotIn("visibility_private", source)


class BrowserStateTests(unittest.TestCase):
    def _encode(self, obj):
        return base64.b64encode(json.dumps(obj).encode("utf8")).decode("ascii")

    def test_rumble_cookie_is_accepted(self):
        state = {"cookies": [{"name": "a", "domain": ".rumble.com"}], "origins": []}
        self.assertEqual(browser_state.restricted_storage_state(self._encode(state))["cookies"][0]["name"], "a")

    def test_foreign_cookie_is_refused(self):
        state = {"cookies": [{"name": "a", "domain": ".vk.ru"}], "origins": []}
        with self.assertRaises(RuntimeError):
            browser_state.restricted_storage_state(self._encode(state))


class PortedRepositoryTests(unittest.TestCase):
    def test_no_excluded_provider_code_is_present(self):
        for path in ROOT.rglob("*"):
            if {".git", "__pycache__", ".pytest_cache"} & set(path.parts) or path.is_dir() or path.name == "test_unlisted_pipeline.py":
                continue
            text = path.read_text(encoding="utf8", errors="ignore").lower()
            for provider in ("vk_", "dailymotion", "rutube", "odnoklass", "vkvideo"):
                self.assertNotIn(provider, text, str(path) + " mentions " + provider)

    def test_no_course_specific_urls_or_titles_are_committed(self):
        for path in list(ROOT.glob("*.py")) + list((ROOT / ".github/workflows").glob("*.yml")):
            text = path.read_text(encoding="utf8")
            self.assertNotIn("mxNd", text, str(path))
            self.assertNotIn("SW.BAND", text, str(path))
            self.assertNotIn("Петра", text, str(path))

    def test_no_workflow_uploads_privately_by_default(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text(encoding="utf8")
            self.assertNotIn('"privacyStatus": "private"', text, str(path))
            self.assertIsNone(re.search(r"privacy_required\W+private", text), str(path))


if __name__ == "__main__":
    unittest.main()
