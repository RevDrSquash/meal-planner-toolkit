#!/usr/bin/env python3
"""Meal Planner configuration for a workspace-vendored PC Express MCP server.

Auth, token refresh, and the MCP tools themselves live in the reviewed
upstream project (FireBall1725/pcexpress-mcp-server). This module only
records the pin, the tool surface, and how a private workspace should
launch that server. It does not capture credentials or drive a browser.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from workspace import WorkspaceNotFoundError, find_workspace_root

UPSTREAM_URL = "https://github.com/FireBall1725/pcexpress-mcp-server.git"
# Reviewed 2026-08-30: current origin/main (includes OAuth refresh, pcx-bff
# search, cart-id rediscovery, and later docs/CI). Do not bump without a diff
# review. See references/pcexpress.md.
REVIEWED_COMMIT = "e8968c9697d5a632d71e05f3c50413c189c3b508"
VENDOR_RELATIVE = Path("vendor") / "pcexpress-mcp-server"
SERVER_SCRIPT = "pcexpress_mcp_server.py"
STATE_DIR_NAME = ".pcexpress-mcp"

# Current MCP tool surface on REVIEWED_COMMIT. search_products is
# token-authenticated on the pcx-bff /products/search route; there is no
# unauthenticated catalog path to smoke-test in CI.
READ_ONLY_TOOLS = (
    "search_products",
    "get_product_details",
    "search_past_orders",
    "get_order_items",
    "view_cart",
)
CART_MUTATING_TOOLS = (
    "add_to_cart",
    "remove_from_cart",
)
ALL_TOOLS = READ_ONLY_TOOLS + CART_MUTATING_TOOLS

# Checkout / payment are intentionally absent from the upstream server.
OUT_OF_SCOPE_TOOLS = ("checkout", "place_order")

REQUIRED_ENV = (
    "PCEXPRESS_REFRESH_TOKEN",
    "PCEXPRESS_BANNER",
    "PCEXPRESS_STORE_ID",
)
OPTIONAL_ENV = (
    "PCEXPRESS_STATE_DIR",
    "PCEXPRESS_CART_ID",
    "PCEXPRESS_CLIENT_SECRET",
)
# Removed by current upstream; leftover keys mean the workspace is still on
# the old HAR / bearer-token bootstrap.
OBSOLETE_ENV = (
    "PCEXPRESS_BEARER_TOKEN",
    "PCEXPRESS_CUSTOMER_ID",
)
# Patterns a *workspace-root* .gitignore must include. The toolkit
# .gitignore does not apply to the parent repository.
REQUIRED_WORKSPACE_GITIGNORE = (".env",)

BANNERS = (
    "superstore",
    "loblaws",
    "nofrills",
    "zehrs",
    "independent",
    "tandt",
)

_TOOL_NAME_RE = re.compile(r'Tool\(\s*name="([A-Za-z0-9_]+)"', re.MULTILINE)
_PLACEHOLDER_VALUES = ("", "your_refresh_token_here", "your_store_id_here", "1234")


def vendor_dir(workspace: Path) -> Path:
    return workspace / VENDOR_RELATIVE


def server_script(workspace: Path) -> Path:
    return vendor_dir(workspace) / SERVER_SCRIPT


def default_state_dir(workspace: Path) -> Path:
    return workspace / STATE_DIR_NAME


def gitignore_covers(text: str, pattern: str) -> bool:
    """True when *pattern* is an active (non-negated) gitignore entry.

    This is a small exact-line matcher for the patterns we ship, not a
    full gitwildmatch implementation.
    """
    wanted = {
        pattern,
        pattern.rstrip("/"),
        pattern.rstrip("/") + "/",
    }
    wanted |= {f"/{item}" for item in list(wanted)}
    wanted_norm = {item.rstrip("/") for item in wanted}
    covered = False
    for raw in text.splitlines():
        # Git comments are only whole lines that start with #. A trailing
        # " # remark" is part of the pattern and does not ignore ".env".
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        token = line[1:].strip() if negated else line
        if token.rstrip("/") in wanted_norm:
            covered = not negated
    return covered


def ignore_pattern_for_state_dir(workspace: Path, state_dir: Path) -> str | None:
    """Return a gitignore pattern for *state_dir* when it is inside *workspace*."""
    workspace_res = workspace.expanduser().resolve()
    candidate = state_dir.expanduser()
    if not candidate.is_absolute():
        candidate = workspace_res / candidate
    try:
        relative = candidate.resolve().relative_to(workspace_res)
    except ValueError:
        return None
    if relative == Path("."):
        return None
    return relative.as_posix().rstrip("/") + "/"


def required_workspace_gitignore_patterns(
    workspace: Path, state_dir: Path
) -> tuple[str, ...]:
    patterns = list(REQUIRED_WORKSPACE_GITIGNORE)
    state_pattern = ignore_pattern_for_state_dir(workspace, state_dir)
    if state_pattern and state_pattern not in patterns:
        patterns.append(state_pattern)
    return tuple(patterns)


def missing_workspace_gitignore_patterns(
    workspace: Path, state_dir: Path
) -> tuple[str, ...]:
    gitignore = workspace / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    return tuple(
        pattern
        for pattern in required_workspace_gitignore_patterns(workspace, state_dir)
        if not gitignore_covers(text, pattern)
    )


def ensure_workspace_secret_gitignore(
    workspace: Path, state_dir: Path
) -> tuple[str, ...]:
    """Append missing secret ignore patterns to the workspace ``.gitignore``.

    Returns the patterns that were added. Does not touch the toolkit tree.
    """
    missing = missing_workspace_gitignore_patterns(workspace, state_dir)
    if not missing:
        return ()
    path = workspace / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing:
        existing += "\n"
    block = (
        "# Meal Planner secrets — do not commit credentials or token state\n"
        + "\n".join(missing)
        + "\n"
    )
    path.write_text(existing + block, encoding="utf-8")
    return missing


def tool_side_effect(name: str) -> str:
    if name in CART_MUTATING_TOOLS:
        return "write"
    if name in READ_ONLY_TOOLS:
        return "read"
    raise KeyError(f"Unknown PC Express tool: {name}")


def tools_declared_in_server_source(source: str) -> tuple[str, ...]:
    """Parse Tool(name=...) registrations from upstream pcexpress_mcp_server.py."""
    return tuple(_TOOL_NAME_RE.findall(source))


def load_env_file(
    path: Path,
    environ: dict[str, str] | None = None,
    *,
    override: bool = False,
) -> dict[str, str]:
    """Load KEY=VALUE lines. Stdlib-only; does not require python-dotenv."""
    dest: dict[str, str] = environ if environ is not None else os.environ  # type: ignore[assignment]
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        loaded[key] = value
        if override or key not in dest:
            dest[key] = value
    return loaded


def _env_value(key: str, file_values: dict[str, str]) -> str | None:
    value = os.environ.get(key)
    if value:
        return value
    return file_values.get(key)


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    return not stripped or stripped in _PLACEHOLDER_VALUES or stripped.startswith("your_")


@dataclass(frozen=True)
class ConfigStatus:
    workspace: Path
    vendor_ok: bool
    server_ok: bool
    env_path: Path | None
    missing_env: tuple[str, ...]
    obsolete_env: tuple[str, ...]
    missing_gitignore: tuple[str, ...]
    state_dir: Path
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def check_config(workspace: Path | None = None) -> ConfigStatus:
    """Validate workspace vendor + env wiring without contacting the network."""
    root = workspace or find_workspace_root()
    vendor = vendor_dir(root)
    script = server_script(root)
    env_path = root / ".env"
    file_values = load_env_file(env_path, environ={}) if env_path.is_file() else {}

    errors: list[str] = []
    warnings: list[str] = []

    vendor_ok = vendor.is_dir()
    server_ok = script.is_file()
    if not vendor_ok:
        errors.append(
            f"Vendored server not found at {vendor}. "
            f"Add it: git submodule add {UPSTREAM_URL} {VENDOR_RELATIVE}"
        )
    elif not server_ok:
        errors.append(f"Missing {SERVER_SCRIPT} under {vendor}")

    missing = tuple(
        key for key in REQUIRED_ENV if _is_placeholder(_env_value(key, file_values))
    )
    if missing:
        errors.append(
            "Workspace .env is missing required PC Express values: " + ", ".join(missing)
        )
    if not env_path.is_file():
        warnings.append("No workspace .env file (copy templates/env.example)")

    obsolete = tuple(key for key in OBSOLETE_ENV if _env_value(key, file_values))
    if obsolete:
        warnings.append(
            "Obsolete credentials still present ("
            + ", ".join(obsolete)
            + "). Current upstream uses PCEXPRESS_REFRESH_TOKEN; "
            "customer/cart ids are discovered at runtime."
        )

    banner = _env_value("PCEXPRESS_BANNER", file_values)
    if banner and banner.lower() not in BANNERS:
        warnings.append(
            f"PCEXPRESS_BANNER={banner!r} is not one of: {', '.join(BANNERS)}"
        )

    configured_state = _env_value("PCEXPRESS_STATE_DIR", file_values)
    state_dir = (
        Path(configured_state).expanduser()
        if configured_state
        else default_state_dir(root)
    )
    if not configured_state:
        warnings.append(
            f"PCEXPRESS_STATE_DIR unset; --serve will use {state_dir}"
        )

    missing_gitignore = missing_workspace_gitignore_patterns(root, state_dir)
    if missing_gitignore:
        warnings.append(
            "Workspace .gitignore does not ignore "
            + ", ".join(missing_gitignore)
            + ". Copy templates/gitignore.example into the private "
            "workspace (the toolkit .gitignore does not apply to the "
            "parent repo). --serve will append missing entries."
        )

    return ConfigStatus(
        workspace=root,
        vendor_ok=vendor_ok,
        server_ok=server_ok,
        env_path=env_path if env_path.is_file() else None,
        missing_env=missing,
        obsolete_env=obsolete,
        missing_gitignore=missing_gitignore,
        state_dir=state_dir,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class ServePlan:
    workspace: Path
    script: Path
    env_file: Path | None
    state_dir: Path
    extra_args: tuple[str, ...]


def plan_serve(
    workspace: Path | None = None,
    extra_args: list[str] | None = None,
) -> ServePlan:
    """Resolve how to exec the vendored server. Does not start it."""
    root = workspace or find_workspace_root()
    script = server_script(root)
    if not script.is_file():
        raise FileNotFoundError(
            f"Vendored PC Express server not found at {script}. "
            f"Add it to the workspace: git submodule add {UPSTREAM_URL} {VENDOR_RELATIVE} "
            f"&& git -C {VENDOR_RELATIVE} checkout {REVIEWED_COMMIT}"
        )
    env_file = root / ".env"
    file_values = load_env_file(env_file, environ={}) if env_file.is_file() else {}
    configured_state = _env_value("PCEXPRESS_STATE_DIR", file_values)
    state_dir = (
        Path(configured_state).expanduser()
        if configured_state
        else default_state_dir(root)
    )
    return ServePlan(
        workspace=root,
        script=script,
        env_file=env_file if env_file.is_file() else None,
        state_dir=state_dir,
        extra_args=tuple(extra_args or ()),
    )


def apply_serve_env(plan: ServePlan, environ: dict[str, str] | None = None) -> None:
    dest: dict[str, str] = environ if environ is not None else os.environ  # type: ignore[assignment]
    if plan.env_file is not None:
        load_env_file(plan.env_file, environ=dest, override=False)
    dest.setdefault("PCEXPRESS_STATE_DIR", str(plan.state_dir))


def serve(extra_args: list[str] | None = None) -> int:
    """Load workspace .env and replace this process with the vendored server."""
    try:
        plan = plan_serve(extra_args=extra_args)
    except (WorkspaceNotFoundError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        added = ensure_workspace_secret_gitignore(plan.workspace, plan.state_dir)
        if added:
            print(
                "Added to workspace .gitignore: " + ", ".join(added),
                file=sys.stderr,
            )
    except OSError as exc:
        print(f"warning: could not update workspace .gitignore: {exc}", file=sys.stderr)
    apply_serve_env(plan)
    os.chdir(plan.workspace)
    os.execv(sys.executable, [sys.executable, str(plan.script), *plan.extra_args])
    return 1  # pragma: no cover — execv does not return


def _print_pin() -> int:
    print(f"url\t{UPSTREAM_URL}")
    print(f"commit\t{REVIEWED_COMMIT}")
    print(f"vendor\t{VENDOR_RELATIVE}")
    print(f"server\t{SERVER_SCRIPT}")
    return 0


def _print_tools() -> int:
    print("name\tside_effect")
    for name in ALL_TOOLS:
        print(f"{name}\t{tool_side_effect(name)}")
    return 0


def _print_check() -> int:
    try:
        status = check_config()
    except WorkspaceNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"workspace\t{status.workspace}")
    print(f"vendor\t{'ok' if status.vendor_ok else 'missing'}")
    print(f"server\t{'ok' if status.server_ok else 'missing'}")
    print(f"env\t{status.env_path or 'missing'}")
    print(f"state_dir\t{status.state_dir}")
    if status.missing_env:
        print("missing_env\t" + ",".join(status.missing_env))
    if status.obsolete_env:
        print("obsolete_env\t" + ",".join(status.obsolete_env))
    if status.missing_gitignore:
        print("missing_gitignore\t" + ",".join(status.missing_gitignore))
    for warning in status.warnings:
        print(f"warning\t{warning}", file=sys.stderr)
    for error in status.errors:
        print(f"error\t{error}", file=sys.stderr)
    return 0 if status.ok else 1


def parse_cli(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse toolkit flags; leftover args after ``--serve`` are extras.

    Flag-style extras such as ``--http`` must be forwarded to the vendored
    server. ``parse_known_args`` is required because a ``nargs=*``
    positional rejects option-looking tokens.
    """
    parser = argparse.ArgumentParser(
        description="PC Express workspace pin, tool catalog, and vendored-server launch"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pin",
        action="store_true",
        help="Print the reviewed upstream URL and commit",
    )
    group.add_argument(
        "--tools",
        action="store_true",
        help="Print the MCP tool catalog (read vs cart-mutating)",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Validate workspace vendor + .env wiring (no network)",
    )
    group.add_argument(
        "--serve",
        action="store_true",
        help=(
            "Load workspace .env and exec the vendored MCP server. "
            "Additional arguments after --serve are forwarded "
            "(example: --serve --http)"
        ),
    )
    args, extra = parser.parse_known_args(argv)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if extra and not args.serve:
        parser.error("unrecognized arguments: " + " ".join(extra))
    return args, extra


def main(argv: list[str] | None = None) -> int:
    args, extra = parse_cli(argv)
    if args.pin:
        return _print_pin()
    if args.tools:
        return _print_tools()
    if args.check:
        return _print_check()
    return serve(extra)


if __name__ == "__main__":
    raise SystemExit(main())
