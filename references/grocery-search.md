# Grocery search

Keep bulky catalog results out of the main planning thread. Search is a
delegate step inside [product-resolution.md](product-resolution.md), not
a second shopping workflow.

## When to search

Run [product-resolution.md](product-resolution.md) first. Search only
the ingredients whose resolve `probe` is `search` (or `details` when a
targeted product lookup failed). Known mappings should usually skip a
broad catalog query.

## Delegation

Split the remaining to-buy list into 2–4 batches (for example produce,
proteins, pantry/dairy). For each batch, follow
[agents/grocery-search.md](../agents/grocery-search.md) in a subagent or
isolated turn. Pass:

- ingredients with quantities
- relevant preferences (budget, brands, diet)
- any known mappings from `shopping/product-mappings.md`

The searcher returns compact JSON (at most a few candidates per
ingredient). The parent ranks and picks with
`python scripts/product_resolve.py resolve … --candidates hits.json`.
The searcher must not write the cart or mappings file.

## Without a provider

If no grocery MCP/API is available, skip live search. Produce a plain
shopping list and, when mappings exist, note the user's usual brand/size
as a hint — not as a live price.

## Provider-specific behavior

Read [grocery-provider.md](grocery-provider.md). For PC Express banners,
also read [pcexpress.md](pcexpress.md). Do not assume those tools exist.
Do not assume product search works without provider authentication.
