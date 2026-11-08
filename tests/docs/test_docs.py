"""The Portuguese and English docs stay in step: same pages, same structure, working links."""
import os
from pathlib import Path
import re
import unittest

from tests.support import ROOT
PT = ROOT / "docs"
EN = PT / "en"
PAIRS = [(ROOT / "README.en.md", ROOT / "README.md")] + \
    [(page, PT / page.name) for page in sorted(EN.glob("*.md"))]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def outline(path):
    """Heading levels and code-fence count, ignoring '#' lines inside code blocks."""
    levels, fences, in_code = [], 0, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fences += 1
            in_code = not in_code
        elif not in_code and re.match(r"#{1,6} ", line):
            levels.append(len(line.split(" ", 1)[0]))
    return levels, fences


def links(path):
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    return [target for target in LINK.findall(text) if not re.match(r"[a-z]+:", target)]


class DocsTests(unittest.TestCase):
    def test_every_page_has_an_english_twin(self):
        self.assertEqual(sorted(p.name for p in PT.glob("*.md")), sorted(p.name for p in EN.glob("*.md")))
        for en, pt in PAIRS:
            self.assertTrue(en.exists(), en)
            self.assertTrue(pt.exists(), pt)

    def test_twins_have_the_same_structure(self):
        for en, pt in PAIRS:
            with self.subTest(page=en.name):
                self.assertEqual(outline(pt), outline(en), "headings or code blocks differ; update both")

    def test_twins_link_to_each_other(self):
        for en, pt in PAIRS:
            with self.subTest(page=en.name):
                self.assertIn(os.path.relpath(pt, en.parent), links(en))
                self.assertIn(os.path.relpath(en, pt.parent), links(pt))

    def test_relative_links_resolve(self):
        for page in [p for pair in PAIRS for p in pair]:
            for target in links(page):
                path = target.split("#")[0]
                if path:
                    with self.subTest(page=page.relative_to(ROOT).as_posix(), link=target):
                        self.assertTrue((page.parent / path).exists())


if __name__ == "__main__":
    unittest.main()
