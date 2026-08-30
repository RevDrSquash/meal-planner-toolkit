# PC Express adapter

Optional. Used when the workspace is wired to a PC Express banner
(Real Canadian Superstore, Loblaws, No Frills, Zehrs, Independent, T&T)
via [pcexpress-mcp-server](https://github.com/FireBall1725/pcexpress-mcp-server).

Do not assume this adapter is present. Check workspace MCP config and
whether tools such as `search_products` are actually available. Do not
assume product search is unauthenticated — current upstream requires a
logged-in PC id session for every tool, including search.

This toolkit does not vendor or fork the server. Auth, token refresh,
cart-id discovery, and the MCP tools live upstream. The toolkit only
records a reviewed pin and a thin workspace launcher
(`scripts/pcexpress.py`).

## Reviewed pin

Re-review the upstream diff before bumping this value.

| Field | Value |
|---|---|
| Repository | `https://github.com/FireBall1725/pcexpress-mcp-server.git` |
| Commit | `e8968c9697d5a632d71e05f3c50413c189c3b508` (origin/main as of 2026-08-21) |
| Reviewed | 2026-08-30 |

That commit includes the OAuth refresh-token flow (`login_pcid.py` /
`TokenManager`), token-authenticated `POST /products/search` (the old
Next.js `buildId` scrape is gone), and cart-id rediscovery on 404. The
toolkit no longer ships Playwright login wrappers, HAR extractors, or a
`_get_build_id` monkey-patch.

Print the same pin from any directory inside a workspace:

```bash
python .agents/skills/meal-planner-toolkit/scripts/pcexpress.py --pin
```

## Workspace setup (user / onboarding)

All of this happens in the **private workspace**, not in this toolkit.

1. Vendor the reviewed commit (not floating `main`):

   ```bash
   git submodule add https://github.com/FireBall1725/pcexpress-mcp-server.git \
     vendor/pcexpress-mcp-server
   git -C vendor/pcexpress-mcp-server checkout \
     e8968c9697d5a632d71e05f3c50413c189c3b508
   ```

2. Install the **vendored server** dependencies in the environment that
   will run MCP (not the toolkit's recipe-import requirements):

   ```bash
   pip install -r vendor/pcexpress-mcp-server/requirements.txt
   ```

3. Copy [templates/env.example](../templates/env.example) to workspace
   `.env`. Set `PCEXPRESS_BANNER` and `PCEXPRESS_STORE_ID`. Leave
   `PCEXPRESS_STATE_DIR` pointed at a workspace-local directory (the
   example uses `.pcexpress-mcp`) so the rotating refresh token stays
   with the private workspace.

   Add the entries from [templates/gitignore.example](../templates/gitignore.example)
   to the **workspace-root** `.gitignore`. This toolkit's `.gitignore`
   does not apply to the parent repo; without those entries a normal
   `git add` can commit live OAuth token state. `scripts/pcexpress.py
   --serve` appends any missing required entries (`.env` and the state
   directory) before launching.

4. One-time login, using upstream's wizard (browser required). From the
   vendor directory:

   ```bash
   python setup.py
   ```

   Or the manual helper: `python login_pcid.py`. After you sign in, the
   browser tries to open a `com.loblaw.pcx://...` link and shows an
   error — that is expected. Paste the full address back into the
   script. Put the printed `PCEXPRESS_REFRESH_TOKEN` into the workspace
   `.env`.

   If `setup.py` wrote `vendor/pcexpress-mcp-server/.env`, copy the
   refresh token into the **workspace** `.env` and keep using that file.
   Customer id and cart id are discovered at runtime; do not store them
   in markdown.

5. Point the agent host at `scripts/pcexpress.py --serve`. That loads
   workspace `.env`, defaults `PCEXPRESS_STATE_DIR` to
   `<workspace>/.pcexpress-mcp` when unset, and execs the vendored
   server. Examples:

   - Cursor: `examples/mcp/cursor.mcp.json` → workspace `.cursor/mcp.json`
   - Claude Code / generic: `examples/mcp/mcp.json` → workspace `.mcp.json`

   Extra arguments after `--serve` are forwarded to the vendored server
   (for example `--serve --http` for HTTP/SSE). You can also launch
   `vendor/pcexpress-mcp-server/pcexpress_mcp_server.py` directly if the
   host starts with cwd at the workspace root and loads `.env` itself.

6. Confirm wiring (no network, no secrets printed):

   ```bash
   python .agents/skills/meal-planner-toolkit/scripts/pcexpress.py --check
   python .agents/skills/meal-planner-toolkit/scripts/pcexpress.py --tools
   ```

Checkout is still manual on the store site. The server cannot place an
order.

## MCP tool surface (reviewed commit)

| Tool | Side effect | Notes |
|---|---|---|
| `search_products` | read-only | Token-authenticated pcx-bff search. Not a public catalog. |
| `get_product_details` | read-only | Price/size/availability for a known code. |
| `search_past_orders` | read-only | Order history. Personal data — do not copy into this toolkit. |
| `get_order_items` | read-only | Line items for one past order. |
| `view_cart` | read-only | Reconcile after writes. |
| `add_to_cart` | **write** | Needs user confirmation. |
| `remove_from_cart` | **write** | Needs user confirmation. |

There is no checkout tool. Search subagents may use the read-only tools
only. They must never call `add_to_cart` or `remove_from_cart`.

The parent uses past-order search for brand/size hints, then passes those
hints to the scout. Only the parent writes the cart after confirmation.

## Product codes

Search results often return `_EA` for items actually sold `_KG`.

- Packaged goods (cans, jars, bags, boxes, cartons): `_EA`
- Loose / by-weight produce: `_KG`, even if search showed `_EA`
- If add fails as non-shoppable, retry the other suffix before giving up
- If both fail, search for a packaged alternative and record the swap
- After `add_to_cart`, call `view_cart` and reconcile — adds can fail
  silently
- Store the working full code (including suffix) in
  `shopping/product-mappings.md`

## Authentication

You sign in once in a real browser. Upstream exchanges that login for a
`PCEXPRESS_REFRESH_TOKEN`. The vendored server then mints short-lived
access tokens over HTTPS and persists the rotated refresh token in
`PCEXPRESS_STATE_DIR`. The server never opens a browser.

Refresh tokens are single-use. Run **one** MCP server instance per token
chain. If tools return `invalid_grant` or ask you to re-run
`login_pcid.py`, repeat the one-time login and update workspace `.env`.

Do not keep leftover `PCEXPRESS_BEARER_TOKEN` or `PCEXPRESS_CUSTOMER_ID`
values; current upstream does not use them.

## Manual authenticated testing

CI and this toolkit's tests do not call PC Express. After login, from
the vendor directory, with workspace env loaded:

```bash
python tools/smoke_api.py
```

That is upstream's own authenticated smoke (profile, orders, cart). Do
not commit its output.

## Security

- Never commit `.env`, `.pcexpress-mcp/`, `*.har`, or browser profiles.
  Ignore them in the **workspace** `.gitignore` (copy
  [templates/gitignore.example](../templates/gitignore.example)). This
  package's `.gitignore` only covers the toolkit repository.
- Re-review the vendored server diff before bumping the pin
- The server can modify a cart; it cannot place orders or access payment
- Do not fork upstream unless a concrete V2 requirement cannot be met
  without a fork
