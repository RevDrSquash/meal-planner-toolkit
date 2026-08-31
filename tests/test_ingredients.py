"""Quantity scaling and ingredient aggregation — synthetic only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ingredients import (  # noqa: E402
    IngredientError,
    aggregate_ingredients,
    apply_ingredient_replacement,
    categorize_ingredient,
    display_quantity,
    format_amount,
    infer_role,
    normalize_name,
    overlap_score,
    parse_ingredient_qty,
    scale_amount,
    scale_ingredient_line,
    serving_scale,
    shared_ingredient_names,
)


class ServingScaleTests(unittest.TestCase):
    def test_scale_amount_halves_and_increases(self) -> None:
        self.assertEqual(scale_amount(500, 4, 2), 250)
        self.assertEqual(scale_amount(1.5, 4, 6), 2.25)
        self.assertEqual(scale_amount(2, 4, 0), 0)

    def test_rejects_non_positive_source_servings(self) -> None:
        with self.assertRaises(IngredientError):
            scale_amount(1, 0, 4)
        with self.assertRaises(IngredientError):
            serving_scale("4", -1)

    def test_missing_source_servings_leaves_unscaled(self) -> None:
        factor, note = serving_scale(None, 6)
        self.assertEqual(factor, 1.0)
        self.assertIsNotNone(note)

    def test_parses_serves_string(self) -> None:
        factor, note = serving_scale("Serves 4", 6)
        self.assertEqual(factor, 1.5)
        self.assertIsNone(note)


class ParseAndNormalizeTests(unittest.TestCase):
    def test_weighted_and_count_lines(self) -> None:
        beef = parse_ingredient_qty("500 g ground beef")
        self.assertEqual(beef.amount, 500)
        self.assertEqual(beef.unit, "g")
        self.assertEqual(beef.name, "ground beef")
        self.assertTrue(beef.scalable)

        onion = parse_ingredient_qty("1 large onion, diced")
        self.assertEqual(onion.amount, 1)
        self.assertIsNone(onion.unit)
        self.assertEqual(onion.name, "onion")
        self.assertEqual(onion.notes, "diced")

    def test_can_and_clove_units(self) -> None:
        tomatoes = parse_ingredient_qty("1 can (796 ml) diced tomatoes")
        self.assertEqual(tomatoes.amount, 1)
        self.assertEqual(tomatoes.unit, "can")
        self.assertEqual(tomatoes.unit_size, "796 ml")
        self.assertEqual(tomatoes.name, "diced tomatoes")

        garlic = parse_ingredient_qty("2 garlic cloves")
        self.assertEqual(garlic.amount, 2)
        self.assertEqual(garlic.unit, "clove")
        self.assertEqual(garlic.name, "garlic")

    def test_to_taste_is_not_scalable(self) -> None:
        salt = parse_ingredient_qty("salt to taste")
        self.assertFalse(salt.scalable)
        self.assertIsNone(salt.amount)
        self.assertEqual(display_quantity(salt), "to taste")

    def test_normalize_aliases(self) -> None:
        self.assertEqual(normalize_name("yellow onion"), "onion")
        self.assertEqual(normalize_name("extra virgin olive oil"), "olive oil")


class ScaleLineTests(unittest.TestCase):
    def test_scales_metric_and_fractions(self) -> None:
        self.assertEqual(
            scale_ingredient_line("500 g ground beef", 4, 2),
            "250 g ground beef",
        )
        scaled = scale_ingredient_line("1½ cups milk", 4, 6)
        self.assertTrue(scaled.startswith("2¼ cups") or scaled.startswith("2 1/4"))
        self.assertIn("milk", scaled)

    def test_leaves_to_taste_unchanged(self) -> None:
        self.assertEqual(
            scale_ingredient_line("salt to taste", 4, 8),
            "salt to taste",
        )

    def test_format_common_fractions(self) -> None:
        self.assertEqual(format_amount(0.5, "cup"), "½")
        self.assertEqual(format_amount(1.5, "cup"), "1½")
        self.assertEqual(format_amount(2, "cup"), "2")
        self.assertEqual(format_amount(3, None), "3")
        self.assertEqual(format_amount(250, "g"), "250")


class AggregationTests(unittest.TestCase):
    def test_merges_duplicate_onions_across_recipes(self) -> None:
        merged = aggregate_ingredients(
            [
                ("1 onion, diced", "Weeknight Chili"),
                ("2 onions", "Tomato Skillet Pasta"),
            ]
        )
        onions = [item for item in merged if item["name"] == "onion"]
        self.assertEqual(len(onions), 1)
        self.assertEqual(onions[0]["amount"], 3)
        self.assertEqual(
            onions[0]["used_in"],
            ["Weeknight Chili", "Tomato Skillet Pasta"],
        )
        self.assertEqual(onions[0]["category"], "produce")
        self.assertNotIn("code", onions[0])
        self.assertNotIn("product_id", onions[0])

    def test_same_ingredient_different_can_sizes_stay_separate(self) -> None:
        merged = aggregate_ingredients(
            [
                "1 can (796 ml) diced tomatoes",
                "1 can (540 ml) diced tomatoes",
            ]
        )
        tomatoes = [item for item in merged if "tomato" in item["name"]]
        self.assertEqual(len(tomatoes), 2)

    def test_converts_compatible_mass_units(self) -> None:
        merged = aggregate_ingredients(
            [
                "500 g ground beef",
                "1 lb ground beef",
            ]
        )
        beef = [item for item in merged if item["name"] == "ground beef"]
        self.assertEqual(len(beef), 1)
        self.assertAlmostEqual(beef[0]["amount"] or 0, 500 + 453.6, places=1)
        self.assertEqual(beef[0]["unit"], "g")
        self.assertEqual(beef[0]["category"], "proteins")
        self.assertEqual(beef[0]["quantity_status"], "approximate")
        self.assertEqual(beef[0]["role"], "essential")

    def test_unquantified_lines_are_kept(self) -> None:
        merged = aggregate_ingredients(["salt to taste", "1 onion"])
        names = {item["name"] for item in merged}
        self.assertIn("onion", names)
        self.assertTrue(any("salt" in item["name"] for item in merged))

    def test_empty_and_blank_entries_are_ignored(self) -> None:
        self.assertEqual(aggregate_ingredients(["", "   "]), [])


class ReplacementAndOverlapTests(unittest.TestCase):
    def test_protein_substitution_keeps_quantity(self) -> None:
        line = apply_ingredient_replacement(
            "500 g ground beef",
            "ground beef",
            "ground turkey",
        )
        self.assertEqual(line, "500 g ground turkey")
        qty = parse_ingredient_qty(line)
        self.assertEqual(categorize_ingredient(qty), "proteins")

    def test_infer_optional_and_garnish_roles(self) -> None:
        garnish = parse_ingredient_qty("cilantro for garnish")
        self.assertEqual(infer_role(garnish), "garnish")
        optional = parse_ingredient_qty("sour cream, optional")
        self.assertEqual(infer_role(optional), "optional")
        core = parse_ingredient_qty("500 g ground beef")
        self.assertEqual(infer_role(core), "essential")

    def test_shared_ingredients_ignore_spices(self) -> None:
        chili = [
            "500 g ground beef",
            "1 onion",
            "2 cloves garlic",
            "1 tsp cumin",
            "salt to taste",
        ]
        pasta = [
            "200 g pasta",
            "1 onion",
            "2 cloves garlic",
            "1 tsp salt",
        ]
        shared = shared_ingredient_names(chili, pasta)
        self.assertEqual(shared, ["garlic", "onion"])
        self.assertGreater(overlap_score(chili, pasta), 0)
        self.assertEqual(overlap_score(["salt"], ["pepper"]), 0)


if __name__ == "__main__":
    unittest.main()
