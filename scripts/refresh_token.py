#!/usr/bin/env python3
"""Capture and refresh PC Express credentials via a persistent Playwright profile.

Modes:
  --login   One-time headed login; captures bearer token + customer/cart/store IDs
  (default) Headless refresh using the saved browser profile
  --check   Print the current token's expiry from .env (no browser)

Usage:
  python scripts/refresh_token.py --login
  python scripts/refresh_token.py
  python scripts/refresh_token.py --check
  python scripts/refresh_token.py --headed   # debug a headless-blocked refresh
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from workspace import find_workspace_root


def _workspace_root() -> Path:
    return find_workspace_root()


def _env_path() -> Path:
    return _workspace_root() / ".env"


def _profile_dir() -> Path:
    return _workspace_root() / ".browser-profile"

BANNER_DOMAINS = {
    "zehrs": "www.zehrs.ca",
    "loblaws": "www.loblaws.ca",
    "nofrills": "www.nofrills.ca",
    "superstore": "www.realcanadiansuperstore.ca",
    "independent": "www.yourindependentgrocer.ca",
    "tandt": "www.tntsupermarket.com",
}

ENV_KEYS = {
    "bearer_token": "PCEXPRESS_BEARER_TOKEN",
    "customer_id": "PCEXPRESS_CUSTOMER_ID",
    "cart_id": "PCEXPRESS_CART_ID",
    "store_id": "PCEXPRESS_STORE_ID",
}

# How long to wait for API traffic to yield a fresh token.
CAPTURE_TIMEOUT_S = 45
# Buffer used by run_server.py to decide when to refresh.
EXPIRY_BUFFER_S = 10 * 60


def token_expiry(token: str | None) -> datetime | None:
    """Decode a JWT's exp claim without verifying the signature."""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        # Pad base64url to a multiple of 4
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = data.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (ValueError, json.JSONDecodeError, OSError, TypeError):
        return None


def token_is_stale(token: str | None, buffer_s: int = EXPIRY_BUFFER_S) -> bool:
    """True if the token is missing, unparseable, or expires within buffer_s."""
    exp = token_expiry(token)
    if exp is None:
        return True
    remaining = (exp - datetime.now(timezone.utc)).total_seconds()
    return remaining <= buffer_s


def _read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file (ignores comments/blanks)."""
    path = path or _env_path()
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _banner_domain(env: dict[str, str] | None = None) -> str:
    env = env if env is not None else _read_env_file()
    banner = (
        env.get("PCEXPRESS_BANNER")
        or os.environ.get("PCEXPRESS_BANNER")
        or "superstore"
    ).lower()
    return BANNER_DOMAINS.get(banner, BANNER_DOMAINS["superstore"])


def update_env(credentials: dict[str, str], path: Path | None = None) -> None:
    """Rewrite PCEXPRESS_* keys in .env in place; create the file if missing.

    Only keys present (non-empty) in *credentials* are written. Other lines and
    comments are preserved.
    """
    path = path or _env_path()
    to_write = {
        ENV_KEYS[k]: v for k, v in credentials.items() if v and k in ENV_KEYS
    }
    if not to_write:
        return

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = [
            "# PC Express credentials - managed by scripts/refresh_token.py",
            "",
            "PCEXPRESS_BANNER=superstore",
        ]

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        replaced = False
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in to_write:
                new_lines.append(f"{key}={to_write[key]}")
                seen.add(key)
                replaced = True
        if not replaced:
            new_lines.append(line)

    for key, value in to_write.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _extract_from_request(url: str, headers: dict, post_data: str | None, creds: dict) -> None:
    """Update *creds* in place from a single API request."""
    if "api.pcexpress.ca" not in url:
        return

    if not creds.get("bearer_token"):
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            if token.startswith("eyJ"):
                creds["bearer_token"] = token

    if not creds.get("cart_id"):
        m = re.search(r"/carts/([a-f0-9\-]{36})", url, re.I)
        if m:
            creds["cart_id"] = m.group(1)

    if not creds.get("customer_id"):
        m = re.search(r"/customers/([a-f0-9\-]{36})", url, re.I)
        if m:
            creds["customer_id"] = m.group(1)

    if not creds.get("store_id"):
        m = re.search(r"[?&]storeId=(\d+)", url)
        if m:
            creds["store_id"] = m.group(1)
        elif post_data and "storeId" in post_data:
            try:
                body = json.loads(post_data)
                if isinstance(body, dict) and body.get("storeId") is not None:
                    creds["store_id"] = str(body["storeId"])
            except (json.JSONDecodeError, TypeError):
                m = re.search(r'"storeId"\s*:\s*"?(\d+)"?', post_data)
                if m:
                    creds["store_id"] = m.group(1)


def _try_access_token_cookie(context, creds: dict) -> None:
    """Fallback: pull AccessToken cookie if Authorization header wasn't seen."""
    if creds.get("bearer_token"):
        return
    try:
        for cookie in context.cookies():
            if cookie.get("name") == "AccessToken" and cookie.get("value", "").startswith("eyJ"):
                creds["bearer_token"] = cookie["value"]
                return
    except Exception:
        return


def _attach_sniffer(context, creds: dict):
    """Listen for api.pcexpress.ca requests and fill *creds*."""

    def on_request(request):
        try:
            headers = request.headers
            post_data = None
            try:
                post_data = request.post_data
            except Exception:
                pass
            _extract_from_request(request.url, headers, post_data, creds)
        except Exception:
            pass

    context.on("request", on_request)
    return on_request


def _wait_for_credentials(
    creds: dict,
    context,
    timeout_s: float = CAPTURE_TIMEOUT_S,
    require_all: bool = False,
) -> bool:
    """Poll until we have a bearer token (and optionally all four values)."""
    deadline = time.monotonic() + timeout_s
    required = ("bearer_token", "customer_id", "cart_id", "store_id") if require_all else ("bearer_token",)

    while time.monotonic() < deadline:
        _try_access_token_cookie(context, creds)
        if all(creds.get(k) for k in required):
            return True
        time.sleep(0.5)

    _try_access_token_cookie(context, creds)
    return all(creds.get(k) for k in required)


def _launch_context(headed: bool):
    from playwright.sync_api import sync_playwright

    profile_dir = _profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=not headed,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, context


def _print_capture_summary(creds: dict) -> None:
    for key in ("bearer_token", "customer_id", "cart_id", "store_id"):
        value = creds.get(key)
        label = ENV_KEYS[key]
        if not value:
            print(f"  {label}: NOT FOUND")
        elif key == "bearer_token":
            exp = token_expiry(value)
            exp_s = exp.astimezone().strftime("%Y-%m-%d %H:%M %Z") if exp else "unknown"
            print(f"  {label}: {value[:24]}...{value[-12:]} (expires {exp_s})")
        else:
            print(f"  {label}: {value}")


def cmd_login() -> int:
    domain = _banner_domain()
    base = f"https://{domain}"
    print(f"Opening headed browser for {domain}")
    print("Log in, pick your store, then open your cart if prompted.")
    print("Waiting for API traffic to capture credentials...\n")

    creds: dict[str, str | None] = {
        "bearer_token": None,
        "customer_id": None,
        "cart_id": None,
        "store_id": None,
    }

    pw, context = _launch_context(headed=True)
    try:
        _attach_sniffer(context, creds)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"{base}/en", wait_until="domcontentloaded")

        # Give the user plenty of time to complete login / 2FA / store pick.
        # We succeed as soon as all four values appear; otherwise wait up to 5 min.
        ok = _wait_for_credentials(creds, context, timeout_s=300, require_all=True)
        if not ok and creds.get("bearer_token"):
            # Token alone is enough to write; nudge user toward cart for IDs.
            print("Token captured; still missing some IDs. Opening cart page...")
            try:
                page.goto(f"{base}/en/cart", wait_until="domcontentloaded")
            except Exception:
                pass
            ok = _wait_for_credentials(creds, context, timeout_s=60, require_all=True)

        if not creds.get("bearer_token"):
            print(
                "ERROR: No bearer token captured. Make sure you finished logging in "
                "and that requests to api.pcexpress.ca appeared.",
                file=sys.stderr,
            )
            return 1

        captured = {k: v for k, v in creds.items() if v}
        update_env(captured)
        print("\nWrote credentials to .env:")
        _print_capture_summary(creds)
        if not all(creds.get(k) for k in ("customer_id", "cart_id", "store_id")):
            print(
                "\nWarning: some IDs are still missing. Browse the cart/account "
                "pages and re-run --login, or fill them manually.",
                file=sys.stderr,
            )
            return 1
        print("\nLogin profile saved. Future refreshes can run headless.")
        return 0
    finally:
        context.close()
        pw.stop()


def cmd_refresh(headed: bool = False) -> int:
    if not _profile_dir().exists():
        print(
            "ERROR: No browser profile at .browser-profile/. "
            "Run: python scripts/refresh_token.py --login",
            file=sys.stderr,
        )
        return 1

    domain = _banner_domain()
    base = f"https://{domain}"
    mode = "headed" if headed else "headless"
    print(f"Refreshing credentials ({mode}) via {domain}...")

    creds: dict[str, str | None] = {
        "bearer_token": None,
        "customer_id": None,
        "cart_id": None,
        "store_id": None,
    }

    pw, context = _launch_context(headed=headed)
    try:
        _attach_sniffer(context, creds)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(f"{base}/en", wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            print(f"Warning: homepage navigation issue: {exc}", file=sys.stderr)

        # Cart page is the most reliable source of cart/customer IDs.
        try:
            page.goto(f"{base}/en/cart", wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            print(f"Warning: cart navigation issue: {exc}", file=sys.stderr)

        ok = _wait_for_credentials(creds, context, timeout_s=CAPTURE_TIMEOUT_S, require_all=False)
        if not ok:
            print(
                "ERROR: Session expired or no token captured. "
                "Rerun with --login to re-authenticate.",
                file=sys.stderr,
            )
            return 1

        captured = {k: v for k, v in creds.items() if v}
        update_env(captured)
        print("Updated .env:")
        _print_capture_summary(creds)
        return 0
    finally:
        context.close()
        pw.stop()


def cmd_check() -> int:
    env = _read_env_file()
    token = env.get("PCEXPRESS_BEARER_TOKEN") or os.environ.get("PCEXPRESS_BEARER_TOKEN")
    if not token or token.startswith("your_"):
        print("No PCEXPRESS_BEARER_TOKEN set in .env")
        return 1

    exp = token_expiry(token)
    if exp is None:
        print("Token present but could not decode expiry (not a JWT?)")
        return 1

    now = datetime.now(timezone.utc)
    remaining = (exp - now).total_seconds()
    local = exp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if remaining <= 0:
        print(f"Token EXPIRED at {local} ({int(-remaining)}s ago)")
        return 1
    mins = int(remaining // 60)
    print(f"Token valid until {local} ({mins} min remaining)")
    if remaining <= EXPIRY_BUFFER_S:
        print(f"Within {EXPIRY_BUFFER_S // 60}-minute refresh buffer (stale for auto-refresh)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh PC Express credentials via Playwright persistent profile"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--login",
        action="store_true",
        help="One-time headed login; save profile and capture all credentials",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Print current token expiry from .env (no browser)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run refresh mode with a visible browser (debugging)",
    )
    args = parser.parse_args(argv)

    if args.login:
        return cmd_login()
    if args.check:
        return cmd_check()
    return cmd_refresh(headed=args.headed)


if __name__ == "__main__":
    sys.exit(main())
