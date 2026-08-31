#!/usr/bin/env python3
"""Grocery-provider capability catalog.

Search and product resolution stay useful when a provider cannot write a
cart. Checkout is always out of scope in V2.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

CAPABILITIES = (
    "search",
    "details",
    "past_orders",
    "view_cart",
    "cart_write",
    "checkout",
)

# Checkout is listed so adapters can say it is absent. V2 never grants it.
OUT_OF_SCOPE = frozenset({"checkout"})

TOOL_TO_CAPABILITY = {
    "search_products": "search",
    "get_product_details": "details",
    "search_past_orders": "past_orders",
    "get_order_items": "past_orders",
    "view_cart": "view_cart",
    "add_to_cart": "cart_write",
    "remove_from_cart": "cart_write",
    "checkout": "checkout",
    "place_order": "checkout",
}

SIDE_EFFECT = {
    "search": "read",
    "details": "read",
    "past_orders": "read",
    "view_cart": "read",
    "cart_write": "write",
    "checkout": "forbidden",
}


@dataclass(frozen=True)
class ProviderCapabilities:
    search: bool = False
    details: bool = False
    past_orders: bool = False
    view_cart: bool = False
    cart_write: bool = False
    checkout: bool = False

    def __post_init__(self) -> None:
        if self.checkout:
            object.__setattr__(self, "checkout", False)

    def has(self, name: str) -> bool:
        if name in OUT_OF_SCOPE:
            return False
        return bool(getattr(self, name, False))

    def can_resolve(self) -> bool:
        return self.search or self.details

    def can_propose(self) -> bool:
        """A proposed cart is local; no remote write capability is required."""
        return True

    def can_mutate(self) -> bool:
        return self.cart_write

    def can_verify(self) -> bool:
        return self.view_cart

    def granted(self) -> tuple[str, ...]:
        return tuple(name for name in CAPABILITIES if self.has(name))

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["checkout"] = False
        payload["granted"] = list(self.granted())
        payload["can_resolve"] = self.can_resolve()
        payload["can_mutate"] = self.can_mutate()
        payload["can_verify"] = self.can_verify()
        return payload


NONE = ProviderCapabilities()
SEARCH_ONLY = ProviderCapabilities(search=True, details=True)
PC_EXPRESS = ProviderCapabilities(
    search=True,
    details=True,
    past_orders=True,
    view_cart=True,
    cart_write=True,
)

ADAPTERS = {
    "none": NONE,
    "search-only": SEARCH_ONLY,
    "search_only": SEARCH_ONLY,
    "pcexpress": PC_EXPRESS,
    "pc-express": PC_EXPRESS,
    "pc_express": PC_EXPRESS,
}


def capabilities_from_tools(tools: list[str] | tuple[str, ...]) -> ProviderCapabilities:
    """Map a provider's declared tools to capabilities. Checkout is never granted."""
    flags = {name: False for name in CAPABILITIES if name != "checkout"}
    for tool in tools:
        capability = TOOL_TO_CAPABILITY.get(str(tool))
        if capability and capability not in OUT_OF_SCOPE:
            flags[capability] = True
    return ProviderCapabilities(**flags)


def adapter_capabilities(name: str) -> ProviderCapabilities:
    key = (name or "none").strip().lower()
    if key not in ADAPTERS:
        raise KeyError(
            f"Unknown grocery adapter {name!r}. Known: "
            + ", ".join(sorted(set(ADAPTERS)))
        )
    return ADAPTERS[key]


def parse_capabilities(raw: dict | ProviderCapabilities | None) -> ProviderCapabilities:
    if raw is None:
        return NONE
    if isinstance(raw, ProviderCapabilities):
        return raw
    return ProviderCapabilities(
        search=bool(raw.get("search")),
        details=bool(raw.get("details")),
        past_orders=bool(raw.get("past_orders")),
        view_cart=bool(raw.get("view_cart")),
        cart_write=bool(raw.get("cart_write")),
        checkout=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print grocery-provider capabilities (checkout is never granted)"
    )
    parser.add_argument(
        "--adapter",
        default="pcexpress",
        help="Adapter name: pcexpress, search-only, none",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write the capability object as JSON",
    )
    args = parser.parse_args(argv)
    try:
        caps = adapter_capabilities(args.adapter)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.json:
        json.dump(caps.as_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    print("capability\tgranted\tside_effect")
    for name in CAPABILITIES:
        granted = "yes" if caps.has(name) else "no"
        print(f"{name}\t{granted}\t{SIDE_EFFECT[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
