#!/usr/bin/env python3
"""Resolve ingredient requirements to compact grocery product picks.

Read-only. This module never talks to a retailer and never mutates a cart.
The grocery-search subagent (or a test fixture) supplies candidate JSON;
learned mappings live in the private workspace ``shopping/product-mappings.md``.

Usage:
    python scripts/product_resolve.py lookup "soy sauce"
    python scripts/product_resolve.py resolve requirements.json
    python scripts/product_resolve.py resolve requirements.json --candidates hits.json
    python scripts/product_resolve.py rank --needed "2 tbsp soy sauce" hits.json
    python scripts/product_resolve.py remember --ingredient "soy sauce" \\
        --brand "Example Brand" --name "soy sauce" --size "500 ml" --id EXAMPLE-SOY-500
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from ingredients import (
    IngredientQty,
    categorize_ingredient,
    convert_amount,
    format_amount,
    normalize_name,
    parse_ingredient_qty,
)
from nutrition_estimate import parse_number
from recipe_finder import (
    META_LINE,
    parse_list_value,
    parse_preferences,
    restriction_hits,
)
from workspace import (
    WorkspaceNotFoundError,
    find_workspace_root,
    path_is_inside_toolkit,
    toolkit_root,
    workspace_paths,
)

MAPPINGS_FILENAME = "product-mappings.md"

# Prefer a known mapping unless another option is clearly better.
UNIT_PRICE_BETTER_RATIO = 0.75
SUBSTANTIAL_PERISHABLE = 2.5
SUBSTANTIAL_SHELF = 6.0
NOTE_PERISHABLE = 1.75
NOTE_SHELF = 3.0
DEFAULT_ALT_LIMIT = 2

PERISHABLE_CATEGORIES = frozenset({"produce", "proteins", "dairy"})
COUNT_UNITS = frozenset({None, "", "each", "ea", "ct", "count", "pk", "pack"})
OPTIONAL_HINTS = ("optional", "garnish", "for serving", "to taste")

FALSEY = frozenset(
    {
        "0",
        "false",
        "no",
        "n",
        "out of stock",
        "sold out",
        "unavailable",
        "not available",
        "not shoppable",
    }
)
ID_SUFFIXES = ("_EA", "_KG", "_ea", "_kg")

MAPPING_LINE = re.compile(
    r"^[-*]\s+(?P<ingredient>.+?)\s+[—–-]\s+(?P<body>.+?)\s*$"
)
PRODUCT_ID = re.compile(
    r"[(`'\[](?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)[)`'\]]"
)
SIZE_TOKEN = re.compile(
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>"
    r"ml|l|liters?|litres?|g|grams?|kg|oz|ounces?|lb|lbs|pounds?|"
    r"tsp|tbsp|cups?|cans?|ct|count|each|ea|pk|pack)\b",
    re.IGNORECASE,
)
PRICE_JUNK = re.compile(r"[^0-9.+-]")


class ProductResolveError(ValueError):
    """Invalid mappings, requirements, or candidate input."""


@dataclass(frozen=True)
class ProductMapping:
    ingredient: str
    brand: str
    name: str
    size: str
    product_id: str | None
    notes: str
    raw: str = ""

    @property
    def label(self) -> str:
        parts = [part for part in (self.brand, self.name) if part]
        return " ".join(parts) or self.ingredient


@dataclass
class ProductCandidate:
    product_id: str | None
    brand: str
    name: str
    package_size: str
    price: float | None
    unit_price: float | None
    on_sale: bool
    available: bool
    notes: str = ""

    @property
    def label(self) -> str:
        parts = [part for part in (self.brand, self.name) if part]
        return " ".join(parts) or (self.product_id or "unknown product")


@dataclass
class ExcessFlag:
    severity: str
    needed_display: str
    package_display: str
    packages: int
    excess_amount: float | None
    excess_unit: str | None
    ratio: float | None
    perishable: bool
    carries_over: bool
    skip_ok: bool
    summary: str


@dataclass
class RankedCandidate:
    candidate: ProductCandidate
    score: float
    reasons: list[str] = field(default_factory=list)
    excess: ExcessFlag | None = None
    diet_hits: list[str] = field(default_factory=list)
    mapping_match: bool = False


@dataclass
class Resolution:
    ingredient: str
    needed: str
    category: str
    mapping: ProductMapping | None
    pick: ProductCandidate | None
    alternatives: list[ProductCandidate]
    reason: str
    source: str
    probe: str
    needs_search: bool
    excess: ExcessFlag | None
    prices_live: bool


def mappings_path(root: Path | None = None) -> Path:
    """Workspace ``shopping/product-mappings.md`` (never the toolkit copy)."""
    paths = workspace_paths(root)
    return paths["shopping"] / MAPPINGS_FILENAME


def parse_product_id(text: str | None) -> str | None:
    if not text:
        return None
    match = PRODUCT_ID.search(str(text).strip())
    if match:
        return match.group("id")
    stripped = str(text).strip().strip("`").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", stripped):
        return stripped
    return None


def normalize_product_id(product_id: str | None) -> str:
    """Compare IDs without retailer pack-vs-weight suffixes."""
    if not product_id:
        return ""
    value = product_id.strip()
    for suffix in ID_SUFFIXES:
        if value.endswith(suffix):
            return value[: -len(suffix)].lower()
    return value.lower()


def parse_mapping_line(line: str) -> ProductMapping | None:
    """Parse one ``ingredient — Brand name, size (`id`)`` line."""
    raw = line.strip()
    if not raw or raw in {"-", "*", "- _", "- *"}:
        return None
    match = MAPPING_LINE.match(raw)
    if not match:
        return None
    ingredient = normalize_name(match.group("ingredient"))
    if not ingredient:
        return None
    body = match.group("body").strip()
    product_id = parse_product_id(body)
    if product_id:
        body = PRODUCT_ID.sub("", body, count=1).strip()
    notes = ""
    for sep in (";", " — ", " – "):
        if sep in body:
            body, notes = body.split(sep, 1)
            body = body.strip()
            notes = notes.strip()
            break
    size = ""
    size_match = None
    for size_match in SIZE_TOKEN.finditer(body):
        pass
    if size_match:
        amount = size_match.group("amount").replace(",", ".")
        size = f"{amount} {size_match.group('unit').lower()}"
        label = (body[: size_match.start()] + body[size_match.end() :]).strip()
    else:
        label = body
    label = label.strip(" ,-—")
    brand, name = _split_brand_name(label, ingredient)
    if not ingredient:
        return None
    return ProductMapping(
        ingredient=ingredient,
        brand=brand,
        name=name or ingredient,
        size=size,
        product_id=product_id,
        notes=notes,
        raw=raw,
    )


def parse_mappings(text: str) -> list[ProductMapping]:
    """Load learned mappings. Blank placeholder lines are ignored."""
    mappings: list[ProductMapping] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        item = parse_mapping_line(raw)
        if item is None or item.ingredient in seen:
            continue
        seen.add(item.ingredient)
        mappings.append(item)
    return mappings


def load_mappings(path: Path | None) -> list[ProductMapping]:
    if path is None or not Path(path).is_file():
        return []
    return parse_mappings(Path(path).read_text(encoding="utf-8"))


def lookup_mapping(
    mappings: list[ProductMapping],
    ingredient: str,
) -> ProductMapping | None:
    """Return the mapping for a normalized ingredient name, if any."""
    wanted = normalize_name(ingredient) or ingredient.strip().lower()
    if not wanted:
        return None
    for item in mappings:
        if item.ingredient == wanted:
            return item
    for item in mappings:
        if item.ingredient in wanted or wanted in item.ingredient:
            if len(item.ingredient) < 3 or len(wanted) < 3:
                continue
            return item
    return None


def format_mapping_line(mapping: ProductMapping) -> str:
    """Canonical durable line. Omits backticks when there is no product id."""
    label = mapping.label
    size = mapping.size.strip()
    body = f"{label}, {size}" if size else label
    if mapping.product_id:
        body = f"{body} (`{mapping.product_id}`)"
    if mapping.notes:
        body = f"{body}; {mapping.notes}"
    return f"- {mapping.ingredient} — {body}"


def upsert_mapping(text: str, mapping: ProductMapping) -> str:
    """Replace or append one ingredient mapping. Does not invent product ids."""
    if mapping.product_id and not parse_product_id(mapping.product_id):
        raise ProductResolveError(
            f"refusing to persist an invalid product id: {mapping.product_id!r}"
        )
    ingredient = normalize_name(mapping.ingredient) or mapping.ingredient.strip().lower()
    if not ingredient:
        raise ProductResolveError("mapping ingredient is required")
    mapping = replace(mapping, ingredient=ingredient)
    line = format_mapping_line(mapping)
    existing = text.replace("\r\n", "\n") if text else ""
    if not existing.strip():
        existing = _blank_mappings_text()
    lines = existing.splitlines()
    replaced = False
    out: list[str] = []
    placeholder_only = False
    for raw in lines:
        parsed = parse_mapping_line(raw)
        if parsed and parsed.ingredient == ingredient:
            if not replaced:
                out.append(line)
                replaced = True
            continue
        stripped = raw.strip()
        if stripped in {"-", "*", "- _"} and not replaced:
            placeholder_only = True
            continue
        out.append(raw)
    if not replaced:
        if out and out[-1].strip():
            if placeholder_only or not any(parse_mapping_line(item) for item in out):
                out.append("")
        out.append(line)
    rendered = "\n".join(out)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def write_mappings(path: Path, text: str) -> None:
    """Write mappings only into the private workspace, never this toolkit."""
    path = Path(path).expanduser().resolve()
    if path_is_inside_toolkit(path):
        raise ProductResolveError(
            "Refusing to write product mappings into the toolkit package. "
            "Learned preferences belong in the private workspace "
            f"shopping/{MAPPINGS_FILENAME}."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remember_mapping(
    path: Path,
    *,
    ingredient: str,
    brand: str = "",
    name: str = "",
    size: str = "",
    product_id: str | None = None,
    notes: str = "",
) -> ProductMapping:
    """Persist one confirmed mapping. Product ids must be provided, not invented."""
    mapping = ProductMapping(
        ingredient=ingredient,
        brand=brand.strip(),
        name=(name or ingredient).strip(),
        size=size.strip(),
        product_id=parse_product_id(product_id) if product_id else None,
        notes=notes.strip(),
    )
    current = ""
    if Path(path).is_file():
        current = Path(path).read_text(encoding="utf-8")
    write_mappings(path, upsert_mapping(current, mapping))
    return mapping


def parse_shopping_preferences(text: str) -> dict:
    """Diet/dislikes plus optional brand and deal hints from preferences.md."""
    prefs = parse_preferences(text)
    prefs.setdefault("preferred_brands", [])
    prefs.setdefault("prefer_deals", True)
    prefs.setdefault("banner", "")
    for raw in text.splitlines():
        match = META_LINE.match(raw.strip())
        if not match:
            continue
        label = match.group(1).strip().lower()
        value = match.group(2).strip()
        if "preferred brand" in label:
            prefs["preferred_brands"] = parse_list_value(value)
        elif any(key in label for key in ("budget", "deal", "sale", "price")):
            lowered = value.lower()
            if any(token in lowered for token in ("not", "ignore", "no ", "don't")):
                prefs["prefer_deals"] = False
        elif "banner" in label or label.endswith("store") or "banner / store" in label:
            prefs["banner"] = value
    return prefs


def load_shopping_preferences(path: Path | None) -> dict:
    if path is None or not Path(path).is_file():
        return parse_shopping_preferences("")
    return parse_shopping_preferences(Path(path).read_text(encoding="utf-8"))


def parse_package_qty(size: str | None) -> IngredientQty | None:
    """Read a package size such as ``500 ml`` or ``1.89 L``."""
    if not size or not str(size).strip():
        return None
    text = str(size).strip()
    match = None
    for match in SIZE_TOKEN.finditer(text):
        pass
    if not match:
        qty = parse_ingredient_qty(text)
        if qty.amount is None:
            return None
        return qty
    amount = parse_number(match.group("amount").replace(",", "."))
    if amount is None:
        return None
    unit = match.group("unit").lower()
    if unit in {"l", "liter", "liters", "litre", "litres"}:
        unit = "l"
    elif unit in {"g", "gram", "grams"}:
        unit = "g"
    elif unit in {"each", "ea", "ct", "count", "pk", "pack"}:
        unit = "each"
    qty = parse_ingredient_qty(f"{amount} {unit}")
    return replace(qty, amount=amount, unit=qty.unit or unit)


def packages_needed(
    needed: IngredientQty,
    package: IngredientQty,
) -> tuple[int, float | None, str | None] | None:
    """How many packages cover *needed*. None when units cannot be compared."""
    if needed.amount is None or package.amount is None or package.amount <= 0:
        return None
    converted = _compatible_amount(needed.amount, needed.unit, package.unit)
    if converted is None:
        return None
    count = max(1, math.ceil((converted - 1e-9) / package.amount))
    total = count * package.amount
    excess = max(0.0, total - converted)
    return count, excess, package.unit


def classify_excess(
    needed: IngredientQty | None,
    package_size: str,
    *,
    category: str = "other",
    notes: str = "",
) -> ExcessFlag:
    """Flag substantial oversupply. Never treat a huge pack as a silent win."""
    needed_display = _needed_display(needed)
    package_display = package_size.strip() or "unknown size"
    perishable = category in PERISHABLE_CATEGORIES
    skip_ok = _is_optional(needed, notes)

    def _with_skip(summary: str) -> str:
        if skip_ok and "skip" not in summary.lower():
            return summary + "; optional/garnish — consider skipping"
        return summary

    if needed is None or needed.amount is None:
        return ExcessFlag(
            severity="unknown",
            needed_display=needed_display,
            package_display=package_display,
            packages=1,
            excess_amount=None,
            excess_unit=None,
            ratio=None,
            perishable=perishable,
            carries_over=not perishable,
            skip_ok=skip_ok,
            summary=_with_skip("needed quantity is not comparable to the package size"),
        )
    package = parse_package_qty(package_size)
    if package is None or package.amount is None:
        return ExcessFlag(
            severity="unknown",
            needed_display=needed_display,
            package_display=package_display,
            packages=1,
            excess_amount=None,
            excess_unit=None,
            ratio=None,
            perishable=perishable,
            carries_over=not perishable,
            skip_ok=skip_ok,
            summary=_with_skip("package size is not comparable to the needed quantity"),
        )
    compared = packages_needed(needed, package)
    if compared is None:
        return ExcessFlag(
            severity="unknown",
            needed_display=needed_display,
            package_display=package_display,
            packages=1,
            excess_amount=None,
            excess_unit=None,
            ratio=None,
            perishable=perishable,
            carries_over=not perishable,
            skip_ok=skip_ok,
            summary=_with_skip(
                "units cannot be compared (flag manually if the pack looks huge)"
            ),
        )
    count, excess_amount, excess_unit = compared
    needed_in_pkg = _compatible_amount(needed.amount, needed.unit, package.unit)
    total = count * (package.amount or 0)
    ratio = (total / needed_in_pkg) if needed_in_pkg else None
    substantial = SUBSTANTIAL_PERISHABLE if perishable else SUBSTANTIAL_SHELF
    note_at = NOTE_PERISHABLE if perishable else NOTE_SHELF
    if ratio is not None and ratio >= substantial:
        severity = "substantial"
    elif ratio is not None and ratio >= note_at:
        severity = "note"
    else:
        severity = "none"
    if skip_ok and severity != "none":
        severity = "substantial" if severity == "substantial" else "note"
    leftover = "perishable waste risk" if perishable else "shelf-stable leftover carries over"
    if severity == "none":
        summary = f"{package_display} covers {needed_display}"
        if count > 1:
            summary = f"{count} × {package_display} covers {needed_display}"
    else:
        excess_text = (
            f"{format_amount(excess_amount, excess_unit)} {excess_unit}".strip()
            if excess_amount is not None and excess_unit
            else "extra"
        )
        summary = (
            f"{needed_display} needed; smallest pack {package_display} "
            f"leaves {excess_text} ({leftover})"
        )
        if skip_ok:
            summary += "; optional/garnish — consider skipping"
    return ExcessFlag(
        severity=severity,
        needed_display=needed_display,
        package_display=package_display,
        packages=count,
        excess_amount=excess_amount,
        excess_unit=excess_unit,
        ratio=ratio,
        perishable=perishable,
        carries_over=not perishable,
        skip_ok=skip_ok,
        summary=summary,
    )


def mapping_still_usable(
    mapping: ProductMapping,
    candidate: ProductCandidate,
    needed: IngredientQty | None,
    category: str,
    prefs: dict | None = None,
) -> bool:
    """True when a learned product is still a reasonable buy."""
    if not candidate_matches_mapping(candidate, mapping):
        return False
    if not candidate.available:
        return False
    if _candidate_diet_hits(candidate, prefs or {}):
        return False
    if needed is None:
        return True
    excess = classify_excess(
        needed,
        candidate.package_size or mapping.size,
        category=category,
        notes=needed.notes,
    )
    if excess.severity == "substantial" and excess.perishable:
        return False
    return True


def candidate_matches_mapping(
    candidate: ProductCandidate,
    mapping: ProductMapping,
) -> bool:
    mapped_id = normalize_product_id(mapping.product_id)
    cand_id = normalize_product_id(candidate.product_id)
    if mapped_id and cand_id and mapped_id == cand_id:
        return True
    if mapping.brand and _brand_match(candidate, mapping.brand):
        if not mapping.size or _sizes_compatible(mapping.size, candidate.package_size):
            return True
    return False


def is_materially_better(
    challenger: RankedCandidate,
    incumbent: RankedCandidate,
) -> bool:
    """Switch away from a known product only for a clear improvement."""
    if incumbent.candidate.available is False and challenger.candidate.available:
        return True
    if incumbent.diet_hits and not challenger.diet_hits:
        return True
    inc_ex = incumbent.excess
    ch_ex = challenger.excess
    if (
        inc_ex
        and ch_ex
        and inc_ex.severity == "substantial"
        and ch_ex.severity != "substantial"
    ):
        return True
    inc_price = incumbent.candidate.unit_price
    ch_price = challenger.candidate.unit_price
    if inc_price and ch_price and ch_price <= inc_price * UNIT_PRICE_BETTER_RATIO:
        # A cheaper warehouse jug is not a reason to drop a learned staple size.
        ch_rank = _excess_rank(ch_ex)
        inc_rank = _excess_rank(inc_ex)
        if ch_rank < inc_rank:
            return True
        if ch_rank <= 1 and inc_rank <= 1:
            return True
    return False


def normalize_candidate(raw: dict) -> ProductCandidate:
    """Accept a few provider-ish key aliases. Tests pass synthetic dicts."""
    if not isinstance(raw, dict):
        raise ProductResolveError("each candidate must be an object")
    product_id = (
        raw.get("id")
        or raw.get("product_id")
        or raw.get("code")
        or raw.get("lid")
    )
    product_id = str(product_id).strip() if product_id else None
    if product_id in {"", "n/a", "N/A", "none"}:
        product_id = None
    brand = str(raw.get("brand") or "").strip()
    name = str(raw.get("name") or raw.get("title") or "").strip()
    size = str(
        raw.get("size") or raw.get("package_size") or raw.get("package") or ""
    ).strip()
    price = parse_price(raw.get("price") if raw.get("price") is not None else raw.get("regular_price"))
    sale_price = parse_price(raw.get("sale_price"))
    unit_price = parse_price(raw.get("unit_price") or raw.get("price_per_unit"))
    on_sale = _as_bool(raw.get("on_sale") if "on_sale" in raw else raw.get("is_sale"))
    if sale_price is not None and price is not None and sale_price < price:
        on_sale = True
        price = sale_price
    if str(raw.get("sale") or "").upper() == "SALE":
        on_sale = True
    available = True
    for key in ("available", "in_stock", "shoppable"):
        if key in raw:
            available = _as_bool(raw.get(key), default=True)
            break
    notes = str(raw.get("notes") or "").strip()
    candidate = ProductCandidate(
        product_id=product_id,
        brand=brand,
        name=name,
        package_size=size,
        price=price,
        unit_price=unit_price,
        on_sale=on_sale,
        available=available,
        notes=notes,
    )
    if candidate.unit_price is None:
        candidate.unit_price = infer_unit_price(candidate)
    return candidate


def infer_unit_price(candidate: ProductCandidate) -> float | None:
    if candidate.price is None:
        return None
    package = parse_package_qty(candidate.package_size)
    if package is None or package.amount is None or package.amount <= 0:
        return None
    if package.unit in {"ml", "l"}:
        ml = convert_amount(package.amount, package.unit, "ml")
        if not ml:
            return None
        return candidate.price / (ml / 100.0)
    if package.unit in {"g", "kg", "oz", "lb"}:
        grams = convert_amount(package.amount, package.unit, "g")
        if not grams:
            return None
        return candidate.price / (grams / 100.0)
    return candidate.price / package.amount


def parse_price(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = PRICE_JUNK.sub("", str(value).replace(",", ""))
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_json_payload(path: Path | str) -> object:
    if str(path) == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_requirements(payload: object) -> list[dict]:
    """Normalize plan rows, aggregate dicts, or ingredient lines."""
    if isinstance(payload, dict):
        rows = payload.get("ingredients") or payload.get("requirements") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ProductResolveError("requirements must be a list or a plan object")
    return [normalize_requirement(row) for row in rows]


def normalize_requirement(row: dict | str) -> dict:
    if isinstance(row, str):
        qty = parse_ingredient_qty(row)
        return {
            "name": qty.name or row.strip(),
            "amount": qty.amount,
            "unit": qty.unit,
            "unit_size": qty.unit_size,
            "display": _needed_display(qty),
            "category": categorize_ingredient(qty),
            "notes": qty.notes,
            "line": row,
            "_qty": qty,
        }
    if not isinstance(row, dict):
        raise ProductResolveError("each requirement must be a string or object")
    name = str(row.get("name") or row.get("ingredient") or "").strip()
    line = str(row.get("line") or row.get("original") or "").strip()
    display = str(row.get("display") or row.get("quantity") or "").strip()
    notes = str(row.get("notes") or "").strip()
    if line:
        qty = parse_ingredient_qty(line)
    elif display and name:
        qty = parse_ingredient_qty(f"{display} {name}")
    elif name:
        qty = parse_ingredient_qty(name)
    else:
        raise ProductResolveError("requirement is missing an ingredient name")
    amount = row.get("amount", qty.amount)
    unit = row.get("unit", qty.unit)
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise ProductResolveError(f"invalid amount for {name!r}") from exc
    rebuilt = IngredientQty(
        amount=amount,
        unit=unit if unit else qty.unit,
        unit_size=row.get("unit_size", qty.unit_size),
        name=normalize_name(name or qty.name) or name or qty.name,
        notes=notes or qty.notes,
        original=line or display or name,
        scalable=amount is not None,
    )
    return {
        "name": rebuilt.name,
        "amount": rebuilt.amount,
        "unit": rebuilt.unit,
        "unit_size": rebuilt.unit_size,
        "display": display or _needed_display(rebuilt),
        "category": row.get("category") or categorize_ingredient(rebuilt),
        "notes": rebuilt.notes,
        "line": line or display or rebuilt.name,
        "used_in": list(row.get("used_in") or []),
        "_qty": rebuilt,
    }


def load_candidate_map(payload: object) -> dict[str, list[ProductCandidate]]:
    """Group mocked/search hits by normalized ingredient."""
    grouped: dict[str, list[ProductCandidate]] = {}
    if payload is None:
        return grouped
    if isinstance(payload, dict) and "candidates" not in payload:
        items = payload.items()
        for key, rows in items:
            if key in {"ingredients", "requirements"}:
                continue
            grouped[normalize_name(str(key)) or str(key).lower()] = [
                normalize_candidate(row) for row in (rows or [])
            ]
        return grouped
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ProductResolveError("candidates must be a list or ingredient map")
    for row in rows:
        if not isinstance(row, dict):
            raise ProductResolveError("each candidate row must be an object")
        ingredient = row.get("ingredient") or row.get("name")
        nested = row.get("candidates")
        if nested is not None:
            key = normalize_name(str(ingredient or "")) or str(ingredient or "").lower()
            grouped.setdefault(key, []).extend(
                normalize_candidate(item) for item in nested
            )
            continue
        key = normalize_name(str(ingredient or "")) or str(ingredient or "").lower()
        grouped.setdefault(key, []).append(normalize_candidate(row))
    return grouped


def rank_candidates(
    candidates: list[dict | ProductCandidate],
    needed: IngredientQty | str,
    *,
    mapping: ProductMapping | None = None,
    prefs: dict | None = None,
    category: str | None = None,
    limit: int = DEFAULT_ALT_LIMIT + 1,
) -> list[RankedCandidate]:
    """Score mocked provider results. Unavailable items stay last."""
    prefs = prefs or {}
    qty = needed if isinstance(needed, IngredientQty) else parse_ingredient_qty(str(needed))
    category = category or categorize_ingredient(qty)
    parsed = [
        item if isinstance(item, ProductCandidate) else normalize_candidate(item)
        for item in candidates
    ]
    priced = [infer_unit_price(item) or item.unit_price for item in parsed]
    finite_prices = [price for price in priced if price]
    cheapest = min(finite_prices) if finite_prices else None
    priciest = max(finite_prices) if finite_prices else None
    ranked: list[RankedCandidate] = []
    for candidate in parsed:
        unit_price = candidate.unit_price or infer_unit_price(candidate)
        if unit_price is not None:
            candidate.unit_price = unit_price
        excess = classify_excess(
            qty,
            candidate.package_size,
            category=category,
            notes=qty.notes,
        )
        diet_hits = _candidate_diet_hits(candidate, prefs)
        reasons: list[str] = []
        score = 0.0
        mapping_match = bool(mapping and candidate_matches_mapping(candidate, mapping))
        if not candidate.available:
            score -= 1000
            reasons.append("unavailable")
        if diet_hits:
            score -= 400
            reasons.append("diet: " + ", ".join(diet_hits))
        if mapping_match:
            score += 80
            reasons.append("learned mapping")
        elif mapping and mapping.brand and _brand_match(candidate, mapping.brand):
            score += 25
            reasons.append(f"usual brand {mapping.brand}")
        for brand in prefs.get("preferred_brands") or []:
            if _brand_match(candidate, brand):
                score += 15
                reasons.append(f"preferred brand {brand}")
                break
        if candidate.on_sale and prefs.get("prefer_deals", True):
            score += 8
            reasons.append("on sale")
        if unit_price and cheapest and priciest:
            if priciest > cheapest:
                score += 20 * (1.0 - (unit_price - cheapest) / (priciest - cheapest))
            else:
                score += 20
            reasons.append(f"unit price {unit_price:.2f}")
        score += _size_fit_score(excess, category)
        if excess.severity == "substantial":
            reasons.append("substantial excess")
        elif excess.severity == "note":
            reasons.append("larger than needed")
        elif excess.severity == "none" and qty.amount is not None:
            reasons.append("size fits")
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                score=score,
                reasons=reasons,
                excess=excess,
                diet_hits=diet_hits,
                mapping_match=mapping_match,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.candidate.label.lower()))
    return ranked[:limit] if limit else ranked


def resolve_requirement(
    requirement: dict | str,
    mappings: list[ProductMapping],
    candidates: list[dict | ProductCandidate] | None = None,
    prefs: dict | None = None,
) -> Resolution:
    """Pick a product for one ingredient. Search results are optional."""
    prefs = prefs or {}
    row = normalize_requirement(requirement)
    qty: IngredientQty = row["_qty"]
    mapping = lookup_mapping(mappings, row["name"])
    parsed = [
        item if isinstance(item, ProductCandidate) else normalize_candidate(item)
        for item in (candidates or [])
    ]
    if not parsed:
        return _resolve_without_candidates(row, qty, mapping)
    ranked = rank_candidates(
        parsed,
        qty,
        mapping=mapping,
        prefs=prefs,
        category=row["category"],
        limit=0,
    )
    pick_ranked = _choose_pick(ranked, mapping, qty, row["category"], prefs)
    alts = [
        item.candidate
        for item in ranked
        if pick_ranked is None or item.candidate is not pick_ranked.candidate
    ][:DEFAULT_ALT_LIMIT]
    if pick_ranked is None:
        return Resolution(
            ingredient=row["name"],
            needed=row["display"],
            category=row["category"],
            mapping=mapping,
            pick=None,
            alternatives=alts,
            reason="no suitable product in the candidate set",
            source="search" if mapping is None else "mapping-fallback-search",
            probe="skip",
            needs_search=False,
            excess=None,
            prices_live=True,
        )
    source = "search"
    if mapping and pick_ranked.mapping_match:
        source = "mapping"
    elif mapping:
        source = "mapping-fallback-search"
    return Resolution(
        ingredient=row["name"],
        needed=row["display"],
        category=row["category"],
        mapping=mapping,
        pick=pick_ranked.candidate,
        alternatives=alts,
        reason=_reason_line(pick_ranked, source),
        source=source,
        probe="skip",
        needs_search=False,
        excess=pick_ranked.excess,
        prices_live=True,
    )


def resolve_requirements(
    requirements: list[dict | str],
    mappings: list[ProductMapping],
    candidate_map: dict[str, list[ProductCandidate]] | None = None,
    prefs: dict | None = None,
) -> list[Resolution]:
    candidate_map = candidate_map or {}
    resolved: list[Resolution] = []
    for row in requirements:
        item = normalize_requirement(row)
        hits = _candidates_for(item["name"], candidate_map)
        resolved.append(resolve_requirement(item, mappings, hits, prefs))
    return resolved


def resolution_to_dict(item: Resolution) -> dict:
    payload = {
        "ingredient": item.ingredient,
        "needed": item.needed,
        "category": item.category,
        "mapping": _mapping_dict(item.mapping),
        "pick": _candidate_dict(item.pick),
        "alternatives": [_candidate_dict(alt) for alt in item.alternatives],
        "reason": item.reason,
        "source": item.source,
        "probe": item.probe,
        "needs_search": item.needs_search,
        "excess": asdict(item.excess) if item.excess else None,
        "prices_live": item.prices_live,
    }
    return payload


def render_resolution(item: Resolution) -> str:
    """Compact grocery-search block. Raw catalog rows stay out of this."""
    pick = item.pick
    lines = [f"### {item.ingredient} ({item.needed})"]
    lines.append(f"- PICK: {_format_pick_line(pick, item.prices_live)}")
    for alt in item.alternatives:
        lines.append(f"- ALT: {_format_pick_line(alt, item.prices_live)}")
    lines.append(f"- Why: {item.reason}")
    if item.excess and item.excess.severity in {"note", "substantial", "unknown"}:
        label = "Excess" if item.excess.severity != "unknown" else "Size"
        lines.append(f"- {label}: {item.excess.summary}")
    return "\n".join(lines)


def render_resolutions(items: list[Resolution]) -> str:
    blocks = [render_resolution(item) for item in items]
    total = _estimated_total(items)
    blocks.append(f"Estimated total (PICKs): {total}")
    return "\n\n".join(blocks) + "\n"


def _resolve_without_candidates(
    row: dict,
    qty: IngredientQty,
    mapping: ProductMapping | None,
) -> Resolution:
    if mapping is None:
        return Resolution(
            ingredient=row["name"],
            needed=row["display"],
            category=row["category"],
            mapping=None,
            pick=None,
            alternatives=[],
            reason="no learned mapping; grocery search needed",
            source="unresolved",
            probe="search",
            needs_search=True,
            excess=None,
            prices_live=False,
        )
    hint = ProductCandidate(
        product_id=mapping.product_id,
        brand=mapping.brand,
        name=mapping.name,
        package_size=mapping.size,
        price=None,
        unit_price=None,
        on_sale=False,
        available=True,
        notes=mapping.notes,
    )
    excess = classify_excess(
        qty,
        mapping.size,
        category=row["category"],
        notes=qty.notes,
    )
    probe = "details" if mapping.product_id else "skip"
    reason = "learned mapping"
    if not mapping.product_id:
        reason = "learned brand/size hint; prices not live-checked"
    elif excess.severity == "substantial" and excess.perishable:
        probe = "search"
        reason = "learned product size looks far too large; search for a smaller pack"
    return Resolution(
        ingredient=row["name"],
        needed=row["display"],
        category=row["category"],
        mapping=mapping,
        pick=hint,
        alternatives=[],
        reason=reason,
        source="mapping",
        probe=probe,
        needs_search=probe == "search",
        excess=excess if excess.severity != "none" else None,
        prices_live=False,
    )


def _choose_pick(
    ranked: list[RankedCandidate],
    mapping: ProductMapping | None,
    qty: IngredientQty,
    category: str,
    prefs: dict,
) -> RankedCandidate | None:
    usable = [
        item
        for item in ranked
        if item.candidate.available and not item.diet_hits
    ]
    if not usable:
        return None
    mapped = next((item for item in usable if item.mapping_match), None)
    if mapped and mapping_still_usable(
        mapping, mapped.candidate, qty, category, prefs
    ) if mapping else False:
        better = next((item for item in usable if item is not mapped and is_materially_better(item, mapped)), None)
        return better or mapped
    return usable[0]


def _candidates_for(
    name: str,
    candidate_map: dict[str, list[ProductCandidate]],
) -> list[ProductCandidate]:
    key = normalize_name(name) or name.lower()
    if key in candidate_map:
        return candidate_map[key]
    for stored, rows in candidate_map.items():
        if stored == key or stored in key or key in stored:
            return rows
    return []


def _candidate_diet_hits(candidate: ProductCandidate, prefs: dict) -> list[str]:
    blob = {
        "title": candidate.label,
        "name": candidate.name,
        "tags": [candidate.brand] if candidate.brand else [],
        "ingredients": [candidate.label],
    }
    return restriction_hits(blob, prefs)


def _brand_match(candidate: ProductCandidate, brand: str) -> bool:
    needle = brand.strip().lower()
    if not needle:
        return False
    hay = f"{candidate.brand} {candidate.name}".lower()
    return needle in hay


def _sizes_compatible(left: str, right: str) -> bool:
    qty_left = parse_package_qty(left)
    qty_right = parse_package_qty(right)
    if qty_left is None or qty_right is None:
        return left.strip().lower() == right.strip().lower()
    if qty_left.amount is None or qty_right.amount is None:
        return False
    converted = _compatible_amount(qty_left.amount, qty_left.unit, qty_right.unit)
    if converted is None or qty_right.amount <= 0:
        return False
    return abs(converted - qty_right.amount) / qty_right.amount <= 0.15


def _compatible_amount(
    amount: float,
    from_unit: str | None,
    to_unit: str | None,
) -> float | None:
    if from_unit in COUNT_UNITS and to_unit in COUNT_UNITS:
        return amount
    return convert_amount(amount, from_unit, to_unit)


def _size_fit_score(excess: ExcessFlag, category: str) -> float:
    if excess.ratio is None:
        return 8.0
    if excess.severity == "none":
        return 22.0 if excess.packages == 1 else max(8.0, 16.0 - (excess.packages - 1) * 2)
    if excess.severity == "note":
        return 10.0 if category in PERISHABLE_CATEGORIES else 14.0
    if excess.perishable:
        return 0.0
    return 8.0


def _excess_rank(flag: ExcessFlag | None) -> int:
    order = {"none": 0, "note": 1, "unknown": 2, "substantial": 3}
    if flag is None:
        return 2
    return order.get(flag.severity, 2)


def _is_optional(needed: IngredientQty | None, notes: str) -> bool:
    blob = " ".join(
        part
        for part in (
            notes,
            needed.notes if needed else "",
            needed.original if needed else "",
        )
        if part
    ).lower()
    return any(hint in blob for hint in OPTIONAL_HINTS)


def _needed_display(qty: IngredientQty | None) -> str:
    if qty is None:
        return "as needed"
    if qty.amount is None:
        return qty.original or qty.name or "as needed"
    amount = format_amount(qty.amount, qty.unit)
    if qty.unit:
        return f"{amount} {qty.unit}"
    return amount


def _split_brand_name(label: str, ingredient: str) -> tuple[str, str]:
    text = label.strip()
    if not text:
        return "", ingredient
    lowered = text.lower()
    ingredient_l = ingredient.lower()
    if ingredient_l and ingredient_l in lowered:
        idx = lowered.index(ingredient_l)
        brand = text[:idx].strip(" ,-")
        name = text[idx:].strip(" ,-")
        return brand, name or ingredient
    tokens = text.split()
    if len(tokens) >= 2:
        return tokens[0], " ".join(tokens[1:])
    return "", text


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in FALSEY


def _mapping_dict(mapping: ProductMapping | None) -> dict | None:
    if mapping is None:
        return None
    return {
        "ingredient": mapping.ingredient,
        "brand": mapping.brand,
        "name": mapping.name,
        "size": mapping.size,
        "product_id": mapping.product_id,
        "notes": mapping.notes,
    }


def _candidate_dict(candidate: ProductCandidate | None) -> dict | None:
    if candidate is None:
        return None
    return {
        "id": candidate.product_id or "n/a",
        "brand": candidate.brand,
        "name": candidate.name,
        "size": candidate.package_size,
        "price": candidate.price,
        "unit_price": candidate.unit_price,
        "on_sale": candidate.on_sale,
        "available": candidate.available,
    }


def _format_pick_line(candidate: ProductCandidate | None, prices_live: bool) -> str:
    if candidate is None:
        return 'n/a | n/a | n/a | unknown | unknown'
    ident = candidate.product_id or "n/a"
    price = _format_money(candidate.price) if prices_live else "unknown"
    if prices_live and candidate.on_sale and candidate.price is not None:
        price = f"{price}, SALE"
    unit = _format_money(candidate.unit_price) if prices_live and candidate.unit_price is not None else "unknown"
    return (
        f"{ident} | {candidate.label} | {candidate.package_size or 'n/a'} | "
        f"{price} | {unit}"
    )


def _format_money(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"${value:.2f}"


def _estimated_total(items: list[Resolution]) -> str:
    if any(item.pick and item.pick.price is None for item in items if item.pick):
        return "unknown"
    if not any(item.pick and item.pick.price is not None for item in items):
        return "unknown"
    total = 0.0
    for item in items:
        if item.pick and item.pick.price is not None:
            packs = item.excess.packages if item.excess else 1
            total += item.pick.price * packs
    return f"${total:.2f}"


def _reason_line(ranked: RankedCandidate, source: str) -> str:
    if ranked.reasons:
        return "; ".join(ranked.reasons[:3])
    if source == "mapping":
        return "learned mapping"
    return "best available candidate"


def _blank_mappings_text() -> str:
    template = toolkit_root() / "templates" / MAPPINGS_FILENAME
    if template.is_file():
        return template.read_text(encoding="utf-8")
    return "# Product mappings\n\n## Mappings\n\n"


def _workspace_paths_or_none() -> dict[str, Path] | None:
    try:
        return workspace_paths(find_workspace_root())
    except WorkspaceNotFoundError:
        return None


def _mappings_file(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    paths = _workspace_paths_or_none()
    if not paths:
        return None
    return paths["shopping"] / MAPPINGS_FILENAME


def _preferences_file(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    paths = _workspace_paths_or_none()
    if not paths:
        return None
    return paths["preferences"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve ingredients to compact grocery product picks "
        "(no retailer calls, no cart writes).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lookup = sub.add_parser("lookup", help="Show learned mappings for ingredients")
    lookup.add_argument("ingredients", nargs="+")
    lookup.add_argument("--mappings", type=Path, default=None)

    resolve = sub.add_parser(
        "resolve",
        help="Resolve requirements; candidates are optional mocked/search JSON",
    )
    resolve.add_argument("requirements", type=Path, help="JSON list or plan object; '-' for stdin")
    resolve.add_argument("--candidates", type=Path, default=None)
    resolve.add_argument("--mappings", type=Path, default=None)
    resolve.add_argument("--preferences", type=Path, default=None)
    resolve.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json (default) or compact markdown",
    )

    rank = sub.add_parser("rank", help="Rank mocked candidates for one needed quantity")
    rank.add_argument("candidates", type=Path)
    rank.add_argument("--needed", required=True, help='e.g. "2 tbsp soy sauce"')
    rank.add_argument("--mappings", type=Path, default=None)
    rank.add_argument("--preferences", type=Path, default=None)
    rank.add_argument("--limit", type=int, default=DEFAULT_ALT_LIMIT + 1)

    remember = sub.add_parser(
        "remember",
        help="Write one confirmed mapping to the workspace (not this toolkit)",
    )
    remember.add_argument("--ingredient", required=True)
    remember.add_argument("--brand", default="")
    remember.add_argument("--name", default="")
    remember.add_argument("--size", default="")
    remember.add_argument("--id", dest="product_id", default=None)
    remember.add_argument("--notes", default="")
    remember.add_argument("--mappings", type=Path, default=None)

    args = parser.parse_args(argv)
    mappings_file = _mappings_file(args.mappings)

    if args.command == "lookup":
        mappings = load_mappings(mappings_file)
        payload = []
        for name in args.ingredients:
            item = lookup_mapping(mappings, name)
            requirement = normalize_requirement(name)
            qty = requirement["_qty"]
            resolved = _resolve_without_candidates(requirement, qty, item)
            payload.append(resolution_to_dict(resolved))
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.command == "remember":
        if mappings_file is None:
            print(
                "No workspace mappings file. Pass --mappings or run from a workspace.",
                file=sys.stderr,
            )
            return 1
        try:
            mapping = remember_mapping(
                mappings_file,
                ingredient=args.ingredient,
                brand=args.brand,
                name=args.name,
                size=args.size,
                product_id=args.product_id,
                notes=args.notes,
            )
        except ProductResolveError as exc:
            print(exc, file=sys.stderr)
            return 1
        json.dump(_mapping_dict(mapping), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    prefs = load_shopping_preferences(_preferences_file(args.preferences))
    mappings = load_mappings(mappings_file)

    if args.command == "rank":
        try:
            raw = load_json_payload(args.candidates)
            grouped = load_candidate_map(raw)
            qty = parse_ingredient_qty(args.needed)
            hits = grouped.get(qty.name or "", None)
            if hits is None:
                if isinstance(raw, list) and raw and "ingredient" not in raw[0]:
                    hits = [normalize_candidate(row) for row in raw]
                else:
                    hits = _candidates_for(qty.name or args.needed, grouped)
            mapping = lookup_mapping(mappings, qty.name or args.needed)
            ranked = rank_candidates(
                hits,
                qty,
                mapping=mapping,
                prefs=prefs,
                limit=args.limit,
            )
        except (OSError, ValueError, json.JSONDecodeError, ProductResolveError) as exc:
            print(f"Error ranking candidates: {exc}", file=sys.stderr)
            return 1
        json.dump(
            [
                {
                    "score": item.score,
                    "reasons": item.reasons,
                    "mapping_match": item.mapping_match,
                    "diet_hits": item.diet_hits,
                    "excess": asdict(item.excess) if item.excess else None,
                    "candidate": _candidate_dict(item.candidate),
                }
                for item in ranked
            ],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    try:
        payload = load_json_payload(args.requirements)
        requirements = load_requirements(payload)
        candidate_map = {}
        if args.candidates is not None:
            candidate_map = load_candidate_map(load_json_payload(args.candidates))
        resolved = resolve_requirements(requirements, mappings, candidate_map, prefs)
    except (OSError, ValueError, json.JSONDecodeError, ProductResolveError) as exc:
        print(f"Error resolving products: {exc}", file=sys.stderr)
        return 1
    if args.format == "text":
        sys.stdout.write(render_resolutions(resolved))
        return 0
    json.dump(
        {
            "picks": [resolution_to_dict(item) for item in resolved],
            "needs_search": [item.ingredient for item in resolved if item.needs_search],
            "excess_flags": [
                {
                    "ingredient": item.ingredient,
                    **asdict(item.excess),
                }
                for item in resolved
                if item.excess and item.excess.severity in {"note", "substantial"}
            ],
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
