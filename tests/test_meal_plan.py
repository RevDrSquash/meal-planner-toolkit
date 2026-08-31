"""Structured meal-plan build and render — synthetic recipes only."""

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

from meal_plan import (  # noqa: E402
    build_plan,
    equipment_hints,
    filter_library,
    library_gap_note,
    load_recipes_from_dir,
    main,
    nutrition_row,
    plan_from_json,
    render_markdown,
    suggest_cook_together,
    write_plan,
)
from recipe_core import recipe_filename, write_recipe  # noqa: E402
from recipe_finder import parse_preferences  # noqa: E402
from recipe_from_markdown import parse_markdown  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _recipe_from_md(name: str) -> dict:
    path = FIXTURES / name
    return parse_markdown(path.read_text(encoding="utf-8"), source_file=path.name)


def _chili() -> dict:
    recipe = _recipe_from_md("weeknight-chili.md")
    recipe["file"] = "weeknight-chili.html"
    return recipe


def _pasta() -> dict:
    recipe = _recipe_from_md("tomato-skillet.md")
    recipe["file"] = "tomato-skillet-pasta.html"
    return recipe


def _broccoli() -> dict:
    recipe = _recipe_from_md("roasted-broccoli.md")
    recipe["file"] = "roasted-broccoli.html"
    return recipe


def _sample_plan(**overrides) -> dict:
    recipes = {
        "Weeknight Chili": _chili(),
        "Tomato Skillet Pasta": _pasta(),
        "Roasted Broccoli": _broccoli(),
    }
    plan = build_plan(
        date_value="2026-08-31",
        household="2 people (synthetic)",
        period="3 dinners",
        servings_per_meal=2,
        meals=[
            {
                "day": "Day 1",
                "slot": "dinner",
                "recipe": "Weeknight Chili",
                "servings": 4,
                "notes": "leftovers for Day 2",
            },
            {
                "day": "Day 2",
                "slot": "dinner",
                "recipe": "Weeknight Chili",
                "servings": 2,
                "leftover": True,
                "notes": "reheat",
            },
            {
                "day": "Day 3",
                "slot": "dinner",
                "recipe": "Tomato Skillet Pasta",
                "servings": 2,
            },
        ],
        recipes=recipes,
        cooking_sessions=[
            {
                "day": "Day 1",
                "title": "Batch chili",
                "cook": ["Weeknight Chili"],
                "reason": "One pot; leftover dinner the next day",
                "equipment": ["large pot; compact oven unused"],
                "leftovers_to_hold": ["Chili for Day 2 dinner"],
                "shared_prep": ["dice onion"],
            },
            {
                "day": "Day 3",
                "title": "Pasta night",
                "cook": ["Tomato Skillet Pasta"],
                "reason": "Shared onion, garlic, and tomatoes with the chili week",
                "equipment": ["one skillet"],
            },
        ],
        deviations=[
            {
                "recipe": "Weeknight Chili",
                "change": "Use ground turkey instead of ground beef",
                "reason": "preference",
                "replace": {"ground beef": "ground turkey"},
            }
        ],
        notes=["Synthetic fixture plan."],
    )
    plan.update(overrides)
    return plan


class LibraryHelperTests(unittest.TestCase):
    def test_gap_note_for_empty_and_thin_libraries(self) -> None:
        empty = library_gap_note(0, 4)
        self.assertIsNotNone(empty)
        assert empty is not None
        self.assertIn("empty", empty.lower())
        self.assertIn("discovery", empty.lower())

        thin = library_gap_note(1, 4)
        self.assertIsNotNone(thin)
        assert thin is not None
        self.assertIn("1 recipe", thin)
        self.assertIsNone(library_gap_note(4, 4))

    def test_filter_library_honors_vegetarian_hard_constraint(self) -> None:
        prefs = parse_preferences(
            "- Restrictions/allergies: vegetarian\n- Dislikes: cilantro\n"
        )
        result = filter_library([_chili(), _pasta(), _broccoli()], prefs)
        names = {item.get("name") for item in result["eligible"]}
        self.assertIn("Tomato Skillet Pasta", names)
        self.assertIn("Roasted Broccoli", names)
        self.assertNotIn("Weeknight Chili", names)
        excluded = {item["name"] for item in result["excluded"]}
        self.assertIn("Weeknight Chili", excluded)

    def test_cook_together_scores_shared_produce(self) -> None:
        pairs = suggest_cook_together([_chili(), _pasta(), _broccoli()])
        chili_pasta = next(
            item
            for item in pairs
            if set(item["recipes"]) == {"Weeknight Chili", "Tomato Skillet Pasta"}
        )
        self.assertIn("onion", chili_pasta["shared"])
        self.assertIn("garlic", chili_pasta["shared"])
        self.assertGreater(chili_pasta["score"], 0)
        self.assertIn("oven", equipment_hints(_broccoli()))
        self.assertIn("stovetop", equipment_hints(_pasta()))


class NutritionTests(unittest.TestCase):
    def test_missing_nutrition_fields_are_blank(self) -> None:
        row = nutrition_row(_broccoli(), 2)
        self.assertTrue(row["missing"])
        self.assertIsNone(row["calories"])
        self.assertIsNone(row["protein"])
        self.assertFalse(row["estimated"])

    def test_present_nutrition_is_copied(self) -> None:
        row = nutrition_row(_chili(), 4)
        self.assertFalse(row["missing"])
        self.assertEqual(row["calories"], "480 kcal")
        self.assertEqual(row["protein"], "32 g")


class BuildPlanTests(unittest.TestCase):
    def test_scales_and_aggregates_with_explicit_deviation(self) -> None:
        plan = _sample_plan()
        self.assertEqual(plan["version"], 2)
        names = [meal["recipe"] for meal in plan["meals"]]
        self.assertEqual(names.count("Weeknight Chili"), 2)

        turkey = [item for item in plan["ingredients"] if item["name"] == "ground turkey"]
        self.assertEqual(len(turkey), 1)
        # Day 1 cooks 4 servings of a 4-serving chili; Day 2 leftover is not recooked.
        self.assertAlmostEqual(turkey[0]["amount"] or 0, 500)
        beef = [item for item in plan["ingredients"] if item["name"] == "ground beef"]
        self.assertEqual(beef, [])

        onions = [item for item in plan["ingredients"] if item["name"] == "onion"]
        self.assertEqual(len(onions), 1)
        self.assertEqual(onions[0]["amount"], 2)
        self.assertIn("Weeknight Chili", onions[0]["used_in"])
        self.assertIn("Tomato Skillet Pasta", onions[0]["used_in"])
        leftover_meals = [meal for meal in plan["meals"] if meal.get("leftover")]
        self.assertEqual(len(leftover_meals), 1)

        self.assertEqual(plan["deviations"][0]["change"].lower().find("turkey") >= 0, True)
        for item in plan["ingredients"]:
            self.assertNotIn("code", item)
            self.assertNotIn("product_id", item)
            self.assertNotIn("sku", item)

    def test_leftover_meal_does_not_add_ingredients(self) -> None:
        plan = _sample_plan()
        tomatoes = [
            item
            for item in plan["ingredients"]
            if item["name"] == "diced tomatoes"
        ]
        # Chili (1 can) + pasta (1 can of the same size) merge.
        self.assertEqual(len(tomatoes), 1)
        self.assertEqual(tomatoes[0]["amount"], 2)

    def test_unknown_recipe_raises(self) -> None:
        with self.assertRaises(Exception):
            build_plan(
                meals=[{"day": "Day 1", "recipe": "Unicorn Stew", "servings": 2}],
                recipes={"Weeknight Chili": _chili()},
            )

    def test_unscaled_when_source_servings_missing(self) -> None:
        recipe = _chili()
        recipe["serves"] = None
        plan = build_plan(
            meals=[{"day": "Day 1", "recipe": "Weeknight Chili", "servings": 8}],
            recipes={"Weeknight Chili": recipe},
        )
        self.assertTrue(any("unscaled" in note for note in plan["notes"]))


class RenderTests(unittest.TestCase):
    def test_render_includes_required_sections(self) -> None:
        markdown = render_markdown(_sample_plan())
        for heading in (
            "## Meal schedule",
            "## Cooking sessions",
            "## Recipe references",
            "## Recipe deviations",
            "## Nutrition",
            "## Ingredient requirements",
        ):
            self.assertIn(heading, markdown)
        self.assertIn("Weeknight Chili", markdown)
        self.assertIn("ground turkey", markdown)
        self.assertIn("Use ground turkey instead of ground beef", markdown)
        self.assertIn("not retailer product IDs", markdown)
        self.assertNotIn("| Code |", markdown)
        self.assertIn("Roasted Broccoli", render_markdown(
            build_plan(
                date_value="2026-08-31",
                meals=[{"day": "Day 1", "recipe": "Roasted Broccoli", "servings": 2}],
                recipes={"Roasted Broccoli": _broccoli()},
            )
        ))
        broccoli_plan = render_markdown(
            build_plan(
                date_value="2026-08-31",
                meals=[{"day": "Day 1", "recipe": "Roasted Broccoli", "servings": 2}],
                recipes={"Roasted Broccoli": _broccoli()},
            )
        )
        self.assertIn("Roasted Broccoli", broccoli_plan)
        # Missing nutrition stays as empty cells, not invented numbers.
        self.assertIn("| Roasted Broccoli | 2 |  |  |  |  |", broccoli_plan)

    def test_empty_deviations_and_sessions_are_explicit(self) -> None:
        plan = build_plan(
            date_value="2026-08-31",
            meals=[{"day": "Day 1", "recipe": "Tomato Skillet Pasta", "servings": 2}],
            recipes={"Tomato Skillet Pasta": _pasta()},
        )
        markdown = render_markdown(plan)
        self.assertIn("- None.", markdown)
        self.assertIn("No grouped cooking sessions", markdown)

    def test_write_plan_and_json_round_trip(self) -> None:
        plan = _sample_plan()
        restored = plan_from_json(json.dumps(plan))
        self.assertEqual(restored["date"], "2026-08-31")
        self.assertEqual(len(restored["meals"]), 3)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "2026-08-31.md"
            write_plan(plan, path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Meal Plan — 2026-08-31"))
            self.assertIn("Batch chili", text)

    def test_loads_html_cards_from_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recipes_dir = Path(raw)
            write_recipe(_chili(), recipes_dir / recipe_filename(_chili()), force=True)
            write_recipe(_pasta(), recipes_dir / recipe_filename(_pasta()), force=True)
            (recipes_dir / "scratch.md").write_text("# leftover\n", encoding="utf-8")
            loaded = load_recipes_from_dir(recipes_dir)
            self.assertEqual(set(loaded), {"Weeknight Chili", "Tomato Skillet Pasta"})
            chili = loaded["Weeknight Chili"]
            self.assertTrue(chili["nutrition"])
            self.assertTrue(chili["file"].endswith(".html"))


class CliTests(unittest.TestCase):
    def test_render_cli_from_json(self) -> None:
        plan = _sample_plan()
        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "plan.json"
            out = Path(raw) / "plan.md"
            src.write_text(json.dumps(plan), encoding="utf-8")
            code = main(["render", str(src), "-o", str(out)])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Ingredient requirements", text)
            self.assertIn("ground turkey", text)

    def test_scale_and_aggregate_cli(self) -> None:
        scale_out = StringIO()
        with patch("sys.stdout", scale_out):
            self.assertEqual(
                main(
                    [
                        "scale",
                        "--from-servings",
                        "4",
                        "--to-servings",
                        "2",
                        "500 g ground beef",
                    ]
                ),
                0,
            )
        self.assertIn("250 g ground beef", scale_out.getvalue())

        agg_out = StringIO()
        with patch("sys.stdout", agg_out):
            self.assertEqual(
                main(
                    [
                        "aggregate",
                        "--source",
                        "Chili",
                        "1 onion",
                        "2 onions",
                    ]
                ),
                0,
            )
        payload = json.loads(agg_out.getvalue())
        onions = [item for item in payload if item["name"] == "onion"]
        self.assertEqual(onions[0]["amount"], 3)
        self.assertEqual(onions[0]["used_in"], ["Chili"])


if __name__ == "__main__":
    unittest.main()
