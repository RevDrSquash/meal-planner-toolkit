"""Product mapping, ranking, and excess helpers — synthetic fixtures only."""

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

from ingredients import convert_amount  # noqa: E402
from product_resolve import (  # noqa: E402
    ProductMapping,
    ProductResolveError,
    classify_excess,
    format_mapping_line,
    is_materially_better,
    load_candidate_map,
    load_mappings,
    load_requirements,
    lookup_mapping,
    main,
    mapping_still_usable,
    normalize_candidate,
    parse_ingredient_qty,
    parse_mapping_line,
    parse_mappings,
    parse_shopping_preferences,
    rank_candidates,
    remember_mapping,
    render_resolutions,
    resolve_requirement,
    resolve_requirements,
    upsert_mapping,
    write_mappings,
)
from workspace import toolkit_root  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "product-resolve"


def _qty(line: str):
    return parse_ingredient_qty(line)


def _mapping(**overrides) -> ProductMapping:
    item = ProductMapping(
        ingredient="soy sauce",
        brand="Example Brand",
        name="soy sauce",
        size="500 ml",
        product_id="EXAMPLE-SOY-500",
        notes="",
    )
    return ProductMapping(**{**item.__dict__, **overrides})


def _cand(**overrides) -> dict:
    row = {
        "id": "EXAMPLE-SOY-500",
        "brand": "Example Brand",
        "name": "Soy Sauce",
        "size": "500 ml",
        "price": 3.49,
        "unit_price": 0.70,
        "on_sale": False,
        "available": True,
    }
    row.update(overrides)
    return row


class MappingParseAndUpdateTests(unittest.TestCase):
    def test_parses_template_and_example_lines(self) -> None:
        parsed = parse_mapping_line(
            "- soy sauce — Example Brand soy sauce 500 ml (`EXAMPLE-SOY-500`)"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.ingredient, "soy sauce")
        self.assertEqual(parsed.brand, "Example Brand")
        self.assertEqual(parsed.size, "500 ml")
        self.assertEqual(parsed.product_id, "EXAMPLE-SOY-500")

        comma = parse_mapping_line(
            "- canned tomatoes — Example Brand diced tomatoes, 796 ml (`EXAMPLE-TOM-796`)"
        )
        self.assertIsNotNone(comma)
        assert comma is not None
        self.assertEqual(comma.ingredient, "canned tomatoes")
        self.assertEqual(comma.size, "796 ml")
        self.assertEqual(comma.product_id, "EXAMPLE-TOM-796")

    def test_parses_hint_without_product_id(self) -> None:
        parsed = parse_mapping_line("- milk — Example Dairy 2% milk, 1 l")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.ingredient, "milk")
        self.assertEqual(parsed.size, "1 l")
        self.assertIsNone(parsed.product_id)
        self.assertNotIn("`", format_mapping_line(parsed))

    def test_skips_blank_placeholder(self) -> None:
        self.assertEqual(parse_mappings("# Product mappings\n\n## Mappings\n\n- \n"), [])

    def test_lookup_uses_normalized_name(self) -> None:
        mappings = load_mappings(FIXTURES / "product-mappings.md")
        found = lookup_mapping(mappings, "low-sodium soy sauce")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.product_id, "EXAMPLE-SOY-500")
        self.assertIsNone(lookup_mapping(mappings, "cilantro"))

    def test_upsert_replaces_same_ingredient(self) -> None:
        text = (FIXTURES / "product-mappings.md").read_text(encoding="utf-8")
        updated = upsert_mapping(
            text,
            _mapping(size="250 ml", product_id="EXAMPLE-SOY-250"),
        )
        mappings = parse_mappings(updated)
        soy = lookup_mapping(mappings, "soy sauce")
        self.assertIsNotNone(soy)
        assert soy is not None
        self.assertEqual(soy.size, "250 ml")
        self.assertEqual(soy.product_id, "EXAMPLE-SOY-250")
        self.assertEqual(sum(1 for item in mappings if item.ingredient == "soy sauce"), 1)
        self.assertIsNotNone(lookup_mapping(mappings, "milk"))

    def test_upsert_replaces_template_placeholder(self) -> None:
        template = (ROOT / "templates" / "product-mappings.md").read_text(encoding="utf-8")
        updated = upsert_mapping(template, _mapping())
        self.assertEqual(updated.count("- soy sauce —"), 1)
        self.assertNotIn("\n- \n", updated)

    def test_upsert_does_not_invent_product_id(self) -> None:
        text = upsert_mapping("", _mapping(product_id=None, brand="House"))
        soy = lookup_mapping(parse_mappings(text), "soy sauce")
        self.assertIsNotNone(soy)
        assert soy is not None
        self.assertIsNone(soy.product_id)
        self.assertNotIn("`", format_mapping_line(soy))
        self.assertIn("House", text)

    def test_upsert_rejects_invalid_id(self) -> None:
        with self.assertRaises(ProductResolveError):
            upsert_mapping("", _mapping(product_id="not a valid id"))

    def test_remember_writes_temp_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "shopping" / "product-mappings.md"
            remember_mapping(
                path,
                ingredient="soy sauce",
                brand="Example Brand",
                name="soy sauce",
                size="500 ml",
                product_id="EXAMPLE-SOY-500",
            )
            mappings = load_mappings(path)
            self.assertEqual(mappings[0].product_id, "EXAMPLE-SOY-500")
            remember_mapping(
                path,
                ingredient="soy sauce",
                brand="Example Brand",
                name="soy sauce",
                size="250 ml",
                product_id="EXAMPLE-SOY-250",
            )
            mappings = load_mappings(path)
            self.assertEqual(len(mappings), 1)
            self.assertEqual(mappings[0].size, "250 ml")

    def test_write_mappings_refuses_toolkit_tree(self) -> None:
        dest = toolkit_root() / "examples" / "workspace" / "shopping" / "product-mappings.md"
        with self.assertRaises(ProductResolveError):
            write_mappings(dest, "- soy sauce — Example Brand soy sauce, 500 ml\n")


class ExcessClassificationTests(unittest.TestCase):
    def test_shelf_stable_oversupply_is_flagged_not_silent(self) -> None:
        flag = classify_excess(_qty("2 tbsp soy sauce"), "500 ml", category="pantry")
        self.assertEqual(flag.severity, "substantial")
        self.assertFalse(flag.perishable)
        self.assertTrue(flag.carries_over)
        self.assertIn("2 tbsp", flag.summary)
        self.assertIn("500 ml", flag.summary)

    def test_perishable_oversupply_is_substantial(self) -> None:
        flag = classify_excess(_qty("200 g strawberries"), "1.5 kg", category="produce")
        self.assertEqual(flag.severity, "substantial")
        self.assertTrue(flag.perishable)
        self.assertFalse(flag.carries_over)
        self.assertIn("waste", flag.summary)

    def test_two_small_packs_are_a_note(self) -> None:
        flag = classify_excess(_qty("500 g ground beef"), "454 g", category="proteins")
        self.assertEqual(flag.packages, 2)
        self.assertIn(flag.severity, {"note", "none"})
        self.assertTrue(flag.perishable)

    def test_close_fit_is_none(self) -> None:
        flag = classify_excess(_qty("500 g ground turkey"), "500 g", category="proteins")
        self.assertEqual(flag.severity, "none")
        self.assertEqual(flag.packages, 1)

    def test_optional_garnish_offers_skip(self) -> None:
        flag = classify_excess(
            _qty("2 tbsp cilantro, optional garnish"),
            "1 bunch",
            category="produce",
            notes="optional garnish",
        )
        self.assertTrue(flag.skip_ok)
        self.assertIn("skip", flag.summary.lower())

    def test_incompatible_units_are_unknown_not_silent(self) -> None:
        flag = classify_excess(_qty("2 onions"), "3 lb bag", category="produce")
        self.assertEqual(flag.severity, "unknown")
        self.assertIn("cannot be compared", flag.summary)

    def test_dairy_litre_vs_cup_is_flagged(self) -> None:
        flag = classify_excess(_qty("1 cup milk"), "1 L", category="dairy")
        self.assertIn(flag.severity, {"note", "substantial"})
        self.assertTrue(flag.perishable)


class RankingAndSelectionTests(unittest.TestCase):
    def test_prefers_learned_mapping_when_still_usable(self) -> None:
        mapping = _mapping()
        ranked = rank_candidates(
            [
                _cand(),
                _cand(
                    id="EXAMPLE-SOY-250",
                    brand="Other Brand",
                    size="250 ml",
                    price=2.79,
                    unit_price=1.12,
                ),
            ],
            "2 tbsp soy sauce",
            mapping=mapping,
            category="pantry",
        )
        self.assertTrue(ranked[0].mapping_match)
        self.assertEqual(ranked[0].candidate.product_id, "EXAMPLE-SOY-500")
        self.assertTrue(
            mapping_still_usable(
                mapping,
                ranked[0].candidate,
                _qty("2 tbsp soy sauce"),
                "pantry",
            )
        )

    def test_unavailable_mapping_falls_back_to_search(self) -> None:
        mapping = ProductMapping(
            ingredient="milk",
            brand="Example Dairy",
            name="2% milk",
            size="1 l",
            product_id="EXAMPLE-MILK-1L",
            notes="",
        )
        resolved = resolve_requirement(
            {"name": "milk", "amount": 1, "unit": "cup", "category": "dairy"},
            [mapping],
            [
                _cand(
                    id="EXAMPLE-MILK-1L",
                    brand="Example Dairy",
                    name="2% Milk",
                    size="1 L",
                    price=3.29,
                    available=False,
                ),
                _cand(
                    id="EXAMPLE-MILK-2L",
                    brand="Example Dairy",
                    name="2% Milk",
                    size="2 L",
                    price=4.99,
                    unit_price=0.25,
                ),
            ],
        )
        self.assertEqual(resolved.source, "mapping-fallback-search")
        self.assertIsNotNone(resolved.pick)
        assert resolved.pick is not None
        self.assertEqual(resolved.pick.product_id, "EXAMPLE-MILK-2L")
        self.assertFalse(resolved.needs_search)

    def test_materially_cheaper_option_beats_mapping(self) -> None:
        mapping = _mapping()
        known = normalize_candidate(_cand(unit_price=1.20))
        cheaper = normalize_candidate(
            _cand(
                id="EXAMPLE-SOY-SALE",
                brand="Other Brand",
                size="500 ml",
                price=1.99,
                unit_price=0.40,
                on_sale=True,
            )
        )
        ranked = rank_candidates(
            [known, cheaper],
            "500 ml soy sauce",
            mapping=mapping,
            category="pantry",
            prefs={"prefer_deals": True},
            limit=0,
        )
        mapped = next(item for item in ranked if item.mapping_match)
        other = next(item for item in ranked if item.candidate.product_id == "EXAMPLE-SOY-SALE")
        self.assertTrue(is_materially_better(other, mapped))
        resolved = resolve_requirement(
            "500 ml soy sauce",
            [mapping],
            [known, cheaper],
        )
        assert resolved.pick is not None
        self.assertEqual(resolved.pick.product_id, "EXAMPLE-SOY-SALE")

    def test_diet_hits_drop_a_known_product(self) -> None:
        mapping = ProductMapping(
            ingredient="sausage",
            brand="Example",
            name="honey garlic sausage",
            size="400 g",
            product_id="EXAMPLE-SAUSAGE",
            notes="",
        )
        resolved = resolve_requirement(
            {"name": "sausage", "amount": 400, "unit": "g", "category": "proteins"},
            [mapping],
            [
                {
                    "id": "EXAMPLE-SAUSAGE",
                    "brand": "Example",
                    "name": "Honey Garlic Sausage",
                    "size": "400 g",
                    "price": 6.49,
                    "available": True,
                },
                {
                    "id": "EXAMPLE-TOFU",
                    "brand": "Grove",
                    "name": "Firm Tofu",
                    "size": "400 g",
                    "price": 2.99,
                    "available": True,
                },
            ],
            prefs={"restrictions": ["vegan"], "dislikes": [], "likes": []},
        )
        assert resolved.pick is not None
        self.assertEqual(resolved.pick.product_id, "EXAMPLE-TOFU")
        self.assertEqual(resolved.source, "mapping-fallback-search")

    def test_unknown_ingredient_needs_search_without_candidates(self) -> None:
        resolved = resolve_requirement("1 bunch cilantro", [], [])
        self.assertTrue(resolved.needs_search)
        self.assertEqual(resolved.probe, "search")
        self.assertIsNone(resolved.pick)
        self.assertEqual(resolved.source, "unresolved")

    def test_known_mapping_skips_broad_search(self) -> None:
        mappings = load_mappings(FIXTURES / "product-mappings.md")
        resolved = resolve_requirement("2 tbsp soy sauce", mappings, [])
        self.assertFalse(resolved.needs_search)
        self.assertEqual(resolved.probe, "details")
        assert resolved.pick is not None
        self.assertEqual(resolved.pick.product_id, "EXAMPLE-SOY-500")
        self.assertFalse(resolved.prices_live)

    def test_rank_prefers_sale_and_preferred_brand(self) -> None:
        prefs = parse_shopping_preferences(
            (FIXTURES / "preferences.md").read_text(encoding="utf-8")
        )
        self.assertEqual(prefs["preferred_brands"], ["Example Brand"])
        self.assertTrue(prefs["prefer_deals"])
        ranked = rank_candidates(
            [
                _cand(id="OTHER", brand="Other Brand", on_sale=False, unit_price=0.80),
                _cand(id="SALE", brand="Example Brand", on_sale=True, unit_price=0.80),
            ],
            "2 tbsp soy sauce",
            prefs=prefs,
            category="pantry",
        )
        self.assertEqual(ranked[0].candidate.product_id, "SALE")
        self.assertTrue(any("on sale" in reason for reason in ranked[0].reasons))

    def test_rank_keeps_unavailable_last(self) -> None:
        ranked = rank_candidates(
            [
                _cand(id="DOWN", available=False, unit_price=0.20),
                _cand(id="UP", available=True, unit_price=0.70),
            ],
            "2 tbsp soy sauce",
            category="pantry",
        )
        self.assertEqual(ranked[0].candidate.product_id, "UP")
        self.assertEqual(ranked[-1].candidate.product_id, "DOWN")


class ResolveWorkflowTests(unittest.TestCase):
    def test_resolve_fixture_set_without_live_retailer(self) -> None:
        mappings = load_mappings(FIXTURES / "product-mappings.md")
        requirements = load_requirements(
            json.loads((FIXTURES / "requirements.json").read_text(encoding="utf-8"))
        )
        candidates = load_candidate_map(
            json.loads((FIXTURES / "candidates.json").read_text(encoding="utf-8"))
        )
        prefs = parse_shopping_preferences(
            (FIXTURES / "preferences.md").read_text(encoding="utf-8")
        )
        resolved = resolve_requirements(requirements, mappings, candidates, prefs)
        by_name = {item.ingredient: item for item in resolved}
        self.assertEqual(by_name["soy sauce"].pick.product_id, "EXAMPLE-SOY-500")
        self.assertEqual(by_name["soy sauce"].source, "mapping")
        self.assertEqual(by_name["milk"].pick.product_id, "EXAMPLE-MILK-2L")
        self.assertEqual(by_name["milk"].source, "mapping-fallback-search")
        self.assertIsNotNone(by_name["ground beef"].pick)
        text = render_resolutions(resolved)
        self.assertIn("PICK:", text)
        self.assertIn("Estimated total", text)
        self.assertNotIn("raw catalog", text.lower())

    def test_volume_units_convert_for_package_compare(self) -> None:
        self.assertAlmostEqual(convert_amount(2, "tbsp", "ml") or 0, 30.0)
        self.assertAlmostEqual(convert_amount(1, "cup", "ml") or 0, 240.0)


class CliTests(unittest.TestCase):
    def test_lookup_and_resolve_use_fixtures_not_a_retailer(self) -> None:
        lookup_out = StringIO()
        with patch("sys.stdout", lookup_out):
            code = main(
                [
                    "lookup",
                    "soy sauce",
                    "cilantro",
                    "--mappings",
                    str(FIXTURES / "product-mappings.md"),
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(lookup_out.getvalue())
        self.assertEqual(payload[0]["probe"], "details")
        self.assertFalse(payload[0]["needs_search"])
        self.assertTrue(payload[1]["needs_search"])

        resolve_out = StringIO()
        with patch("sys.stdout", resolve_out):
            code = main(
                [
                    "resolve",
                    str(FIXTURES / "requirements.json"),
                    "--candidates",
                    str(FIXTURES / "candidates.json"),
                    "--mappings",
                    str(FIXTURES / "product-mappings.md"),
                    "--preferences",
                    str(FIXTURES / "preferences.md"),
                ]
            )
        self.assertEqual(code, 0)
        result = json.loads(resolve_out.getvalue())
        picks = {item["ingredient"]: item for item in result["picks"]}
        self.assertEqual(picks["soy sauce"]["pick"]["id"], "EXAMPLE-SOY-500")
        self.assertEqual(picks["milk"]["source"], "mapping-fallback-search")
        self.assertEqual(result["needs_search"], [])

    def test_rank_cli_reads_mocked_candidates(self) -> None:
        buffer = StringIO()
        with patch("sys.stdout", buffer):
            code = main(
                [
                    "rank",
                    str(FIXTURES / "candidates.json"),
                    "--needed",
                    "2 tbsp soy sauce",
                    "--mappings",
                    str(FIXTURES / "product-mappings.md"),
                ]
            )
        self.assertEqual(code, 0)
        ranked = json.loads(buffer.getvalue())
        self.assertEqual(ranked[0]["candidate"]["id"], "EXAMPLE-SOY-500")
        self.assertTrue(ranked[0]["mapping_match"])

    def test_remember_cli_writes_outside_the_toolkit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "product-mappings.md"
            buffer = StringIO()
            with patch("sys.stdout", buffer):
                code = main(
                    [
                        "remember",
                        "--ingredient",
                        "soy sauce",
                        "--brand",
                        "Example Brand",
                        "--name",
                        "soy sauce",
                        "--size",
                        "500 ml",
                        "--id",
                        "EXAMPLE-SOY-500",
                        "--mappings",
                        str(path),
                    ]
                )
            self.assertEqual(code, 0)
            written = load_mappings(path)
            self.assertEqual(written[0].product_id, "EXAMPLE-SOY-500")

    def test_remember_cli_refuses_toolkit_path(self) -> None:
        dest = toolkit_root() / "templates" / "product-mappings.md"
        err = StringIO()
        with patch("sys.stderr", err):
            code = main(
                [
                    "remember",
                    "--ingredient",
                    "soy sauce",
                    "--brand",
                    "Nope",
                    "--mappings",
                    str(dest),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("toolkit", err.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
