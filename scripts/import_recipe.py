#!/usr/bin/env python3
"""Import a recipe from a URL into the workspace recipes/ directory.

Works with any recipe site that publishes schema.org/Recipe structured data
(JSON-LD), which includes HelloFresh and most major recipe sites and blogs.

Usage:
    python scripts/import_recipe.py <recipe-url> [more-urls...]
    python scripts/import_recipe.py --force <recipe-url>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from recipe_core import (
    fetch_page,
    normalize_url,
    recipe_from_jsonld,
    slug_from_url,
    write_recipe,
)
from workspace import WorkspaceNotFoundError, find_workspace_root, workspace_paths


def default_recipes_dir() -> Path:
    try:
        return workspace_paths(find_workspace_root())["recipes"]
    except WorkspaceNotFoundError:
        return Path.cwd() / "recipes"


def import_url(url: str, recipes_dir: Path, force: bool) -> Path:
    normalized = normalize_url(url)
    slug = slug_from_url(normalized)
    page_html = fetch_page(normalized)
    recipe = recipe_from_jsonld(page_html, normalized)
    return write_recipe(recipe, recipes_dir / f"{slug}.html", force)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import recipes from URLs as HTML files in recipes/.",
    )
    parser.add_argument("urls", nargs="+", help="One or more recipe URLs")
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
    for url in args.urls:
        try:
            output_path = import_url(url, recipes_dir, args.force)
            print(f"Wrote {output_path}")
        except (RuntimeError, ValueError, FileExistsError) as exc:
            print(f"Error importing {url}: {exc}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
