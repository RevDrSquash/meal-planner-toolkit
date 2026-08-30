# PC Express adapter

Optional. Used when the workspace is wired to a PC Express banner
(Real Canadian Superstore, Loblaws, No Frills, Zehrs, Independent, T&T)
via [pcexpress-mcp-server](https://github.com/FireBall1725/pcexpress-mcp-server).

Do not assume this adapter is present. Check workspace MCP config and
whether tools such as `search_products` are actually available.

## Workspace setup (user / onboarding)

1. Vendor the MCP server in the **workspace** (not this toolkit), pinned
   and reviewed:

   ```bash
   git submodule add https://github.com/FireBall1725/pcexpress-mcp-server.git vendor/pcexpress-mcp-server
   ```

2. Copy [templates/env.example](../templates/env.example) to workspace
   `.env` and set `PCEXPRESS_BANNER`.
3. Install workspace Python deps (toolkit requirements + vendored server).
4. Capture credentials (writes workspace `.env` and `.browser-profile/`):

   ```bash
   python .agents/skills/meal-planner-toolkit/scripts/refresh_token.py --login
   python .agents/skills/meal-planner-toolkit/scripts/refresh_token.py --check
   ```

   Adjust the script path if this toolkit is embedded elsewhere. From any
   directory inside the workspace you can also run the scripts by absolute
   path; they locate the workspace by walking up to `workspace.yaml`.

5. Point the agent host at `scripts/run_server.py`. Examples:
   - Cursor: `examples/mcp/cursor.mcp.json` → workspace `.cursor/mcp.json`
   - Claude Code / generic: `examples/mcp/mcp.json` → workspace `.mcp.json`

`run_server.py` loads workspace `.env`, refreshes a stale bearer token via
Playwright, then starts the vendored server. Checkout is still manual.

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

## Parent vs search subagent

Search subagents may use: `search_products`, `get_product_details`,
`search_past_orders`, `get_order_items`, `view_cart`.

They must never call `add_to_cart` or `remove_from_cart`.

The parent uses past-order search for brand/size hints, then passes those
hints to the scout. Only the parent writes the cart after confirmation.

## Token expiry

Bearer tokens expire after a few hours. `run_server.py` refreshes on
startup when the JWT is missing or within 10 minutes of expiry. If tools
return auth errors:

```bash
python scripts/refresh_token.py --login
```

(from this toolkit's `scripts/` directory, with cwd inside the workspace).

## Security

- Never commit `.env`, `*.har`, or `.browser-profile/`
- Re-review the vendored server diff before bumping the pin
- The server can modify a cart; it cannot place orders or access payment
