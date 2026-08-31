#!/usr/bin/env python3
"""Convert a hand-written markdown recipe into workspace recipes/ HTML.

Use this for recipes that aren't online (family recipes, cookbook recipes).
Copy templates/recipe-template.md, fill it in, then run this script. The
output uses the same HTML card layout as URL-imported recipes. Do not keep
a second Markdown copy in recipes/ unless the user asks for a source archive.

Usage:
    python scripts/recipe_from_markdown.py my-recipe.md [more.md ...]
    python scripts/recipe_from_markdown.py --force my-recipe.md

Expected markdown structure (see templates/recipe-template.md):

    # Recipe Name

    - Serves: 4
    - Time: 45 min
    - Tags: beef, one-pot
    - Source: https://example.com/optional-original-url

    ## Ingredients
    - 500 g ground beef

    ## Nutrition
    - Calories: 640 kcal

    ## Instructions
    1. Do the first thing.

    ## Notes
    Optional free text.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

from recipe_core import (
    RecipeExistsError,
    find_existing_recipe,
    recipe_filename,
    write_recipe,
)
from workspace import WorkspaceNotFoundError, find_workspace_root, workspace_paths


def default_recipes_dir() -> Path:
    try:
        return workspace_paths(find_workspace_root())["recipes"]
    except WorkspaceNotFoundError:
        return Path.cwd() / "recipes"

META_LINE = re.compile(r"^-\s*([A-Za-z ]+?)\s*:\s*(.+)$")
LIST_ITEM = re.compile(r"^(?:[-*]|\d+[.)])\s+(.+)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_markdown(text: str, source_file: str | None = None) -> dict:
    lines = text.splitlines()

    name = None
    sections: dict[str, list[str]] = {}
    preamble: list[str] = []
    current: list[str] | None = preamble

    for line in lines:
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if level == 1 and name is None:
                name = title
                current = preamble
            else:
                key = title.lower()
                sections[key] = []
                current = sections[key]
            continue
        if current is not None:
            current.append(line)

    if not name:
        raise ValueError("Missing a top-level '# Recipe Name' heading")

    serves = None
    total_time = None
    source_url = None
    tags: list[str] = []
    for line in preamble:
        meta = META_LINE.match(line.strip())
        if not meta:
            continue
        label = meta.group(1).strip().lower()
        value = meta.group(2).strip()
        if label in ("serves", "servings", "yield"):
            serves = value
        elif label in ("time", "total time"):
            total_time = value
        elif label in ("tags", "tag"):
            tags = [t.strip() for t in value.split(",") if t.strip()]
        elif label in ("source", "url", "source url"):
            if value.startswith(("http://", "https://")):
                source_url = value
            elif not source_file:
                source_file = value

    ingredients = _list_items(sections.get("ingredients", []))

    nutrition: list[dict] = []
    for item in _list_items(sections.get("nutrition", [])):
        if ":" in item:
            label, value = item.split(":", 1)
            nutrition.append(
                {
                    "label": label.strip(),
                    "value": value.strip(),
                    "estimated": False,
                }
            )

    instructions = [
        html.escape(step) for step in _list_items(sections.get("instructions", []))
    ]

    notes_text = "\n".join(sections.get("notes", [])).strip()
    notes = None
    if notes_text:
        paragraphs = [
            f"<p>{html.escape(p.strip())}</p>"
            for p in re.split(r"\n\s*\n", notes_text)
            if p.strip()
        ]
        notes = "\n".join(paragraphs)

    return {
        "name": name,
        "serves": serves,
        "total_time": total_time,
        "tags": tags,
        "image": None,
        "ingredients": ingredients,
        "nutrition": nutrition,
        "instructions": instructions,
        "notes": notes,
        "source_url": source_url,
        "source_file": source_file,
    }


def _list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        match = LIST_ITEM.match(line.strip())
        if match:
            items.append(match.group(1).strip())
    return items


def convert_file(md_path: Path, recipes_dir: Path, force: bool) -> Path:
    text = md_path.read_text(encoding="utf-8")
    recipe = parse_markdown(text, source_file=md_path.name)
    target = recipes_dir / recipe_filename(recipe)
    try:
        return write_recipe(recipe, target, force)
    except RecipeExistsError as exc:
        if exc.same_recipe and not force:
            return exc.path
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert markdown recipes into HTML files in recipes/.",
    )
    parser.add_argument("files", nargs="+", type=Path, help="Markdown recipe files")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing recipe files",
    )
    parser.add_argument(
        "--recipes-dir",
        type=Path,
        default=None,
        help="Output directory (default: workspace recipes/)",
    )
    args = parser.parse_args(argv)
    recipes_dir = args.recipes_dir or default_recipes_dir()

    errors = 0
    for md_path in args.files:
        try:
            preview = parse_markdown(
                md_path.read_text(encoding="utf-8"),
                source_file=md_path.name,
            )
            existed = find_existing_recipe(recipes_dir, preview) is not None
            output_path = convert_file(md_path, recipes_dir, force=args.force)
            if existed and not args.force:
                print(f"Already present {output_path}")
            else:
                print(f"Wrote {output_path}")
        except RecipeExistsError as exc:
            if exc.same_recipe:
                print(f"Already present {exc.path}")
            else:
                print(f"Error converting {md_path}: {exc}", file=sys.stderr)
                errors += 1
        except (OSError, ValueError) as exc:
            print(f"Error converting {md_path}: {exc}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
