#!/usr/bin/env python3
"""Locate the private meal-planning workspace from any toolkit script.

The toolkit is application code. User state lives in a parent workspace that
embeds this package (usually as a Git submodule). Scripts must never treat
the toolkit directory itself as the workspace.

Discovery order, starting at cwd (or an explicit start path) and walking up:

1. A ``workspace.yaml`` or ``meal-planner.yaml`` file.
2. The conventional pair ``preferences.md`` + ``recipes/``.

Optional ``WORKSPACE_ROOT`` overrides discovery.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

CONFIG_NAMES = ("workspace.yaml", "meal-planner.yaml")

DEFAULT_PATHS = {
    "preferences": "preferences.md",
    "staples": "staples.md",
    "pantry": "pantry.md",
    "recipes": "recipes",
    "plans": "plans",
    "shopping": "shopping",
}

REQUIRED_ONBOARDING = ("preferences", "recipes")


class WorkspaceNotFoundError(FileNotFoundError):
    """Raised when no meal-planning workspace can be located."""


def toolkit_root() -> Path:
    """Return this toolkit package root (the directory that contains SKILL.md)."""
    return Path(__file__).resolve().parent.parent


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


def onboarding_complete(root: Path | None = None) -> bool:
    """True when the minimum onboarding files/dirs exist."""
    try:
        paths = workspace_paths(root)
    except WorkspaceNotFoundError:
        return False
    for key in REQUIRED_ONBOARDING:
        path = paths[key]
        if key in {"recipes", "plans", "shopping"}:
            if not path.is_dir():
                return False
        elif not path.is_file():
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the meal-planning workspace root")
    parser.add_argument("--paths", action="store_true", help="Print resolved path mappings")
    parser.add_argument(
        "--check-onboarding",
        action="store_true",
        help="Exit 0 if onboarding is complete, 1 otherwise",
    )
    args = parser.parse_args(argv)
    try:
        root = find_workspace_root()
    except WorkspaceNotFoundError as exc:
        print(exc, file=__import__("sys").stderr)
        return 1
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
