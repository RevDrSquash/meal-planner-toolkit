"""schema.org Recipe parsing — local fixtures only, no live sites."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from import_recipe import ingest  # noqa: E402
from recipe_core import (  # noqa: E402
    RECIPE_TEMPLATE,
    RecipeExistsError,
    enrich_nutrition,
    recipe_from_jsonld,
    write_recipe,
)


FIXTURES = ROOT / "tests" / "fixtures"


class JsonLdFixtureTests(unittest.TestCase):
    def test_parses_complete_recipe(self) -> None:
        page = (FIXTURES / "schemaorg-recipe.html").read_text(encoding="utf-8")
        recipe = recipe_from_jsonld(page, "https://example.test/creamy-tomato-pasta")
        self.assertEqual(recipe["name"], "Creamy Tomato Pasta")
        self.assertEqual(recipe["serves"], "4 servings")
        self.assertEqual(recipe["total_time"], "35 min")
        self.assertEqual(recipe["tags"], ["Italian", "Pasta"])
        self.assertTrue(recipe["image"].endswith("creamy-tomato-pasta.jpg"))
        self.assertIn("400 g pasta", recipe["ingredients"])
        self.assertEqual(len(recipe["instructions"]), 3)
        self.assertIn("Boil the pasta", recipe["instructions"][0])
        labels = {item["label"]: item for item in recipe["nutrition"]}
        self.assertEqual(labels["Calories"]["value"], "620 kcal")
        self.assertFalse(labels["Calories"]["estimated"])
        self.assertEqual(labels["Fiber"]["value"], "5 g")
        self.assertEqual(recipe["source_url"], "https://example.test/creamy-tomato-pasta")

    def test_parses_graph_and_howto_section(self) -> None:
        page = (FIXTURES / "schemaorg-graph.html").read_text(encoding="utf-8")
        recipe = recipe_from_jsonld(page, "https://example.test/soup")
        self.assertEqual(recipe["name"], "Graph Wrapped Soup")
        self.assertEqual(recipe["serves"], "2")
        self.assertIn("<strong>Cook</strong>", recipe["instructions"][0])
        self.assertIn("Sweat the onion.", recipe["instructions"][0])

    def test_missing_recipe_raises(self) -> None:
        page = (FIXTURES / "schemaorg-malformed.html").read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            recipe_from_jsonld(page, "https://example.test/missing")

    def test_empty_and_non_recipe_payloads(self) -> None:
        with self.assertRaises(RuntimeError):
            recipe_from_jsonld("<html></html>", None)
        with self.assertRaises(RuntimeError):
            recipe_from_jsonld(
                '<script type="application/ld+json">{"@type":"WebPage"}</script>',
                None,
            )

    def test_untitled_when_name_missing(self) -> None:
        page = """<script type="application/ld+json">
        {"@type":"Recipe","recipeIngredient":["1 egg"],
         "recipeInstructions":["Cook."]}</script>"""
        recipe = recipe_from_jsonld(page, None)
        self.assertEqual(recipe["name"], "Untitled Recipe")
        self.assertEqual(recipe["ingredients"], ["1 egg"])
        self.assertEqual(recipe["nutrition"], [])

    def test_no_nutrition_is_estimated_on_enrich(self) -> None:
        page = (FIXTURES / "schemaorg-no-nutrition.html").read_text(encoding="utf-8")
        recipe = enrich_nutrition(recipe_from_jsonld(page, "https://example.test/oats"))
        labels = {item["label"]: item for item in recipe["nutrition"]}
        for key in ("Calories", "Protein", "Fat", "Carbohydrates"):
            self.assertIn(key, labels)
            self.assertTrue(labels[key]["estimated"])
        self.assertGreater(int(labels["Calories"]["value"].split()[0]), 100)

    def test_partial_nutrition_keeps_sourced_values(self) -> None:
        page = """<script type="application/ld+json">
        {"@type":"Recipe","name":"Overnight Oats","recipeYield":"2",
         "recipeIngredient":["80 g oats","240 ml milk","1 banana"],
         "recipeInstructions":["Mix."],
         "nutrition":{"calories":"300 kcal"}}</script>"""
        recipe = enrich_nutrition(recipe_from_jsonld(page, None))
        labels = {item["label"]: item for item in recipe["nutrition"]}
        self.assertEqual(labels["Calories"]["value"], "300 kcal")
        self.assertFalse(labels["Calories"]["estimated"])
        self.assertTrue(labels["Protein"]["estimated"])

    def test_import_local_html_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            path, already = ingest(
                str(FIXTURES / "schemaorg-recipe.html"),
                out_dir,
                force=False,
            )
            self.assertFalse(already)
            self.assertEqual(path.name, "creamy-tomato-pasta.html")
            html = path.read_text(encoding="utf-8")
            self.assertIn("Creamy Tomato Pasta", html)
            self.assertIn("620 kcal", html)
            again, already = ingest(
                str(FIXTURES / "schemaorg-recipe.html"),
                out_dir,
                force=False,
            )
            self.assertTrue(already)
            self.assertEqual(again, path)
            self.assertEqual(len(list(out_dir.glob("*.html"))), 1)

    def test_canonical_template_is_parseable(self) -> None:
        recipe = recipe_from_jsonld(
            RECIPE_TEMPLATE.read_text(encoding="utf-8"),
            None,
        )
        self.assertEqual(recipe["name"], "Weeknight Chili")
        self.assertEqual(recipe["source_file"], "hand-authored")
        self.assertGreaterEqual(len(recipe["ingredients"]), 5)

    def test_url_ingest_collision_with_different_card(self) -> None:
        page = (FIXTURES / "schemaorg-recipe.html").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            occupant = out_dir / "creamy-tomato-pasta.html"
            occupant.write_text(
                "<html><h1>Unrelated Card</h1></html>",
                encoding="utf-8",
            )
            with patch("import_recipe.fetch_page", return_value=page):
                with self.assertRaises(RecipeExistsError) as ctx:
                    ingest(
                        "https://example.test/creamy-tomato-pasta",
                        out_dir,
                        force=False,
                    )
            self.assertFalse(ctx.exception.same_recipe)
            self.assertEqual(
                occupant.read_text(encoding="utf-8"),
                "<html><h1>Unrelated Card</h1></html>",
            )

    def test_url_ingest_same_recipe_is_noop(self) -> None:
        page = (FIXTURES / "schemaorg-recipe.html").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as raw:
            out_dir = Path(raw)
            with patch("import_recipe.fetch_page", return_value=page):
                path, already = ingest(
                    "https://example.test/creamy-tomato-pasta",
                    out_dir,
                    force=False,
                )
                self.assertFalse(already)
                again, already = ingest(
                    "https://example.test/creamy-tomato-pasta",
                    out_dir,
                    force=False,
                )
            self.assertTrue(already)
            self.assertEqual(again, path)
            self.assertEqual(len(list(out_dir.glob("*.html"))), 1)

    def test_pdf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pdf = Path(raw) / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake")
            with self.assertRaises(ValueError) as ctx:
                ingest(str(pdf), Path(raw), force=False)
            self.assertIn("PDF", str(ctx.exception))

    def test_write_round_trips_jsonld(self) -> None:
        page = (FIXTURES / "schemaorg-recipe.html").read_text(encoding="utf-8")
        recipe = recipe_from_jsonld(page, "https://example.test/creamy-tomato-pasta")
        with tempfile.TemporaryDirectory() as raw:
            path = write_recipe(recipe, Path(raw) / "creamy-tomato-pasta.html", True)
            loaded = recipe_from_jsonld(path.read_text(encoding="utf-8"), None)
            self.assertEqual(loaded["name"], recipe["name"])
            self.assertEqual(loaded["ingredients"], recipe["ingredients"])
            self.assertEqual(loaded["source_url"], recipe["source_url"])


if __name__ == "__main__":
    unittest.main()
