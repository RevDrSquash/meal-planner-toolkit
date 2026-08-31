# Grocery search

Keep bulky catalog results out of the main planning thread.

## Delegation

Start from the shopping-list artifact
([shopping-list.md](shopping-list.md)): `name`, `display` / `parts`,
`role`, and `notes`. Do not pass recipe HTML, meal-plan internals, or
pantry files. Skip `assumed_in_pantry`. Ask about
`needs_confirmation` before searching.

Split **to-buy** items into 2–4 batches (for example produce, proteins,
pantry/dairy). For each batch, follow [agents/grocery-search.md](../agents/grocery-search.md)
in a subagent or isolated turn. Pass:

- shopping-list items with quantities (`display` or `parts`)
- relevant preferences (budget, brands, diet)
- any known mappings from `shopping/product-mappings.md`

The parent chooses from shortlists, then builds a proposed cart with
[cart.md](cart.md). The searcher must not write the cart or approve
mutations. Search remains useful when the provider has no cart-write
capability.

## Without a provider

If no grocery MCP/API is available, skip live search. The shopping-list
markdown is the list the user takes to the store. When mappings exist,
note the user's usual brand/size as a hint — not as a live price.

## Provider-specific behavior

Read [grocery-provider.md](grocery-provider.md). For PC Express banners,
also read [pcexpress.md](pcexpress.md). Do not assume those tools exist.
Do not assume product search works without provider authentication.
