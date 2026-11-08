from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
from tests.support import ROOT
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("announcer_stub", ROOT / "examples/announcer_stub/announcer.py")
announcer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(announcer)


class AnnouncerTests(unittest.TestCase):
    def test_file_matches_what_the_hermes_patch_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)
            path = announcer.write_announcement("Hello radio.", tmp, now)
            item = json.loads(path.read_text())
            self.assertEqual(item["text"], "Hello radio.")
            self.assertEqual(datetime.fromisoformat(item["at"]), now)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [path.name])
            self.assertTrue(path.name.endswith(".json") and not path.name.startswith("."))

    @patch.dict(os.environ, {"BAOFENG_LANG": "en"})
    def test_hourly_rule(self):
        at = lambda h, m: datetime(2026, 11, 6, h, m)
        self.assertEqual(announcer.due_announcements(at(10, 0), None, 9, 18), ["It is 10 o'clock."])
        self.assertEqual(announcer.due_announcements(at(10, 0), 10, 9, 18), [])
        self.assertEqual(announcer.due_announcements(at(10, 1), None, 9, 18), [])
        self.assertEqual(announcer.due_announcements(at(20, 0), None, 9, 18), [])

    @patch.dict(os.environ, {"BAOFENG_LANG": "pt-BR"})
    def test_portuguese_time_check(self):
        self.assertEqual(announcer.due_announcements(datetime(2026, 11, 6, 10, 0), None, 9, 18), ["São 10 horas."])
        self.assertEqual(announcer.time_check(1), "É uma hora.")
        self.assertEqual(announcer.time_check(12), "É meio-dia.")
        self.assertEqual(announcer.time_check(15, "en"), "It is 15 o'clock.")


if __name__ == "__main__":
    unittest.main()
