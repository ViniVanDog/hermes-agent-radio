from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
from tests.support import ROOT
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("mcp_stub_server", ROOT / "examples/mcp_stub/server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
NOW = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)


class StubTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("BAOFENG_LANG", None)

    def test_time_is_one_short_line(self):
        line = server.radio_time(NOW)
        self.assertEqual(line, "It is 14:30 on Friday, 6 November.")
        self.assertNotIn("\n", line)

    def test_messages_follow_baofeng_lang(self):
        os.environ["BAOFENG_LANG"] = "pt-BR"
        self.assertEqual(server.radio_time(NOW), "São 14:30 de sexta-feira, 6 de novembro.")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            limit = server.RateLimit(1, 600)
            self.assertEqual(server.radio_note("pilhas", path, NOW, limit), "Nota salva: pilhas")
            self.assertIn("vazia", server.radio_note(" ", path, NOW, limit))
            self.assertIn("Notas demais", server.radio_note("mais", path, NOW, limit))
        self.assertEqual(set(server.MESSAGES["en"]), set(server.MESSAGES["pt"]))

    def test_note_saved_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            limit = server.RateLimit(5, 600)
            self.assertEqual(server.radio_note("  buy   batteries ", path, NOW, limit), "Saved note: buy batteries")
            self.assertIn("buy batteries", path.read_text())
            self.assertIn("empty", server.radio_note("   ", path, NOW, limit))
            self.assertIn("too long", server.radio_note("x" * 300, path, NOW, limit))
            self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_write_tool_is_rate_limited(self):
        clock = [0.0]
        limit = server.RateLimit(2, 600, clock=lambda: clock[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            self.assertTrue(server.radio_note("one", path, NOW, limit).startswith("Saved"))
            self.assertTrue(server.radio_note("two", path, NOW, limit).startswith("Saved"))
            self.assertIn("Too many", server.radio_note("three", path, NOW, limit))
            clock[0] = 601
            self.assertTrue(server.radio_note("four", path, NOW, limit).startswith("Saved"))
            self.assertEqual(len(path.read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
