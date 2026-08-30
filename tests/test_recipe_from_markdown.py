"""Markdown recipe conversion — synthetic weeknight chili only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recipe_from_markdown import convert_file  # noqa: E402


class RecipeFromMarkdownTests(unittest.TestCase):
    def test_converts_fixture(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "weeknight-chili.md"
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            path = convert_file(fixture, out_dir, force=True)
            html = path.read_text(encoding="utf-8")
            self.assertEqual(path.name, "weeknight-chili.html")
            self.assertIn("Weeknight Chili", html)
            self.assertIn("500 g ground beef", html)
            self.assertIn("Brown the ground beef", html)
            self.assertIn("480 kcal", html)


if __name__ == "__main__":
    unittest.main()
