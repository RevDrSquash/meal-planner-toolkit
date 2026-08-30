# Grocery search

Keep bulky catalog results out of the main planning thread.

## Delegation

Split the to-buy list into 2–4 batches (for example produce, proteins,
pantry/dairy). For each batch, follow [agents/grocery-search.md](../agents/grocery-search.md)
in a subagent or isolated turn. Pass:

- ingredients with quantities
- relevant preferences (budget, brands, diet)
- any known mappings from `shopping/product-mappings.md`

The parent chooses from shortlists. The searcher must not write the cart.

## Without a provider

If no grocery MCP/API is available, skip live search. Produce a plain
shopping list and, when mappings exist, note the user's usual brand/size
as a hint — not as a live price.

## Provider-specific behavior

Read [grocery-provider.md](grocery-provider.md). For PC Express banners,
also read [pcexpress.md](pcexpress.md). Do not assume those tools exist.
