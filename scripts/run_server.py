#!/usr/bin/env python3
"""Entry point for a workspace-vendored PC Express MCP server.

Credentials and the vendored server live in the private workspace. This
script lives in the toolkit; it loads workspace `.env`, refreshes a stale
token, then starts `vendor/pcexpress-mcp-server`.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from workspace import find_workspace_root

TOOLKIT = Path(__file__).resolve().parent.parent
WORKSPACE = find_workspace_root()
ENV_PATH = WORKSPACE / ".env"
REFRESH_SCRIPT = Path(__file__).resolve().parent / "refresh_token.py"
VENDOR = WORKSPACE / "vendor" / "pcexpress-mcp-server"

load_dotenv(ENV_PATH)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refresh_token import token_is_stale  # noqa: E402


def _ensure_fresh_token() -> None:
    """Refresh workspace .env via Playwright if the bearer token is stale."""
    token = os.environ.get("PCEXPRESS_BEARER_TOKEN")
    if not token_is_stale(token):
        return

    if not REFRESH_SCRIPT.exists():
        print(
            "WARNING: Bearer token is stale/missing and refresh_token.py was not found.",
            file=sys.stderr,
        )
        return

    print(
        "Bearer token missing or near expiry; running refresh_token.py ...",
        file=sys.stderr,
    )
    try:
        result = subprocess.run(
            [sys.executable, str(REFRESH_SCRIPT)],
            cwd=str(WORKSPACE),
            timeout=90,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print(
            "WARNING: Credential refresh timed out after 90s. "
            "Start the server anyway; tool calls may fail with auth errors. "
            "Recovery: python scripts/refresh_token.py --login",
            file=sys.stderr,
        )
        return
    except OSError as exc:
        print(
            f"WARNING: Could not run credential refresh: {exc}. "
            "Recovery: python scripts/refresh_token.py --login",
            file=sys.stderr,
        )
        return

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", file=sys.stderr)
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)

    if result.returncode != 0:
        print(
            "WARNING: Credential refresh failed. Starting server anyway; "
            "tool calls may fail with auth errors. "
            "Recovery: python scripts/refresh_token.py --login",
            file=sys.stderr,
        )
        return

    load_dotenv(ENV_PATH, override=True)
    print("Credentials refreshed; continuing MCP server startup.", file=sys.stderr)


_ensure_fresh_token()

if not VENDOR.is_dir():
    sys.stderr.write(
        f"ERROR: Vendored PC Express server not found at {VENDOR}.\n"
        "Add it to the workspace: git submodule add "
        "https://github.com/FireBall1725/pcexpress-mcp-server.git "
        "vendor/pcexpress-mcp-server\n"
    )
    raise SystemExit(1)

sys.path.insert(0, str(VENDOR))

import pcexpress_mcp_server  # noqa: E402
from pcexpress_mcp_server import main  # noqa: E402

# The vendored _get_build_id fetches the store homepage with a bare, outdated
# User-Agent, which now trips the site's bot protection: it returns a small
# JavaScript challenge page with no buildId in it, so search_products fails
# with "Could not extract build ID from website". Sending a full set of
# modern browser headers gets the real page back.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def _get_build_id(self) -> str:
    import re

    import requests

    response = requests.get(f"https://{self.domain}/en", headers=_BROWSER_HEADERS)
    response.raise_for_status()
    match = re.search(r'buildId":"([^"]+)"', response.text)
    if match:
        return match.group(1)
    raise ValueError("Could not extract build ID from website")


pcexpress_mcp_server.PCExpressAPI._get_build_id = _get_build_id

if __name__ == "__main__":
    asyncio.run(main())
