"""Tests for headed PC Express login helper (no network, no browser)."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

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


class MainDiscoveryTests(unittest.TestCase):
    def _minimal_workspace(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "workspace.yaml").write_text("version: 2\n", encoding="utf-8")
        (root / "recipes").mkdir()
        return root

    def test_main_uses_find_workspace_root_not_toolkit_parent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ws = self._minimal_workspace(Path(raw) / "my-household")
            fake_manual = mock.Mock()
            fake_manual.build_authorize_url.return_value = "https://auth.example/"
            fake_manual.exchange_code.return_value = {
                "refresh_token": "rtok",
                "expires_in": 3600,
            }
            fake_cfg = mock.Mock(CLIENT_SECRET="secret")
            with mock.patch("pcexpress_login.find_workspace_root", return_value=ws) as find:
                with mock.patch(
                    "pcexpress_login.load_vendor_modules",
                    return_value=(fake_manual, fake_cfg),
                ):
                    with mock.patch(
                        "pcexpress_login.run_browser_login", return_value="code1"
                    ):
                        with mock.patch.dict("os.environ", {}, clear=False):
                            rc = main(["--no-write"])
            find.assert_called_once()
            self.assertEqual(rc, 0)

    def test_no_write_does_not_create_env_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ws = self._minimal_workspace(Path(raw))
            fake_manual = mock.Mock()
            fake_manual.build_authorize_url.return_value = "https://auth.example/"
            fake_manual.exchange_code.return_value = {
                "refresh_token": "rtok",
                "expires_in": 3600,
            }
            fake_cfg = mock.Mock(CLIENT_SECRET="secret")
            with mock.patch("pcexpress_login.find_workspace_root", return_value=ws):
                with mock.patch(
                    "pcexpress_login.load_vendor_modules",
                    return_value=(fake_manual, fake_cfg),
                ):
                    with mock.patch(
                        "pcexpress_login.run_browser_login", return_value="code1"
                    ):
                        buf = io.StringIO()
                        with mock.patch("sys.stdout", buf):
                            rc = main(["--no-write"])
            self.assertEqual(rc, 0)
            self.assertFalse((ws / ".env").exists())
            self.assertFalse((ws / ".pcexpress-mcp").exists())
            self.assertIn("PCEXPRESS_REFRESH_TOKEN=rtok", buf.getvalue())

    def test_missing_workspace_returns_one(self) -> None:
        with mock.patch(
            "pcexpress_login.find_workspace_root",
            side_effect=WorkspaceNotFoundError("no workspace"),
        ):
            self.assertEqual(main([]), 1)


if __name__ == "__main__":
    unittest.main()
