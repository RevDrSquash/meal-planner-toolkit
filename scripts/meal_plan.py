#!/usr/bin/env python3
"""Structured V2 meal + cooking plan: build, serialize, and render.

Meal selection and cooking-session grouping stay one workflow. This module
does the deterministic work: scale servings, apply recorded deviations,
aggregate ingredient requirements, collect nutrition, and write the
human-readable plan artifact. The agent still chooses meals and sessions.

Usage:
    python scripts/meal_plan.py render plan.json
    python scripts/meal_plan.py scale --from-servings 4 --to-servings 6 "500 g ground beef"
    python scripts/meal_plan.py aggregate --source "Chili" "1 onion" "2 onions"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ingredients import (
    PRODUCT_ID_KEYS,
    IngredientError,
    aggregate_ingredients,
    apply_ingredient_replacement,
    overlap_score,
    scale_ingredient_line,
    serving_scale,
    shared_ingredient_names,
)
from nutrition_estimate import parse_servings
from recipe_core import recipe_from_jsonld
from recipe_finder import (
    collection_too_small,
    load_local_recipes,
    load_preferences,
    restriction_hits,
    score_candidate,
)
from workspace import WorkspaceNotFoundError, find_workspace_root, workspace_paths

PLAN_VERSION = 2
CORE_MACROS = ("Calories", "Protein", "Fat", "Carbohydrates")
MACRO_KEYS = {
    "Calories": "calories",
    "Protein": "protein",
    "Fat": "fat",
    "Carbohydrates": "carbohydrates",
}

OVEN_HINTS = frozenset(
    {"oven", "sheet-pan", "sheet pan", "baked", "bake", "roast", "roasted"}
)
PRESSURE_HINTS = frozenset(
    {"pressure-cooker", "pressure cooker", "instant pot", "instant-pot"}
)
STOVETOP_HINTS = frozenset(
    {"one-pot", "one pot", "skillet", "stovetop", "stove"}
)


def load_recipe_card(path: Path) -> dict:
    """Load a canonical HTML card into the shared recipe dict."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    recipe = recipe_from_jsonld(text, None)
    recipe["file"] = path.name
    recipe["path"] = str(path)
    return recipe


def load_recipes_from_dir(recipes_dir: Path) -> dict[str, dict]:
    """Map recipe name → full card (ingredients, nutrition, provenance)."""
    recipes: dict[str, dict] = {}
    if recipes_dir is None or not Path(recipes_dir).is_dir():
        return recipes
    for path in sorted(Path(recipes_dir).glob("*.html")):
        try:
            recipe = load_recipe_card(path)
        except (OSError, RuntimeError):
            continue
        name = recipe.get("name") or path.stem
        recipes[str(name)] = recipe
    return recipes


def library_gap_note(available: int, requested_meals: int) -> str | None:
    """Surface a thin library instead of inventing unsupported recipes."""
    if available <= 0:
        return (
            "The recipe library is empty. Use recipe discovery to import "
            "supported cards rather than inventing recipes."
        )
    if requested_meals > 0 and available < requested_meals:
        return (
            f"Only {available} recipe(s) for {requested_meals} requested meal(s). "
            "Repeats or recipe discovery are needed; do not invent unsupported recipes."
        )
    return None


def filter_library(recipes: list[dict], prefs: dict) -> dict:
    """Split local cards into hard-constraint misses vs eligible candidates."""
    eligible: list[dict] = []
    excluded: list[dict] = []
    max_minutes = prefs.get("max_minutes") if isinstance(prefs, dict) else None
    for recipe in recipes:
        candidate = {
            "title": recipe.get("name") or recipe.get("title") or "",
            "name": recipe.get("name") or recipe.get("title") or "",
            "tags": list(recipe.get("tags") or []),
            "ingredients": list(recipe.get("ingredients") or []),
            "total_time": recipe.get("total_time"),
            "filename": recipe.get("filename") or recipe.get("file"),
        }
        hits = restriction_hits(candidate, prefs)
        if hits:
            excluded.append(
                {
                    "name": candidate["name"],
                    "filename": candidate.get("filename"),
                    "reasons": hits,
                }
            )
            continue
        score, reasons = score_candidate(
            candidate, "", prefs, max_minutes
        )
        item = dict(recipe)
        item["score"] = score
        item["reasons"] = reasons
        eligible.append(item)
    eligible.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            str(item.get("name") or "").lower(),
        )
    )
    return {
        "eligible": eligible,
        "excluded": excluded,
        "collection_too_small": collection_too_small(len(recipes), prefs),
    }


def equipment_hints(recipe: dict) -> list[str]:
    """Light equipment tags from recipe tags/name. Agent still reads tools.md."""
    blob = " ".join(
        [
            str(recipe.get("name") or ""),
            " ".join(str(tag) for tag in (recipe.get("tags") or [])),
        ]
    ).lower()
    hints: list[str] = []
    if any(token in blob for token in OVEN_HINTS):
        hints.append("oven")
    if any(token in blob for token in PRESSURE_HINTS):
        hints.append("pressure-cooker")
    if any(token in blob for token in STOVETOP_HINTS):
        hints.append("stovetop")
    return hints


def suggest_cook_together(
    recipes: list[dict],
    *,
    min_score: float = 0.15,
) -> list[dict]:
    """Pairs that share significant ingredients and do not obviously clash."""
    pairs: list[dict] = []
    for index, left in enumerate(recipes):
        left_lines = left.get("ingredients") or []
        left_equip = set(equipment_hints(left))
        for right in recipes[index + 1 :]:
            right_lines = right.get("ingredients") or []
            score = overlap_score(left_lines, right_lines)
            if score < min_score:
                continue
            right_equip = set(equipment_hints(right))
            shared_equip = sorted(left_equip & right_equip)
            pairs.append(
                {
                    "recipes": [
                        left.get("name") or left.get("title"),
                        right.get("name") or right.get("title"),
                    ],
                    "score": round(score, 3),
                    "shared": shared_ingredient_names(left_lines, right_lines),
                    "equipment_hints": sorted(left_equip | right_equip),
                    "shared_equipment": shared_equip,
                }
            )
    pairs.sort(key=lambda item: (-item["score"], item["recipes"][0] or ""))
    return pairs


def apply_deviations(ingredients: list[str], deviations: list[dict]) -> list[str]:
    """Apply recorded substitutions to a recipe's ingredient lines."""
    updated = list(ingredients)
    for deviation in deviations:
        mapping = _replacement_map(deviation)
        if not mapping:
            continue
        next_lines: list[str] = []
        for line in updated:
            for old, new in mapping.items():
                line = apply_ingredient_replacement(line, old, new)
            next_lines.append(line)
        updated = next_lines
    return updated


def nutrition_row(recipe: dict, servings: float | None) -> dict:
    """Per-serving macros from the card. Missing fields stay blank."""
    items = recipe.get("nutrition") or []
    values = {key: None for key in MACRO_KEYS.values()}
    estimated = False
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        key = MACRO_KEYS.get(label)
        if not key:
            continue
        value = str(item.get("value") or "").strip()
        values[key] = value or None
        if item.get("estimated"):
            estimated = True
    present = [values[MACRO_KEYS[label]] for label in CORE_MACROS]
    return {
        "meal": recipe.get("name") or "Untitled Recipe",
        "servings": servings,
        "calories": values["calories"],
        "protein": values["protein"],
        "fat": values["fat"],
        "carbohydrates": values["carbohydrates"],
        "estimated": estimated,
        "missing": all(value is None for value in present),
    }


def build_plan(
    *,
    date_value: str | None = None,
    household: str | None = None,
    period: str | None = None,
    servings_per_meal: float | None = None,
    meals: list[dict],
    recipes: dict[str, dict],
    cooking_sessions: list[dict] | None = None,
    deviations: list[dict] | None = None,
    notes: list[str] | None = None,
    library_notes: list[str] | None = None,
) -> dict:
    """Assemble a structured plan from chosen meals and source recipes."""
    plan_date = date_value or date.today().isoformat()
    deviations = [dict(item) for item in (deviations or [])]
    recipe_index = _index_recipes(recipes)

    meal_rows: list[dict] = []
    references: list[dict] = []
    nutrition: list[dict] = []
    contributions: list[dict] = []
    scale_notes: list[str] = []
    seen_refs: set[tuple[str, float | None]] = set()

    for raw in meals:
        meal = dict(raw)
        recipe_key = meal.get("recipe") or meal.get("name")
        recipe = _lookup_recipe(recipe_index, recipe_key)
        if recipe is None:
            raise IngredientError(f"Unknown recipe in meal schedule: {recipe_key!r}")
        planned = meal.get("servings")
        if planned is None:
            planned = servings_per_meal
        if planned is None:
            planned = parse_servings(recipe.get("serves")) or 1.0
        planned = float(planned)
        leftover = bool(
            meal.get("leftover")
            or meal.get("reheat")
            or meal.get("cook") is False
        )
        _, scale_note = serving_scale(recipe.get("serves"), planned)
        if scale_note and not leftover:
            scale_notes.append(f"{recipe.get('name')}: {scale_note}")

        recipe_devs = [
            item
            for item in deviations
            if _same_recipe_name(item.get("recipe"), recipe.get("name"))
        ]
        if not leftover:
            lines = apply_deviations(list(recipe.get("ingredients") or []), recipe_devs)
            source_servings = parse_servings(recipe.get("serves")) or 1.0
            scaled_lines = [
                scale_ingredient_line(line, source_servings, planned) for line in lines
            ]
            for line in scaled_lines:
                contributions.append({"line": line, "source": recipe.get("name")})

        meal_rows.append(
            {
                "day": meal.get("day") or "",
                "slot": meal.get("slot") or meal.get("meal") or "dinner",
                "recipe": recipe.get("name"),
                "file": recipe.get("file") or recipe.get("filename"),
                "servings": planned,
                "leftover": leftover,
                "notes": meal.get("notes") or "",
            }
        )
        if not leftover:
            ref_key = (str(recipe.get("name")), planned)
            if ref_key not in seen_refs:
                seen_refs.add(ref_key)
                references.append(
                    {
                        "name": recipe.get("name"),
                        "file": recipe.get("file") or recipe.get("filename"),
                        "source_servings": parse_servings(recipe.get("serves")),
                        "planned_servings": planned,
                        "source_url": recipe.get("source_url"),
                    }
                )
        nutrition.append(nutrition_row(recipe, planned))

    ingredients = aggregate_ingredients(contributions)
    for item in ingredients:
        extra = PRODUCT_ID_KEYS & set(item)
        if extra:
            raise IngredientError(
                "ingredient requirements must not include product identifiers"
            )

    auto_library = library_notes
    if auto_library is None:
        note = library_gap_note(len(recipe_index), len(meals))
        auto_library = [note] if note else []

    all_notes = list(notes or [])
    all_notes.extend(scale_notes)

    return {
        "version": PLAN_VERSION,
        "date": plan_date,
        "household": household or "",
        "period": period or "",
        "servings_per_meal": servings_per_meal,
        "meals": meal_rows,
        "cooking_sessions": [dict(item) for item in (cooking_sessions or [])],
        "recipes": references,
        "deviations": deviations,
        "nutrition": nutrition,
        "ingredients": ingredients,
        "library_notes": list(auto_library),
        "notes": all_notes,
    }


def render_markdown(plan: dict) -> str:
    """Human-readable plan artifact for workspace plans/."""
    title_date = plan.get("date") or "YYYY-MM-DD"
    lines = [
        f"# Meal Plan — {title_date}",
        "",
        f"- Household: {plan.get('household') or ''}",
        f"- Period: {plan.get('period') or ''}",
        f"- Servings per meal: {_display(plan.get('servings_per_meal'))}",
        "",
        "## Meal schedule",
        "",
        "| Day | Meal | Recipe | Servings | Notes |",
        "|---|---|---|---|---|",
    ]
    for meal in plan.get("meals") or []:
        notes = meal.get("notes") or ""
        if meal.get("leftover") and "leftover" not in str(notes).lower():
            notes = f"{notes}; leftover".strip("; ")
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(meal.get("day")),
                    _cell(meal.get("slot")),
                    _cell(meal.get("recipe")),
                    _format_number(meal.get("servings")),
                    _cell(notes),
                ]
            )
            + " |"
        )
    if not plan.get("meals"):
        lines.append("|  |  |  |  |  |")

    lines.extend(["", "## Cooking sessions", ""])
    sessions = plan.get("cooking_sessions") or []
    if not sessions:
        lines.append(
            "No grouped cooking sessions. Each meal is cooked for the day it is eaten."
        )
        lines.append("")
    for session in sessions:
        heading = session.get("title") or session.get("day") or "Session"
        day = session.get("day")
        if day and session.get("title") and day not in str(heading):
            heading = f"{day} — {session.get('title')}"
        lines.append(f"### {heading}")
        lines.append("")
        cook = session.get("cook") or session.get("recipes") or []
        if isinstance(cook, list):
            cook = ", ".join(str(item) for item in cook if item)
        lines.append(f"- Cook: {cook}")
        if session.get("reason") or session.get("why"):
            lines.append(f"- Why together: {session.get('reason') or session.get('why')}")
        if session.get("equipment"):
            equipment = session.get("equipment")
            if isinstance(equipment, list):
                equipment = ", ".join(str(item) for item in equipment)
            lines.append(f"- Equipment / capacity: {equipment}")
        leftovers = session.get("leftovers_to_hold") or session.get("leftovers") or []
        if leftovers:
            if isinstance(leftovers, list):
                leftovers = "; ".join(str(item) for item in leftovers)
            lines.append(f"- Leftovers to hold: {leftovers}")
        shared = session.get("shared_prep") or []
        if shared:
            if isinstance(shared, list):
                shared = "; ".join(str(item) for item in shared)
            lines.append(f"- Shared prep: {shared}")
        if session.get("notes"):
            lines.append(f"- Notes: {session.get('notes')}")
        lines.append("")

    lines.extend(["## Recipe references", ""])
    refs = plan.get("recipes") or []
    if not refs:
        lines.append("- None.")
    for ref in refs:
        filename = ref.get("file") or ""
        path = f"`recipes/{filename}`" if filename else (ref.get("name") or "recipe")
        source = ref.get("source_servings")
        planned = ref.get("planned_servings")
        source_bit = f"source serves {source}" if source else "source servings unknown"
        lines.append(f"- {path} — {ref.get('name')}; {source_bit}, planned servings {planned}")
    lines.append("")

    lines.extend(["## Recipe deviations", ""])
    deviations = plan.get("deviations") or []
    if not deviations:
        lines.append("- None.")
    for item in deviations:
        recipe = item.get("recipe") or "Recipe"
        change = item.get("change") or ""
        reason = item.get("reason")
        suffix = f" ({reason})" if reason else ""
        lines.append(f"- {recipe}: {change}{suffix}")
    lines.append("")

    lines.extend(
        [
            "## Nutrition",
            "",
            "Per serving when the recipe card lists macros. Blank cells mean the "
            "card has no figure. Values marked estimated were inferred from ingredients.",
            "",
            "| Meal | Servings | Calories | Protein | Fat | Carbs |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in plan.get("nutrition") or []:
        mark = " (estimated)" if row.get("estimated") else ""
        meal_name = f"{_cell(row.get('meal'))}{mark}"
        lines.append(
            "| "
            + " | ".join(
                [
                    meal_name,
                    _format_number(row.get("servings")),
                    _cell(row.get("calories")),
                    _cell(row.get("protein")),
                    _cell(row.get("fat")),
                    _cell(row.get("carbohydrates")),
                ]
            )
            + " |"
        )
    if not plan.get("nutrition"):
        lines.append("|  |  |  |  |  |  |")
    lines.append("")

    lines.extend(
        [
            "## Ingredient requirements",
            "",
            "Normalized amounts for the shopping-list step. These are ingredients, "
            "not retailer product IDs.",
            "",
            "| Ingredient | Quantity | Category | Used in |",
            "|---|---|---|---|",
        ]
    )
    for item in plan.get("ingredients") or []:
        used = item.get("used_in") or []
        if isinstance(used, list):
            used = ", ".join(str(name) for name in used)
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(item.get("name")),
                    _cell(item.get("display") or item.get("quantity")),
                    _cell(item.get("category")),
                    _cell(used),
                ]
            )
            + " |"
        )
    if not plan.get("ingredients"):
        lines.append("|  |  |  |  |")
    lines.append("")

    library_notes = plan.get("library_notes") or []
    if library_notes:
        lines.extend(["## Library notes", ""])
        for note in library_notes:
            lines.append(f"- {note}")
        lines.append("")

    extra_notes = plan.get("notes") or []
    lines.extend(["## Notes", ""])
    if extra_notes:
        for note in extra_notes:
            lines.append(f"- {note}")
    else:
        lines.append("- ")
    lines.append("")
    return "\n".join(lines)


def plan_to_json(plan: dict) -> str:
    return json.dumps(plan, indent=2, ensure_ascii=False) + "\n"


def plan_from_json(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Plan JSON must be an object")
    return data


def write_plan(plan: dict, output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(plan), encoding="utf-8")
    return output_path


def _index_recipes(recipes: dict[str, dict] | list[dict]) -> dict[str, dict]:
    if isinstance(recipes, dict):
        items = recipes.values()
        index = {str(key).strip().lower(): value for key, value in recipes.items()}
    else:
        items = recipes
        index = {}
    for recipe in items:
        name = str(recipe.get("name") or "").strip().lower()
        filename = str(recipe.get("file") or recipe.get("filename") or "").strip().lower()
        if name:
            index[name] = recipe
        if filename:
            index[filename] = recipe
    return index


def _lookup_recipe(index: dict[str, dict], key) -> dict | None:
    if key is None:
        return None
    return index.get(str(key).strip().lower())


def _same_recipe_name(left, right) -> bool:
    if not left or not right:
        return False
    return str(left).strip().lower() == str(right).strip().lower()


def _replacement_map(deviation: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    raw = deviation.get("replace") or deviation.get("replacements")
    if isinstance(raw, dict):
        for old, new in raw.items():
            if old and new:
                mapping[str(old)] = str(new)
    old = deviation.get("ingredient")
    new = deviation.get("replacement")
    if old and new:
        mapping[str(old)] = str(new)
    return mapping


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "/").replace("\n", " ")


def _display(value) -> str:
    if value is None:
        return ""
    return str(value)


def _format_number(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _cell(value)


def _workspace_paths_or_none() -> dict[str, Path] | None:
    try:
        return workspace_paths(find_workspace_root())
    except WorkspaceNotFoundError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and render a structured meal + cooking plan.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="Render a plan JSON file as markdown")
    render.add_argument("plan", type=Path, help="Plan JSON (or '-' for stdin)")
    render.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write markdown here instead of stdout",
    )

    scale = sub.add_parser("scale", help="Scale ingredient lines by servings")
    scale.add_argument("--from-servings", dest="from_servings", type=float, required=True)
    scale.add_argument("--to-servings", dest="to_servings", type=float, required=True)
    scale.add_argument("lines", nargs="+", help="Ingredient lines")

    aggregate = sub.add_parser("aggregate", help="Normalize and merge ingredient lines")
    aggregate.add_argument("--source", default=None, help="Recipe name for used-in")
    aggregate.add_argument("lines", nargs="+", help="Ingredient lines")

    eligible = sub.add_parser(
        "eligible",
        help="Filter the local library by hard preference constraints",
    )
    eligible.add_argument("--recipes-dir", type=Path, default=None)
    eligible.add_argument("--preferences", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "render":
        if str(args.plan) == "-":
            payload = sys.stdin.read()
        else:
            payload = args.plan.read_text(encoding="utf-8")
        try:
            plan = plan_from_json(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"Error reading plan: {exc}", file=sys.stderr)
            return 1
        markdown = render_markdown(plan)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(markdown, encoding="utf-8")
        else:
            sys.stdout.write(markdown)
            if not markdown.endswith("\n"):
                sys.stdout.write("\n")
        return 0

    if args.command == "scale":
        for line in args.lines:
            print(scale_ingredient_line(line, args.from_servings, args.to_servings))
        return 0

    if args.command == "aggregate":
        entries = [
            {"line": line, "source": args.source} if args.source else line
            for line in args.lines
        ]
        json.dump(aggregate_ingredients(entries), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    paths = _workspace_paths_or_none()
    recipes_dir = args.recipes_dir
    preferences_path = args.preferences
    if paths:
        recipes_dir = recipes_dir or paths["recipes"]
        preferences_path = preferences_path or paths["preferences"]
    prefs = load_preferences(preferences_path)
    local = load_local_recipes(recipes_dir)
    result = filter_library(local, prefs)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
