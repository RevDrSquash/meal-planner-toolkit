"""PC Express integration tests — configuration and tool catalog only.

No network, no credentials, no store or order data.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from pcexpress import (  # noqa: E402
    ALL_TOOLS,
    CART_MUTATING_TOOLS,
    OBSOLETE_ENV,
    READ_ONLY_TOOLS,
    REQUIRED_ENV,
    REVIEWED_COMMIT,
    SERVER_SCRIPT,
    UPSTREAM_URL,
    VENDOR_RELATIVE,
    ConfigStatus,
    apply_serve_env,
    check_config,
    ensure_workspace_secret_gitignore,
    gitignore_covers,
    ignore_pattern_for_state_dir,
    load_env_file,
    main,
    missing_workspace_gitignore_patterns,
    parse_cli,
    plan_serve,
    serve,
    tool_side_effect,
    tools_declared_in_server_source,
)


class PinAndCatalogTests(unittest.TestCase):
    def test_reviewed_commit_is_full_sha(self) -> None:
        self.assertRegex(REVIEWED_COMMIT, r"^[0-9a-f]{40}$")
        self.assertIn("FireBall1725/pcexpress-mcp-server", UPSTREAM_URL)

    def test_docs_record_the_same_pin(self) -> None:
        docs = (ROOT / "references" / "pcexpress.md").read_text(encoding="utf-8")
        self.assertIn(REVIEWED_COMMIT, docs)
        self.assertIn("FireBall1725/pcexpress-mcp-server", docs)

    def test_tool_catalog_covers_current_surface(self) -> None:
        expected = {
            "search_products",
            "get_product_details",
            "search_past_orders",
            "get_order_items",
            "view_cart",
            "add_to_cart",
            "remove_from_cart",
        }
        self.assertEqual(set(ALL_TOOLS), expected)
        self.assertEqual(set(READ_ONLY_TOOLS) | set(CART_MUTATING_TOOLS), expected)
        self.assertFalse(set(READ_ONLY_TOOLS) & set(CART_MUTATING_TOOLS))

    def test_side_effects(self) -> None:
        for name in READ_ONLY_TOOLS:
            self.assertEqual(tool_side_effect(name), "read")
        for name in CART_MUTATING_TOOLS:
            self.assertEqual(tool_side_effect(name), "write")
        with self.assertRaises(KeyError):
            tool_side_effect("checkout")

    def test_search_products_is_authenticated(self) -> None:
        """Open question from DEF-60: search is no longer an unauthenticated path.

        Current upstream posts to token-authenticated pcx-bff /products/search.
        CI must not call that network. Authenticated checks stay manual.
        """
        docs = (ROOT / "references" / "pcexpress.md").read_text(encoding="utf-8")
        self.assertIn("search_products", READ_ONLY_TOOLS)
        self.assertIn("token-authenticated", docs)
        self.assertIn("manual", docs.lower())

    def test_parses_upstream_tool_registrations(self) -> None:
        source = """
        return [
            Tool(
                name="search_products",
                description="Search",
            ),
            Tool(name="add_to_cart", description="Write"),
        ]
        """
        self.assertEqual(
            tools_declared_in_server_source(source),
            ("search_products", "add_to_cart"),
        )


class EnvAndConfigTests(unittest.TestCase):
    def test_load_env_file_ignores_comments_and_preserves_existing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / ".env"
            path.write_text(
                "# comment\n"
                "export PCEXPRESS_BANNER=superstore\n"
                "PCEXPRESS_STORE_ID='0545'\n"
                "PCEXPRESS_REFRESH_TOKEN=placeholder\n",
                encoding="utf-8",
            )
            dest = {"PCEXPRESS_BANNER": "already-set"}
            loaded = load_env_file(path, environ=dest, override=False)
            self.assertEqual(loaded["PCEXPRESS_BANNER"], "superstore")
            self.assertEqual(dest["PCEXPRESS_BANNER"], "already-set")
            self.assertEqual(dest["PCEXPRESS_STORE_ID"], "0545")

    def _workspace(self, root: Path, *, vendor: bool = True, env: dict[str, str] | None = None) -> Path:
        (root / "workspace.yaml").write_text("version: 2\n", encoding="utf-8")
        (root / "recipes").mkdir()
        if vendor:
            script = root / VENDOR_RELATIVE / SERVER_SCRIPT
            script.parent.mkdir(parents=True)
            script.write_text("# fake vendored server\n", encoding="utf-8")
        if env is not None:
            lines = [f"{key}={value}" for key, value in env.items()]
            (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return root

    def test_check_config_reports_missing_vendor_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=False, env=None)
            status = check_config(root)
            self.assertIsInstance(status, ConfigStatus)
            self.assertFalse(status.ok)
            self.assertFalse(status.vendor_ok)
            self.assertEqual(status.missing_env, REQUIRED_ENV)
            self.assertTrue(any("Vendored server not found" in err for err in status.errors))

    def test_check_config_accepts_complete_synthetic_workspace(self) -> None:
        env = {
            "PCEXPRESS_REFRESH_TOKEN": "synthetic-refresh-token",
            "PCEXPRESS_BANNER": "superstore",
            "PCEXPRESS_STORE_ID": "0545",
            "PCEXPRESS_STATE_DIR": "/tmp/synthetic-pcexpress-state",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=env)
            status = check_config(root)
            self.assertEqual(status.errors, ())
            self.assertTrue(status.ok)
            self.assertTrue(status.vendor_ok)
            self.assertTrue(status.server_ok)
            self.assertEqual(status.missing_env, ())
            self.assertEqual(status.obsolete_env, ())

    def test_check_config_flags_obsolete_bearer_keys(self) -> None:
        env = {
            "PCEXPRESS_REFRESH_TOKEN": "synthetic-refresh-token",
            "PCEXPRESS_BANNER": "superstore",
            "PCEXPRESS_STORE_ID": "0545",
            "PCEXPRESS_BEARER_TOKEN": "eyJ-old-style",
            "PCEXPRESS_CUSTOMER_ID": "not-a-real-customer",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=env)
            status = check_config(root)
            self.assertTrue(status.ok)
            self.assertEqual(status.obsolete_env, OBSOLETE_ENV)
            self.assertTrue(any("Obsolete credentials" in w for w in status.warnings))

    def test_placeholders_count_as_missing(self) -> None:
        env = {
            "PCEXPRESS_REFRESH_TOKEN": "your_refresh_token_here",
            "PCEXPRESS_BANNER": "superstore",
            "PCEXPRESS_STORE_ID": "your_store_id_here",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=env)
            status = check_config(root)
            self.assertFalse(status.ok)
            self.assertIn("PCEXPRESS_REFRESH_TOKEN", status.missing_env)
            self.assertIn("PCEXPRESS_STORE_ID", status.missing_env)

    def test_plan_serve_defaults_state_dir_and_forwards_args(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=None)
            plan = plan_serve(root, extra_args=["--http"])
            self.assertEqual(plan.workspace, root)
            self.assertEqual(plan.script, root / VENDOR_RELATIVE / SERVER_SCRIPT)
            self.assertIsNone(plan.env_file)
            self.assertEqual(plan.state_dir, root / ".pcexpress-mcp")
            self.assertEqual(plan.extra_args, ("--http",))
            dest: dict[str, str] = {}
            apply_serve_env(plan, environ=dest)
            self.assertEqual(dest["PCEXPRESS_STATE_DIR"], str(plan.state_dir))

    def test_plan_serve_requires_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=False)
            with self.assertRaises(FileNotFoundError) as ctx:
                plan_serve(root)
            self.assertIn("git submodule add", str(ctx.exception))
            self.assertIn(REVIEWED_COMMIT, str(ctx.exception))

    def test_cli_pin_and_tools(self) -> None:
        self.assertEqual(main(["--pin"]), 0)
        self.assertEqual(main(["--tools"]), 0)

    def test_cli_serve_forwards_flag_style_extras(self) -> None:
        args, extra = parse_cli(["--serve", "--http"])
        self.assertTrue(args.serve)
        self.assertEqual(extra, ["--http"])
        args, extra = parse_cli(["--serve", "--http", "--port", "8080"])
        self.assertEqual(extra, ["--http", "--port", "8080"])
        args, extra = parse_cli(["--serve", "--", "--http"])
        self.assertEqual(extra, ["--http"])
        with mock.patch("pcexpress.serve", return_value=0) as serve:
            self.assertEqual(main(["--serve", "--http"]), 0)
            serve.assert_called_once_with(["--http"])

    def test_cli_non_serve_rejects_unknown_flags(self) -> None:
        with self.assertRaises(SystemExit):
            parse_cli(["--pin", "--http"])
        with self.assertRaises(SystemExit):
            parse_cli(["--check", "--http"])

    def test_gitignore_covers_exact_and_slash_variants(self) -> None:
        text = ".env\n.pcexpress-mcp/\n"
        self.assertTrue(gitignore_covers(text, ".env"))
        self.assertTrue(gitignore_covers(text, ".pcexpress-mcp/"))
        self.assertTrue(gitignore_covers(".pcexpress-mcp\n", ".pcexpress-mcp/"))
        self.assertTrue(gitignore_covers("/.pcexpress-mcp\n", ".pcexpress-mcp/"))
        self.assertFalse(gitignore_covers(".env\n", ".pcexpress-mcp/"))
        self.assertFalse(gitignore_covers("!.pcexpress-mcp/\n", ".pcexpress-mcp/"))
        self.assertFalse(gitignore_covers(".env # never commit\n", ".env"))
        self.assertFalse(
            gitignore_covers(".pcexpress-mcp/ # tokens\n", ".pcexpress-mcp/")
        )
        self.assertTrue(gitignore_covers("# .env is below\n.env\n", ".env"))
        self.assertFalse(gitignore_covers("  .env\n", ".env"))
        self.assertFalse(gitignore_covers(".env/\n", ".env"))
        self.assertFalse(gitignore_covers("/.env/\n", ".env"))
        self.assertFalse(gitignore_covers("  .pcexpress-mcp/\n", ".pcexpress-mcp/"))

    def test_missing_gitignore_detects_workspace_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=None)
            missing = missing_workspace_gitignore_patterns(
                root, root / ".pcexpress-mcp"
            )
            self.assertIn(".env", missing)
            self.assertIn(".pcexpress-mcp/", missing)
            (root / ".gitignore").write_text(".env\n.pcexpress-mcp/\n", encoding="utf-8")
            self.assertEqual(
                missing_workspace_gitignore_patterns(root, root / ".pcexpress-mcp"),
                (),
            )

    def test_state_dir_outside_workspace_is_not_a_gitignore_gap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace"
            outside = Path(raw) / "tokens"
            root.mkdir()
            self.assertIsNone(ignore_pattern_for_state_dir(root, outside))
            missing = missing_workspace_gitignore_patterns(root, outside)
            self.assertEqual(missing, (".env",))

    def test_ensure_gitignore_appends_missing_secret_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=None)
            (root / ".gitignore").write_text("# keep me\n*.pyc\n", encoding="utf-8")
            added = ensure_workspace_secret_gitignore(root, root / ".pcexpress-mcp")
            self.assertEqual(added, (".env", ".pcexpress-mcp/"))
            text = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("# keep me", text)
            self.assertIn(".env", text)
            self.assertIn(".pcexpress-mcp/", text)
            self.assertEqual(
                ensure_workspace_secret_gitignore(root, root / ".pcexpress-mcp"),
                (),
            )

    def test_inline_hash_remark_does_not_count_as_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=None)
            (root / ".gitignore").write_text(
                ".env # never commit this\n.pcexpress-mcp/ # tokens\n",
                encoding="utf-8",
            )
            missing = missing_workspace_gitignore_patterns(
                root, root / ".pcexpress-mcp"
            )
            self.assertEqual(missing, (".env", ".pcexpress-mcp/"))
            added = ensure_workspace_secret_gitignore(root, root / ".pcexpress-mcp")
            self.assertEqual(added, (".env", ".pcexpress-mcp/"))
            text = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("\n.env\n", f"\n{text}")
            self.assertTrue(gitignore_covers(text, ".env"))
            self.assertTrue(gitignore_covers(text, ".pcexpress-mcp/"))

    def test_indented_or_directory_only_env_does_not_count_as_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=None)
            (root / ".gitignore").write_text(
                "  .env\n.env/\n.pcexpress-mcp/\n",
                encoding="utf-8",
            )
            missing = missing_workspace_gitignore_patterns(
                root, root / ".pcexpress-mcp"
            )
            self.assertEqual(missing, (".env",))
            added = ensure_workspace_secret_gitignore(root, root / ".pcexpress-mcp")
            self.assertEqual(added, (".env",))
            text = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("\n.env\n", f"\n{text}")
            self.assertTrue(gitignore_covers(text, ".env"))

    def test_check_config_warns_when_workspace_gitignore_omits_secrets(self) -> None:
        env = {
            "PCEXPRESS_REFRESH_TOKEN": "synthetic-refresh-token",
            "PCEXPRESS_BANNER": "superstore",
            "PCEXPRESS_STORE_ID": "0545",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw), vendor=True, env=env)
            status = check_config(root)
            self.assertTrue(status.ok)
            self.assertIn(".pcexpress-mcp/", status.missing_gitignore)
            self.assertTrue(any("gitignore" in w.lower() for w in status.warnings))
            (root / ".gitignore").write_text(".env\n.pcexpress-mcp/\n", encoding="utf-8")
            status = check_config(root)
            self.assertEqual(status.missing_gitignore, ())


class ExampleAndTemplateTests(unittest.TestCase):
    def test_env_example_uses_refresh_token_not_bearer(self) -> None:
        text = (ROOT / "templates" / "env.example").read_text(encoding="utf-8")
        for key in REQUIRED_ENV:
            self.assertIn(key, text)
        self.assertIn("PCEXPRESS_STATE_DIR", text)
        self.assertIn("workspace", text.lower())
        self.assertIn("gitignore.example", text)
        for key in OBSOLETE_ENV:
            self.assertNotIn(key, text)
        self.assertNotRegex(text, r"eyJ[A-Za-z0-9_-]{10,}")

    def test_workspace_gitignore_template_covers_token_state(self) -> None:
        text = (ROOT / "templates" / "gitignore.example").read_text(encoding="utf-8")
        self.assertIn(".env", text)
        self.assertIn(".pcexpress-mcp/", text)
        self.assertIn("does not apply", text)
        self.assertNotRegex(text, r"eyJ[A-Za-z0-9_-]{10,}")
        docs = (ROOT / "references" / "pcexpress.md").read_text(encoding="utf-8")
        self.assertIn("gitignore.example", docs)
        self.assertIn("workspace-root", docs)
        self.assertIn("--http", docs)

    def test_mcp_examples_launch_toolkit_serve(self) -> None:
        for name in ("mcp.json", "cursor.mcp.json"):
            path = ROOT / "examples" / "mcp" / name
            data = json.loads(path.read_text(encoding="utf-8"))
            args = data["mcpServers"]["pcexpress"]["args"]
            self.assertTrue(any(str(item).endswith("scripts/pcexpress.py") for item in args))
            self.assertIn("--serve", args)
            self.assertFalse(any("run_server.py" in str(item) for item in args))
            dumped = json.dumps(data)
            self.assertNotIn("PCEXPRESS_REFRESH_TOKEN", dumped)
            self.assertNotIn("PCEXPRESS_BEARER_TOKEN", dumped)

    def test_obsolete_scripts_are_gone(self) -> None:
        scripts = ROOT / "scripts"
        self.assertFalse((scripts / "run_server.py").exists())
        self.assertFalse((scripts / "refresh_token.py").exists())
        self.assertTrue((scripts / "pcexpress.py").is_file())

    def test_requirements_do_not_pull_playwright(self) -> None:
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("playwright", text)
        self.assertNotIn("python-dotenv", text)

    def test_synthetic_example_workspace_has_no_provider_secrets(self) -> None:
        example = ROOT / "examples" / "workspace"
        self.assertFalse((example / ".env").exists())
        prefs = (example / "preferences.md").read_text(encoding="utf-8")
        self.assertIn("Grocery provider integration: none", prefs)
        mappings = (example / "shopping" / "product-mappings.md").read_text(encoding="utf-8")
        self.assertIn("Synthetic example", mappings)
        self.assertNotIn("PCEXPRESS_", mappings)


class ServeFailureTests(unittest.TestCase):
    def _workspace(self, root: Path, *, vendor: bool = True) -> Path:
        (root / "workspace.yaml").write_text("version: 2\n", encoding="utf-8")
        (root / "recipes").mkdir()
        if vendor:
            script = root / VENDOR_RELATIVE / SERVER_SCRIPT
            script.parent.mkdir(parents=True)
            script.write_text("# fake vendored server\n", encoding="utf-8")
        return root

    def test_serve_appends_gitignore_and_forwards_http(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self._workspace(Path(raw))
            with mock.patch("pcexpress.find_workspace_root", return_value=root):
                with mock.patch("pcexpress.os.execv") as execv:
                    with mock.patch("pcexpress.os.chdir"):
                        serve(["--http"])
            gitignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(".env", gitignore)
            self.assertIn(".pcexpress-mcp/", gitignore)
            execv.assert_called_once()
            launched = execv.call_args[0][1]
            self.assertEqual(launched[0], sys.executable)
            self.assertEqual(launched[-1], "--http")
            self.assertTrue(str(launched[1]).endswith(SERVER_SCRIPT))

    def test_serve_returns_error_without_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            env = os.environ.copy()
            env.pop("WORKSPACE_ROOT", None)
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("pcexpress.find_workspace_root") as find:
                    from workspace import WorkspaceNotFoundError

                    find.side_effect = WorkspaceNotFoundError("no workspace")
                    self.assertEqual(main(["--serve"]), 1)


if __name__ == "__main__":
    unittest.main()
