#!/usr/bin/env python3
"""Proposed cart, review flags, and approved mutation application.

Read-only proposal construction needs no provider write access. Cart
mutations run only after an explicit approval step. Checkout is never
attempted.

Usage:
    python scripts/cart.py propose shopping.json --resolved resolved.json
    python scripts/cart.py approve proposal.json --include "soy sauce"
    python scripts/cart.py apply approved.json --dry-run
    python scripts/cart.py apply approved.json --mock mock-provider.json
    python scripts/cart.py capabilities --adapter pcexpress
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ingredients import (
    ROLE_ESSENTIAL,
    ROLE_GARNISH,
    ROLE_OPTIONAL,
    VOLUME_TO_ML,
    convert_amount,
    normalize_name,
    parse_ingredient_qty,
)
from provider import (
    CAPABILITIES,
    NONE,
    OUT_OF_SCOPE,
    SIDE_EFFECT,
    ProviderCapabilities,
    adapter_capabilities,
    parse_capabilities,
)
from shopping_list import (
    PANTRY_ASSUMED,
    PANTRY_BUY,
    names_match,
)

SCHEMA_VERSION = 1
KIND = "proposed-cart"
KIND_RESOLVED = "resolved-products"

STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"
STATUS_APPLIED = "applied"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

ACTION_ADD = "add"
ACTION_SKIP = "skip"
ACTION_UNAVAILABLE = "unavailable"
ACTION_REVIEW = "review"
ACTION_KEEP = "keep"

OP_ADD = "add"
OP_REMOVE = "remove"

FLAG_SUBSTITUTION = "substitution"
FLAG_UNAVAILABLE = "unavailable"
FLAG_EXCESS = "excess"
FLAG_PRICE_CHANGE = "price_change"
FLAG_OPTIONAL_SKIP = "optional_skip"
FLAG_UNRESOLVED = "unresolved"
FLAG_ALREADY_IN_CART = "already_in_cart"
FLAG_MEAL_PLAN = "meal_plan_suggestion"
FLAG_STAPLE = "staple"

SKIP_OK_ROLES = frozenset({ROLE_OPTIONAL, ROLE_GARNISH})
PERISHABLE_CATEGORIES = frozenset({"produce", "proteins", "dairy"})
GENERIC_TOKENS = frozenset(
    {
        "the",
        "and",
        "with",
        "fresh",
        "organic",
        "brand",
        "example",
        "farm",
        "lean",
        "extra",
        "large",
        "small",
        "pack",
        "pkg",
    }
)

# Align with the product-resolution helper so flags stay consistent.
UNIT_PRICE_BETTER_RATIO = 0.75
SIGNIFICANT_PRICE_RATIO = 0.25
SUBSTANTIAL_PERISHABLE = 2.5
SUBSTANTIAL_SHELF = 6.0
NOTE_PERISHABLE = 1.75
NOTE_SHELF = 3.0

CHECKOUT_OPS = frozenset({"checkout", "place_order", "pay", "purchase"})


class CartError(ValueError):
    """Invalid cart proposal, approval, or mutation input."""


class ApprovalRequired(CartError):
    """Raised when a mutation is attempted before the user approves."""


class CapabilityError(CartError):
    """Raised when the provider cannot perform the requested cart step."""


class CheckoutForbidden(CartError):
    """Checkout and payment are out of scope for V2."""


@dataclass
class MutationResult:
    ok: bool
    op: str
    product_id: str
    quantity: int
    error: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "op": self.op,
            "product_id": self.product_id,
            "quantity": self.quantity,
            "error": self.error,
            "detail": self.detail,
        }


@dataclass
class MockCartProvider:
    """In-memory cart used by tests and ``--mock``. No network."""

    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            search=True,
            details=True,
            view_cart=True,
            cart_write=True,
        )
    )
    fail: dict[str, str] = field(default_factory=dict)
    cart: list[dict] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    def add_to_cart(self, product_id: str, quantity: int) -> MutationResult:
        self.calls.append(("add_to_cart", product_id, int(quantity)))
        reason = self.fail.get(product_id)
        if reason:
            return MutationResult(
                ok=False,
                op=OP_ADD,
                product_id=product_id,
                quantity=int(quantity),
                error=reason,
                detail="provider rejected add",
            )
        self._upsert(product_id, int(quantity), add=True)
        return MutationResult(True, OP_ADD, product_id, int(quantity))

    def remove_from_cart(self, product_id: str, quantity: int) -> MutationResult:
        self.calls.append(("remove_from_cart", product_id, int(quantity)))
        reason = self.fail.get(product_id)
        if reason:
            return MutationResult(
                ok=False,
                op=OP_REMOVE,
                product_id=product_id,
                quantity=int(quantity),
                error=reason,
                detail="provider rejected remove",
            )
        self._upsert(product_id, int(quantity), add=False)
        return MutationResult(True, OP_REMOVE, product_id, int(quantity))

    def view_cart(self) -> dict:
        self.calls.append(("view_cart", None, None))
        return {
            "items": [dict(item) for item in self.cart],
            "item_count": len(self.cart),
        }

    def checkout(self, *args, **kwargs) -> None:
        raise CheckoutForbidden("checkout is out of scope in V2")

    def _upsert(self, product_id: str, quantity: int, *, add: bool) -> None:
        for item in self.cart:
            if item.get("id") == product_id:
                current = int(item.get("quantity") or 0)
                item["quantity"] = current + quantity if add else max(0, current - quantity)
                if item["quantity"] <= 0:
                    self.cart.remove(item)
                return
        if add:
            self.cart.append({"id": product_id, "quantity": quantity})


def load_json(path: Path | str) -> dict | list:
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, (dict, list)):
        raise CartError("JSON payload must be an object or array")
    return data


def load_resolved(payload: dict | list | None) -> list[dict]:
    """Accept product-resolve output, a picks list, or embedded pick rows."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [_normalize_resolution(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise CartError("resolved products must be an object or list")
    rows = payload.get("picks") or payload.get("items") or payload.get("resolutions")
    if rows is None and any(key in payload for key in ("ingredient", "name", "pick")):
        return [_normalize_resolution(payload)]
    if not isinstance(rows, list):
        raise CartError("resolved products must include a picks/items list")
    return [_normalize_resolution(item) for item in rows if isinstance(item, dict)]


def _normalize_resolution(row: dict) -> dict:
    pick = row.get("pick") or row.get("product") or row.get("chosen")
    if isinstance(pick, dict):
        pick = _normalize_product(pick)
    alternatives = [
        _normalize_product(alt)
        for alt in (row.get("alternatives") or row.get("alts") or [])
        if isinstance(alt, dict)
    ]
    mapping = row.get("mapping") or row.get("usual")
    usual = None
    if isinstance(mapping, dict):
        usual = _normalize_product(mapping)
        if not usual.get("id") and mapping.get("product_id"):
            usual["id"] = mapping.get("product_id")
        if not usual.get("size") and mapping.get("size"):
            usual["size"] = mapping.get("size")
    name = (
        row.get("ingredient")
        or row.get("name")
        or (pick or {}).get("name")
        or ""
    )
    return {
        "ingredient": normalize_name(str(name)) or str(name).strip(),
        "needed": row.get("needed") or row.get("display") or "",
        "category": row.get("category") or "",
        "role": row.get("role"),
        "origin": row.get("origin"),
        "sources": list(row.get("sources") or row.get("used_in") or []),
        "pick": pick,
        "alternatives": alternatives,
        "usual": usual,
        "reason": row.get("reason") or "",
        "source": row.get("source") or "",
        "excess": row.get("excess"),
        "prices_live": bool(row.get("prices_live", pick is not None and (pick or {}).get("price") is not None)),
        "raw_name": str(name).strip(),
    }


def _normalize_product(raw: dict) -> dict:
    product_id = raw.get("id") or raw.get("product_id") or raw.get("code")
    if product_id in {"n/a", "N/A", ""}:
        product_id = None
    available = raw.get("available", True)
    if isinstance(available, str):
        available = available.strip().lower() not in {
            "0",
            "false",
            "no",
            "out of stock",
            "sold out",
            "unavailable",
        }
    return {
        "id": product_id,
        "brand": raw.get("brand") or "",
        "name": raw.get("name") or raw.get("label") or "",
        "size": raw.get("size") or raw.get("package_size") or "",
        "price": _as_float(raw.get("price")),
        "unit_price": _as_float(raw.get("unit_price")),
        "on_sale": bool(raw.get("on_sale") or raw.get("sale")),
        "available": bool(available),
        "notes": raw.get("notes") or "",
    }


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def product_matches_ingredient(ingredient: str, product_name: str) -> bool:
    if names_match(ingredient, product_name):
        return True
    left = {
        token
        for token in normalize_name(ingredient).split()
        if token and token not in GENERIC_TOKENS
    }
    right = {
        token
        for token in normalize_name(product_name).split()
        if token and token not in GENERIC_TOKENS
    }
    if not left or not right:
        return False
    return left <= right or len(left & right) >= min(2, len(left))


def _volume_convert(amount: float, from_unit: str | None, to_unit: str | None) -> float | None:
    if from_unit in VOLUME_TO_ML and to_unit in VOLUME_TO_ML:
        ml = float(amount) * VOLUME_TO_ML[from_unit]
        return ml / VOLUME_TO_ML[to_unit]
    return None


def skip_ok_for(role: str | None, notes: Iterable[str] | None = None) -> bool:
    if (role or ROLE_ESSENTIAL) in SKIP_OK_ROLES:
        return True
    blob = " ".join(str(note) for note in (notes or [])).lower()
    return "optional" in blob or "garnish" in blob or "for serving" in blob


def classify_excess(
    needed: str,
    package_size: str,
    *,
    category: str = "",
    packages: int | None = None,
) -> dict | None:
    """Flag a shoppable pack that is much larger than the recipe need."""
    if not needed or not package_size:
        return None
    need = parse_ingredient_qty(needed)
    pack = parse_ingredient_qty(package_size)
    if need.amount is None or pack.amount is None:
        return {
            "severity": "unknown",
            "needed_display": needed,
            "package_display": package_size,
            "packages": packages or 1,
            "ratio": None,
            "perishable": category in PERISHABLE_CATEGORIES,
            "skip_ok": False,
            "summary": f"needed {needed}; shoppable size {package_size}",
        }
    converted = convert_amount(need.amount, need.unit, pack.unit)
    if converted is None:
        converted = _volume_convert(need.amount, need.unit, pack.unit)
    if converted is None:
        return {
            "severity": "unknown",
            "needed_display": needed,
            "package_display": package_size,
            "packages": packages or 1,
            "ratio": None,
            "perishable": category in PERISHABLE_CATEGORIES,
            "skip_ok": False,
            "summary": f"needed {needed}; shoppable size {package_size}",
        }
    pack_amount = pack.amount
    count = packages or max(1, int((converted + pack_amount - 1e-9) // pack_amount) or 1)
    total = pack_amount * count
    ratio = total / converted if converted else None
    perishable = category in PERISHABLE_CATEGORIES
    substantial = SUBSTANTIAL_PERISHABLE if perishable else SUBSTANTIAL_SHELF
    note = NOTE_PERISHABLE if perishable else NOTE_SHELF
    if ratio is None:
        severity = "unknown"
    elif ratio >= substantial:
        severity = "substantial"
    elif ratio >= note:
        severity = "note"
    else:
        severity = "none"
    if severity == "none":
        return {
            "severity": "none",
            "needed_display": needed,
            "package_display": package_size,
            "packages": count,
            "ratio": ratio,
            "perishable": perishable,
            "skip_ok": False,
            "summary": f"{count} × {package_size} covers {needed}",
        }
    leftover = "perishable leftover" if perishable else "extra pantry stock"
    return {
        "severity": severity,
        "needed_display": needed,
        "package_display": package_size,
        "packages": count,
        "ratio": ratio,
        "perishable": perishable,
        "skip_ok": False,
        "summary": (
            f"{count} × {package_size} for {needed} "
            f"({ratio:.1f}× needed; {leftover})"
        ),
    }


def propose_cart(
    *,
    shopping_list: dict | None = None,
    resolved: dict | list | None = None,
    current_cart: dict | list | None = None,
    provider: str | ProviderCapabilities | dict | None = None,
    source_shopping_list: str | None = None,
) -> dict:
    """Build a reviewable proposed cart. Does not call a provider."""
    shopping = dict(shopping_list or {})
    shop_items = [
        dict(item)
        for item in (shopping.get("items") or [])
        if isinstance(item, dict)
    ]
    resolutions = load_resolved(resolved)
    caps = _capabilities_arg(provider)
    current = _cart_items(current_cart)

    proposed: list[dict] = []
    used_resolution_indexes: set[int] = set()

    for shop in shop_items:
        if shop.get("pantry_status") == PANTRY_ASSUMED:
            continue
        if shop.get("pantry_status") not in {PANTRY_BUY, None, ""}:
            # needs_confirmation stays off the cart unless it was resolved.
            match_idx = _find_resolution_index(shop.get("name") or "", resolutions)
            if match_idx is None:
                continue
        match_idx = _find_resolution_index(shop.get("name") or "", resolutions)
        resolution = resolutions[match_idx] if match_idx is not None else None
        if match_idx is not None:
            used_resolution_indexes.add(match_idx)
        proposed.append(_line_from_parts(shop, resolution, current))

    for index, resolution in enumerate(resolutions):
        if index in used_resolution_indexes:
            continue
        if not resolution.get("pick") and not resolution.get("alternatives"):
            continue
        proposed.append(_line_from_parts({}, resolution, current))

    proposed.sort(key=lambda item: (item.get("name") or "").lower())
    review = _build_review(proposed)
    payload = {
        "version": SCHEMA_VERSION,
        "kind": KIND,
        "status": STATUS_PROPOSED,
        "source_plan": shopping.get("source_plan") or "",
        "source_shopping_list": source_shopping_list or shopping.get("source_plan") or "",
        "provider": _provider_label(provider),
        "capabilities": caps.as_dict(),
        "items": proposed,
        "review": review,
        "estimated_total": _estimated_total(proposed),
        "approval": None,
        "mutations": [],
        "result": None,
    }
    return payload


def _line_from_parts(shop: dict, resolution: dict | None, current: list[dict]) -> dict:
    resolution = resolution or {}
    pick = resolution.get("pick")
    name = (
        shop.get("name")
        or resolution.get("ingredient")
        or resolution.get("raw_name")
        or (pick or {}).get("name")
        or "item"
    )
    role = shop.get("role") or resolution.get("role") or ROLE_ESSENTIAL
    notes = list(shop.get("notes") or [])
    skip_ok = skip_ok_for(role, notes)
    needed = shop.get("display") or resolution.get("needed") or ""
    origin = shop.get("origin") or resolution.get("origin") or "recipe"
    sources = list(shop.get("sources") or resolution.get("sources") or [])
    planning_subs = list(shop.get("substitutions") or [])
    usual = resolution.get("usual")
    alternatives = list(resolution.get("alternatives") or [])
    flags: list[str] = []
    line_notes = list(notes)

    if origin == "staple" or shop.get("staple"):
        flags.append(FLAG_STAPLE)

    available = bool(pick and pick.get("available") and pick.get("id"))
    excess = resolution.get("excess")
    if not isinstance(excess, dict) and pick and pick.get("size") and needed:
        excess = classify_excess(
            needed,
            pick.get("size") or "",
            category=shop.get("category") or resolution.get("category") or "",
        )
    if isinstance(excess, dict) and excess.get("severity") in {None, "none"}:
        packages = excess.get("packages") or 1
        excess_flag = None
    elif isinstance(excess, dict):
        packages = excess.get("packages") or 1
        excess_flag = excess
    else:
        packages = 1
        excess_flag = None

    quantity = max(1, int(packages or 1))
    current_qty = _quantity_in_cart(current, (pick or {}).get("id"))
    already = current_qty >= quantity and available

    substitution = _substitution_flag(name, pick, usual, planning_subs, resolution.get("source") or "")
    price_change = _price_change_flag(pick, usual, alternatives)
    meal_plan = _meal_plan_suggestion(
        name=name,
        role=role,
        skip_ok=skip_ok,
        pick=pick,
        alternatives=alternatives,
        sources=sources,
        available=available,
        price_change=price_change,
    )

    if not available:
        flags.append(FLAG_UNAVAILABLE if pick or alternatives else FLAG_UNRESOLVED)
        action = ACTION_SKIP if skip_ok else ACTION_UNAVAILABLE
        if skip_ok:
            flags.append(FLAG_OPTIONAL_SKIP)
            line_notes.append("optional/garnish — can skip if missing")
        else:
            line_notes.append("essential item has no shoppable product")
        quantity = 0
    elif already:
        flags.append(FLAG_ALREADY_IN_CART)
        action = ACTION_KEEP
        line_notes.append("already in the remote cart at the needed quantity")
    elif (
        substitution
        or (excess_flag and excess_flag.get("severity") in {"substantial", "unknown"})
        or price_change
        or meal_plan
    ):
        action = ACTION_REVIEW
    else:
        action = ACTION_ADD

    if substitution:
        flags.append(FLAG_SUBSTITUTION)
        line_notes.append(substitution["summary"])
    if excess_flag and excess_flag.get("severity") in {"note", "substantial", "unknown"}:
        flags.append(FLAG_EXCESS)
        line_notes.append(excess_flag.get("summary") or "package larger than needed")
    if price_change:
        flags.append(FLAG_PRICE_CHANGE)
        line_notes.append(price_change["summary"])
    if meal_plan:
        flags.append(FLAG_MEAL_PLAN)
        line_notes.append(meal_plan["summary"])
    if resolution.get("reason"):
        line_notes.append(str(resolution["reason"]))

    # Deduplicate notes while keeping order.
    seen: set[str] = set()
    unique_notes = []
    for note in line_notes:
        if note and note not in seen:
            seen.add(note)
            unique_notes.append(note)

    unique_flags = []
    for flag in flags:
        if flag not in unique_flags:
            unique_flags.append(flag)

    return {
        "name": name,
        "needed": needed,
        "role": role,
        "origin": origin,
        "sources": sources,
        "staple": bool(shop.get("staple") or origin == "staple"),
        "skip_ok": skip_ok,
        "action": action,
        "quantity": quantity if action in {ACTION_ADD, ACTION_REVIEW, ACTION_KEEP} else 0,
        "product": pick,
        "alternatives": alternatives,
        "usual": usual,
        "planning_substitutions": planning_subs,
        "flags": unique_flags,
        "notes": unique_notes,
        "reason": resolution.get("reason") or "",
        "source": resolution.get("source") or "",
        "excess": excess_flag,
        "substitution": substitution,
        "price_change": price_change,
        "meal_plan_suggestion": meal_plan,
        "already_in_cart": already,
    }


def _substitution_flag(
    name: str,
    pick: dict | None,
    usual: dict | None,
    planning_subs: list[dict],
    source: str,
) -> dict | None:
    if planning_subs:
        first = planning_subs[0]
        return {
            "kind": "planning",
            "from": first.get("from"),
            "to": first.get("to") or name,
            "summary": (
                f"plan uses {first.get('to') or name} instead of {first.get('from')}"
            ),
        }
    if pick and not product_matches_ingredient(name, pick.get("name") or ""):
        return {
            "kind": "product",
            "from": name,
            "to": pick.get("name") or pick.get("id"),
            "summary": f"pick {pick.get('name') or pick.get('id')} is not the same food as {name}",
        }
    if usual and pick and usual.get("id") and pick.get("id") and usual["id"] != pick["id"]:
        if source == "mapping-fallback-search" or not product_matches_ingredient(
            usual.get("name") or name, pick.get("name") or ""
        ):
            return {
                "kind": "usual",
                "from": usual.get("name") or usual.get("id"),
                "to": pick.get("name") or pick.get("id"),
                "summary": (
                    f"usual product {usual.get('id')} replaced by {pick.get('id')}"
                ),
            }
    return None


def _price_change_flag(
    pick: dict | None,
    usual: dict | None,
    alternatives: list[dict],
) -> dict | None:
    if not pick:
        return None
    if usual and usual.get("unit_price") and pick.get("unit_price"):
        usual_up = usual["unit_price"]
        pick_up = pick["unit_price"]
        if usual_up > 0:
            delta = (pick_up - usual_up) / usual_up
            if abs(delta) >= SIGNIFICANT_PRICE_RATIO or pick.get("on_sale"):
                direction = "cheaper" if delta < 0 else "more expensive"
                return {
                    "kind": "vs_usual",
                    "summary": (
                        f"unit price is {abs(delta) * 100:.0f}% {direction} "
                        f"than the usual product"
                    ),
                    "usual_unit_price": usual_up,
                    "pick_unit_price": pick_up,
                    "on_sale": bool(pick.get("on_sale")),
                }
    if pick.get("on_sale") and usual and usual.get("id") and pick.get("id") != usual.get("id"):
        return {
            "kind": "sale_swap",
            "summary": "on-sale product is a different id than the usual mapping",
            "on_sale": True,
        }
    cheaper = None
    for alt in alternatives:
        if not alt.get("available") or alt.get("unit_price") is None or pick.get("unit_price") is None:
            continue
        if alt["unit_price"] <= pick["unit_price"] * UNIT_PRICE_BETTER_RATIO:
            cheaper = alt
            break
    if cheaper:
        return {
            "kind": "cheaper_alt",
            "summary": (
                f"alternative {cheaper.get('name') or cheaper.get('id')} is "
                "materially cheaper per unit"
            ),
            "alternative": cheaper,
            "on_sale": bool(cheaper.get("on_sale")),
        }
    return None


def _meal_plan_suggestion(
    *,
    name: str,
    role: str,
    skip_ok: bool,
    pick: dict | None,
    alternatives: list[dict],
    sources: list[str],
    available: bool,
    price_change: dict | None,
) -> dict | None:
    if skip_ok or role in SKIP_OK_ROLES:
        return None
    recipes = sources or []
    if not available:
        alt = next((item for item in alternatives if item.get("available")), None)
        if alt:
            return {
                "kind": "swap_for_availability",
                "ingredient": name,
                "suggested": alt.get("name") or alt.get("id"),
                "because": "usual/needed item is unavailable; a substitute is in stock",
                "affects": recipes,
                "sale": bool(alt.get("on_sale")),
                "summary": (
                    f"consider using {alt.get('name') or alt.get('id')} "
                    f"for {', '.join(recipes) or name}"
                ),
            }
        return {
            "kind": "omit_or_change_meal",
            "ingredient": name,
            "suggested": None,
            "because": "essential item is unavailable and no substitute was found",
            "affects": recipes,
            "sale": False,
            "summary": (
                f"{name} is unavailable"
                + (f" — affects {', '.join(recipes)}" if recipes else "")
            ),
        }
    saleish = bool(
        (pick and pick.get("on_sale"))
        or (price_change and (price_change.get("on_sale") or price_change.get("kind") == "cheaper_alt"))
    )
    if (
        pick
        and saleish
        and not product_matches_ingredient(name, pick.get("name") or "")
    ):
        return {
            "kind": "swap_for_sale",
            "ingredient": name,
            "suggested": pick.get("name") or pick.get("id"),
            "because": "a substantial sale makes a reasonable substitute worthwhile",
            "affects": recipes,
            "sale": True,
            "summary": (
                f"sale on {pick.get('name') or pick.get('id')} could replace {name}"
                + (f" in {', '.join(recipes)}" if recipes else "")
            ),
        }
    cheaper = (price_change or {}).get("alternative")
    if cheaper and not product_matches_ingredient(name, cheaper.get("name") or ""):
        return {
            "kind": "swap_for_sale",
            "ingredient": name,
            "suggested": cheaper.get("name") or cheaper.get("id"),
            "because": "a substantial sale makes a reasonable substitute worthwhile",
            "affects": recipes,
            "sale": True,
            "summary": (
                f"consider {cheaper.get('name') or cheaper.get('id')} "
                f"instead of {name} (sale / unit price)"
            ),
        }
    return None


def _build_review(items: list[dict]) -> dict:
    def _take(flag: str, extra: Callable[[dict], bool] | None = None) -> list[dict]:
        rows = []
        for item in items:
            if flag in (item.get("flags") or []) and (extra is None or extra(item)):
                rows.append(_review_row(item))
        return rows

    skippable = [
        _review_row(item)
        for item in items
        if item.get("skip_ok")
        and (
            item.get("action") == ACTION_SKIP
            or FLAG_OPTIONAL_SKIP in (item.get("flags") or [])
            or item.get("role") in SKIP_OK_ROLES
        )
    ]
    return {
        "substitutions": _take(FLAG_SUBSTITUTION),
        "unavailable": [
            _review_row(item)
            for item in items
            if item.get("action") == ACTION_UNAVAILABLE
            or (FLAG_UNAVAILABLE in (item.get("flags") or []) and not item.get("skip_ok"))
        ],
        "price_changes": _take(FLAG_PRICE_CHANGE),
        "excess": _take(FLAG_EXCESS),
        "skippable": skippable,
        "omissions": [
            _review_row(item)
            for item in items
            if item.get("action") == ACTION_SKIP or FLAG_OPTIONAL_SKIP in (item.get("flags") or [])
        ],
        "meal_plan_suggestions": [
            {
                **_review_row(item),
                **(item.get("meal_plan_suggestion") or {}),
            }
            for item in items
            if item.get("meal_plan_suggestion")
        ],
        "unresolved": _take(FLAG_UNRESOLVED),
    }


def _review_row(item: dict) -> dict:
    product = item.get("product") or {}
    return {
        "name": item.get("name"),
        "needed": item.get("needed"),
        "role": item.get("role"),
        "action": item.get("action"),
        "product_id": product.get("id"),
        "product": product.get("name"),
        "sources": list(item.get("sources") or []),
        "flags": list(item.get("flags") or []),
        "summary": (item.get("notes") or [item.get("reason") or ""])[0]
        if (item.get("notes") or item.get("reason"))
        else "",
    }


def approve_proposal(
    proposal: dict,
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    remove: Iterable[str] | dict | None = None,
    accept_substitutions: bool | Iterable[str] = True,
    accept_suggestions: Iterable[str] | bool = False,
) -> dict:
    """Mark a proposal approved and freeze the mutation list. No provider I/O."""
    if proposal.get("kind") not in {KIND, None}:
        raise CartError(f"cannot approve artifact kind {proposal.get('kind')!r}")
    approved = dict(proposal)
    include_names = [normalize_name(name) or name.lower() for name in (include or [])]
    exclude_names = [normalize_name(name) or name.lower() for name in (exclude or [])]
    accept_all_subs = accept_substitutions is True
    accept_sub_names = (
        []
        if isinstance(accept_substitutions, bool)
        else [normalize_name(name) or name.lower() for name in accept_substitutions]
    )
    accepted_suggestions: list[str] = []
    if accept_suggestions is True:
        accepted_suggestions = [
            item.get("name")
            for item in (approved.get("review") or {}).get("meal_plan_suggestions") or []
        ]
    elif accept_suggestions:
        accepted_suggestions = list(accept_suggestions)

    mutations: list[dict] = []
    items = [dict(item) for item in approved.get("items") or []]
    for item in items:
        name = item.get("name") or ""
        key = normalize_name(name) or name.lower()
        product = item.get("product") or {}
        product_id = product.get("id")
        if _matches_names(key, name, exclude_names):
            item["action"] = ACTION_SKIP
            item["quantity"] = 0
            continue
        if include_names and not _matches_names(key, name, include_names):
            item["action"] = ACTION_SKIP
            item["quantity"] = 0
            continue
        if item.get("action") == ACTION_UNAVAILABLE:
            continue
        if item.get("action") == ACTION_SKIP:
            continue
        if item.get("action") == ACTION_KEEP:
            continue
        if not product_id or not product.get("available"):
            item["action"] = ACTION_UNAVAILABLE
            continue
        if FLAG_SUBSTITUTION in (item.get("flags") or []):
            if not accept_all_subs and not _matches_names(key, name, accept_sub_names):
                item["action"] = ACTION_SKIP
                item["quantity"] = 0
                continue
        quantity = int(item.get("quantity") or 1)
        if quantity <= 0:
            continue
        item["action"] = ACTION_ADD
        mutations.append(
            {
                "op": OP_ADD,
                "product_id": product_id,
                "quantity": quantity,
                "name": name,
                "role": item.get("role"),
            }
        )

    for removal in _normalize_removals(remove):
        mutations.append(removal)

    approved["items"] = items
    approved["status"] = STATUS_APPROVED
    approved["approval"] = {
        "approved": True,
        "include": list(include or []),
        "exclude": list(exclude or []),
        "remove": [row for row in mutations if row["op"] == OP_REMOVE],
        "accept_substitutions": accept_substitutions
        if isinstance(accept_substitutions, bool)
        else list(accept_substitutions),
        "accept_suggestions": accepted_suggestions,
    }
    approved["mutations"] = mutations
    approved["review"] = _build_review(items)
    approved["estimated_total"] = _estimated_total(items)
    approved["result"] = None
    return approved


def apply_approved(
    proposal: dict,
    provider: MockCartProvider,
    *,
    approved: bool | None = None,
) -> dict:
    """Apply approved mutations. Refuses checkout and unapproved proposals."""
    if approved is False:
        raise ApprovalRequired("user declined the proposed cart; no remote changes")
    if not proposal_is_approved(proposal) and not approved:
        raise ApprovalRequired(
            "proposed cart is not approved; refuse to call add/remove"
        )
    caps = getattr(provider, "capabilities", None) or NONE
    if isinstance(caps, dict):
        caps = parse_capabilities(caps)
    if not caps.can_mutate():
        raise CapabilityError(
            "provider has search/resolution but no cart-write capability; "
            "use the proposed cart as a store list"
        )
    mutations = list(proposal.get("mutations") or [])
    if any(str(row.get("op") or "").lower() in CHECKOUT_OPS | OUT_OF_SCOPE for row in mutations):
        raise CheckoutForbidden("checkout is out of scope in V2")

    results: list[dict] = []
    for row in mutations:
        op = str(row.get("op") or "")
        product_id = str(row.get("product_id") or "")
        quantity = int(row.get("quantity") or 1)
        if op in CHECKOUT_OPS:
            raise CheckoutForbidden("checkout is out of scope in V2")
        if op == OP_ADD:
            outcome = provider.add_to_cart(product_id, quantity)
        elif op == OP_REMOVE:
            outcome = provider.remove_from_cart(product_id, quantity)
        else:
            raise CartError(f"unknown mutation op {op!r}")
        if not isinstance(outcome, MutationResult):
            outcome = MutationResult(
                ok=bool(getattr(outcome, "ok", outcome)),
                op=op,
                product_id=product_id,
                quantity=quantity,
                error=None if getattr(outcome, "ok", outcome) else "rejected",
            )
        results.append(outcome.as_dict())

    snapshot = None
    if caps.can_verify() and hasattr(provider, "view_cart"):
        snapshot = provider.view_cart()

    failures = [row for row in results if not row.get("ok")]
    applied = {
        **proposal,
        "status": STATUS_PARTIAL
        if failures and any(row.get("ok") for row in results)
        else STATUS_FAILED
        if failures
        else STATUS_APPLIED,
        "result": {
            "mutations": results,
            "failures": failures,
            "cart": snapshot,
            "checkout": False,
        },
    }
    return applied


def proposal_is_approved(proposal: dict) -> bool:
    if proposal.get("status") == STATUS_APPROVED:
        return True
    approval = proposal.get("approval") or {}
    return bool(approval.get("approved")) and bool(proposal.get("mutations"))


def render_markdown(proposal: dict) -> str:
    title = proposal.get("source_plan") or proposal.get("source_shopping_list") or "proposed cart"
    caps = proposal.get("capabilities") or {}
    lines = [
        f"# Proposed Cart — {title}",
        "",
        f"Status: `{proposal.get('status') or STATUS_PROPOSED}`",
        f"Provider: `{proposal.get('provider') or 'none'}`",
        "",
        "Review this list before any remote cart change. Checkout stays manual.",
        "",
        "## Capabilities",
        "",
        (
            f"- Search/resolve: {'yes' if caps.get('can_resolve') else 'no'} · "
            f"Cart write: {'yes' if caps.get('can_mutate') else 'no'} · "
            f"View cart: {'yes' if caps.get('can_verify') else 'no'} · "
            "Checkout: never"
        ),
        "",
    ]
    if not caps.get("can_mutate"):
        lines.extend(
            [
                "This provider cannot write a cart. Use the list below in the store, "
                "or resolve products without requesting a fill.",
                "",
            ]
        )

    adds = [
        item
        for item in proposal.get("items") or []
        if item.get("action") in {ACTION_ADD, ACTION_REVIEW, ACTION_KEEP}
    ]
    lines.extend(["## Proposed adds", ""])
    if adds:
        for item in adds:
            lines.append(f"- {_item_bullet(item)}")
        lines.append("")
    else:
        lines.append("- None.")
        lines.append("")

    review = proposal.get("review") or {}
    sections = (
        ("Substitutions", "substitutions"),
        ("Unavailable / unresolved essentials", "unavailable"),
        ("Price-driven changes", "price_changes"),
        ("Excess / waste", "excess"),
        ("Optional items that can be skipped", "skippable"),
        ("Omissions", "omissions"),
        ("Meal-plan suggestions", "meal_plan_suggestions"),
    )
    for heading, key in sections:
        rows = review.get(key) or []
        lines.extend([f"## {heading}", ""])
        if not rows:
            lines.append("- None.")
            lines.append("")
            continue
        for row in rows:
            summary = row.get("summary") or row.get("because") or row.get("name")
            extra = ""
            if row.get("suggested"):
                extra = f" → {row['suggested']}"
            lines.append(f"- **{row.get('name')}**{extra} — {summary}")
        lines.append("")

    unresolved = review.get("unresolved") or []
    if unresolved:
        lines.extend(["## Unresolved to-buy items", ""])
        for row in unresolved:
            lines.append(f"- **{row.get('name')}** — {row.get('summary') or 'no product pick'}")
        lines.append("")

    total = proposal.get("estimated_total")
    total_text = f"${total:.2f}" if isinstance(total, (int, float)) else "unknown"
    lines.extend(
        [
            f"Estimated total (proposed adds): {total_text}",
            "",
            "## Approval",
            "",
        ]
    )
    if proposal.get("status") == STATUS_APPROVED:
        lines.append("Approved. Mutations are listed below and have not been applied until `apply`.")
        lines.append("")
        for mutation in proposal.get("mutations") or []:
            lines.append(
                f"- {mutation.get('op')} `{mutation.get('product_id')}` "
                f"× {mutation.get('quantity')} ({mutation.get('name')})"
            )
        lines.append("")
    else:
        lines.extend(
            [
                "Not approved. Do not call `add_to_cart` or `remove_from_cart`.",
                "",
            ]
        )
    lines.extend(
        [
            "## Checkout",
            "",
            "Out of scope. Pay on the store site after reviewing the live cart.",
            "",
        ]
    )
    return "\n".join(lines)


def cart_to_json(proposal: dict) -> str:
    return json.dumps(proposal, indent=2, ensure_ascii=False) + "\n"


def mock_provider_from_payload(payload: dict | None) -> MockCartProvider:
    data = dict(payload or {})
    caps = parse_capabilities(data.get("capabilities"))
    if data.get("capabilities") is None:
        caps = ProviderCapabilities(
            search=True,
            details=True,
            view_cart=True,
            cart_write=True,
        )
    fail = data.get("fail") or data.get("failures") or {}
    if isinstance(fail, list):
        fail = {str(item): "out of stock" for item in fail}
    cart = data.get("cart") or data.get("items") or []
    if isinstance(cart, dict):
        cart = cart.get("items") or []
    return MockCartProvider(
        capabilities=caps,
        fail={str(key): str(value) for key, value in fail.items()},
        cart=[dict(item) for item in cart if isinstance(item, dict)],
    )


def _capabilities_arg(provider: str | ProviderCapabilities | dict | None) -> ProviderCapabilities:
    if provider is None:
        return NONE
    if isinstance(provider, ProviderCapabilities):
        return provider
    if isinstance(provider, dict):
        if any(key in provider for key in CAPABILITIES):
            return parse_capabilities(provider)
        name = provider.get("adapter") or provider.get("name") or provider.get("provider")
        if name:
            return adapter_capabilities(str(name))
        return parse_capabilities(provider)
    return adapter_capabilities(str(provider))


def _provider_label(provider: str | ProviderCapabilities | dict | None) -> str:
    if provider is None:
        return "none"
    if isinstance(provider, str):
        return provider
    if isinstance(provider, ProviderCapabilities):
        if provider.can_mutate():
            return "custom"
        if provider.can_resolve():
            return "search-only"
        return "none"
    if isinstance(provider, dict):
        return str(
            provider.get("adapter")
            or provider.get("name")
            or provider.get("provider")
            or ("search-only" if provider.get("search") and not provider.get("cart_write") else "custom")
        )
    return "custom"


def _find_resolution_index(name: str, resolutions: list[dict]) -> int | None:
    if not name:
        return None
    for index, row in enumerate(resolutions):
        if names_match(name, row.get("ingredient") or row.get("raw_name") or ""):
            return index
    return None


def _cart_items(current: dict | list | None) -> list[dict]:
    if current is None:
        return []
    if isinstance(current, list):
        rows = current
    else:
        rows = current.get("items") or current.get("cart") or []
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_id = row.get("id") or row.get("product_id")
        if not product_id:
            continue
        items.append(
            {
                "id": product_id,
                "quantity": int(row.get("quantity") or 1),
                "name": row.get("name") or "",
            }
        )
    return items


def _quantity_in_cart(current: list[dict], product_id: str | None) -> int:
    if not product_id:
        return 0
    return sum(int(item.get("quantity") or 0) for item in current if item.get("id") == product_id)


def _estimated_total(items: list[dict]) -> float | None:
    total = 0.0
    saw_price = False
    for item in items:
        if item.get("action") not in {ACTION_ADD, ACTION_REVIEW}:
            continue
        product = item.get("product") or {}
        price = product.get("price")
        if price is None:
            return None
        saw_price = True
        total += float(price) * max(1, int(item.get("quantity") or 1))
    return round(total, 2) if saw_price else None


def _item_bullet(item: dict) -> str:
    product = item.get("product") or {}
    label = " ".join(part for part in (product.get("brand"), product.get("name")) if part) or item.get("name")
    ident = product.get("id") or "n/a"
    size = product.get("size") or "n/a"
    price = product.get("price")
    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "unknown"
    if product.get("on_sale") and isinstance(price, (int, float)):
        price_text += ", SALE"
    flags = ", ".join(item.get("flags") or []) or item.get("action")
    qty = item.get("quantity") or 0
    return (
        f"**{item.get('name')}** — {item.get('needed') or 'as needed'} → "
        f"{qty} × `{ident}` {label} ({size}, {price_text}) [{flags}]"
    )


def _matches_names(key: str, name: str, wanted: list[str]) -> bool:
    if not wanted:
        return False
    for item in wanted:
        if key == item or names_match(name, item) or names_match(key, item):
            return True
    return False


def _normalize_removals(remove: Iterable[str] | dict | None) -> list[dict]:
    if not remove:
        return []
    rows = []
    if isinstance(remove, dict):
        remove = [remove]
    for item in remove:
        if isinstance(item, str):
            rows.append({"op": OP_REMOVE, "product_id": item, "quantity": 1, "name": item})
        elif isinstance(item, dict):
            product_id = item.get("id") or item.get("product_id")
            if not product_id:
                continue
            rows.append(
                {
                    "op": OP_REMOVE,
                    "product_id": product_id,
                    "quantity": int(item.get("quantity") or 1),
                    "name": item.get("name") or product_id,
                }
            )
    return rows


def _read_payload(path: Path | None) -> dict | list | None:
    if path is None:
        return None
    if str(path) == "-":
        return json.load(sys.stdin)
    return load_json(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a proposed grocery cart, require approval, then apply "
            "mocked provider mutations. Never checks out."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose", help="Build a reviewable proposed cart (no writes)")
    propose.add_argument("shopping", type=Path, help="Shopping-list JSON")
    propose.add_argument("--resolved", type=Path, default=None, help="Resolved-product JSON")
    propose.add_argument("--current-cart", type=Path, default=None, help="Current remote cart JSON")
    propose.add_argument(
        "--provider",
        default="pcexpress",
        help="Adapter name or 'search-only' / 'none'",
    )
    propose.add_argument("--json", action="store_true", help="Write JSON to stdout")
    propose.add_argument("-o", "--output", type=Path, default=None)
    propose.add_argument("--markdown-out", type=Path, default=None)

    approve = sub.add_parser("approve", help="Freeze approved mutations (still no writes)")
    approve.add_argument("proposal", type=Path)
    approve.add_argument("--include", action="append", default=[], help="Ingredient to add (repeatable)")
    approve.add_argument("--exclude", action="append", default=[], help="Ingredient to skip (repeatable)")
    approve.add_argument("--remove", action="append", default=[], help="Product id to remove (repeatable)")
    approve.add_argument(
        "--reject-substitutions",
        action="store_true",
        help="Skip lines flagged as substitutions",
    )
    approve.add_argument("--json", action="store_true")
    approve.add_argument("-o", "--output", type=Path, default=None)
    approve.add_argument("--markdown-out", type=Path, default=None)

    apply_cmd = sub.add_parser("apply", help="Apply an approved proposal via a mock provider")
    apply_cmd.add_argument("proposal", type=Path)
    apply_cmd.add_argument("--mock", type=Path, default=None, help="Mock provider JSON")
    apply_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print mutations without calling the provider",
    )
    apply_cmd.add_argument("--json", action="store_true")
    apply_cmd.add_argument("-o", "--output", type=Path, default=None)

    caps = sub.add_parser("capabilities", help="Show adapter capabilities")
    caps.add_argument("--adapter", default="pcexpress")
    caps.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "capabilities":
        try:
            capabilities = adapter_capabilities(args.adapter)
        except KeyError as exc:
            print(exc, file=sys.stderr)
            return 1
        if args.json:
            json.dump(capabilities.as_dict(), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        print("capability\tgranted\tside_effect")
        for name in CAPABILITIES:
            print(f"{name}\t{'yes' if capabilities.has(name) else 'no'}\t{SIDE_EFFECT[name]}")
        return 0

    if args.command == "propose":
        try:
            shopping = load_json(args.shopping)
            if not isinstance(shopping, dict):
                raise CartError("shopping list must be a JSON object")
            resolved = _read_payload(args.resolved)
            current = _read_payload(args.current_cart)
            proposal = propose_cart(
                shopping_list=shopping,
                resolved=resolved,
                current_cart=current if isinstance(current, (dict, list)) else None,
                provider=args.provider,
                source_shopping_list=str(args.shopping),
            )
        except (OSError, json.JSONDecodeError, CartError, KeyError) as exc:
            print(f"Error proposing cart: {exc}", file=sys.stderr)
            return 1
        return _write_artifact(proposal, args)

    if args.command == "approve":
        try:
            proposal = load_json(args.proposal)
            if not isinstance(proposal, dict):
                raise CartError("proposal must be a JSON object")
            approved = approve_proposal(
                proposal,
                include=args.include or None,
                exclude=args.exclude or None,
                remove=args.remove or None,
                accept_substitutions=not args.reject_substitutions,
            )
        except (OSError, json.JSONDecodeError, CartError) as exc:
            print(f"Error approving cart: {exc}", file=sys.stderr)
            return 1
        return _write_artifact(approved, args)

    try:
        proposal = load_json(args.proposal)
        if not isinstance(proposal, dict):
            raise CartError("proposal must be a JSON object")
        if args.dry_run:
            if not proposal_is_approved(proposal):
                raise ApprovalRequired(
                    "proposed cart is not approved; refuse to call add/remove"
                )
            json.dump({"dry_run": True, "mutations": proposal.get("mutations") or []}, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        mock_payload = load_json(args.mock) if args.mock else {}
        if args.mock and not isinstance(mock_payload, dict):
            raise CartError("mock provider JSON must be an object")
        provider = mock_provider_from_payload(mock_payload if isinstance(mock_payload, dict) else {})
        applied = apply_approved(proposal, provider)
    except (OSError, json.JSONDecodeError, CartError) as exc:
        print(f"Error applying cart: {exc}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(cart_to_json(applied), encoding="utf-8")
    if args.json or args.output:
        if not args.output:
            sys.stdout.write(cart_to_json(applied))
    else:
        sys.stdout.write(cart_to_json(applied))
    return 0


def _write_artifact(proposal: dict, args) -> int:
    markdown = render_markdown(proposal)
    artifact = cart_to_json(proposal)
    if getattr(args, "output", None):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(artifact, encoding="utf-8")
    if getattr(args, "markdown_out", None):
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")
    if getattr(args, "json", False) or getattr(args, "output", None):
        if not getattr(args, "output", None):
            sys.stdout.write(artifact)
    else:
        sys.stdout.write(markdown if markdown.endswith("\n") else markdown + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
