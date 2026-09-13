"""Tests for headed PC Express login helper (no network, no browser)."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from pcexpress import gitignore_covers  # noqa: E402
from pcexpress_login import (  # noqa: E402
    AuthorizeError,
    code_from_url,
    find_store_ids,
    main,
    skeleton,
    store_id_from_home_store,
    upsert_env_value,
)
from workspace import WorkspaceNotFoundError  # noqa: E402


class UpsertEnvTests(unittest.TestCase):
    def test_replaces_in_place_and_preserves_comments(self) -> None:
        text = "# banner\nPCEXPRESS_BANNER=loblaws\nPCEXPRESS_STORE_ID=0545\n"
        out = upsert_env_value(text, "PCEXPRESS_REFRESH_TOKEN", "tok-abc")
        self.assertIn("# banner\n", out)
        self.assertIn("PCEXPRESS_BANNER=loblaws\n", out)
        self.assertIn("PCEXPRESS_REFRESH_TOKEN=tok-abc\n", out)

    def test_comments_duplicate_keys(self) -> None:
        text = "PCEXPRESS_STORE_ID=1111\nPCEXPRESS_STORE_ID=2222\n"
        out = upsert_env_value(text, "PCEXPRESS_STORE_ID", "3333")
        self.assertEqual(out.count("PCEXPRESS_STORE_ID=3333"), 1)
        self.assertIn("superseded by pcexpress_login.py", out)


class CodeFromUrlTests(unittest.TestCase):
    def test_direct_custom_scheme(self) -> None:
        url = "com.loblaw.pcx://callback?code=abc123&state=xyz"
        self.assertEqual(code_from_url(url), ("abc123", "xyz"))

    def test_redirect_url_wrapper(self) -> None:
        inner = "com.loblaw.pcx://callback?code=wrapped&state=s1"
        url = "https://login.example/success?redirectURL=" + urllib.parse.quote(
            inner, safe=""
        )
        self.assertEqual(code_from_url(url), ("wrapped", "s1"))

    def test_double_encoded_wrapper(self) -> None:
        # PCID sometimes percent-encodes the already-encoded app redirect.
        inner = "com.loblaw.pcx://callback?code=twice&state=s2"
        once = urllib.parse.quote(inner, safe="")
        twice = urllib.parse.quote(once, safe="")
        url = "https://login.example/success?redirectURL=" + twice
        self.assertEqual(code_from_url(url), ("twice", "s2"))

    def test_fully_encoded_url(self) -> None:
        # The whole redirect forwarded as one percent-encoded blob (no `?`).
        raw = "com.loblaw.pcx://callback?code=blob&state=s3"
        self.assertEqual(
            code_from_url(urllib.parse.quote(raw, safe="")), ("blob", "s3")
        )

    def test_no_code_returns_none(self) -> None:
        self.assertIsNone(code_from_url("https://accounts.example/login?foo=bar"))
        self.assertIsNone(code_from_url(""))

    def test_oauth_error_raises(self) -> None:
        with self.assertRaises(AuthorizeError):
            code_from_url("com.loblaw.pcx://cb?error=access_denied&error_description=nope")


class StoreDiscoveryTests(unittest.TestCase):
    def test_home_store_shapes(self) -> None:
        self.assertEqual(store_id_from_home_store(545), "0545")
        self.assertEqual(store_id_from_home_store("545"), "0545")
        self.assertEqual(store_id_from_home_store({"storeId": 1234}), "1234")

    def test_find_store_ids_in_cart(self) -> None:
        cart = {"lines": [{"fulfillmentStore": {"storeId": "0999"}}]}
        hits = find_store_ids(cart)
        self.assertTrue(any(sid == "0999" for _, sid in hits))

    def test_skeleton_redacts_nested_content(self) -> None:
        cart = {"address": "secret street", "storeId": "0545"}
        sk = skeleton(cart)
        self.assertEqual(sk["storeId"], "0545")
        self.assertNotIn("secret", str(sk))


TOKENS = {"refresh_token": "rtok", "access_token": "atok", "expires_in": 3600}


class MainTests(unittest.TestCase):
    """Drive main() end to end with the browser, vendor modules, and network mocked.

    Every test points the locator at a temp workspace via WORKSPACE_ROOT (the
    override find_workspace_root honours) rather than mocking the locator, so
    the script is exercised the way a user runs it. PCEXPRESS_* variables from
    the developer's shell are stripped so they cannot leak into assertions.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name) / "my-household"
        self.ws.mkdir()
        (self.ws / "workspace.yaml").write_text("version: 2\n", encoding="utf-8")
        (self.ws / "recipes").mkdir()

        self.manual = mock.Mock()
        self.manual.build_authorize_url.return_value = "https://auth.example/"
        self.manual.exchange_code.return_value = dict(TOKENS)
        self.cfg = mock.Mock(CLIENT_SECRET="secret")
        self.load_vendor = mock.Mock(return_value=(self.manual, self.cfg))

        clean_env = {
            k: v for k, v in os.environ.items() if not k.startswith("PCEXPRESS_")
        }
        clean_env["WORKSPACE_ROOT"] = str(self.ws)
        for patch in (
            mock.patch.dict("os.environ", clean_env, clear=True),
            mock.patch("pcexpress_login.load_vendor_modules", self.load_vendor),
            mock.patch("pcexpress_login.run_browser_login", return_value="code1"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def _tree_is_clean(self, root: Path) -> None:
        for name in (".env", ".env.tmp", ".pcexpress-mcp", ".browser-profile"):
            self.assertFalse((root / name).exists(), f"{name} created under {root}")

    def test_main_resolves_workspace_with_locator_not_script_location(self) -> None:
        rc, _ = self._run(["--no-write"])
        self.assertEqual(rc, 0)
        # The vendored server is looked up under the *workspace* the locator
        # returned, not under the toolkit (the old script used parents[1]).
        (vendor,), _ = self.load_vendor.call_args
        self.assertEqual(vendor, self.ws / "vendor" / "pcexpress-mcp-server")
        self.assertNotIn(ROOT, vendor.parents)

    def test_no_write_does_not_create_env_or_state(self) -> None:
        rc, out = self._run(["--no-write"])
        self.assertEqual(rc, 0)
        self._tree_is_clean(self.ws)
        self._tree_is_clean(ROOT)
        self.assertIn("PCEXPRESS_REFRESH_TOKEN=rtok", out)

    def test_write_targets_workspace_only(self) -> None:
        (self.ws / ".env").write_text(
            "# household\nPCEXPRESS_BANNER=superstore\n"
            "PCEXPRESS_STORE_ID=your_store_id_here\n",
            encoding="utf-8",
        )
        (self.ws / ".gitignore").write_text("*.pyc\n", encoding="utf-8")

        with mock.patch("pcexpress_login.discover_store_id", return_value="0545"):
            rc, out = self._run([])
        self.assertEqual(rc, 0)

        env_text = (self.ws / ".env").read_text(encoding="utf-8")
        self.assertIn("# household\n", env_text)
        self.assertIn("PCEXPRESS_BANNER=superstore\n", env_text)
        self.assertIn("PCEXPRESS_REFRESH_TOKEN=rtok\n", env_text)
        self.assertIn("PCEXPRESS_STORE_ID=0545\n", env_text)
        self.assertNotIn("your_store_id_here", env_text)
        self.assertNotIn("rtok", out, "refresh token must not be printed")

        state = json.loads(
            (self.ws / ".pcexpress-mcp" / "pcid_token_state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["refresh_token"], "rtok")
        self.assertEqual(state["access_token"], "atok")

        ignore = (self.ws / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.pyc\n", ignore)
        for pattern in (".env", ".pcexpress-mcp/", ".browser-profile/"):
            self.assertTrue(gitignore_covers(ignore, pattern), pattern)

        self._tree_is_clean(ROOT)

    def test_write_keeps_real_store_id_unless_forced(self) -> None:
        (self.ws / ".env").write_text(
            "PCEXPRESS_BANNER=superstore\nPCEXPRESS_STORE_ID=1001\n", encoding="utf-8"
        )
        with mock.patch("pcexpress_login.discover_store_id", return_value="0545") as disc:
            rc, out = self._run([])
        self.assertEqual(rc, 0)
        disc.assert_not_called()
        self.assertIn("PCEXPRESS_STORE_ID=1001\n", (self.ws / ".env").read_text("utf-8"))

        with mock.patch("pcexpress_login.discover_store_id", return_value="0545"):
            rc, out = self._run(["--force-store"])
        self.assertEqual(rc, 0)
        self.assertIn("PCEXPRESS_STORE_ID=0545\n", (self.ws / ".env").read_text("utf-8"))

    def test_missing_workspace_returns_one(self) -> None:
        with mock.patch(
            "pcexpress_login.find_workspace_root",
            side_effect=WorkspaceNotFoundError("no workspace"),
        ):
            self.assertEqual(main([]), 1)


if __name__ == "__main__":
    unittest.main()
