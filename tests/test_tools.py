"""Release tooling and the media-player snapshot parser."""
import os
import sys
import unittest
from unittest import mock

from support import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_notes  # noqa: E402
import mpris  # noqa: E402
from version import VERSION  # noqa: E402

README = """## What changed from the first version

**v2.2.0 — newer.** No schema change.
- thing one

**v2.1.0 — older.**
- thing two

## Getting the app
"""


class ReleaseNotesTests(unittest.TestCase):
    def test_entry_stops_at_the_next_version(self):
        entry = release_notes.changelog_entry(README, "2.2.0")
        self.assertIn("thing one", entry)
        self.assertNotIn("thing two", entry)

    def test_last_entry_stops_at_the_next_section(self):
        entry = release_notes.changelog_entry(README, "2.1.0")
        self.assertIn("thing two", entry)
        self.assertNotIn("Getting the app", entry)

    def test_version_prefix_does_not_match_a_longer_version(self):
        self.assertIsNone(release_notes.changelog_entry(README, "2.2"))

    def test_readme_has_an_entry_for_this_version(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            self.assertIsNotNone(release_notes.changelog_entry(f.read(), VERSION),
                                 f"add a 'What changed' entry for v{VERSION} to README.md")


class SnapshotTests(unittest.TestCase):
    SEP = "\x1f"

    def test_parses_one_playerctl_call(self):
        raw = self.SEP.join(["Playing", "61500000", "0.5", "Track", "true", "Song", "Band", "180000000"])
        with mock.patch.object(mpris, "run", return_value=raw):
            snap = mpris.snapshot("firefox")
        self.assertEqual(snap, {"status": "Playing", "text": "Song — Band", "length": 180.0,
                                "position": 61.5, "volume": 0.5, "shuffle": "On", "loop": "Track"})

    def test_nothing_loaded_falls_back_to_status(self):
        with mock.patch.object(mpris, "run", side_effect=["", "Stopped"]):
            snap = mpris.snapshot("firefox")
        self.assertEqual(snap["status"], "Stopped")
        self.assertIsNone(snap["position"])


if __name__ == "__main__":
    unittest.main()
