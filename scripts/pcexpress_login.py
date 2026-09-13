#!/usr/bin/env python3
"""Headed PC id login that writes the refresh token into the workspace `.env`.

Opens a real (visible) Chromium window on the PC id sign-in page. You sign in
yourself — password, 2FA, whatever the account needs — and the script watches
the browser for the OAuth redirect, pulls out the authorization code, exchanges
it for tokens, and then:

  * writes PCEXPRESS_REFRESH_TOKEN into <workspace>/.env (in place),
  * seeds <workspace>/<PCEXPRESS_STATE_DIR>/pcid_token_state.json so the
    vendored MCP server uses the new token chain immediately, and
  * if PCEXPRESS_STORE_ID is missing or still a placeholder, derives the store
    id from your account — profile `homeStore` (pickup), `lastStoreId`, or the
    active cart (delivery: the fulfilling store lives on the cart) — and fills
    it in too.

Nothing is pasted by hand and nothing is printed to the terminal that you would
need to copy around. The OAuth constants, the token exchange, and the profile
request are imported from the vendored server (vendor/pcexpress-mcp-server),
so this never drifts from upstream's client configuration.

Usage (from anywhere inside the private workspace):

    python .agents/skills/meal-planner-toolkit/scripts/pcexpress_login.py
    python .../pcexpress_login.py --timeout 600     # slow 2FA
    python .../pcexpress_login.py --fresh-profile   # ignore saved browser profile
    python .../pcexpress_login.py --force-store     # overwrite PCEXPRESS_STORE_ID
    python .../pcexpress_login.py --dump-profile    # raw profile + cart JSON (debug)
    python .../pcexpress_login.py --no-write        # print only, touch nothing

Requires Playwright with Chromium (optional extra; not in toolkit requirements.txt):

    pip install playwright && python -m playwright install chromium

Restart the MCP server after a successful run so it picks up the new token.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

from pcexpress import (
    ensure_workspace_secret_gitignore,
    gitignore_covers,
    load_env_file,
    vendor_dir,
)
from workspace import WorkspaceNotFoundError, find_workspace_root

DEFAULT_STATE_DIR = ".pcexpress-mcp"
STATE_FILE = "pcid_token_state.json"
TOKEN_KEY = "PCEXPRESS_REFRESH_TOKEN"
STORE_KEY = "PCEXPRESS_STORE_ID"
BANNER_KEY = "PCEXPRESS_BANNER"
# Same placeholder semantics as the toolkit's pcexpress.py --check.
PLACEHOLDER_VALUES = ("", "your_store_id_here", "1234")
STORE_ID_KEYS = ("storeId", "store_id", "id", "code", "storeNumber", "number")
WRAPPER_PARAMS = ("redirectURL", "redirectUrl", "redirect_url", "redirect_uri")
LOGIN_GITIGNORE_EXTRA = (".browser-profile/",)


# --------------------------------------------------------------------------- .env


def _env_key_of(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].strip()
    return stripped.partition("=")[0].strip()


def upsert_env_value(text: str, key: str, value: str) -> str:
    """Return *text* with `key=value` replaced in place, or appended.

    Preserves every other line, comments, ordering, and the file's newline
    style. Only the first active line for *key* is replaced; any later
    duplicates are commented out so the effective value is unambiguous.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline) if text else []
    trailing_blank = bool(lines) and lines[-1] == ""
    if trailing_blank:
        lines = lines[:-1]

    replaced = False
    out: list[str] = []
    for line in lines:
        if _env_key_of(line) == key:
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            else:
                out.append(f"# {line}  # superseded by pcexpress_login.py")
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    return newline.join(out) + newline


def write_env_values(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = existing
    for key, value in values.items():
        updated = upsert_env_value(updated, key, value)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(updated)
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    return stripped in PLACEHOLDER_VALUES or stripped.startswith("your_")


def resolve_state_dir(workspace: Path, env_values: dict[str, str]) -> Path:
    configured = os.environ.get("PCEXPRESS_STATE_DIR") or env_values.get(
        "PCEXPRESS_STATE_DIR"
    )
    state = Path(configured).expanduser() if configured else Path(DEFAULT_STATE_DIR)
    if not state.is_absolute():
        state = workspace / state
    return state


def write_token_state(state_dir: Path, tokens: dict) -> Path:
    """Seed the server's rotating-token state file with the fresh chain."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / STATE_FILE
    payload = {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens.get("access_token"),
        "expires_at": time.time() + int(tokens.get("expires_in") or 0),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


def ensure_login_gitignore(workspace: Path, state_dir: Path) -> tuple[str, ...]:
    """Ensure workspace .gitignore covers secrets, token state, and browser profile."""
    added = list(ensure_workspace_secret_gitignore(workspace, state_dir))
    path = workspace / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    missing_extra = [
        p for p in LOGIN_GITIGNORE_EXTRA if not gitignore_covers(text, p)
    ]
    if not missing_extra:
        return tuple(added)
    existing = text
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing:
        existing += "\n"
    block = (
        "# pcexpress_login.py — never commit browser profile state\n"
        + "\n".join(missing_extra)
        + "\n"
    )
    path.write_text(existing + block, encoding="utf-8")
    return tuple(added) + tuple(missing_extra)


# ------------------------------------------------------------------ redirect parse


class AuthorizeError(RuntimeError):
    pass


def code_from_url(url: str, depth: int = 0) -> tuple[str, str | None] | None:
    """Return (code, state) if *url* carries an OAuth authorization response."""
    if not url or depth > 3:
        return None
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    if "error" in query:
        desc = query.get("error_description", [""])[0]
        raise AuthorizeError(f"{query['error'][0]} {desc}".strip())
    if "code" in query and query["code"][0]:
        return query["code"][0], query.get("state", [None])[0]

    for key in WRAPPER_PARAMS:
        if key in query and query[key][0]:
            inner = query[key][0]
            found = code_from_url(inner, depth + 1)
            if found is None and "%" in inner:
                found = code_from_url(urllib.parse.unquote(inner), depth + 1)
            if found:
                return found

    if "%3F" in url.upper() and "code" not in query:
        return code_from_url(urllib.parse.unquote(url), depth + 1)
    return None


# ---------------------------------------------------------------- store discovery


def store_id_from_home_store(home_store) -> str | None:
    if home_store is None or isinstance(home_store, bool):
        return None
    if isinstance(home_store, int):
        return str(home_store).zfill(4)
    if isinstance(home_store, str):
        s = home_store.strip()
        return s.zfill(4) if s.isdigit() and len(s) <= 5 else None
    if isinstance(home_store, dict):
        for key in STORE_ID_KEYS:
            if key in home_store:
                found = store_id_from_home_store(home_store[key])
                if found:
                    return found
        for value in home_store.values():
            if isinstance(value, dict):
                found = store_id_from_home_store(value)
                if found:
                    return found
    return None


class _StaticTokens:
    def __init__(self, access_token: str):
        self._token = access_token

    def get_access_token(self, force: bool = False) -> str:  # noqa: ARG002
        return self._token


def make_api(access_token: str, banner: str, cart_id: str | None = None):
    import pcexpress_mcp_server as server  # type: ignore[import-not-found]

    return server.PCExpressAPI(_StaticTokens(access_token), cart_id, "1234", banner)


CART_STORE_KEYS = {
    "storeid",
    "sellerid",
    "storenumber",
    "store_id",
    "storecode",
    "store",
    "homestore",
    "pickupstore",
    "deliverystore",
    "fulfillmentstore",
}
INTERESTING_KEY_PARTS = ("store", "seller")


def find_store_ids(obj, path: str = "$") -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}"
            if key.lower() in CART_STORE_KEYS:
                found = store_id_from_home_store(value)
                if found:
                    hits.append((here, found))
                    continue
            hits.extend(find_store_ids(value, here))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            hits.extend(find_store_ids(value, f"{path}[{i}]"))
    return hits


def skeleton(obj, depth: int = 0, max_depth: int = 8):
    if depth >= max_depth:
        return "..."
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if any(part in key.lower() for part in INTERESTING_KEY_PARTS) and not isinstance(
                value, (dict, list)
            ):
                out[key] = value
            else:
                out[key] = skeleton(value, depth + 1, max_depth)
        return out
    if isinstance(obj, list):
        if not obj:
            return []
        return [skeleton(obj[0], depth + 1, max_depth), f"... {len(obj)} item(s)"]
    return type(obj).__name__


def _pick(hits: list[tuple[str, str]], source: str) -> str | None:
    if not hits:
        return None
    counts: dict[str, int] = {}
    for _, sid in hits:
        counts[sid] = counts.get(sid, 0) + 1
    if len(counts) == 1:
        sid = next(iter(counts))
        print(f"Store discovery: {source} -> store {sid} ({hits[0][0]})")
        return sid
    best = max(counts, key=counts.get)  # type: ignore[arg-type]
    print(
        f"Store discovery: {source} mentions several store ids; using the most common ({best}).",
        file=sys.stderr,
    )
    for p, sid in hits:
        print(f"  {sid}  at {p}", file=sys.stderr)
    return best


def discover_store_id(tokens: dict, banner: str, dump_profile: bool = False) -> str | None:
    access_token = tokens.get("access_token")
    if not access_token:
        print("Store discovery skipped: token response had no access_token.", file=sys.stderr)
        return None
    try:
        api = make_api(access_token, banner)
    except ImportError as exc:
        print(
            f"Store discovery skipped: could not import the vendored server ({exc}). "
            "Install: pip install -r vendor/pcexpress-mcp-server/requirements.txt",
            file=sys.stderr,
        )
        return None
    try:
        profile = api.get_customer()
    except Exception as exc:  # noqa: BLE001
        print(f"Store discovery skipped: profile request failed ({exc}).", file=sys.stderr)
        return None

    if dump_profile:
        print("\n--- raw profile (GET /customers) ---")
        print(json.dumps(profile, indent=2, sort_keys=True))
        print("--- end profile ---\n")

    if not isinstance(profile, dict):
        print(
            "Store discovery: profile response was not a JSON object; raw value follows:",
            file=sys.stderr,
        )
        print(json.dumps(profile, indent=2), file=sys.stderr)
        return None

    home_store = profile.get("homeStore")
    if home_store is not None:
        sid = store_id_from_home_store(home_store)
        if sid:
            print(f"Store discovery: profile homeStore -> store {sid}")
            return sid
        print(
            "Store discovery: `homeStore` is not a shape this script understands. "
            "Raw value (please capture this so the parser can be taught):",
            file=sys.stderr,
        )
        print(json.dumps(home_store, indent=2, sort_keys=True), file=sys.stderr)

    sid = store_id_from_home_store(profile.get("lastStoreId"))
    if sid:
        print(f"Store discovery: profile lastStoreId -> store {sid}")
        return sid

    cart_id = profile.get("cartId")
    if not cart_id:
        print(
            "Store discovery: no homeStore/lastStoreId and the account has no active cart. "
            "Add one item to your cart on the PC Express site or app, then re-run.",
            file=sys.stderr,
        )
        return None
    try:
        cart = make_api(access_token, banner, cart_id).get_cart()
    except Exception as exc:  # noqa: BLE001
        print(f"Store discovery: cart request failed ({exc}).", file=sys.stderr)
        return None

    if dump_profile:
        print("\n--- raw cart (GET /carts/{cartId}) ---")
        print(json.dumps(cart, indent=2, sort_keys=True))
        print("--- end cart ---\n")

    sid = _pick(find_store_ids(cart), "active cart")
    if sid:
        return sid

    print(
        "Store discovery: no homeStore, no lastStoreId, and nothing store-like in the cart.\n"
        "Cart structure (key names and types only; values shown just for store/seller keys) —\n"
        "please capture this so the parser can be taught:",
        file=sys.stderr,
    )
    print(json.dumps(skeleton(cart), indent=2), file=sys.stderr)
    print(
        "If the cart is empty, add one item on the PC Express site or app and re-run; "
        "the fulfilling store is recorded per line. Or set PCEXPRESS_STORE_ID by hand.",
        file=sys.stderr,
    )
    return None


# ------------------------------------------------------------------- browser flow


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def load_vendor_modules(vendor: Path):
    if not (vendor / "login_pcid.py").is_file():
        raise SystemExit(
            f"Vendored server not found at {vendor}.\n"
            "Run: git submodule update --init --recursive"
        )
    sys.path.insert(0, str(vendor))
    try:
        import login_pcid  # type: ignore[import-not-found]
        import pcid_config  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            f"Could not import the vendored login helpers ({exc}).\n"
            f"Install server deps: pip install -r {vendor / 'requirements.txt'}"
        ) from exc
    return login_pcid, pcid_config


def run_browser_login(
    auth_url: str,
    expected_state: str,
    timeout_s: int,
    fresh: bool,
    profile_dir: Path,
) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is not installed.\n"
            "  pip install playwright && python -m playwright install chromium"
        ) from exc

    found: dict[str, object] = {}

    def consider(url: str | None) -> None:
        if found or not url:
            return
        try:
            hit = code_from_url(url)
        except AuthorizeError as exc:
            found["error"] = str(exc)
            return
        if hit:
            found["code"], found["state"] = hit

    def on_response(response) -> None:
        if 300 <= response.status < 400:
            consider(response.headers.get("location"))

    def wire(page) -> None:
        page.on("framenavigated", lambda frame: consider(frame.url))
        page.on("request", lambda req: consider(req.url))
        page.on("response", on_response)

    launch_args = ["--disable-blink-features=AutomationControlled"]
    with sync_playwright() as p:
        if fresh:
            browser = p.chromium.launch(headless=False, args=launch_args)
            context = browser.new_context(viewport=None)
        else:
            profile_dir.mkdir(parents=True, exist_ok=True)
            browser = None
            context = p.chromium.launch_persistent_context(
                str(profile_dir), headless=False, args=launch_args, viewport=None
            )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        context.on("page", wire)
        page = context.pages[0] if context.pages else context.new_page()
        wire(page)

        print("A browser window is open. Sign in with your PC id there.")
        print("When the sign-in finishes this script picks up the redirect on its own —")
        print("you do not need to copy anything. (The browser may show a 'can't open")
        print("com.loblaw.pcx://' error; that is expected.)\n")

        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            consider(page.url)
            if not found:
                raise SystemExit(f"Could not open the PC id sign-in page: {exc}") from exc

        deadline = time.monotonic() + timeout_s
        while not found:
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"Timed out after {timeout_s}s waiting for the sign-in to finish. "
                    "Re-run with --timeout <seconds> if you need longer."
                )
            pages = list(context.pages)
            if not pages:
                raise SystemExit("The browser window was closed before sign-in finished.")
            for pg in pages:
                with contextlib.suppress(Exception):
                    consider(pg.url)
            with contextlib.suppress(Exception):
                pages[0].wait_for_timeout(250)

        with contextlib.suppress(Exception):
            context.close()
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()

    if "error" in found:
        raise SystemExit(f"PC id returned an authorization error: {found['error']}")
    if found.get("state") != expected_state:
        raise SystemExit(
            "OAuth state mismatch on the redirect — refusing to exchange the code. "
            "Re-run the login; if it keeps happening, try --fresh-profile."
        )
    return str(found["code"])


# --------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headed PC id login that writes the refresh token into workspace .env"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="seconds to wait for you to finish signing in (default 300)",
    )
    parser.add_argument(
        "--fresh-profile",
        action="store_true",
        help="use a throwaway browser profile instead of .browser-profile/pcid",
    )
    parser.add_argument(
        "--force-store",
        action="store_true",
        help=f"overwrite {STORE_KEY} in .env from your account's store even if already set",
    )
    parser.add_argument(
        "--dump-profile",
        action="store_true",
        help=(
            "print the raw PC Express profile and cart JSON (contains your name, "
            "postal code, and delivery address)"
        ),
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="do not touch .env or the token state; print the values instead",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        workspace = find_workspace_root()
    except WorkspaceNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    env_path = workspace / ".env"
    profile_dir = workspace / ".browser-profile" / "pcid"
    vendor = vendor_dir(workspace)

    env_values = load_env_file(env_path, environ={}, override=False)
    for key, value in env_values.items():
        os.environ.setdefault(key, value)

    manual, cfg = load_vendor_modules(vendor)
    if not cfg.CLIENT_SECRET:
        print("No PC Express client secret available (PCEXPRESS_CLIENT_SECRET).", file=sys.stderr)
        return 1

    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(24))
    nonce = _b64url(secrets.token_bytes(16))
    auth_url = manual.build_authorize_url(challenge, state, nonce)

    code = run_browser_login(
        auth_url, state, args.timeout, args.fresh_profile, profile_dir
    )
    print("Sign-in detected. Exchanging the authorization code for tokens...")
    tokens = manual.exchange_code(code, verifier)
    refresh = tokens.get("refresh_token")
    if not refresh:
        print(
            f"Token response had no refresh_token (keys: {sorted(tokens)}). "
            "Check that the scope still includes offline_access.",
            file=sys.stderr,
        )
        return 1

    current_store = os.environ.get(STORE_KEY) or env_values.get(STORE_KEY)
    banner = os.environ.get(BANNER_KEY) or env_values.get(BANNER_KEY)
    want_store = args.force_store or is_placeholder(current_store) or args.dump_profile
    discovered_store: str | None = None
    if want_store:
        if not banner:
            print(
                f"Store discovery skipped: set {BANNER_KEY} in .env first (the profile "
                "endpoint is per banner).",
                file=sys.stderr,
            )
        else:
            discovered_store = discover_store_id(tokens, banner, dump_profile=args.dump_profile)

    if args.no_write:
        print("\n--no-write: nothing written. Values:\n")
        print(f"{TOKEN_KEY}={refresh}")
        if discovered_store:
            print(f"{STORE_KEY}={discovered_store}")
        print(f"(access token expires in {tokens.get('expires_in')}s)")
        return 0

    state_dir = resolve_state_dir(workspace, env_values)
    added = ensure_login_gitignore(workspace, state_dir)
    if added:
        print("Added to .gitignore: " + ", ".join(added))
    values = {TOKEN_KEY: refresh}
    store_written = False
    if discovered_store and (args.force_store or is_placeholder(current_store)):
        values[STORE_KEY] = discovered_store
        store_written = True
    write_env_values(env_path, values)
    state_path = write_token_state(state_dir, tokens)

    print(f"\nWrote {TOKEN_KEY} to {env_path}")
    if store_written:
        print(f"Wrote {STORE_KEY}={discovered_store} (derived from your PC Express account)")
    elif discovered_store and discovered_store != (current_store or "").strip():
        print(
            f"Note: .env has {STORE_KEY}={current_store}, but your account points at store "
            f"{discovered_store}. Re-run with --force-store to switch."
        )
    elif is_placeholder(current_store):
        print(f"{STORE_KEY} is still unset — set it in .env by hand (see messages above).")
    print(f"Seeded token state at {state_path}")
    print(
        "\nDone. Restart the PC Express MCP server (reload MCP in Cursor / restart the host)\n"
        "so it starts from the new token chain. The token itself was not printed."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130) from None
