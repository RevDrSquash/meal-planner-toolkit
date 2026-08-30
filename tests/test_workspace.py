"""Workspace discovery tests — synthetic fixture only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from workspace import (
    WorkspaceNotFoundError,
    find_workspace_root,
    onboarding_complete,
    workspace_paths,
)


class WorkspaceDiscoveryTests(unittest.TestCase):
    def test_finds_workspace_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "workspace.yaml").write_text(
                "version: 2\npaths:\n  recipes: recipes\n",
                encoding="utf-8",
            )
            (root / "recipes").mkdir()
            (root / "nested").mkdir()
            found = find_workspace_root(root / "nested")
            self.assertEqual(found, root)

    def test_finds_conventional_layout_without_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "preferences.md").write_text("# prefs\n", encoding="utf-8")
            (root / "recipes").mkdir()
            found = find_workspace_root(root)
            self.assertEqual(found, root)
            self.assertTrue(onboarding_complete(root))

    def test_missing_workspace_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(WorkspaceNotFoundError):
                find_workspace_root(Path(raw))

    def test_custom_recipe_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "workspace.yaml").write_text(
                "version: 2\npaths:\n  recipes: meals/cards\n",
                encoding="utf-8",
            )
            paths = workspace_paths(root)
            self.assertEqual(paths["recipes"], root / "meals" / "cards")


if __name__ == "__main__":
    unittest.main()
