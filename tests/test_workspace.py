"""Workspace discovery, init, and onboarding detection — synthetic fixtures only."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from workspace import (  # noqa: E402
    WorkspaceInitError,
    WorkspaceNotFoundError,
    find_workspace_root,
    init_workspace,
    initialization_gaps,
    main,
    onboarding_complete,
    onboarding_gaps,
    path_is_inside_toolkit,
    preferences_need_interview,
    resolve_init_root,
    toolkit_root,
    workspace_initialized,
    workspace_paths,
)

INITIALIZED_FIXTURE = ROOT / "examples" / "workspace"
UNINITIALIZED_FIXTURE = ROOT / "tests" / "fixtures" / "workspace-uninitialized"


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
            (root / "preferences.md").write_text(
                "# prefs\n\n- Number of people: 2\n",
                encoding="utf-8",
            )
            (root / "recipes").mkdir()
            found = find_workspace_root(root)
            self.assertEqual(found, root)
            self.assertFalse(workspace_initialized(root))
            # Discovery succeeds, but preferences + recipes/ alone are no
            # longer onboarding: staples.md and pantry.md must exist too,
            # or the first shopping list bills the household for salt.
            self.assertFalse(onboarding_complete(root))
            self.assertEqual(
                onboarding_gaps(root),
                ["staples.md is missing", "pantry.md is missing"],
            )
            for name in ("staples.md", "pantry.md"):
                (root / name).write_text(f"# {name}\n", encoding="utf-8")
            self.assertTrue(onboarding_complete(root))

    def test_missing_workspace_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(WorkspaceNotFoundError):
                find_workspace_root(Path(raw))

    def test_custom_recipe_and_tools_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "workspace.yaml").write_text(
                "version: 2\npaths:\n  recipes: meals/cards\n  tools: kitchen.md\n",
                encoding="utf-8",
            )
            paths = workspace_paths(root)
            self.assertEqual(paths["recipes"], root / "meals" / "cards")
            self.assertEqual(paths["tools"], root / "kitchen.md")
            self.assertEqual(paths["pantry"], root / "pantry.md")


class OnboardingDetectionTests(unittest.TestCase):
    def test_uninitialized_fixture_is_not_ready(self) -> None:
        # Deliberately does not assert find_workspace_root() raises here.
        # That function answers "which workspace encloses this path", not
        # "is this path a workspace", so once the toolkit is vendored into a
        # real workspace the walk up from this fixture finds the enclosing
        # workspace.yaml and returns it. test_missing_workspace_raises covers
        # the raising behaviour from a temp dir with no workspace above it.
        self.assertTrue(UNINITIALIZED_FIXTURE.is_dir())
        self.assertFalse(onboarding_complete(UNINITIALIZED_FIXTURE))
        self.assertFalse(workspace_initialized(UNINITIALIZED_FIXTURE))

    def test_initialized_example_workspace(self) -> None:
        self.assertTrue(INITIALIZED_FIXTURE.is_dir())
        self.assertEqual(find_workspace_root(INITIALIZED_FIXTURE), INITIALIZED_FIXTURE)
        self.assertTrue(workspace_initialized(INITIALIZED_FIXTURE))
        self.assertTrue(onboarding_complete(INITIALIZED_FIXTURE))
        paths = workspace_paths(INITIALIZED_FIXTURE)
        self.assertTrue(paths["tools"].is_file())
        self.assertTrue(paths["plans"].is_dir())
        self.assertTrue((paths["shopping"] / "product-mappings.md").is_file())

    def test_stock_preferences_are_not_onboarded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            actions = init_workspace(root)
            self.assertEqual(actions["preferences"], "created")
            self.assertTrue(workspace_initialized(root))
            self.assertFalse(onboarding_complete(root))
            self.assertTrue(preferences_need_interview(root / "preferences.md"))

    def test_empty_preferences_need_interview(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "preferences.md"
            path.write_text("\n", encoding="utf-8")
            self.assertTrue(preferences_need_interview(path))


class InitWorkspaceTests(unittest.TestCase):
    def test_init_creates_expected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            actions = init_workspace(root)
            expected_created = {
                "workspace.yaml",
                "preferences",
                "staples",
                "pantry",
                "tools",
                "recipes",
                "plans",
                "shopping",
                "product-mappings",
                "recipes-readme",
                "plans-readme",
            }
            self.assertEqual(set(actions), expected_created)
            self.assertTrue(all(status == "created" for status in actions.values()))
            self.assertTrue(workspace_initialized(root))
            self.assertTrue((root / "workspace.yaml").is_file())
            self.assertTrue((root / "preferences.md").is_file())
            self.assertTrue((root / "staples.md").is_file())
            self.assertTrue((root / "pantry.md").is_file())
            self.assertTrue((root / "tools.md").is_file())
            self.assertTrue((root / "recipes").is_dir())
            self.assertTrue((root / "plans").is_dir())
            self.assertTrue((root / "shopping").is_dir())
            self.assertTrue((root / "shopping" / "product-mappings.md").is_file())
            self.assertTrue((root / "recipes" / "README.md").is_file())
            self.assertTrue((root / "plans" / "README.md").is_file())
            recipes_readme = (root / "recipes" / "README.md").read_text(encoding="utf-8")
            self.assertIn("keeps the directory in Git", recipes_readme)
            tools = (root / "tools.md").read_text(encoding="utf-8")
            self.assertIn("Notable exceptions", tools)

    def test_init_does_not_overwrite_existing_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "preferences.md").write_text(
                "# Household prefs\n\n- Number of people: 4\n",
                encoding="utf-8",
            )
            (root / "staples.md").write_text("# My staples\n- Oats\n", encoding="utf-8")
            (root / "recipes").mkdir()
            actions = init_workspace(root)
            self.assertEqual(actions["preferences"], "exists")
            self.assertEqual(actions["staples"], "exists")
            self.assertEqual(actions["tools"], "created")
            self.assertEqual(actions["recipes"], "exists")
            self.assertEqual(
                (root / "preferences.md").read_text(encoding="utf-8"),
                "# Household prefs\n\n- Number of people: 4\n",
            )
            self.assertEqual(
                (root / "staples.md").read_text(encoding="utf-8"),
                "# My staples\n- Oats\n",
            )
            self.assertTrue((root / "tools.md").is_file())
            self.assertTrue(onboarding_complete(root))
            self.assertEqual(actions["recipes-readme"], "created")
            self.assertTrue((root / "recipes" / "README.md").is_file())

    def test_init_does_not_overwrite_existing_directory_readmes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "recipes").mkdir()
            (root / "recipes" / "README.md").write_text("# Keep my recipes\n", encoding="utf-8")
            actions = init_workspace(root)
            self.assertEqual(actions["recipes-readme"], "exists")
            self.assertEqual(
                (root / "recipes" / "README.md").read_text(encoding="utf-8"),
                "# Keep my recipes\n",
            )
            self.assertEqual(actions["plans-readme"], "created")
            self.assertTrue((root / "plans" / "README.md").is_file())

    def test_init_respects_custom_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "workspace.yaml").write_text(
                "version: 2\npaths:\n  tools: kitchen-notes.md\n  recipes: meals\n",
                encoding="utf-8",
            )
            init_workspace(root)
            self.assertTrue((root / "kitchen-notes.md").is_file())
            self.assertFalse((root / "tools.md").exists())
            self.assertTrue((root / "meals").is_dir())
            self.assertTrue((root / "meals" / "README.md").is_file())
            self.assertEqual(workspace_paths(root)["tools"], root / "kitchen-notes.md")

    def test_init_refuses_toolkit_root(self) -> None:
        with self.assertRaises(WorkspaceInitError):
            init_workspace(toolkit_root())

    def test_init_refuses_toolkit_subdirectories(self) -> None:
        for rel in ("scripts", "examples", "tests", "templates"):
            with self.subTest(rel=rel):
                self.assertTrue(path_is_inside_toolkit(toolkit_root() / rel))
                with self.assertRaises(WorkspaceInitError):
                    init_workspace(toolkit_root() / rel)

    def test_path_is_inside_toolkit_excludes_sibling_dirs(self) -> None:
        sibling = toolkit_root().parent / f"{toolkit_root().name}-not-toolkit"
        self.assertFalse(path_is_inside_toolkit(sibling))
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(path_is_inside_toolkit(Path(raw)))

    def test_resolve_init_root_cwd_inside_toolkit_is_caught_by_init(self) -> None:
        scripts_dir = toolkit_root() / "scripts"
        env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "workspace.find_workspace_root",
                side_effect=WorkspaceNotFoundError("missing"),
            ):
                with patch("workspace.Path.cwd", return_value=scripts_dir):
                    root = resolve_init_root()
        self.assertEqual(root, scripts_dir)
        with self.assertRaises(WorkspaceInitError):
            init_workspace(root)

    def test_cli_init_refuses_cwd_inside_toolkit_when_undiscovered(self) -> None:
        scripts_dir = toolkit_root() / "scripts"
        env = {k: v for k, v in os.environ.items() if k != "WORKSPACE_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "workspace.find_workspace_root",
                side_effect=WorkspaceNotFoundError("missing"),
            ):
                with patch("workspace.Path.cwd", return_value=scripts_dir):
                    self.assertEqual(main(["--init"]), 1)
        self.assertFalse((scripts_dir / "workspace.yaml").exists())
        self.assertFalse((scripts_dir / "preferences.md").exists())

    def test_cli_init_and_checks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.assertEqual(main(["--init", "--root", str(root)]), 0)
            with patch.dict(os.environ, {"WORKSPACE_ROOT": str(root)}):
                self.assertEqual(main(["--check-initialized"]), 0)
                self.assertEqual(main(["--check-onboarding"]), 1)
            prefs = root / "preferences.md"
            prefs.write_text(
                prefs.read_text(encoding="utf-8") + "\n- Number of people: 3\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"WORKSPACE_ROOT": str(root)}):
                self.assertEqual(main(["--check-onboarding"]), 0)

    def test_cli_example_fixture_is_complete(self) -> None:
        with patch.dict(os.environ, {"WORKSPACE_ROOT": str(INITIALIZED_FIXTURE)}):
            self.assertEqual(main(["--check-initialized"]), 0)
            self.assertEqual(main(["--check-onboarding"]), 0)
            self.assertEqual(main(["--check-initialized"]), 0)

    def test_cli_uninitialized_fixture(self) -> None:
        with patch.dict(os.environ, {"WORKSPACE_ROOT": str(UNINITIALIZED_FIXTURE)}):
            self.assertEqual(main(["--check-initialized"]), 1)
            self.assertEqual(main(["--check-onboarding"]), 1)


class OnboardingGapTests(unittest.TestCase):
    """A gate the caller can act on, not a bare exit code."""

    def _workspace(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "workspace.yaml").write_text("version: 2\n", encoding="utf-8")
        (root / "recipes").mkdir()
        for name in ("preferences.md", "staples.md", "pantry.md"):
            (root / name).write_text(f"# {name}\n\n- filled in\n", encoding="utf-8")
        return root

    def test_complete_workspace_has_no_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            self.assertEqual(onboarding_gaps(root), [])
            self.assertTrue(onboarding_complete(root))

    def test_missing_preferences_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            (root / "preferences.md").unlink()
            self.assertIn("preferences.md is missing", onboarding_gaps(root))
            self.assertFalse(onboarding_complete(root))

    def test_stock_preferences_are_reported_distinctly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            template = toolkit_root() / "templates" / "preferences.md"
            (root / "preferences.md").write_text(
                template.read_text(encoding="utf-8"), encoding="utf-8"
            )
            gaps = onboarding_gaps(root)
            self.assertIn(
                "preferences.md is empty or still the stock template", gaps
            )

    def test_staples_and_pantry_gate_on_existence(self) -> None:
        """Deleting either re-triggers onboarding; this is the case that was missed."""
        for name in ("staples.md", "pantry.md"):
            with self.subTest(missing=name):
                with tempfile.TemporaryDirectory() as raw:
                    root = self._workspace(Path(raw))
                    (root / name).unlink()
                    self.assertIn(f"{name} is missing", onboarding_gaps(root))
                    self.assertFalse(onboarding_complete(root))

    def test_gaps_do_not_inspect_staples_contents(self) -> None:
        """Existence only — contents accumulate during normal use."""
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            (root / "staples.md").write_text("", encoding="utf-8")
            self.assertNotIn("staples.md is missing", onboarding_gaps(root))

    def test_missing_recipes_directory_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            (root / "recipes").rmdir()
            self.assertIn("recipes/ directory is missing", onboarding_gaps(root))

    def test_initialization_gaps_name_each_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            gaps = initialization_gaps(root)
            self.assertIn("tools.md is missing", gaps)
            self.assertIn("plans/ directory is missing", gaps)

    def test_cli_check_onboarding_explains_and_says_stop(self) -> None:
        """The silent exit code is what let a bad workspace reach meal planning."""
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            (root / "preferences.md").unlink()
            (root / "staples.md").unlink()
            err = io.StringIO()
            with patch.dict(os.environ, {"WORKSPACE_ROOT": str(root)}):
                with redirect_stderr(err):
                    self.assertEqual(main(["--check-onboarding"]), 1)
            text = err.getvalue()
            self.assertIn("onboarding incomplete", text)
            self.assertIn("preferences.md is missing", text)
            self.assertIn("staples.md is missing", text)
            self.assertIn("STOP", text)
            self.assertIn("references/onboarding.md", text)

    def test_cli_check_initialized_explains_and_suggests_init(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            err = io.StringIO()
            with patch.dict(os.environ, {"WORKSPACE_ROOT": str(root)}):
                with redirect_stderr(err):
                    self.assertEqual(main(["--check-initialized"]), 1)
            text = err.getvalue()
            self.assertIn("workspace not initialized", text)
            self.assertIn("tools.md is missing", text)
            self.assertIn("--init", text)


class TemplateDefaultTests(unittest.TestCase):
    """Defaults ship populated so the first shopping list is not wrong."""

    def test_pantry_template_ships_basics(self) -> None:
        text = (toolkit_root() / "templates" / "pantry.md").read_text(
            encoding="utf-8"
        )
        for item in ("Salt", "Black pepper", "Cooking oil"):
            self.assertIn(item, text)

    def test_staples_template_ships_empty(self) -> None:
        """Deliberate: a guessed staple gets bought on every order."""
        text = (toolkit_root() / "templates" / "staples.md").read_text(
            encoding="utf-8"
        )
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertTrue(headings)
        entries = [
            line
            for line in text.splitlines()
            if line.startswith("- ") and line.strip() != "-"
        ]
        self.assertEqual(entries, [], f"staples.md must ship empty, found: {entries}")

    def test_pantry_template_has_a_home_for_canned_goods(self) -> None:
        """Without this category, canned items land under Dry goods."""
        text = (toolkit_root() / "templates" / "pantry.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Canned and jarred", text)

    def test_init_writes_populated_pantry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_workspace(root)
            text = (root / "pantry.md").read_text(encoding="utf-8")
            self.assertIn("Salt", text)
            self.assertIn("Cooking oil", text)


if __name__ == "__main__":
    unittest.main()
