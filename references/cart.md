# Proposed cart and approved mutation

Turn resolved grocery products (and recurring staples already on the
shopping list) into a **reviewable proposed cart**. Change the connected
provider cart only after the user approves the important choices.

Product search and resolution stay useful when the provider cannot write
a cart. Checkout and payment are out of scope.

## When to use this

After [shopping-list.md](shopping-list.md) and product picks from
[grocery-search.md](grocery-search.md) (or
[product-resolution.md](product-resolution.md) when that helper is
present). Do not start here from a meal plan.

## Capabilities

Ask what the adapter can actually do before offering a fill. A provider
may expose search without cart-write:

```bash
python scripts/cart.py capabilities --adapter pcexpress
python scripts/cart.py capabilities --adapter search-only
python scripts/provider.py --adapter pcexpress
```

| Capability | Proposal | Mutation |
|---|---|---|
| search / details | enough to resolve picks | not required |
| cart_write | not required | required to add/remove |
| view_cart | optional (diff vs current) | verify after writes |
| checkout | **never** | **never** |

If `cart_write` is missing, still build and review the proposed cart.
That list is what the user takes to the store. Do not pretend a fill
happened.

PC Express in V2: search, details, past orders, view cart, and
add/remove. There is no checkout tool. See
[pcexpress.md](pcexpress.md) and [grocery-provider.md](grocery-provider.md).

## Input

- Shopping-list JSON (`kind: shopping-list`) — `name`, `display`,
  `role`, `origin`, `sources`, `pantry_status`, planning `substitutions`
- Resolved-product JSON — `picks[]` with `ingredient`, `pick`,
  `alternatives`, optional `mapping` / `usual`, `excess`, `reason`
- Optional current remote cart (`items: [{id, quantity}]`)
- Provider / capability name (`pcexpress`, `search-only`, `none`)

Skip `assumed_in_pantry`. Include staples that are on the to-buy list.
Do not require meal-plan JSON or recipe cards.

Resolved rows should look like the grocery-search / product-resolve
output (`ingredient`, `needed`, `pick.id`, `pick.size`, `pick.price`,
`pick.available`). Synthetic examples live in `tests/fixtures/cart/`.

## Procedure

1. **Propose locally. Do not write the remote cart.**

   ```bash
   python scripts/cart.py propose plans/YYYY-MM-DD-shopping.json \
     --resolved plans/YYYY-MM-DD-resolved.json \
     --provider pcexpress \
     -o plans/YYYY-MM-DD-cart.json \
     --markdown-out plans/YYYY-MM-DD-cart.md
   ```

   Pass `--current-cart` when `view_cart` is available so items already
   present are marked keep, not added twice.

2. **Show the whole review before asking for approval.** The markdown
   always includes substitutions, unavailable essentials, price-driven
   changes, excess/waste, optional skips, omissions, and meal-plan
   suggestions. Do not hide those sections when they are empty — say
   "None." Never hide a populated one.

   Distinguish:

   - **Essential / core** recipe items — blocking if missing
   - **Optional / garnish** — may be skipped without changing the meal

3. **Suggest small meal-plan changes when they are worth it.** The
   helper flags these; it does not edit `plans/`. Offer them when:

   - an essential item is unavailable and a substitute is in stock
   - a substantial sale makes a different-but-reasonable product
     worthwhile

   Wait for the user. If they accept, record a plan deviation the usual
   way, then rebuild the shopping list / proposal. Do not silently swap
   a meal.

4. **Require an explicit yes.** Search subagents must not approve or
   mutate. The parent asks, then:

   ```bash
   python scripts/cart.py approve plans/YYYY-MM-DD-cart.json \
     --include "ground turkey" --include "onion" \
     --exclude cilantro \
     -o plans/YYYY-MM-DD-cart-approved.json
   ```

   Until `status` is `approved`, `apply` refuses and must not call
   `add_to_cart` or `remove_from_cart`.

5. **Apply only the approved mutations.** On PC Express, the parent
   calls `add_to_cart` / `remove_from_cart` for each row in
   `mutations[]`. Automated tests inject a mock:

   ```bash
   python scripts/cart.py apply plans/YYYY-MM-DD-cart-approved.json --dry-run
   python scripts/cart.py apply plans/YYYY-MM-DD-cart-approved.json \
     --mock tests/fixtures/cart/mock-provider.json
   ```

6. **Verify.** If the provider has `view_cart`, read the cart after
   writes and reconcile quantities. Adds can fail silently or return
   out-of-stock. Record partial failures; do not retry checkout.

7. **Stop.** The user pays on the store site. Never call a checkout or
   payment tool.

After a successful add the user is happy with, append the brand / size /
id to workspace `shopping/product-mappings.md`. Never write mappings
into this toolkit.

## Approval boundary

| Step | Remote cart I/O |
|---|---|
| propose | none |
| review markdown | none |
| approve | none — freezes `mutations[]` |
| apply / provider add-remove | only after `status: approved` |
| view_cart | read-only, after writes when supported |
| checkout | forbidden |

## Partial failures

Treat `out of stock`, `not shoppable`, and missing lines after
`view_cart` as partial results. Keep the successful adds. Surface the
failed names and offer a substitute or a skip. Do not require a live
authenticated call to reason about that path — tests use
`MockCartProvider`.

## Without cart-write

Search-only (or no provider) still produces the proposed cart. The
human list is the handoff. Capabilities must say `cart_write: no`.
