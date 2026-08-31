#!/usr/bin/env python3
"""Retailer-independent shopping-list handoff.

Planning finishes with normalized ingredient requirements (see meal_plan.py).
This module turns those requirements plus pantry and staples into a shopping
list a human can use, and a grocery provider can consume, without meal-plan
internals or store product IDs.

Usage:
    python scripts/shopping_list.py plan.json
    python scripts/shopping_list.py plan.json --json
    python scripts/shopping_list.py plan.json -o plans/YYYY-MM-DD-shopping.json \\
        --markdown-out plans/YYYY-MM-DD-shopping.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

from ingredients import (
    CATEGORY_ORDER,
    PRODUCT_ID_KEYS,
    QTY_APPROXIMATE,
    QTY_EXACT,
    QTY_UNCERTAIN,
    RETAILER_KEYS,
    ROLE_ESSENTIAL,
    VOLUME_TO_ML,
    IngredientQty,
    aggregate_ingredients,
    apply_ingredient_replacement,
    assert_no_product_ids,
    categorize_ingredient,
    display_quantity,
    infer_role,
    normalize_name,
    parse_ingredient_qty,
    scale_ingredient_line,
    stronger_role,
    convert_amount,
)
from workspace import WorkspaceNotFoundError, find_workspace_root, workspace_paths

SCHEMA_VERSION = 1
KIND = "shopping-list"

PANTRY_BUY = "buy"
PANTRY_ASSUMED = "assumed_in_pantry"
PANTRY_CONFIRM = "needs_confirmation"

ORIGIN_RECIPE = "recipe"
ORIGIN_STAPLE = "staple"

CATEGORY_HEADINGS = {
    "produce": "Produce",
    "proteins": "Proteins",
    "dairy": "Dairy and eggs",
    "pantry": "Pantry",
    "frozen": "Frozen",
    "other": "Other",
}

COOKING_OILS = frozenset(
    {"oil", "olive oil", "vegetable oil", "canola oil", "cooking oil"}
)
SPECIALTY_OILS = frozenset({"sesame oil", "coconut oil"})

SKIP_STOCK_SECTIONS = frozenset(
    {"how to use", "notes", "learned mappings", "mappings"}
)
INSTRUCTIONAL_PREFIXES = (
    "edit this file",
    "tell the agent",
    "preferred store",
    "leave this sparse",
    "add items the household",
    "items listed here",
)

UNCERTAIN_STOCK = re.compile(
    r"\b(when low|when below|if needed|if low|check|maybe|confirm|"
    r"running low|if out)\b",
    re.IGNORECASE,
)
BULLET = re.compile(r"^[-*]\s+(.+)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
DASH_SPLIT = re.compile(r"\s+[—–-]\s+")

FORBIDDEN_KEY_HINTS = (
    "pcexpress",
    "pc_express",
    "product_id",
    "productid",
    "offer_id",
    "offerid",
)


class ShoppingListError(ValueError):
    """Invalid shopping-list input or retailer identifiers in the artifact."""


def parse_stock_markdown(text: str) -> list[dict]:
    """Parse pantry.md or staples.md bullets into synthetic stock rows."""
    items: list[dict] = []
    section = ""
    skip_section = False
    for raw_line in text.splitlines():
        heading = HEADING.match(raw_line)
        if heading:
            section = heading.group(2).strip()
            skip_section = section.lower() in SKIP_STOCK_SECTIONS
            continue
        if skip_section:
            continue
        bullet = BULLET.match(raw_line.strip())
        if not bullet:
            continue
        body = bullet.group(1).strip()
        if not body or body in {"-", "—"}:
            continue
        if body.lower().startswith(INSTRUCTIONAL_PREFIXES):
            continue
        name_part, hint = _split_stock_line(body)
        if not name_part:
            continue
        items.append(
            {
                "name": normalize_name(name_part) or name_part.lower(),
                "label": name_part.strip(),
                "quantity_hint": hint,
                "uncertain": bool(UNCERTAIN_STOCK.search(body)),
                "section": section,
                "original": body,
            }
        )
    return items


def names_match(left: str, right: str) -> bool:
    """True when two pantry/recipe names refer to the same shoppable food."""
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in SPECIALTY_OILS or b in SPECIALTY_OILS:
        return False
    if a in COOKING_OILS and b in COOKING_OILS:
        return True
    return False


def requirements_from_meals(meals: Iterable[dict]) -> list[dict]:
    """Scale recipe lines and aggregate them the same way planning does."""
    contributions: list[dict] = []
    for meal in meals:
        recipe = str(meal.get("recipe") or meal.get("name") or "").strip()
        source_serves = float(meal.get("recipe_serves") or meal.get("source_serves") or 1)
        planned = float(
            meal.get("planned_serves") or meal.get("servings") or source_serves
        )
        deviations = list(meal.get("deviations") or meal.get("modifications") or [])
        for raw in meal.get("ingredients") or []:
            line, role = _unpack_line(raw)
            if not line:
                continue
            if _is_omitted(line, deviations):
                continue
            for old, new in _replacements(deviations).items():
                line = apply_ingredient_replacement(line, old, new)
            scaled = scale_ingredient_line(line, source_serves, planned)
            entry: dict = {"line": scaled, "source": recipe or None}
            if role:
                entry["role"] = role
            contributions.append(entry)
    return aggregate_ingredients(contributions)


def build_shopping_list(
    *,
    plan: dict | None = None,
    meals: list[dict] | None = None,
    pantry: str | list[dict] | None = None,
    staples: str | list[dict] | None = None,
    pantry_out: Iterable[str] = (),
    pantry_confirm: Iterable[str] = (),
    source_plan: str | None = None,
) -> dict:
    """Build the retailer-independent shopping-list artifact."""
    plan = dict(plan or {})
    pantry_rows = _as_stock(pantry)
    staple_rows = _as_stock(staples)
    out_names = [normalize_name(name) or name.lower() for name in pantry_out if name]
    confirm_names = [
        normalize_name(name) or name.lower() for name in pantry_confirm if name
    ]
    confirm_names.extend(
        name for name in (plan.get("pantry_confirm") or []) if name
    )
    out_names.extend(name for name in (plan.get("pantry_out") or []) if name)
    out_names = [normalize_name(name) or name.lower() for name in out_names]
    confirm_names = [normalize_name(name) or name.lower() for name in confirm_names]

    requirements = _collect_requirements(plan, meals)
    deviations = list(plan.get("deviations") or [])
    omitted = _omit_names(deviations)
    requirements = [
        item
        for item in requirements
        if not any(names_match(item.get("name") or "", name) for name in omitted)
    ]

    items = [_requirement_to_item(item, deviations) for item in requirements]
    items = _merge_same_name(items)

    for item in items:
        _apply_pantry(item, pantry_rows, out_names, confirm_names)

    for staple in staple_rows:
        _add_staple(items, staple, pantry_rows, out_names, confirm_names)

    items.sort(
        key=lambda item: (
            CATEGORY_ORDER.index(item["category"])
            if item["category"] in CATEGORY_ORDER
            else len(CATEGORY_ORDER),
            item["name"].lower(),
        )
    )

    payload = {
        "version": SCHEMA_VERSION,
        "kind": KIND,
        "source_plan": source_plan
        or plan.get("source_plan")
        or plan.get("date")
        or "",
        "items": items,
    }
    assert_retailer_independent(payload)
    return payload


def shopping_list_to_json(shopping_list: dict) -> str:
    assert_retailer_independent(shopping_list)
    return json.dumps(shopping_list, indent=2, ensure_ascii=False) + "\n"


def render_markdown(shopping_list: dict) -> str:
    """Human-readable list. No product codes or live prices."""
    title = shopping_list.get("source_plan") or "YYYY-MM-DD"
    lines = [
        f"# Shopping List — {title}",
        "",
        f"Source plan: `{_plan_ref(title)}`",
        "",
        "Retailer-independent. Product IDs and prices belong to grocery search,",
        "not this list.",
        "",
    ]

    to_buy = [item for item in shopping_list.get("items") or [] if item.get("pantry_status") == PANTRY_BUY]
    confirm = [
        item
        for item in shopping_list.get("items") or []
        if item.get("pantry_status") == PANTRY_CONFIRM
    ]
    assumed = [
        item
        for item in shopping_list.get("items") or []
        if item.get("pantry_status") == PANTRY_ASSUMED
    ]

    lines.extend(["## To buy", ""])
    if to_buy:
        lines.extend(_render_grouped(to_buy))
    else:
        lines.append("- None.")
        lines.append("")

    lines.extend(["## Confirm before skipping", ""])
    if confirm:
        for item in confirm:
            lines.append(f"- {_bullet(item, include_pantry_hint=True)}")
        lines.append("")
    else:
        lines.append("- None.")
        lines.append("")

    lines.extend(["## Assumed pantry stock", ""])
    if assumed:
        for item in assumed:
            lines.append(f"- {_bullet(item)}")
        lines.append("")
    else:
        lines.append("- None.")
        lines.append("")

    uncertain = [
        item
        for item in shopping_list.get("items") or []
        if item.get("quantity_status") == QTY_UNCERTAIN
        and item.get("pantry_status") != PANTRY_ASSUMED
    ]
    if uncertain:
        lines.extend(["## Uncertain quantities", ""])
        lines.append(
            "These amounts could not be combined without inventing a unit conversion."
        )
        lines.append("")
        for item in uncertain:
            parts = item.get("parts") or []
            detail = " + ".join(
                str(part.get("display") or "") for part in parts if part.get("display")
            ) or item.get("display")
            lines.append(f"- **{item['name']}** — {detail}")
        lines.append("")

    substitutions = []
    for item in shopping_list.get("items") or []:
        for sub in item.get("substitutions") or []:
            substitutions.append(sub)
    if substitutions:
        lines.extend(["## Substitutions from planning", ""])
        for sub in substitutions:
            recipe = sub.get("recipe") or "Recipe"
            note = f" ({sub['note']})" if sub.get("note") else ""
            lines.append(
                f"- {recipe}: {sub.get('to')} instead of {sub.get('from')}{note}"
            )
        lines.append("")

    lines.extend(
        [
            "## Excess flags",
            "",
            "Fill after grocery search if a shoppable size is much larger than needed.",
            "",
            "- ",
            "",
        ]
    )
    return "\n".join(lines)


def assert_retailer_independent(payload: dict) -> None:
    """Refuse store/product identifiers in the shopping-list artifact."""
    for path, key, _value in _walk_keys(payload):
        lowered = str(key).lower()
        if key in RETAILER_KEYS or key in PRODUCT_ID_KEYS:
            raise ShoppingListError(
                f"shopping list must not include retailer field {key!r} at {path}"
            )
        if any(hint in lowered for hint in FORBIDDEN_KEY_HINTS):
            raise ShoppingListError(
                f"shopping list must not include retailer field {key!r} at {path}"
            )
    for item in payload.get("items") or []:
        assert_no_product_ids(item)


def load_plan(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ShoppingListError("Plan JSON must be an object")
    return data


def _collect_requirements(plan: dict, meals: list[dict] | None) -> list[dict]:
    if meals:
        return requirements_from_meals(meals)
    raw = plan.get("requirements")
    if raw:
        if all(isinstance(item, dict) and item.get("line") for item in raw):
            return aggregate_ingredients(raw)
        return [dict(item) for item in raw]
    ingredients = plan.get("ingredients") or []
    if ingredients and all(
        isinstance(item, dict) and (item.get("line") or item.get("original"))
        and not item.get("name")
        for item in ingredients
    ):
        return aggregate_ingredients(ingredients)
    return [dict(item) for item in ingredients]


def _requirement_to_item(item: dict, deviations: list[dict]) -> dict:
    if item.get("line") and not item.get("name"):
        qty = parse_ingredient_qty(str(item["line"]))
        item = {
            "name": qty.name or str(item["line"]).strip(),
            "amount": qty.amount,
            "unit": qty.unit,
            "unit_size": qty.unit_size,
            "display": display_quantity(qty),
            "category": categorize_ingredient(qty),
            "used_in": [item["source"]] if item.get("source") else [],
            "role": item.get("role") or infer_role(qty),
            "quantity_status": QTY_EXACT if qty.amount is not None else QTY_UNCERTAIN,
        }
    name = str(item.get("name") or "").strip()
    amount = item.get("amount")
    unit = item.get("unit")
    unit_size = item.get("unit_size")
    qty = IngredientQty(
        amount=amount,
        unit=unit,
        unit_size=unit_size,
        name=name,
        notes="",
        original=str(item.get("display") or name),
        scalable=amount is not None,
    )
    display = item.get("display") or display_quantity(qty)
    sources = item.get("sources") or item.get("used_in") or []
    if isinstance(sources, str):
        sources = [sources]
    role = item.get("role") or infer_role(display)
    status = item.get("quantity_status") or (
        QTY_EXACT if amount is not None else QTY_UNCERTAIN
    )
    substitutions = list(item.get("substitutions") or [])
    substitutions.extend(_substitutions_for(name, sources, deviations))
    payload = {
        "name": name,
        "amount": amount,
        "unit": unit,
        "unit_size": unit_size,
        "display": display,
        "quantity_status": status,
        "parts": item.get("parts"),
        "role": role,
        "category": item.get("category") or categorize_ingredient(qty),
        "sources": list(sources),
        "origin": item.get("origin") or ORIGIN_RECIPE,
        "pantry_status": item.get("pantry_status") or PANTRY_BUY,
        "substitutions": substitutions,
        "notes": list(item.get("notes") or []),
        "staple": bool(item.get("staple")),
    }
    if payload["parts"] is None:
        del payload["parts"]
    return payload


def _merge_same_name(items: list[dict]) -> list[dict]:
    """One shoppable row per canonical name. Incompatible units stay uncertain."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in items:
        key = item["name"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    return [_combine_group(groups[key]) for key in order]


def _combine_group(group: list[dict]) -> dict:
    if len(group) == 1:
        return dict(group[0])
    merged = dict(group[0])
    merged["sources"] = list(merged.get("sources") or [])
    merged["notes"] = list(merged.get("notes") or [])
    merged["substitutions"] = list(merged.get("substitutions") or [])
    parts = [_part_from_item(group[0])]
    status = merged.get("quantity_status") or QTY_EXACT
    for other in group[1:]:
        merged["role"] = stronger_role(merged["role"], other.get("role") or ROLE_ESSENTIAL)
        if other.get("staple"):
            merged["staple"] = True
        if other.get("origin") == ORIGIN_STAPLE and merged.get("origin") != ORIGIN_STAPLE:
            merged["staple"] = True
        for source in other.get("sources") or []:
            if source not in merged["sources"]:
                merged["sources"].append(source)
        for note in other.get("notes") or []:
            if note not in merged["notes"]:
                merged["notes"].append(note)
        for sub in other.get("substitutions") or []:
            if sub not in merged["substitutions"]:
                merged["substitutions"].append(sub)
        converted = _shopping_convert(
            other.get("amount"),
            other.get("unit"),
            merged.get("unit"),
            other.get("unit_size"),
            merged.get("unit_size"),
        )
        if (
            converted is not None
            and merged.get("amount") is not None
            and other.get("amount") is not None
            and status != QTY_UNCERTAIN
            and other.get("quantity_status") != QTY_UNCERTAIN
        ):
            amount, conv_status = converted
            merged["amount"] = (merged["amount"] or 0) + amount
            if conv_status == QTY_APPROXIMATE or other.get("quantity_status") == QTY_APPROXIMATE:
                status = QTY_APPROXIMATE
            qty = IngredientQty(
                amount=merged["amount"],
                unit=merged.get("unit"),
                unit_size=merged.get("unit_size"),
                name=merged["name"],
                notes="",
                original=merged["name"],
                scalable=True,
            )
            merged["display"] = display_quantity(qty)
            parts.append(_part_from_item(other))
        else:
            status = QTY_UNCERTAIN
            parts.append(_part_from_item(other))
    merged["quantity_status"] = status
    if status == QTY_UNCERTAIN:
        merged["amount"] = None
        merged["unit"] = None
        merged["unit_size"] = None
        merged["parts"] = parts
        merged["display"] = " + ".join(
            str(part.get("display") or "") for part in parts if part.get("display")
        )
    return merged


def _part_from_item(item: dict) -> dict:
    return {
        "amount": item.get("amount"),
        "unit": item.get("unit"),
        "unit_size": item.get("unit_size"),
        "display": item.get("display"),
    }


def _shopping_convert(
    amount,
    from_unit,
    to_unit,
    from_size=None,
    to_size=None,
) -> tuple[float, str] | None:
    if amount is None:
        return None
    if from_size and to_size and from_size != to_size:
        return None
    if from_unit == to_unit:
        return float(amount), QTY_EXACT
    basic = convert_amount(float(amount), from_unit, to_unit)
    if basic is not None:
        return basic, QTY_APPROXIMATE
    if from_unit in VOLUME_TO_ML and to_unit in VOLUME_TO_ML:
        ml = float(amount) * VOLUME_TO_ML[from_unit]
        return ml / VOLUME_TO_ML[to_unit], QTY_APPROXIMATE
    return None


def _apply_pantry(
    item: dict,
    pantry_rows: list[dict],
    out_names: list[str],
    confirm_names: list[str],
) -> None:
    if any(names_match(item["name"], name) for name in out_names):
        item["pantry_status"] = PANTRY_BUY
        _add_note(item, "temporarily out — buy this order")
        return
    match = _find_stock(item["name"], pantry_rows)
    forced = any(names_match(item["name"], name) for name in confirm_names)
    if match is None and not forced:
        item["pantry_status"] = PANTRY_BUY
        return
    if forced or (match and match.get("uncertain")):
        item["pantry_status"] = PANTRY_CONFIRM
        if match and match.get("quantity_hint"):
            _add_note(item, match["quantity_hint"])
        _add_note(item, "listed in pantry; confirm stock before skipping")
        return
    item["pantry_status"] = PANTRY_ASSUMED


def _add_staple(
    items: list[dict],
    staple: dict,
    pantry_rows: list[dict],
    out_names: list[str],
    confirm_names: list[str],
) -> None:
    existing = next(
        (item for item in items if names_match(item["name"], staple["name"])),
        None,
    )
    hint = staple.get("quantity_hint")
    if existing is not None:
        existing["staple"] = True
        if hint:
            _add_note(existing, f"staple restock: {hint}")
        else:
            _add_note(existing, "also a recurring staple")
        if staple.get("uncertain") and existing.get("pantry_status") == PANTRY_ASSUMED:
            existing["pantry_status"] = PANTRY_CONFIRM
            _add_note(existing, "staple is due when stock is low; confirm before skipping")
        return

    qty = parse_ingredient_qty(
        f"{hint} {staple['label']}" if hint else staple["label"]
    )
    display = hint or "as needed"
    if hint and qty.amount is not None and qty.name:
        display = display_quantity(qty)
    item = {
        "name": staple["name"],
        "amount": qty.amount if hint and qty.name == staple["name"] else None,
        "unit": qty.unit if hint and qty.name == staple["name"] else None,
        "unit_size": None,
        "display": display,
        "quantity_status": QTY_EXACT if qty.amount is not None and hint else QTY_UNCERTAIN,
        "role": ROLE_ESSENTIAL,
        "category": _staple_category(staple),
        "sources": [],
        "origin": ORIGIN_STAPLE,
        "pantry_status": PANTRY_BUY,
        "substitutions": [],
        "notes": [hint] if hint else [],
        "staple": True,
    }
    if staple.get("uncertain"):
        item["quantity_status"] = QTY_UNCERTAIN
    _apply_pantry(item, pantry_rows, out_names, confirm_names)
    temporarily_out = any(names_match(item["name"], name) for name in out_names)
    if item["pantry_status"] == PANTRY_ASSUMED and not staple.get("uncertain"):
        # Already on hand and not a "when low" restock — do not add a second row.
        return
    if staple.get("uncertain") and not temporarily_out:
        item["pantry_status"] = PANTRY_CONFIRM
        _add_note(item, "recurring staple; buy only if stock is low")
    items.append(item)


def _staple_category(staple: dict) -> str:
    section = str(staple.get("section") or "").lower()
    for key, heading in CATEGORY_HEADINGS.items():
        if key in section or heading.lower() in section:
            return key
    qty = parse_ingredient_qty(staple.get("label") or staple["name"])
    qty = IngredientQty(
        qty.amount,
        qty.unit,
        qty.unit_size,
        staple["name"],
        qty.notes,
        staple.get("original") or staple["name"],
        qty.scalable,
    )
    return categorize_ingredient(qty)


def _substitutions_for(
    name: str,
    sources: list[str],
    deviations: list[dict],
) -> list[dict]:
    found: list[dict] = []
    for deviation in deviations:
        for old, new in _replacements(deviation).items():
            if not names_match(name, new):
                continue
            recipe = deviation.get("recipe")
            if recipe and sources and recipe not in sources:
                if not any(str(recipe).lower() == str(src).lower() for src in sources):
                    continue
            found.append(
                {
                    "from": normalize_name(old) or old,
                    "to": normalize_name(new) or new,
                    "recipe": recipe,
                    "note": deviation.get("reason") or deviation.get("change"),
                }
            )
    return found


def _replacements(deviations) -> dict[str, str]:
    if isinstance(deviations, dict):
        deviations = [deviations]
    mapping: dict[str, str] = {}
    for deviation in deviations or []:
        raw = deviation.get("replace") or deviation.get("replacements")
        if isinstance(raw, dict):
            for old, new in raw.items():
                if old and new:
                    mapping[str(old)] = str(new)
        old = deviation.get("ingredient")
        new = deviation.get("replacement") or deviation.get("with")
        if old and new:
            mapping[str(old)] = str(new)
    return mapping


def _omit_names(deviations: Iterable[dict]) -> list[str]:
    names: list[str] = []
    for deviation in deviations:
        omit = deviation.get("omit")
        if omit is True and deviation.get("ingredient"):
            names.append(str(deviation["ingredient"]))
        elif isinstance(omit, str):
            names.append(omit)
        elif isinstance(omit, list):
            names.extend(str(item) for item in omit)
        change = str(deviation.get("change") or "")
        if change.lower().startswith("omit ") and deviation.get("ingredient"):
            names.append(str(deviation["ingredient"]))
    return names


def _is_omitted(line: str, deviations: Iterable[dict]) -> bool:
    qty = parse_ingredient_qty(line)
    for name in _omit_names(deviations):
        if qty.name and names_match(qty.name, name):
            return True
        if name.lower() in line.lower():
            return True
    return False


def _unpack_line(raw) -> tuple[str, str | None]:
    if isinstance(raw, str):
        return raw.strip(), None
    if isinstance(raw, dict):
        return str(raw.get("line") or raw.get("original") or "").strip(), raw.get("role")
    return str(raw).strip(), None


def _as_stock(value: str | list[dict] | None) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, str):
        return parse_stock_markdown(value)
    return [dict(item) for item in value]


def _split_stock_line(body: str) -> tuple[str, str | None]:
    parts = DASH_SPLIT.split(body, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return body.strip(), None


def _find_stock(name: str, rows: list[dict]) -> dict | None:
    for row in rows:
        if names_match(name, row.get("name") or row.get("label") or ""):
            return row
    return None


def _add_note(item: dict, note: str) -> None:
    notes = item.setdefault("notes", [])
    if note and note not in notes:
        notes.append(note)


def _plan_ref(title: str) -> str:
    if title.endswith(".md") or "/" in title:
        return title
    return f"plans/{title}.md"


def _render_grouped(items: list[dict]) -> list[str]:
    lines: list[str] = []
    by_category: dict[str, list[dict]] = {}
    for item in items:
        by_category.setdefault(item.get("category") or "other", []).append(item)
    for category in CATEGORY_ORDER:
        group = by_category.pop(category, [])
        if not group:
            continue
        lines.append(f"### {CATEGORY_HEADINGS.get(category, category.title())}")
        lines.append("")
        for item in group:
            lines.append(f"- {_bullet(item)}")
        lines.append("")
    for category, group in by_category.items():
        lines.append(f"### {CATEGORY_HEADINGS.get(category, category.title())}")
        lines.append("")
        for item in group:
            lines.append(f"- {_bullet(item)}")
        lines.append("")
    return lines


def _bullet(item: dict, include_pantry_hint: bool = False) -> str:
    name = item.get("name") or "item"
    display = item.get("display") or "as needed"
    bits = [f"**{name}** — {display}"]
    extras: list[str] = []
    role = item.get("role")
    if role and role != ROLE_ESSENTIAL:
        extras.append(role)
    sources = item.get("sources") or []
    if sources:
        extras.append(", ".join(str(src) for src in sources))
    if item.get("origin") == ORIGIN_STAPLE:
        extras.append("staple")
    elif item.get("staple"):
        extras.append("also a staple")
    if include_pantry_hint:
        extras.append("confirm pantry")
    if extras:
        bits.append(f"({'; '.join(extras)})")
    return " ".join(bits)


def _walk_keys(node, path: str = "$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from _walk_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_keys(value, f"{path}[{index}]")


def _workspace_stock() -> tuple[str | None, str | None]:
    try:
        paths = workspace_paths(find_workspace_root())
    except WorkspaceNotFoundError:
        return None, None
    pantry = paths["pantry"]
    staples = paths["staples"]
    pantry_text = pantry.read_text(encoding="utf-8") if pantry.is_file() else None
    staples_text = staples.read_text(encoding="utf-8") if staples.is_file() else None
    return pantry_text, staples_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a retailer-independent shopping list from a meal-plan JSON "
            "plus pantry and staples. No grocery provider required."
        )
    )
    parser.add_argument("plan", type=Path, help="Plan JSON from meal_plan.build_plan")
    parser.add_argument(
        "--pantry",
        type=Path,
        default=None,
        help="pantry.md (workspace file used when omitted)",
    )
    parser.add_argument(
        "--staples",
        type=Path,
        default=None,
        help="staples.md (workspace file used when omitted)",
    )
    parser.add_argument(
        "--pantry-out",
        action="append",
        default=[],
        help="Name that is temporarily out (repeatable)",
    )
    parser.add_argument(
        "--pantry-confirm",
        action="append",
        default=[],
        help="Name that needs a stock check (repeatable)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write the shopping-list artifact to stdout",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write the JSON artifact here",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        help="Write the human-readable list here",
    )
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
    except (OSError, json.JSONDecodeError, ShoppingListError) as exc:
        print(f"Error reading plan: {exc}", file=sys.stderr)
        return 1

    pantry_text = args.pantry.read_text(encoding="utf-8") if args.pantry else None
    staples_text = args.staples.read_text(encoding="utf-8") if args.staples else None
    if pantry_text is None or staples_text is None:
        ws_pantry, ws_staples = _workspace_stock()
        pantry_text = pantry_text if pantry_text is not None else ws_pantry
        staples_text = staples_text if staples_text is not None else ws_staples

    try:
        shopping = build_shopping_list(
            plan=plan,
            pantry=pantry_text,
            staples=staples_text,
            pantry_out=args.pantry_out,
            pantry_confirm=args.pantry_confirm,
        )
    except ShoppingListError as exc:
        print(f"Error building shopping list: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(shopping)
    artifact = shopping_list_to_json(shopping)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(artifact, encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")

    if args.json or args.output:
        if not args.output:
            sys.stdout.write(artifact)
    else:
        sys.stdout.write(markdown)
        if not markdown.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
