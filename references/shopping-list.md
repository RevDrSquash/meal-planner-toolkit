# Shopping-list generation

Derive the to-buy list from a confirmed (or draft) meal plan plus staples.

## Inputs

- The plan in workspace `plans/`
- `pantry.md` — exclude these unless the user is out
- `staples.md` — add recurring items that are due
- `shopping/product-mappings.md` — preferred brand/size/code when present

## Procedure

1. Union recipe ingredients (after modifications) with requested staples.
2. Subtract pantry stock. Temporary "we're out" overrides apply to this
   list only unless the user asks to edit `pantry.md`.
3. Group items in a way that is easy to shop (produce, proteins,
   dairy/eggs, pantry, frozen, other).
4. Write or update the plan's shopping section, or a sibling file using
   [templates/shopping-list.md](../templates/shopping-list.md).
5. If a grocery provider is configured, resolve products and prices via
   [grocery-search.md](grocery-search.md). Otherwise leave product/price
   columns blank for the user.
6. Include a nutrition summary only when the user asked or recipes already
   have nutrition data on the HTML card (sourced or estimated). Use
   [templates/nutrition-summary.md](../templates/nutrition-summary.md).

## Learned mappings

When a product is confirmed in a cart or the user says "this is what I
buy", append a line to `shopping/product-mappings.md`. Never invent a
product code. Never put mappings in this toolkit repo.
