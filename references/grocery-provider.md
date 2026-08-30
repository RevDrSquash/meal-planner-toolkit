# Grocery provider contract

V2 supports one optional adapter: PC Express. The interface below is
deliberately small so another store can be documented the same way later.

## Capabilities

A provider integration may expose some or all of:

| Capability | Purpose | Side effect |
|---|---|---|
| search products | Resolve an ingredient to shoppable options | read-only |
| product details | Price, size, availability for a known id | read-only |
| past orders | Hint brands/sizes the user actually bought | read-only |
| view cart | Reconcile after writes | read-only |
| add/remove cart | Build an order | write — needs user confirmation |
| checkout | Place an order | **out of scope** — never implement |

## Rules for every adapter

- Cart writes stay in the parent conversation, after the user confirms the
  plan, pantry check, and excess flags.
- Search-only work may be delegated to a grocery-search subagent.
- Persist successful product identities in the workspace
  `shopping/product-mappings.md` (and `pantry.md` when the user wants a
  staple pinned). Never persist tokens or customer IDs in markdown.
- If the adapter is missing or auth fails, degrade to a file-based list.

## Workspace wiring

Provider secrets live in the workspace `.env` (gitignored). MCP config is
workspace-local (Cursor, Claude Code, or any other client). This toolkit
only ships scripts and examples — see `examples/mcp/` and
[pcexpress.md](pcexpress.md).
