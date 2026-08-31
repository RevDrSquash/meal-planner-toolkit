"""Retailer-independent shopping-list handoff — synthetic fixtures only."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from meal_plan import build_plan  # noqa: E402
from recipe_from_markdown import parse_markdown  # noqa: E402
from shopping_list import (  # noqa: E402
    KIND,
    SCHEMA_VERSION,
    ShoppingListError,
    assert_retailer_independent,
    build_shopping_list,
    main,
    names_match,
    parse_stock_markdown,
    render_markdown,
    requirements_from_meals,
    shopping_list_to_json,
)

FIXTURES = ROOT / "tests" / "fixtures"
STOCK = FIXTURES / "shopping-list"


def _recipe(name: str) -> dict:
    path = FIXTURES / name
    recipe = parse_markdown(path.read_text(encoding="utf-8"), source_file=path.name)
    recipe["file"] = path.with_suffix(".html").name
    return recipe


def _chili() -> dict:
    return _recipe("weeknight-chili.md")


def _pasta() -> dict:
    return _recipe("tomato-skillet.md")


def _sample_plan() -> dict:
    return build_plan(
        date_value="2026-08-31",
        household="2 people (synthetic)",
        period="2 dinners",
        servings_per_meal=2,
        meals=[
            {
                "day": "Day 1",
                "slot": "dinner",
                "recipe": "Weeknight Chili",
                "servings": 4,
            },
            {
                "day": "Day 2",
                "slot": "dinner",
                "recipe": "Tomato Skillet Pasta",
                "servings": 2,
            },
        ],
        recipes={
            "Weeknight Chili": _chili(),
            "Tomato Skillet Pasta": _pasta(),
        },
        deviations=[
            {
                "recipe": "Weeknight Chili",
                "change": "Use ground turkey instead of ground beef",
                "reason": "preference",
                "replace": {"ground beef": "ground turkey"},
            }
        ],
    )


def _stock() -> tuple[str, str]:
    return (
        (STOCK / "pantry.md").read_text(encoding="utf-8"),
        (STOCK / "staples.md").read_text(encoding="utf-8"),
    )


def _item(shopping: dict, name: str) -> dict:
    matches = [item for item in shopping["items"] if item["name"] == name]
    if not matches:
        raise AssertionError(f"missing {name!r} in {[i['name'] for i in shopping['items']]}")
    return matches[0]


class AggregationTests(unittest.TestCase):
    def test_merges_duplicate_ingredients_across_recipes(self) -> None:
        requirements = requirements_from_meals(
            [
                {
                    "recipe": "Weeknight Chili",
                    "recipe_serves": 4,
                    "planned_serves": 4,
                    "ingredients": ["1 onion, diced", "500 g ground beef"],
                },
                {
                    "recipe": "Tomato Skillet Pasta",
                    "recipe_serves": 2,
                    "planned_serves": 2,
                    "ingredients": ["1 onion, diced", "200 g pasta"],
                },
            ]
        )
        shopping = build_shopping_list(plan={"ingredients": requirements})
        onion = _item(shopping, "onion")
        self.assertEqual(onion["amount"], 2)
        self.assertEqual(onion["quantity_status"], "exact")
        self.assertEqual(
            onion["sources"],
            ["Weeknight Chili", "Tomato Skillet Pasta"],
        )
        self.assertEqual(onion["role"], "essential")
        self.assertEqual(onion["origin"], "recipe")

    def test_merges_serving_scaled_compatible_units(self) -> None:
        requirements = requirements_from_meals(
            [
                {
                    "recipe": "Chili",
                    "recipe_serves": 4,
                    "planned_serves": 4,
                    "ingredients": ["500 g ground beef"],
                },
                {
                    "recipe": "Tacos",
                    "recipe_serves": 2,
                    "planned_serves": 4,
                    "ingredients": ["1 lb ground beef"],
                },
            ]
        )
        shopping = build_shopping_list(plan={"ingredients": requirements})
        beef = _item(shopping, "ground beef")
        # 500 g (unscaled) + 2 lb (scaled 2→4)
        self.assertAlmostEqual(beef["amount"] or 0, 500 + 2 * 453.6, places=1)
        self.assertEqual(beef["unit"], "g")
        self.assertEqual(beef["quantity_status"], "approximate")
        self.assertIn("Chili", beef["sources"])
        self.assertIn("Tacos", beef["sources"])


class UncertainQuantityTests(unittest.TestCase):
    def test_incompatible_units_are_not_invented(self) -> None:
        shopping = build_shopping_list(
            plan={
                "ingredients": [
                    {
                        "name": "diced tomatoes",
                        "amount": 1,
                        "unit": "can",
                        "unit_size": "796 ml",
                        "display": "1 can (796 ml)",
                        "category": "pantry",
                        "used_in": ["Chili"],
                    },
                    {
                        "name": "diced tomatoes",
                        "amount": 400,
                        "unit": "g",
                        "display": "400 g",
                        "category": "pantry",
                        "used_in": ["Pasta"],
                    },
                ]
            }
        )
        tomatoes = _item(shopping, "diced tomatoes")
        self.assertEqual(tomatoes["quantity_status"], "uncertain")
        self.assertIsNone(tomatoes["amount"])
        self.assertIn("1 can (796 ml)", tomatoes["display"])
        self.assertIn("400 g", tomatoes["display"])
        self.assertEqual(len(tomatoes.get("parts") or []), 2)

    def test_ambiguous_unquantified_plus_counted_stays_uncertain(self) -> None:
        shopping = build_shopping_list(
            meals=[
                {
                    "recipe": "Soup",
                    "recipe_serves": 4,
                    "planned_serves": 4,
                    "ingredients": ["salt to taste", "1 tsp salt"],
                }
            ]
        )
        salt = _item(shopping, "salt")
        self.assertEqual(salt["quantity_status"], "uncertain")
        self.assertIsNone(salt["amount"])
        self.assertIn("+", salt["display"])


class PantryAndStapleTests(unittest.TestCase):
    def test_clear_pantry_stock_is_assumed_not_bought(self) -> None:
        pantry, staples = _stock()
        shopping = build_shopping_list(
            meals=[
                {
                    "recipe": "Weeknight Chili",
                    "recipe_serves": 4,
                    "planned_serves": 4,
                    "ingredients": [
                        "500 g ground beef",
                        "2 tbsp chili powder",
                        "1 tsp cumin",
                        "salt to taste",
                    ],
                }
            ],
            pantry=pantry,
            staples=staples,
        )
        self.assertEqual(_item(shopping, "chili powder")["pantry_status"], "assumed_in_pantry")
        self.assertEqual(_item(shopping, "cumin")["pantry_status"], "assumed_in_pantry")
        self.assertEqual(_item(shopping, "ground beef")["pantry_status"], "buy")
        markdown = render_markdown(shopping)
        self.assertIn("## Assumed pantry stock", markdown)
        self.assertIn("chili powder", markdown)
        self.assertIn("**ground beef**", markdown)

    def test_temporarily_out_overrides_pantry(self) -> None:
        pantry, staples = _stock()
        shopping = build_shopping_list(
            meals=[
                {
                    "recipe": "Stir fry",
                    "ingredients": ["2 tbsp soy sauce", "1 tbsp oil"],
                }
            ],
            pantry=pantry,
            staples=staples,
            pantry_out=["soy sauce"],
        )
        soy = _item(shopping, "soy sauce")
        self.assertEqual(soy["pantry_status"], "buy")
        self.assertTrue(any("temporarily out" in note for note in soy["notes"]))
        oil = _item(shopping, "oil")
        self.assertEqual(oil["pantry_status"], "assumed_in_pantry")

    def test_uncertain_pantry_and_when_low_staples_need_confirmation(self) -> None:
        pantry, staples = _stock()
        shopping = build_shopping_list(
            meals=[
                {
                    "recipe": "Weeknight Chili",
                    "recipe_serves": 4,
                    "planned_serves": 4,
                    "ingredients": ["1 onion, diced", "1 can (540 ml) red kidney beans"],
                }
            ],
            pantry=pantry,
            staples=staples,
        )
        onion = _item(shopping, "onion")
        self.assertEqual(onion["pantry_status"], "buy")
        self.assertTrue(onion["staple"])
        self.assertTrue(any("when low" in note for note in onion["notes"]))

        eggs = _item(shopping, "egg")
        self.assertEqual(eggs["origin"], "staple")
        self.assertEqual(eggs["pantry_status"], "needs_confirmation")
        self.assertTrue(any("below 4" in note or "when" in note for note in eggs["notes"]))

        beans = next(
            item
            for item in shopping["items"]
            if item["origin"] == "staple" and "bean" in item["name"]
        )
        self.assertEqual(beans["pantry_status"], "buy")
        self.assertNotEqual(beans["name"], _item(shopping, "red kidney beans")["name"])

    def test_template_how_to_use_section_is_ignored(self) -> None:
        parsed = parse_stock_markdown(
            "# Pantry\n\n## How to use\n\n- Edit this file when usual stock changes\n\n"
            "## Dry goods\n\n- Salt\n"
        )
        names = {item["name"] for item in parsed}
        self.assertEqual(names, {"salt"})

    def test_cooking_oil_covers_olive_oil_not_sesame(self) -> None:
        self.assertTrue(names_match("Cooking oil", "olive oil"))
        self.assertFalse(names_match("Cooking oil", "sesame oil"))


class SubstitutionAndRoleTests(unittest.TestCase):
    def test_planning_substitution_is_preserved(self) -> None:
        pantry, staples = _stock()
        shopping = build_shopping_list(
            plan=_sample_plan(),
            pantry=pantry,
            staples=staples,
        )
        turkey = _item(shopping, "ground turkey")
        self.assertEqual(turkey["pantry_status"], "buy")
        self.assertEqual(turkey["substitutions"][0]["from"], "ground beef")
        self.assertEqual(turkey["substitutions"][0]["to"], "ground turkey")
        names = {item["name"] for item in shopping["items"]}
        self.assertNotIn("ground beef", names)

    def test_omit_deviation_drops_ingredient(self) -> None:
        shopping = build_shopping_list(
            meals=[
                {
                    "recipe": "Tacos",
                    "ingredients": [
                        "500 g ground beef",
                        "cilantro for garnish",
                    ],
                    "deviations": [{"omit": "cilantro"}],
                }
            ]
        )
        names = {item["name"] for item in shopping["items"]}
        self.assertIn("ground beef", names)
        self.assertFalse(any("cilantro" in name for name in names))

    def test_optional_and_garnish_roles_survive_merge(self) -> None:
        shopping = build_shopping_list(
            meals=[
                {
                    "recipe": "Tacos",
                    "ingredients": [
                        {"line": "sour cream, optional", "role": "optional"},
                        "1 tomato",
                    ],
                },
                {
                    "recipe": "Chili",
                    "ingredients": ["cilantro for garnish", "1 tomato"],
                },
            ]
        )
        cream = _item(shopping, "sour cream")
        self.assertEqual(cream["role"], "optional")
        cilantro = next(
            item for item in shopping["items"] if "cilantro" in item["name"]
        )
        self.assertEqual(cilantro["role"], "garnish")
        tomato = _item(shopping, "tomato")
        self.assertEqual(tomato["role"], "essential")
        self.assertEqual(tomato["amount"], 2)


class SerializationTests(unittest.TestCase):
    def test_artifact_is_retailer_independent_and_stable(self) -> None:
        pantry, staples = _stock()
        shopping = build_shopping_list(
            plan=_sample_plan(),
            pantry=pantry,
            staples=staples,
        )
        self.assertEqual(shopping["version"], SCHEMA_VERSION)
        self.assertEqual(shopping["kind"], KIND)
        self.assertEqual(shopping["source_plan"], "2026-08-31")
        for item in shopping["items"]:
            for key in (
                "name",
                "display",
                "role",
                "sources",
                "pantry_status",
                "origin",
                "quantity_status",
            ):
                self.assertIn(key, item)
            self.assertNotIn("code", item)
            self.assertNotIn("product_id", item)
            self.assertNotIn("sku", item)
            self.assertNotIn("price", item)

        text = shopping_list_to_json(shopping)
        loaded = json.loads(text)
        self.assertEqual(loaded["kind"], KIND)
        assert_retailer_independent(loaded)

        with self.assertRaises(ShoppingListError):
            assert_retailer_independent(
                {
                    "version": 1,
                    "kind": KIND,
                    "items": [
                        {
                            "name": "ground turkey",
                            "display": "500 g",
                            "product_id": "20123456_EA",
                        }
                    ],
                }
            )

    def test_markdown_is_usable_without_a_provider(self) -> None:
        pantry, staples = _stock()
        shopping = build_shopping_list(
            plan=_sample_plan(),
            pantry=pantry,
            staples=staples,
            pantry_out=["soy sauce"],
        )
        markdown = render_markdown(shopping)
        self.assertIn("# Shopping List — 2026-08-31", markdown)
        self.assertIn("## To buy", markdown)
        self.assertIn("## Confirm before skipping", markdown)
        self.assertIn("## Assumed pantry stock", markdown)
        self.assertIn("ground turkey", markdown)
        self.assertIn("Substitutions from planning", markdown)
        self.assertNotIn("20123456_EA", markdown)
        self.assertNotIn("product_id", markdown)
        self.assertNotIn("PC Express", markdown)


class CliTests(unittest.TestCase):
    def test_cli_writes_json_and_markdown(self) -> None:
        pantry, staples = _stock()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(_sample_plan()), encoding="utf-8")
            pantry_path = root / "pantry.md"
            staples_path = root / "staples.md"
            pantry_path.write_text(pantry, encoding="utf-8")
            staples_path.write_text(staples, encoding="utf-8")
            out_json = root / "list.json"
            out_md = root / "list.md"
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = main(
                    [
                        str(plan_path),
                        "--pantry",
                        str(pantry_path),
                        "--staples",
                        str(staples_path),
                        "-o",
                        str(out_json),
                        "--markdown-out",
                        str(out_md),
                    ]
                )
            self.assertEqual(code, 0)
            artifact = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(artifact["kind"], KIND)
            self.assertTrue(out_md.read_text(encoding="utf-8").startswith("# Shopping List"))


if __name__ == "__main__":
    unittest.main()
