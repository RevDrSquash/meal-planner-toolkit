"""Recipe discovery helpers — synthetic candidates and collection only."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from recipe_core import recipe_filename, write_recipe  # noqa: E402
from recipe_finder import (  # noqa: E402
    collection_threshold,
    collection_too_small,
    expand_restriction,
    find_near_duplicate,
    load_local_recipes,
    main,
    parse_minutes,
    parse_preferences,
    rank_candidates,
    restriction_hits,
    title_jaccard,
)

FIXTURES = ROOT / "tests" / "fixtures" / "recipe-finder"
CANDIDATES = json.loads((FIXTURES / "candidates.json").read_text(encoding="utf-8"))


def _card(**overrides) -> dict:
    recipe = {
        "name": "Weeknight Chili",
        "serves": "4",
        "total_time": "45 min",
        "tags": ["beef", "one-pot"],
        "image": None,
        "ingredients": ["500 g ground beef", "1 onion"],
        "nutrition": [],
        "instructions": ["Brown the beef."],
        "notes": None,
        "source_url": "https://example.test/weeknight-chili",
        "source_file": None,
    }
    recipe.update(overrides)
    return recipe


def _prefs(**overrides) -> dict:
    text = (FIXTURES / "preferences.md").read_text(encoding="utf-8")
    prefs = parse_preferences(text)
    prefs.update(overrides)
    return prefs


class PreferenceParseTests(unittest.TestCase):
    def test_reads_diet_likes_time_and_cycle(self) -> None:
        prefs = _prefs()
        self.assertEqual(prefs["restrictions"], ["vegetarian", "peanut allergy"])
        self.assertEqual(prefs["dislikes"], ["cilantro"])
        self.assertEqual(prefs["likes"], ["weeknight one-pot meals"])
        self.assertEqual(prefs["goals"], [])
        self.assertEqual(prefs["meals_per_cycle"], 4)
        self.assertEqual(prefs["max_minutes"], 45)

    def test_none_values_are_empty(self) -> None:
        prefs = parse_preferences(
            "- Restrictions/allergies: none\n"
            "- Dislikes: n/a\n"
            "- Likes / preferences: \n"
            "- Meals to plan per cycle: 5 dinners\n"
        )
        self.assertEqual(prefs["restrictions"], [])
        self.assertEqual(prefs["dislikes"], [])
        self.assertEqual(prefs["likes"], [])
        self.assertEqual(prefs["meals_per_cycle"], 5)

    def test_expands_allergy_and_diet_phrases(self) -> None:
        self.assertEqual(expand_restriction("peanut allergy"), "peanut")
        self.assertEqual(expand_restriction("allergic to shellfish"), "shellfish")
        self.assertEqual(expand_restriction("no pork"), "pork")
        self.assertEqual(expand_restriction("gluten free"), "gluten-free")
        self.assertEqual(expand_restriction("veggie"), "vegetarian")


class DuplicateDetectionTests(unittest.TestCase):
    def test_same_source_url_ignores_query_string(self) -> None:
        local = [
            {
                "name": "Weeknight Chili",
                "filename": "weeknight-chili.html",
                "source_url": "https://example.test/weeknight-chili",
            }
        ]
        found = find_near_duplicate(
            {
                "title": "Someone Else's Chili",
                "url": "https://example.test/weeknight-chili?utm=finder",
            },
            local,
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["filename"], "weeknight-chili.html")

    def test_near_duplicate_title(self) -> None:
        local = [{"name": "Weeknight Chili", "filename": "weeknight-chili.html"}]
        self.assertGreaterEqual(title_jaccard("Easy Weeknight Chili Recipe", "Weeknight Chili"), 0.7)
        found = find_near_duplicate({"title": "Easy Weeknight Chili Recipe"}, local)
        self.assertIsNotNone(found)

    def test_distinct_chicken_recipes_are_not_duplicates(self) -> None:
        local = [{"name": "Chicken Tikka Masala", "filename": "chicken-tikka-masala.html"}]
        self.assertIsNone(find_near_duplicate({"title": "Chicken Tacos"}, local))
        self.assertLess(title_jaccard("Chicken Tikka Masala", "Chicken Tacos"), 0.7)

    def test_slug_match_counts_as_duplicate(self) -> None:
        local = [{"name": "Lentil Chili", "filename": "lentil-chili.html"}]
        found = find_near_duplicate({"title": "Lentil Chili!"}, local)
        self.assertIsNotNone(found)

    def test_indexes_synthetic_html_collection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recipes_dir = Path(raw)
            write_recipe(_card(), recipes_dir / recipe_filename(_card()), force=True)
            write_recipe(
                _card(
                    name="Lemon Pasta",
                    source_url="https://example.test/lemon-pasta",
                    tags=["pasta"],
                    ingredients=["pasta", "lemon"],
                ),
                recipes_dir / "lemon-pasta.html",
                force=True,
            )
            (recipes_dir / "notes.md").write_text("# leftover input\n", encoding="utf-8")
            indexed = load_local_recipes(recipes_dir)
            names = {item["name"] for item in indexed}
            self.assertEqual(names, {"Weeknight Chili", "Lemon Pasta"})
            chili = next(item for item in indexed if item["name"] == "Weeknight Chili")
            self.assertEqual(chili["source_url"], "https://example.test/weeknight-chili")
            self.assertIn("ground beef", " ".join(chili["ingredients"]))


class RestrictionFilterTests(unittest.TestCase):
    def test_vegetarian_and_peanut_and_dislike(self) -> None:
        prefs = _prefs()
        beef = restriction_hits(
            {"title": "Beef Chili", "ingredients": ["ground beef"]},
            prefs,
        )
        self.assertTrue(any("vegetarian" in hit for hit in beef))

        peanut = restriction_hits(
            {
                "title": "Creamy Peanut Noodles",
                "ingredients": ["peanut butter", "cilantro"],
            },
            prefs,
        )
        self.assertTrue(any("peanut" in hit for hit in peanut))
        self.assertTrue(any("cilantro" in hit for hit in peanut))

        lentil = restriction_hits(
            {
                "title": "Vegetarian Lentil Chili",
                "ingredients": ["lentils", "beans"],
                "tags": ["vegetarian"],
            },
            prefs,
        )
        self.assertEqual(lentil, [])

    def test_coconut_milk_is_not_dairy(self) -> None:
        prefs = parse_preferences("- Restrictions/allergies: vegan\n")
        hits = restriction_hits(
            {
                "title": "Coconut Curry",
                "ingredients": ["coconut milk", "chickpeas"],
            },
            prefs,
        )
        self.assertEqual(hits, [])

    def test_diet_labels_and_lookalikes_are_not_restrictions(self) -> None:
        gluten_free = parse_preferences("- Restrictions/allergies: gluten-free\n")
        self.assertEqual(
            restriction_hits(
                {
                    "title": "Gluten-Free Lentil Chili",
                    "tags": ["gluten-free"],
                    "ingredients": ["lentils", "beans"],
                },
                gluten_free,
            ),
            [],
        )

        vegan = parse_preferences("- Restrictions/allergies: vegan\n")
        self.assertEqual(
            restriction_hits(
                {
                    "title": "Creamy Meatless Chili",
                    "tags": ["meatless", "vegan"],
                    "ingredients": ["butternut squash", "beans"],
                },
                vegan,
            ),
            [],
        )

        vegetarian = parse_preferences("- Restrictions/allergies: vegetarian\n")
        self.assertTrue(
            any(
                "chicken" in hit
                for hit in restriction_hits(
                    {"title": "Soup", "ingredients": ["chicken-stock"]},
                    vegetarian,
                )
            )
        )

    def test_nut_butter_is_not_dairy(self) -> None:
        prefs = parse_preferences("- Restrictions/allergies: vegan\n")
        hits = restriction_hits(
            {
                "title": "Peanut Noodles",
                "ingredients": ["peanut butter", "rice noodles"],
            },
            prefs,
        )
        self.assertEqual(hits, [])

    def test_request_diet_excludes_meat_even_without_pref(self) -> None:
        ranked = rank_candidates(
            [
                {"title": "Chicken Tacos", "url": "https://example.test/chicken-tacos"},
                {"title": "Bean Tacos", "url": "https://example.test/bean-tacos"},
            ],
            "vegetarian tacos",
            parse_preferences("- Restrictions/allergies: none\n"),
            local_recipes=[],
        )
        titles = [item["title"] for item in ranked["shortlist"]]
        self.assertEqual(titles, ["Bean Tacos"])
        self.assertTrue(
            any("restriction" in item["reason"] for item in ranked["excluded"])
        )


class RankingTests(unittest.TestCase):
    def test_shortlist_drops_duplicates_and_violations(self) -> None:
        local = [
            {
                "name": "Weeknight Chili",
                "filename": "weeknight-chili.html",
                "source_url": "https://example.test/weeknight-chili",
            }
        ]
        ranked = rank_candidates(
            CANDIDATES,
            "vegetarian weeknight chili",
            _prefs(),
            local,
            limit=5,
        )
        titles = [item["title"] for item in ranked["shortlist"]]
        self.assertEqual(titles[0], "Vegetarian Lentil Chili")
        self.assertNotIn("Easy Weeknight Chili", titles)
        self.assertNotIn("Weeknight Chili", titles)
        self.assertNotIn("Creamy Peanut Noodles", titles)
        self.assertNotIn("Chicken Tikka Masala", titles)
        excluded_titles = {item["title"] for item in ranked["excluded"]}
        self.assertIn("Easy Weeknight Chili", excluded_titles)
        self.assertIn("Weeknight Chili", excluded_titles)
        self.assertIn("Creamy Peanut Noodles", excluded_titles)
        self.assertTrue(ranked["collection_too_small"])

    def test_time_constraint_and_likes_affect_order(self) -> None:
        ranked = rank_candidates(
            [
                {
                    "title": "Slow Vegetable Chili",
                    "url": "https://example.test/slow",
                    "summary": "All-day chili",
                    "tags": ["chili"],
                    "total_time": "6 hrs",
                },
                {
                    "title": "Weeknight Lentil Chili",
                    "url": "https://example.test/fast",
                    "summary": "One-pot weeknight chili",
                    "tags": ["chili", "one-pot"],
                    "total_time": "30 min",
                },
            ],
            "chili",
            _prefs(),
            local_recipes=[],
        )
        titles = [item["title"] for item in ranked["shortlist"]]
        self.assertEqual(titles[0], "Weeknight Lentil Chili")
        top_reasons = " ".join(ranked["shortlist"][0]["reasons"])
        self.assertIn("one-pot", top_reasons)
        self.assertIn("45 min", top_reasons)

    def test_empty_candidates_yield_empty_shortlist(self) -> None:
        ranked = rank_candidates([], "chili", _prefs(), [])
        self.assertEqual(ranked["shortlist"], [])
        self.assertEqual(ranked["excluded"], [])


class CollectionSizeTests(unittest.TestCase):
    def test_threshold_follows_meals_per_cycle(self) -> None:
        self.assertEqual(collection_threshold({"meals_per_cycle": 4}), 4)
        self.assertEqual(collection_threshold({}), 4)
        self.assertTrue(collection_too_small(0, {"meals_per_cycle": 4}))
        self.assertTrue(collection_too_small(3, {"meals_per_cycle": 4}))
        self.assertFalse(collection_too_small(4, {"meals_per_cycle": 4}))
        self.assertFalse(collection_too_small(1, {"meals_per_cycle": 1}))

    def test_parse_minutes(self) -> None:
        self.assertEqual(parse_minutes("45 min"), 45)
        self.assertEqual(parse_minutes("1 hr 15 min"), 75)
        self.assertEqual(parse_minutes("6 hrs"), 360)
        self.assertIsNone(parse_minutes("until done"))


class CliTests(unittest.TestCase):
    def test_shortlist_cli_uses_fixtures_not_the_web(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recipes_dir = Path(raw)
            write_recipe(_card(), recipes_dir / recipe_filename(_card()), force=True)
            argv = [
                "--shortlist",
                str(FIXTURES / "candidates.json"),
                "--request",
                "vegetarian weeknight chili",
                "--preferences",
                str(FIXTURES / "preferences.md"),
                "--recipes-dir",
                str(recipes_dir),
                "--limit",
                "3",
            ]
            from io import StringIO
            from unittest.mock import patch

            buffer = StringIO()
            with patch("sys.stdout", buffer):
                code = main(argv)
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["shortlist"][0]["title"], "Vegetarian Lentil Chili")
            self.assertEqual(payload["local_count"], 1)
            self.assertTrue(payload["collection_too_small"])

    def test_index_and_check_collection_cli(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recipes_dir = Path(raw)
            write_recipe(_card(), recipes_dir / recipe_filename(_card()), force=True)
            from io import StringIO
            from unittest.mock import patch

            index_out = StringIO()
            with patch("sys.stdout", index_out):
                self.assertEqual(
                    main(["--index", "--recipes-dir", str(recipes_dir)]),
                    0,
                )
            indexed = json.loads(index_out.getvalue())
            self.assertEqual(indexed["count"], 1)
            self.assertEqual(indexed["recipes"][0]["name"], "Weeknight Chili")

            check_out = StringIO()
            with patch("sys.stdout", check_out):
                self.assertEqual(
                    main(
                        [
                            "--check-collection",
                            "--recipes-dir",
                            str(recipes_dir),
                            "--preferences",
                            str(FIXTURES / "preferences.md"),
                        ]
                    ),
                    0,
                )
            text = check_out.getvalue()
            self.assertIn("recipes\t1", text)
            self.assertIn("threshold\t4", text)
            self.assertIn("too_small\ttrue", text)

    def test_shortlist_requires_request(self) -> None:
        self.assertEqual(main(["--shortlist", str(FIXTURES / "candidates.json")]), 2)


if __name__ == "__main__":
    unittest.main()
