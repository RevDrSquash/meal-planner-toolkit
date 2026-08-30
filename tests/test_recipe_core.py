"""Helpers for slugs, durations, rendering, and duplicate detection."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recipe_core import (  # noqa: E402
    RecipeExistsError,
    find_existing_recipe,
    format_duration,
    iso_duration_from_human,
    normalize_url,
    peek_recipe_identity,
    recipe_filename,
    recipe_from_jsonld,
    render_html,
    slug_from_url,
    slugify,
    write_recipe,
)


def _sample_recipe(**overrides) -> dict:
    recipe = {
        "name": "Weeknight Chili",
        "serves": "4",
        "total_time": "45 min",
        "tags": ["beef"],
        "image": None,
        "ingredients": ["500 g ground beef", "1 onion"],
        "nutrition": [
            {"label": "Calories", "value": "480 kcal", "estimated": False},
            {"label": "Protein", "value": "32 g", "estimated": False},
            {"label": "Fat", "value": "22 g", "estimated": False},
            {"label": "Carbohydrates", "value": "34 g", "estimated": False},
        ],
        "instructions": ["Brown the beef."],
        "notes": None,
        "source_url": "https://example.test/weeknight-chili",
        "source_file": None,
    }
    recipe.update(overrides)
    return recipe


class HelperTests(unittest.TestCase):
    def test_normalize_url_adds_https_and_strips_slash(self) -> None:
        self.assertEqual(
            normalize_url("example.test/recipe/"),
            "https://example.test/recipe",
        )

    def test_slugify_and_url_slug(self) -> None:
        self.assertEqual(slugify("Weeknight Chili"), "weeknight-chili")
        self.assertEqual(
            slug_from_url(
                "https://example.test/recipes/creamy-tuscan-chicken-a1b2c3d4e5f6a1b2"
            ),
            "creamy-tuscan-chicken",
        )
        with self.assertRaises(ValueError):
            slugify("???")

    def test_filename_prefers_human_readable_name(self) -> None:
        recipe = _sample_recipe(
            source_url="https://example.test/recipes/abc123def456abc123"
        )
        self.assertEqual(recipe_filename(recipe), "weeknight-chili.html")
        untitled = _sample_recipe(
            name="Untitled Recipe",
            source_url="https://example.test/recipes/fallback-slug-here",
        )
        self.assertEqual(recipe_filename(untitled), "fallback-slug-here.html")

    def test_duration_round_trip(self) -> None:
        self.assertEqual(format_duration("PT1H20M"), "1 hr 20 min")
        self.assertEqual(format_duration("PT35M"), "35 min")
        self.assertEqual(iso_duration_from_human("1 hr 20 min"), "PT1H20M")
        self.assertEqual(iso_duration_from_human("45 min"), "PT45M")
        self.assertIsNone(iso_duration_from_human("until done"))


class RenderTests(unittest.TestCase):
    def test_render_includes_jsonld_and_provenance(self) -> None:
        html = render_html(_sample_recipe(), imported_on="2026-01-15")
        self.assertIn("<h1>Weeknight Chili</h1>", html)
        self.assertIn('application/ld+json', html)
        self.assertIn("500 g ground beef", html)
        self.assertIn("480 kcal", html)
        self.assertIn("https://example.test/weeknight-chili", html)
        self.assertIn("Imported 2026-01-15", html)
        self.assertNotIn("(estimated)", html)
        parsed = recipe_from_jsonld(html, None)
        self.assertEqual(parsed["name"], "Weeknight Chili")
        self.assertEqual(parsed["source_url"], "https://example.test/weeknight-chili")
        self.assertEqual(parsed["serves"], "4")

    def test_render_marks_estimated_macros(self) -> None:
        recipe = _sample_recipe(
            nutrition=[
                {"label": "Calories", "value": "270 kcal", "estimated": True},
                {"label": "Protein", "value": "11 g", "estimated": True},
            ]
        )
        html = render_html(recipe, imported_on="2026-01-15")
        self.assertIn('data-estimated="true"', html)
        self.assertIn("(estimated)", html)
        self.assertIn("inferred from ingredients", html)
        parsed = recipe_from_jsonld(html, None)
        calories = next(item for item in parsed["nutrition"] if item["label"] == "Calories")
        self.assertTrue(calories["estimated"])

    def test_escapes_instruction_text_from_jsonld(self) -> None:
        page = (
            '<script type="application/ld+json">'
            '{"@type":"Recipe","name":"X","recipeIngredient":["1 egg"],'
            '"recipeInstructions":["Heat & stir <em>gently</em>"]}'
            "</script>"
        )
        recipe = recipe_from_jsonld(page, None)
        html = render_html(recipe, imported_on="2026-01-15")
        self.assertIn(
            '<div class="step-body">Heat &amp; stir &lt;em&gt;gently&lt;/em&gt;</div>',
            html,
        )


class DuplicateTests(unittest.TestCase):
    def test_write_refuses_silent_duplicate(self) -> None:
        recipe = _sample_recipe()
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            path = write_recipe(recipe, out_dir / recipe_filename(recipe), force=False)
            self.assertTrue(path.exists())
            with self.assertRaises(RecipeExistsError) as ctx:
                write_recipe(recipe, out_dir / recipe_filename(recipe), force=False)
            self.assertTrue(ctx.exception.same_recipe)
            self.assertEqual(len(list(out_dir.glob("*.html"))), 1)

    def test_finds_same_source_url_under_renamed_file(self) -> None:
        recipe = _sample_recipe()
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            renamed = out_dir / "custom-filename.html"
            write_recipe(recipe, renamed, force=True)
            found = find_existing_recipe(out_dir, recipe)
            self.assertEqual(found, renamed)

    def test_force_overwrites_existing_identity(self) -> None:
        recipe = _sample_recipe()
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            renamed = out_dir / "legacy-slug.html"
            write_recipe(recipe, renamed, force=True)
            updated = dict(recipe)
            updated["notes"] = "<p>Leftovers freeze well.</p>"
            path = write_recipe(updated, out_dir / recipe_filename(updated), force=True)
            self.assertEqual(path, renamed)
            self.assertIn("Leftovers freeze well", path.read_text(encoding="utf-8"))
            self.assertEqual(len(list(out_dir.glob("*.html"))), 1)

    def test_find_existing_ignores_different_card_on_slug(self) -> None:
        recipe = _sample_recipe()
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            (out_dir / "weeknight-chili.html").write_text(
                "<html><h1>Unrelated Card</h1></html>",
                encoding="utf-8",
            )
            self.assertIsNone(find_existing_recipe(out_dir, recipe))

    def test_collision_with_different_recipe_same_filename(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            write_recipe(_sample_recipe(), out_dir / "weeknight-chili.html", force=True)
            other = _sample_recipe(
                name="Weeknight Chili",
                source_url="https://other.test/chili",
            )
            # Same name is treated as the same household card (no second file).
            with self.assertRaises(RecipeExistsError) as ctx:
                write_recipe(other, out_dir / "weeknight-chili.html", force=False)
            self.assertTrue(ctx.exception.same_recipe)

            stranger = _sample_recipe(
                name="Something Else",
                source_url="https://other.test/else",
            )
            # Drop a conflicting file at the stranger's intended name.
            (out_dir / "something-else.html").write_text(
                "<html><h1>Unrelated Card</h1></html>",
                encoding="utf-8",
            )
            with self.assertRaises(RecipeExistsError) as ctx:
                write_recipe(stranger, out_dir / "something-else.html", force=False)
            self.assertFalse(ctx.exception.same_recipe)

    def test_peek_identity_without_jsonld(self) -> None:
        html = (
            "<html><h1>Hand Card</h1>"
            '<p>Source: <a href="https://example.test/hand">https://example.test/hand</a></p>'
            "<p>Source file: notebook.md</p></html>"
        )
        identity = peek_recipe_identity(html)
        self.assertEqual(identity["name"], "Hand Card")
        self.assertEqual(identity["source_url"], "https://example.test/hand")
        self.assertEqual(identity["source_file"], "notebook.md")


if __name__ == "__main__":
    unittest.main()
