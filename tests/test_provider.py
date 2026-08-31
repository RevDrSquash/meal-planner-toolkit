"""Provider capability catalog — no network, no credentials."""

from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from pcexpress import ALL_TOOLS, provider_capabilities  # noqa: E402
from provider import (  # noqa: E402
    NONE,
    PC_EXPRESS,
    SEARCH_ONLY,
    adapter_capabilities,
    capabilities_from_tools,
    main,
    parse_capabilities,
)


class CapabilityTests(unittest.TestCase):
    def test_search_only_cannot_mutate(self) -> None:
        self.assertTrue(SEARCH_ONLY.can_resolve())
        self.assertTrue(SEARCH_ONLY.can_propose())
        self.assertFalse(SEARCH_ONLY.can_mutate())
        self.assertFalse(SEARCH_ONLY.has("checkout"))
        self.assertNotIn("cart_write", SEARCH_ONLY.granted())

    def test_pcexpress_has_cart_write_but_never_checkout(self) -> None:
        self.assertTrue(PC_EXPRESS.can_resolve())
        self.assertTrue(PC_EXPRESS.can_mutate())
        self.assertTrue(PC_EXPRESS.can_verify())
        self.assertFalse(PC_EXPRESS.has("checkout"))
        self.assertFalse(PC_EXPRESS.checkout)
        payload = PC_EXPRESS.as_dict()
        self.assertFalse(payload["checkout"])

    def test_checkout_flag_is_stripped(self) -> None:
        caps = parse_capabilities({"search": True, "checkout": True, "cart_write": True})
        self.assertTrue(caps.cart_write)
        self.assertFalse(caps.checkout)
        self.assertFalse(caps.has("checkout"))

    def test_tools_without_cart_write_stay_search_only(self) -> None:
        caps = capabilities_from_tools(
            ("search_products", "get_product_details", "view_cart")
        )
        self.assertTrue(caps.search)
        self.assertTrue(caps.view_cart)
        self.assertFalse(caps.cart_write)
        self.assertFalse(caps.has("checkout"))

    def test_checkout_tool_is_never_granted(self) -> None:
        caps = capabilities_from_tools(("add_to_cart", "checkout", "place_order"))
        self.assertTrue(caps.cart_write)
        self.assertFalse(caps.checkout)

    def test_pcexpress_module_matches_catalog(self) -> None:
        caps = provider_capabilities()
        self.assertEqual(caps.granted(), PC_EXPRESS.granted())
        self.assertEqual(set(ALL_TOOLS) & {"checkout", "place_order"}, set())

    def test_unknown_adapter_errors(self) -> None:
        with self.assertRaises(KeyError):
            adapter_capabilities("unknown-store")
        self.assertEqual(adapter_capabilities("none"), NONE)

    def test_cli_lists_pcexpress(self) -> None:
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            self.assertEqual(main(["--adapter", "search-only", "--json"]), 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["search"])
        self.assertFalse(payload["cart_write"])
        self.assertFalse(payload["checkout"])


if __name__ == "__main__":
    unittest.main()
