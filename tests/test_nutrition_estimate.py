"""Ingredient parsing and macronutrient estimates — synthetic only."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from nutrition_estimate import (  # noqa: E402
    estimate_macros,
    lookup_food,
    parse_ingredient,
    parse_number,
    parse_servings,
)


class ParseTests(unittest.TestCase):
    def test_parse_servings(self) -> None:
        self.assertEqual(parse_servings("4"), 4.0)
        self.assertEqual(parse_servings("4 servings"), 4.0)
        self.assertEqual(parse_servings("Serves 6"), 6.0)
        self.assertIsNone(parse_servings(None))
        self.assertIsNone(parse_servings("a few"))

    def test_parse_number_fractions(self) -> None:
        self.assertEqual(parse_number("1.5"), 1.5)
        self.assertEqual(parse_number("1/2"), 0.5)
        self.assertEqual(parse_number("1 1/2"), 1.5)
        self.assertEqual(parse_number("½"), 0.5)
        self.assertEqual(parse_number("1½"), 1.5)
        self.assertEqual(parse_number("2½"), 2.5)

    def test_parse_weighted_ingredient(self) -> None:
        parsed = parse_ingredient("500 g ground beef")
        self.assertEqual(parsed.grams, 500)
        self.assertEqual(parsed.food, "ground beef")
        self.assertIsNotNone(parsed.macros)

    def test_parse_mixed_unicode_fraction(self) -> None:
        parsed = parse_ingredient("1½ cups flour")
        self.assertAlmostEqual(parsed.grams or 0, 180)
        self.assertEqual(parsed.food, "flour")
        self.assertIsNotNone(parsed.macros)

    def test_parse_can_with_parenthetical_size(self) -> None:
        parsed = parse_ingredient("1 can (796 ml) diced tomatoes")
        self.assertAlmostEqual(parsed.grams or 0, 796)
        self.assertEqual(parsed.food, "diced tomatoes")
        self.assertIsNotNone(parsed.macros)

    def test_parse_can_count_after_parenthetical_size(self) -> None:
        parsed = parse_ingredient("2 (796 ml) cans diced tomatoes")
        self.assertAlmostEqual(parsed.grams or 0, 1592)
        self.assertEqual(parsed.food, "diced tomatoes")
        self.assertIsNotNone(parsed.macros)

    def test_unit_token_does_not_match_food_prefixes(self) -> None:
        onion = parse_ingredient("1 large onion, diced")
        self.assertEqual(onion.grams, 150)
        self.assertEqual(onion.food, "onion")

        lemon = parse_ingredient("1 lemon")
        self.assertEqual(lemon.grams, 60)
        self.assertEqual(lemon.food, "lemon")

        garlic = parse_ingredient("2 garlic cloves")
        self.assertEqual(garlic.grams, 6)
        self.assertEqual(garlic.food, "garlic")

        green = parse_ingredient("1 green beans")
        self.assertIsNone(green.grams)
        self.assertEqual(green.food, "green beans")

    def test_parse_count_and_cloves(self) -> None:
        onion = parse_ingredient("1 onion, diced")
        self.assertEqual(onion.grams, 150)
        self.assertEqual(onion.food, "onion")
        garlic = parse_ingredient("2 cloves garlic")
        self.assertEqual(garlic.grams, 6)
        self.assertEqual(garlic.food, "garlic")

    def test_skip_to_taste(self) -> None:
        parsed = parse_ingredient("salt and pepper to taste")
        self.assertIsNone(parsed.grams)
        self.assertIsNone(parsed.macros)

    def test_lookup_aliases(self) -> None:
        self.assertIsNotNone(lookup_food("yellow onion"))
        self.assertIsNotNone(lookup_food("extra virgin olive oil"))
        self.assertIsNone(lookup_food("powdered unicorn horn"))


class EstimateTests(unittest.TestCase):
    def test_estimates_known_breakfast(self) -> None:
        result = estimate_macros(
            ["80 g oats", "240 ml milk", "1 banana"],
            "2",
        )
        self.assertIsNotNone(result)
        assert result is not None
        calories = int(result["calories"].split()[0])
        protein = int(result["protein"].split()[0])
        self.assertGreaterEqual(calories, 200)
        self.assertLessEqual(calories, 400)
        self.assertGreaterEqual(protein, 8)
        self.assertTrue(result["calories"].endswith("kcal"))
        self.assertTrue(result["protein"].endswith("g"))

    def test_estimates_chili_like_list(self) -> None:
        result = estimate_macros(
            [
                "500 g ground beef",
                "1 onion, diced",
                "2 cloves garlic",
                "1 can (796 ml) diced tomatoes",
                "1 can (540 ml) red kidney beans",
                "2 tbsp chili powder",
                "1 tsp cumin",
            ],
            "4",
        )
        self.assertIsNotNone(result)
        assert result is not None
        calories = int(result["calories"].split()[0])
        self.assertGreaterEqual(calories, 300)
        self.assertLessEqual(calories, 800)

    def test_refuses_without_servings(self) -> None:
        self.assertIsNone(estimate_macros(["80 g oats", "240 ml milk"], None))

    def test_refuses_unknown_substantial_ingredient(self) -> None:
        self.assertIsNone(
            estimate_macros(
                ["400 g seitan", "1 onion"],
                "2",
            )
        )

    def test_refuses_single_matched_item(self) -> None:
        self.assertIsNone(estimate_macros(["80 g oats"], "1"))

    def test_refuses_empty(self) -> None:
        self.assertIsNone(estimate_macros([], "2"))
        self.assertIsNone(estimate_macros(["salt to taste", "pepper to taste"], "2"))

    def test_estimates_mixed_unicode_amounts(self) -> None:
        result = estimate_macros(
            ["1½ cups flour", "80 g oats", "240 ml milk"],
            "4",
        )
        self.assertIsNotNone(result)

    def test_optional_garnish_does_not_block_estimate(self) -> None:
        result = estimate_macros(
            [
                "80 g oats",
                "240 ml milk",
                "2 tbsp sour cream, optional",
                "1/4 cup cheese for serving",
            ],
            "2",
        )
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
