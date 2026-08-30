"""Markdown recipe conversion — synthetic fixtures only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recipe_core import RecipeExistsError, recipe_from_jsonld  # noqa: E402
from recipe_from_markdown import convert_file, parse_markdown  # noqa: E402


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
            self.assertIn("Source file: weeknight-chili.md", html)
            self.assertNotIn("(estimated)", html)
            parsed = recipe_from_jsonld(html, None)
            self.assertEqual(parsed["source_file"], "weeknight-chili.md")

    def test_filename_uses_recipe_name_not_scratch_stem(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "weeknight-chili.md"
        with tempfile.TemporaryDirectory() as raw:
            scratch = Path(raw) / "scratch.md"
            scratch.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            path = convert_file(scratch, Path(raw) / "out", force=True)
            self.assertEqual(path.name, "weeknight-chili.html")

    def test_missing_heading_raises(self) -> None:
        text = (ROOT / "tests" / "fixtures" / "no-heading.md").read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_markdown(text)

    def test_minimal_markdown_estimates_macros(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "minimal-recipe.md"
        recipe = parse_markdown(fixture.read_text(encoding="utf-8"), source_file="minimal-recipe.md")
        self.assertEqual(recipe["nutrition"], [])
        with tempfile.TemporaryDirectory() as raw:
            path = convert_file(fixture, Path(raw), force=True)
            html = path.read_text(encoding="utf-8")
            self.assertIn("(estimated)", html)
            parsed = recipe_from_jsonld(html, None)
            labels = {item["label"] for item in parsed["nutrition"]}
            self.assertTrue(
                {"Calories", "Protein", "Fat", "Carbohydrates"} <= labels
            )

    def test_source_url_from_preamble(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "sourced-markdown.md"
        recipe = parse_markdown(
            fixture.read_text(encoding="utf-8"),
            source_file="sourced-markdown.md",
        )
        self.assertEqual(recipe["source_url"], "https://example.test/pantry-beans")
        with tempfile.TemporaryDirectory() as raw:
            path = convert_file(fixture, Path(raw), force=True)
            html = path.read_text(encoding="utf-8")
            self.assertIn("https://example.test/pantry-beans", html)
            self.assertIn("Source file: sourced-markdown.md", html)
            parsed = recipe_from_jsonld(html, None)
            calories = next(item for item in parsed["nutrition"] if item["label"] == "Calories")
            self.assertFalse(calories["estimated"])
            protein = next(item for item in parsed["nutrition"] if item["label"] == "Protein")
            self.assertFalse(protein["estimated"])
            # Fat/carbs were not sourced; they may be estimated from ingredients.
            fat = next(item for item in parsed["nutrition"] if item["label"] == "Fat")
            self.assertTrue(fat["estimated"])

    def test_rerun_does_not_duplicate(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "weeknight-chili.md"
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            first = convert_file(fixture, out_dir, force=False)
            second = convert_file(fixture, out_dir, force=False)
            self.assertEqual(first, second)
            self.assertEqual(len(list(out_dir.glob("*.html"))), 1)
            # A different recipe that would collide on filename if names matched
            # is not this case; converting again with force overwrites.
            convert_file(fixture, out_dir, force=True)
            self.assertEqual(len(list(out_dir.glob("*.html"))), 1)

    def test_collision_still_errors(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "weeknight-chili.md"
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            convert_file(fixture, out_dir, force=True)
            stranger = out_dir / "weeknight-chili.html"
            stranger.write_text("<html><h1>Not Chili</h1></html>", encoding="utf-8")
            with self.assertRaises(RecipeExistsError) as ctx:
                convert_file(fixture, out_dir, force=False)
            self.assertFalse(ctx.exception.same_recipe)


if __name__ == "__main__":
    unittest.main()
