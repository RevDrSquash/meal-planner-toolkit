"""Proposed cart, review flags, and approval-gated mutations.

Synthetic fixtures only. Provider cart calls are mocked.
"""

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

from cart import (  # noqa: E402
    ACTION_KEEP,
    ACTION_SKIP,
    ACTION_UNAVAILABLE,
    KIND,
    STATUS_APPLIED,
    STATUS_APPROVED,
    STATUS_PARTIAL,
    STATUS_PROPOSED,
    ApprovalRequired,
    CapabilityError,
    CheckoutForbidden,
    MockCartProvider,
    apply_approved,
    approve_proposal,
    classify_excess,
    main,
    product_matches_ingredient,
    proposal_is_approved,
    propose_cart,
    render_markdown,
    skip_ok_for,
)
from provider import PC_EXPRESS, SEARCH_ONLY  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "cart"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _proposal(**kwargs) -> dict:
    shopping = kwargs.pop("shopping_list", None) or _load("shopping.json")
    resolved = kwargs.pop("resolved", None)
    if resolved is None:
        resolved = _load("resolved.json")
    return propose_cart(
        shopping_list=shopping,
        resolved=resolved,
        provider=kwargs.pop("provider", "pcexpress"),
        **kwargs,
    )


def _item(proposal: dict, name: str) -> dict:
    matches = [item for item in proposal["items"] if item["name"] == name]
    if not matches:
        raise AssertionError(f"missing {name!r} in {[i['name'] for i in proposal['items']]}")
    return matches[0]


class MatchingAndRoleTests(unittest.TestCase):
    def test_product_name_matches_ingredient_tokens(self) -> None:
        self.assertTrue(product_matches_ingredient("soy sauce", "Example Brand Soy Sauce"))
        self.assertTrue(product_matches_ingredient("onion", "Yellow Onions"))
        self.assertFalse(product_matches_ingredient("ground turkey", "Lean Ground Beef"))

    def test_optional_and_garnish_are_skippable(self) -> None:
        self.assertTrue(skip_ok_for("optional"))
        self.assertTrue(skip_ok_for("garnish"))
        self.assertFalse(skip_ok_for("essential"))
        self.assertTrue(skip_ok_for("essential", ["optional topping"]))


class ProposalConstructionTests(unittest.TestCase):
    def test_proposed_cart_joins_shopping_list_and_resolutions(self) -> None:
        proposal = _proposal()
        self.assertEqual(proposal["kind"], KIND)
        self.assertEqual(proposal["status"], STATUS_PROPOSED)
        self.assertEqual(proposal["source_plan"], "2026-08-31")
        names = {item["name"] for item in proposal["items"]}
        self.assertIn("ground turkey", names)
        self.assertIn("egg", names)
        self.assertIn("soy sauce", names)
        self.assertNotIn("chili powder", names)
        self.assertIsNone(proposal["approval"])
        self.assertEqual(proposal["mutations"], [])

    def test_staples_are_included_when_resolved(self) -> None:
        egg = _item(_proposal(), "egg")
        self.assertTrue(egg["staple"])
        self.assertEqual(egg["origin"], "staple")
        self.assertEqual(egg["product"]["id"], "EXAMPLE-EGG-12")
        self.assertIn("staple", egg["flags"])

    def test_assumed_pantry_items_stay_off_the_cart(self) -> None:
        proposal = _proposal()
        self.assertFalse(any(item["name"] == "chili powder" for item in proposal["items"]))

    def test_search_only_provider_can_still_propose(self) -> None:
        proposal = _proposal(provider=SEARCH_ONLY)
        self.assertTrue(proposal["capabilities"]["can_resolve"])
        self.assertFalse(proposal["capabilities"]["can_mutate"])
        self.assertGreater(len(proposal["items"]), 0)
        markdown = render_markdown(proposal)
        self.assertIn("cannot write a cart", markdown)


class ReviewFlagTests(unittest.TestCase):
    def test_planning_substitution_is_surfaced(self) -> None:
        turkey = _item(_proposal(), "ground turkey")
        self.assertIn("substitution", turkey["flags"])
        self.assertEqual(turkey["substitution"]["from"], "ground beef")
        review = _proposal()["review"]
        names = {row["name"] for row in review["substitutions"]}
        self.assertIn("ground turkey", names)

    def test_unavailable_essential_is_not_hidden(self) -> None:
        peppers = _item(_proposal(), "bell pepper")
        self.assertEqual(peppers["action"], ACTION_UNAVAILABLE)
        self.assertFalse(peppers["skip_ok"])
        self.assertIn("unavailable", peppers["flags"])
        review = _proposal()["review"]
        self.assertTrue(any(row["name"] == "bell pepper" for row in review["unavailable"]))

    def test_optional_and_garnish_can_be_skipped(self) -> None:
        cilantro = _item(_proposal(), "cilantro")
        self.assertTrue(cilantro["skip_ok"])
        self.assertEqual(cilantro["action"], ACTION_SKIP)
        self.assertIn("optional_skip", cilantro["flags"])
        sour = _item(_proposal(), "sour cream")
        self.assertTrue(sour["skip_ok"])
        self.assertEqual(sour["role"], "optional")
        review = _proposal()["review"]
        skippable = {row["name"] for row in review["skippable"]}
        self.assertIn("cilantro", skippable)
        self.assertIn("sour cream", skippable)

    def test_excess_and_price_changes_are_flagged(self) -> None:
        soy = _item(_proposal(), "soy sauce")
        self.assertIn("excess", soy["flags"])
        self.assertEqual(soy["excess"]["severity"], "substantial")
        self.assertIn("price_change", soy["flags"])
        turkey = _item(_proposal(), "ground turkey")
        self.assertIn("price_change", turkey["flags"])
        review = _proposal()["review"]
        self.assertTrue(any(row["name"] == "soy sauce" for row in review["excess"]))
        self.assertTrue(any(row["name"] == "ground turkey" for row in review["price_changes"]))

    def test_sale_on_different_food_suggests_a_meal_plan_change(self) -> None:
        shopping = {
            "source_plan": "2026-08-31",
            "items": [
                {
                    "name": "chicken thighs",
                    "display": "500 g",
                    "role": "essential",
                    "category": "proteins",
                    "sources": ["Sheet Pan Chicken"],
                    "origin": "recipe",
                    "pantry_status": "buy",
                    "substitutions": [],
                    "notes": [],
                }
            ],
        }
        resolved = {
            "picks": [
                {
                    "ingredient": "chicken thighs",
                    "needed": "500 g",
                    "category": "proteins",
                    "source": "search",
                    "pick": {
                        "id": "EXAMPLE-DRUM-500",
                        "brand": "Example Farm",
                        "name": "Chicken Drumsticks",
                        "size": "500 g",
                        "price": 3.99,
                        "unit_price": 7.98,
                        "on_sale": True,
                        "available": True,
                    },
                    "alternatives": [
                        {
                            "id": "EXAMPLE-THIGH-500",
                            "brand": "Example Farm",
                            "name": "Chicken Thighs",
                            "size": "500 g",
                            "price": 6.49,
                            "unit_price": 12.98,
                            "available": True,
                        }
                    ],
                }
            ]
        }
        proposal = propose_cart(
            shopping_list=shopping, resolved=resolved, provider="pcexpress"
        )
        thighs = _item(proposal, "chicken thighs")
        self.assertIn("meal_plan_suggestion", thighs["flags"])
        self.assertEqual(thighs["meal_plan_suggestion"]["kind"], "swap_for_sale")
        self.assertIn("Sheet Pan Chicken", thighs["meal_plan_suggestion"]["affects"])

    def test_meal_plan_suggestion_when_essential_is_unavailable(self) -> None:
        peppers = _item(_proposal(), "bell pepper")
        suggestion = peppers["meal_plan_suggestion"]
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["kind"], "swap_for_availability")
        self.assertEqual(suggestion["suggested"], "Zucchini")
        self.assertIn("Tomato Skillet Pasta", suggestion["affects"])
        review = _proposal()["review"]
        self.assertTrue(
            any(row["name"] == "bell pepper" for row in review["meal_plan_suggestions"])
        )

    def test_classify_excess_marks_huge_shelf_pack(self) -> None:
        flag = classify_excess("2 tbsp soy sauce", "1.89 L", category="pantry")
        self.assertIsNotNone(flag)
        self.assertEqual(flag["severity"], "substantial")
        self.assertFalse(flag["perishable"])

    def test_current_cart_diff_keeps_existing_qty(self) -> None:
        proposal = _proposal(current_cart=_load("current-cart.json"))
        onion = _item(proposal, "onion")
        self.assertEqual(onion["action"], ACTION_KEEP)
        self.assertIn("already_in_cart", onion["flags"])


class ApprovalBoundaryTests(unittest.TestCase):
    def test_propose_does_not_create_mutations(self) -> None:
        proposal = _proposal()
        self.assertEqual(proposal["status"], STATUS_PROPOSED)
        self.assertFalse(proposal_is_approved(proposal))
        self.assertEqual(proposal["mutations"], [])

    def test_apply_before_approval_does_not_call_provider(self) -> None:
        proposal = _proposal()
        provider = MockCartProvider()
        with self.assertRaises(ApprovalRequired):
            apply_approved(proposal, provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(provider.cart, [])

    def test_approve_then_apply_adds_only_approved_products(self) -> None:
        proposal = _proposal()
        approved = approve_proposal(
            proposal,
            include=["ground turkey", "egg", "onion"],
            exclude=["cilantro", "sour cream"],
        )
        self.assertEqual(approved["status"], STATUS_APPROVED)
        self.assertTrue(proposal_is_approved(approved))
        ids = {row["product_id"] for row in approved["mutations"]}
        self.assertIn("EXAMPLE-TURKEY-SALE", ids)
        self.assertIn("EXAMPLE-EGG-12", ids)
        self.assertNotIn("EXAMPLE-SOUR-250", ids)
        provider = MockCartProvider()
        applied = apply_approved(approved, provider)
        self.assertEqual(applied["status"], STATUS_APPLIED)
        add_calls = [call for call in provider.calls if call[0] == "add_to_cart"]
        self.assertTrue(add_calls)
        self.assertTrue(any(call[0] == "view_cart" for call in provider.calls))
        self.assertIsNotNone(applied["result"]["cart"])
        self.assertFalse(applied["result"]["checkout"])

    def test_search_only_provider_cannot_apply(self) -> None:
        approved = approve_proposal(_proposal(), include=["onion"])
        provider = MockCartProvider(capabilities=SEARCH_ONLY)
        with self.assertRaises(CapabilityError):
            apply_approved(approved, provider)
        self.assertEqual(provider.calls, [])

    def test_partial_out_of_stock_does_not_require_live_calls(self) -> None:
        approved = approve_proposal(_proposal(), include=["ground turkey", "egg"])
        provider = MockCartProvider(fail={"EXAMPLE-EGG-12": "out of stock"})
        applied = apply_approved(approved, provider)
        self.assertEqual(applied["status"], STATUS_PARTIAL)
        failures = applied["result"]["failures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["product_id"], "EXAMPLE-EGG-12")
        self.assertEqual(failures[0]["error"], "out of stock")
        cart_ids = {item["id"] for item in applied["result"]["cart"]["items"]}
        self.assertIn("EXAMPLE-TURKEY-SALE", cart_ids)
        self.assertNotIn("EXAMPLE-EGG-12", cart_ids)

    def test_checkout_is_never_attempted(self) -> None:
        approved = approve_proposal(_proposal(), include=["onion"])
        approved["mutations"].append(
            {"op": "checkout", "product_id": "none", "quantity": 1}
        )
        provider = MockCartProvider()
        with self.assertRaises(CheckoutForbidden):
            apply_approved(approved, provider)
        self.assertFalse(any(call[0] == "checkout" for call in provider.calls))
        with self.assertRaises(CheckoutForbidden):
            provider.checkout()

    def test_markdown_never_hides_review_sections(self) -> None:
        markdown = render_markdown(_proposal())
        for heading in (
            "Substitutions",
            "Unavailable / unresolved essentials",
            "Price-driven changes",
            "Excess / waste",
            "Optional items that can be skipped",
            "Meal-plan suggestions",
            "Checkout",
        ):
            self.assertIn(heading, markdown)
        self.assertIn("Not approved", markdown)
        self.assertIn("bell pepper", markdown)
        self.assertIn("cilantro", markdown)
        self.assertIn("1.89 L", markdown)


class CliTests(unittest.TestCase):
    def test_cli_propose_approve_dry_run_and_mock_apply(self) -> None:
        shopping = FIXTURES / "shopping.json"
        resolved = FIXTURES / "resolved.json"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            proposed = root / "proposed.json"
            approved = root / "approved.json"
            applied = root / "applied.json"
            md = root / "proposed.md"
            self.assertEqual(
                main(
                    [
                        "propose",
                        str(shopping),
                        "--resolved",
                        str(resolved),
                        "--provider",
                        "pcexpress",
                        "-o",
                        str(proposed),
                        "--markdown-out",
                        str(md),
                    ]
                ),
                0,
            )
            artifact = json.loads(proposed.read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], STATUS_PROPOSED)
            self.assertTrue(md.read_text(encoding="utf-8").startswith("# Proposed Cart"))

            stderr = StringIO()
            with patch("sys.stderr", stderr):
                code = main(["apply", str(proposed), "--dry-run"])
            self.assertEqual(code, 1)
            self.assertIn("not approved", stderr.getvalue())

            self.assertEqual(
                main(
                    [
                        "approve",
                        str(proposed),
                        "--include",
                        "ground turkey",
                        "--include",
                        "egg",
                        "--exclude",
                        "cilantro",
                        "-o",
                        str(approved),
                    ]
                ),
                0,
            )
            approved_payload = json.loads(approved.read_text(encoding="utf-8"))
            self.assertEqual(approved_payload["status"], STATUS_APPROVED)
            self.assertTrue(approved_payload["mutations"])

            stdout = StringIO()
            with patch("sys.stdout", stdout):
                self.assertEqual(main(["apply", str(approved), "--dry-run"]), 0)
            dry = json.loads(stdout.getvalue())
            self.assertTrue(dry["dry_run"])
            self.assertTrue(dry["mutations"])

            self.assertEqual(
                main(
                    [
                        "apply",
                        str(approved),
                        "--mock",
                        str(FIXTURES / "mock-provider.json"),
                        "-o",
                        str(applied),
                    ]
                ),
                0,
            )
            result = json.loads(applied.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], STATUS_PARTIAL)
            self.assertEqual(result["result"]["failures"][0]["error"], "out of stock")

    def test_cli_capabilities(self) -> None:
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            self.assertEqual(main(["capabilities", "--adapter", "search-only", "--json"]), 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["search"])
        self.assertFalse(payload["can_mutate"])
        self.assertEqual(PC_EXPRESS.cart_write, True)


class DocsTests(unittest.TestCase):
    def test_manual_pcexpress_checklist_exists(self) -> None:
        docs = (ROOT / "references" / "pcexpress.md").read_text(encoding="utf-8")
        self.assertIn("Manual authenticated cart-mutation checklist", docs)
        self.assertIn("add_to_cart", docs)
        self.assertIn("view_cart", docs)
        self.assertIn("checkout", docs.lower())
        cart = (ROOT / "references" / "cart.md").read_text(encoding="utf-8")
        self.assertIn("approval", cart.lower())
        self.assertIn("search-only", cart.lower())
        self.assertIn("never", cart.lower())


if __name__ == "__main__":
    unittest.main()
