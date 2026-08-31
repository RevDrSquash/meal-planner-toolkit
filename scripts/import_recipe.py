#!/usr/bin/env python3
"""Import a recipe into the workspace recipes/ directory as canonical HTML.

Accepts recipe page URLs (schema.org/Recipe JSON-LD), hand-authored markdown,
or a local HTML page that already contains Recipe JSON-LD. Every input is
normalized through recipe_core so planning skills only ever read one format.

Usage:
    python scripts/import_recipe.py <recipe-url> [more-urls...]
    python scripts/import_recipe.py path/to/recipe.md
    python scripts/import_recipe.py --force <recipe-url>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from recipe_core import (
    RecipeExistsError,
    fetch_page,
    find_existing_recipe,
    normalize_url,
    recipe_filename,
    recipe_from_jsonld,
    write_recipe,
)
from recipe_from_markdown import parse_markdown
from workspace import WorkspaceNotFoundError, find_workspace_root, workspace_paths

SUPPORTED_MARKDOWN = {".md", ".markdown"}
SUPPORTED_HTML = {".html", ".htm"}


def default_recipes_dir() -> Path:
    try:
        return workspace_paths(find_workspace_root())["recipes"]
    except WorkspaceNotFoundError:
        return Path.cwd() / "recipes"


def _commit(recipe: dict, recipes_dir: Path, force: bool) -> Path:
    target = recipes_dir / recipe_filename(recipe)
    try:
        return write_recipe(recipe, target, force)
    except RecipeExistsError as exc:
        if exc.same_recipe and not force:
            return exc.path
        raise


def import_url(url: str, recipes_dir: Path, force: bool) -> Path:
    normalized = normalize_url(url)
    page_html = fetch_page(normalized)
    recipe = recipe_from_jsonld(page_html, normalized)
    return _commit(recipe, recipes_dir, force)


def import_markdown_file(md_path: Path, recipes_dir: Path, force: bool) -> Path:
    text = md_path.read_text(encoding="utf-8")
    recipe = parse_markdown(text, source_file=md_path.name)
    return _commit(recipe, recipes_dir, force)


def import_html_file(html_path: Path, recipes_dir: Path, force: bool) -> Path:
    page_html = html_path.read_text(encoding="utf-8")
    recipe = recipe_from_jsonld(page_html, None)
    if not recipe.get("source_file") and not recipe.get("source_url"):
        recipe["source_file"] = html_path.name
    return _commit(recipe, recipes_dir, force)


def ingest(source: str, recipes_dir: Path, force: bool) -> tuple[Path, bool]:
    """Normalize one source. Returns (path, already_present)."""
    path = Path(source)
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_MARKDOWN:
            before = find_existing_recipe(
                recipes_dir,
                parse_markdown(path.read_text(encoding="utf-8"), source_file=path.name),
            )
            written = import_markdown_file(path, recipes_dir, force)
            return written, bool(before) and not force
        if suffix in SUPPORTED_HTML:
            recipe = recipe_from_jsonld(path.read_text(encoding="utf-8"), None)
            before = find_existing_recipe(recipes_dir, recipe)
            written = import_html_file(path, recipes_dir, force)
            return written, bool(before) and not force
        if suffix == ".pdf":
            raise ValueError(
                "PDF ingestion is not supported. Convert the recipe to "
                "markdown using templates/recipe-template.md, or import a "
                "URL / HTML page that publishes schema.org Recipe data."
            )
        raise ValueError(f"Unsupported file type: {suffix or path.name}")

    normalized = normalize_url(source)
    # Peek identity after fetch so duplicate detection can use the real name.
    page_html = fetch_page(normalized)
    recipe = recipe_from_jsonld(page_html, normalized)
    before = find_existing_recipe(recipes_dir, recipe)
    written = _commit(recipe, recipes_dir, force)
    return written, bool(before) and not force


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import recipes as HTML files in recipes/.",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="Recipe URLs, markdown files, or local HTML pages",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing card for the same recipe",
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
    for source in args.sources:
        try:
            output_path, already = ingest(source, recipes_dir, args.force)
            if already:
                print(f"Already present {output_path}")
            else:
                print(f"Wrote {output_path}")
        except RecipeExistsError as exc:
            print(f"Error importing {source}: {exc}", file=sys.stderr)
            errors += 1
        except (RuntimeError, ValueError, OSError) as exc:
            print(f"Error importing {source}: {exc}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
