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

Search without `add/remove cart` is a valid V2 configuration. Product
resolution and a proposed cart still work; the user shops from the file.
Ask `python scripts/cart.py capabilities --adapter <name>` (or
`python scripts/provider.py --adapter <name>`) before offering a fill.

Cart mutation is a **separate** step from search. See
[cart.md](cart.md): propose locally, show substitutions / waste /
omissions, get an explicit yes, then write. Checkout stays manual.

## Input

Resolve the retailer-independent shopping-list artifact produced by
[shopping-list.md](shopping-list.md) (`name`, quantity, notes). Do not
require meal-plan JSON, recipe cards, or pantry markdown. Ignore any
product identifiers that are not already in workspace
`shopping/product-mappings.md`.

## Rules for every adapter

- Cart writes stay in the parent conversation, after the user confirms the
  plan, pantry check, excess flags, and the [proposed cart](cart.md).
- Search-only work may be delegated to a grocery-search subagent.
- Persist successful product identities in the workspace
  `shopping/product-mappings.md` (and `pantry.md` when the user wants a
  staple pinned). Never persist tokens or customer IDs in markdown.
- If the adapter is missing or auth fails, degrade to a file-based list.

## Workspace wiring

Provider secrets live in the workspace `.env`. Ignore `.env` and provider
token-state directories in the **workspace** `.gitignore` (the toolkit
`.gitignore` does not apply to the parent repo; see
`templates/gitignore.example`). MCP config is workspace-local (Cursor,
Claude Code, or any other client). This toolkit only ships a pin, a thin
launcher, and examples — see `examples/mcp/` and
[pcexpress.md](pcexpress.md). A provider may require authentication
even for product search; do not assume an unauthenticated catalog.
