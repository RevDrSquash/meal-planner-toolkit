#!/usr/bin/env python3
"""Locate the private meal-planning workspace from any toolkit script.

The toolkit is application code. User state lives in a parent workspace that
embeds this package (usually as a Git submodule). Scripts must never treat
the toolkit directory itself as the workspace.

Discovery order, starting at cwd (or an explicit start path) and walking up:

1. A ``workspace.yaml`` or ``meal-planner.yaml`` file.
2. The conventional pair ``preferences.md`` + ``recipes/``.

Optional ``WORKSPACE_ROOT`` overrides discovery.

Use ``--init`` to create the default layout from templates. Existing user
files are never overwritten.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

CONFIG_NAMES = ("workspace.yaml", "meal-planner.yaml")

DEFAULT_PATHS = {
    "preferences": "preferences.md",
    "staples": "staples.md",
    "pantry": "pantry.md",
    "tools": "tools.md",
    "recipes": "recipes",
    "plans": "plans",
    "shopping": "shopping",
}

DIRECTORY_KEYS = frozenset({"recipes", "plans", "shopping"})
REQUIRED_ONBOARDING = ("preferences", "recipes")

# Template filename in templates/ → workspace path key, or a path relative to
# the shopping directory for product mappings.
FILE_TEMPLATES = {
    "preferences": "preferences.md",
    "staples": "staples.md",
    "pantry": "pantry.md",
    "tools": "tools.md",
}

# Starter README copied into otherwise-empty contract directories so Git
# records them. shopping/ is tracked via product-mappings.md instead.
DIRECTORY_README_TEMPLATES = {
    "recipes": "recipes-readme.md",
    "plans": "plans-readme.md",
}


class WorkspaceNotFoundError(FileNotFoundError):
    """Raised when no meal-planning workspace can be located."""


class WorkspaceInitError(RuntimeError):
    """Raised when workspace initialization is refused or cannot run."""


def toolkit_root() -> Path:
    """Return this toolkit package root (the directory that contains SKILL.md)."""
    return Path(__file__).resolve().parent.parent


def path_is_inside_toolkit(path: Path) -> bool:
    """True when *path* is the toolkit root or a directory inside it."""
    resolved = Path(path).expanduser().resolve()
    return resolved.is_relative_to(toolkit_root())


def _parse_simple_yaml(text: str) -> dict[str, str]:
    """Parse the tiny subset of YAML used by workspace.yaml (flat key: value)."""
    values: dict[str, str] = {}
    in_paths = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped in {"paths:", "paths: {}"}:
            in_paths = True
            continue
        if not line.startswith(" ") and stripped.endswith(":") and stripped != "paths:":
            in_paths = False
            key = stripped[:-1].strip()
            values[key] = ""
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if in_paths or key in DEFAULT_PATHS or key == "version":
            values[key] = value
    return values


def load_workspace_config(root: Path) -> dict[str, str]:
    """Return path mappings relative to *root*, filling in defaults."""
    parsed: dict[str, str] = {}
    for name in CONFIG_NAMES:
        config = root / name
        if config.is_file():
            parsed = _parse_simple_yaml(config.read_text(encoding="utf-8"))
            break
    paths = dict(DEFAULT_PATHS)
    for key in DEFAULT_PATHS:
        if parsed.get(key):
            paths[key] = parsed[key]
    return paths


def find_workspace_root(start: Path | None = None) -> Path:
    """Walk up from *start* (default: cwd) until a workspace is found."""
    override = os.environ.get("WORKSPACE_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if root.is_dir():
            return root
        raise WorkspaceNotFoundError(f"WORKSPACE_ROOT is not a directory: {root}")

    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if any((candidate / name).is_file() for name in CONFIG_NAMES):
            return candidate
        if (candidate / "preferences.md").is_file() and (candidate / "recipes").is_dir():
            return candidate
    raise WorkspaceNotFoundError(
        "Could not locate a meal-planning workspace. "
        "Expected workspace.yaml (or preferences.md + recipes/) above "
        f"{start}. Run onboarding or set WORKSPACE_ROOT."
    )


def workspace_paths(root: Path | None = None) -> dict[str, Path]:
    """Absolute paths for the conventional workspace files and directories."""
    root = root or find_workspace_root()
    relative = load_workspace_config(root)
    return {key: (root / rel) for key, rel in relative.items()}


def _normalize_markdown(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def preferences_need_interview(preferences_path: Path) -> bool:
    """True when preferences.md is missing, empty, or still the stock template."""
    if not preferences_path.is_file():
        return True
    user = _normalize_markdown(preferences_path.read_text(encoding="utf-8"))
    if not user:
        return True
    template = toolkit_root() / "templates" / "preferences.md"
    if template.is_file():
        stock = _normalize_markdown(template.read_text(encoding="utf-8"))
        if user == stock:
            return True
    return False


def onboarding_complete(root: Path | None = None) -> bool:
    """True when the user has been interviewed (preferences are not stock).

    Missing ``recipes/`` still counts as incomplete. A stock copy of
    ``templates/preferences.md`` does not count as onboarded, so ``--init``
    alone does not skip the interview.
    """
    try:
        paths = workspace_paths(root)
    except WorkspaceNotFoundError:
        return False
    if not paths["recipes"].is_dir():
        return False
    return not preferences_need_interview(paths["preferences"])


def workspace_initialized(root: Path | None = None) -> bool:
    """True when every contract path exists (files and directories)."""
    try:
        root = root or find_workspace_root()
    except WorkspaceNotFoundError:
        return False
    if not any((root / name).is_file() for name in CONFIG_NAMES):
        return False
    paths = workspace_paths(root)
    for key, path in paths.items():
        if key in DIRECTORY_KEYS:
            if not path.is_dir():
                return False
        elif not path.is_file():
            return False
    mappings = paths["shopping"] / "product-mappings.md"
    return mappings.is_file()


def _copy_if_missing(source: Path, dest: Path) -> str:
    """Copy *source* to *dest* when the destination does not exist.

    Returns ``created`` or ``exists``. Never overwrites an existing file.
    """
    if dest.exists():
        return "exists"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return "created"


def init_workspace(root: Path, *, templates_dir: Path | None = None) -> dict[str, str]:
    """Create missing default-layout files. Never overwrite existing user files.

    Returns a mapping of logical name → ``created`` or ``exists``.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise WorkspaceInitError(f"Workspace root is not a directory: {root}")
    if path_is_inside_toolkit(root):
        raise WorkspaceInitError(
            "Refusing to initialize a directory inside the toolkit package "
            "as a private workspace. Run --init from the parent workspace, "
            "or pass --root."
        )

    templates_dir = Path(templates_dir) if templates_dir else toolkit_root() / "templates"
    actions: dict[str, str] = {}

    config_exists = any((root / name).is_file() for name in CONFIG_NAMES)
    default_config = templates_dir / "workspace.yaml"
    if not config_exists:
        actions["workspace.yaml"] = _copy_if_missing(default_config, root / "workspace.yaml")
    else:
        actions["workspace.yaml"] = "exists"

    relative = load_workspace_config(root)

    for key in DIRECTORY_KEYS:
        path = root / relative[key]
        if path.is_dir():
            actions[key] = "exists"
        elif path.exists():
            actions[key] = "exists"
        else:
            path.mkdir(parents=True, exist_ok=True)
            actions[key] = "created"

    for key, template_name in FILE_TEMPLATES.items():
        dest = root / relative[key]
        actions[key] = _copy_if_missing(templates_dir / template_name, dest)

    mappings = (root / relative["shopping"]) / "product-mappings.md"
    actions["product-mappings"] = _copy_if_missing(
        templates_dir / "product-mappings.md",
        mappings,
    )

    for key, template_name in DIRECTORY_README_TEMPLATES.items():
        dest = (root / relative[key]) / "README.md"
        actions[f"{key}-readme"] = _copy_if_missing(
            templates_dir / template_name,
            dest,
        )
    return actions


def resolve_init_root(explicit: Path | None = None) -> Path:
    """Root to initialize: ``--root``, ``WORKSPACE_ROOT``, discovered, or cwd."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    override = os.environ.get("WORKSPACE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    try:
        return find_workspace_root()
    except WorkspaceNotFoundError:
        return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the meal-planning workspace root")
    parser.add_argument("--paths", action="store_true", help="Print resolved path mappings")
    parser.add_argument(
        "--check-onboarding",
        action="store_true",
        help="Exit 0 if the onboarding interview has been completed, 1 otherwise",
    )
    parser.add_argument(
        "--check-initialized",
        action="store_true",
        help="Exit 0 if every contract path exists, 1 otherwise",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Create missing workspace files from templates (never overwrites)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Workspace root for --init (default: discovered root or cwd)",
    )
    args = parser.parse_args(argv)

    if args.init:
        try:
            root = resolve_init_root(args.root)
            actions = init_workspace(root)
        except WorkspaceInitError as exc:
            print(exc, file=sys.stderr)
            return 1
        for key, status in actions.items():
            print(f"{key}\t{status}")
        return 0

    try:
        root = find_workspace_root()
    except WorkspaceNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.check_initialized:
        return 0 if workspace_initialized(root) else 1
    if args.check_onboarding:
        return 0 if onboarding_complete(root) else 1
    if args.paths:
        for key, path in workspace_paths(root).items():
            print(f"{key}\t{path}")
        return 0
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
