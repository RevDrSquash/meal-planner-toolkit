# Shopping-list generation

Derive the to-buy list from a confirmed (or draft) meal plan plus staples.

The plan's **Ingredient requirements** table is the input: normalized
names and quantities after servings and deviations. It is not a store
catalog. Do not treat those rows as retailer product IDs.

## Inputs

- The plan in workspace `plans/` — start from Ingredient requirements
- `pantry.md` — exclude these unless the user is out
- `staples.md` — add recurring items that are due
- `shopping/product-mappings.md` — preferred brand/size/code when present

## Procedure

1. Take the plan's ingredient requirements (already scaled, aggregated,
   and after recorded deviations). Add requested staples. Leftover /
   reheat meals on the schedule are already excluded from that table.
2. Subtract pantry stock. Temporary "we're out" overrides apply to this
   list only unless the user asks to edit `pantry.md`.
3. Group items in a way that is easy to shop (produce, proteins,
   dairy/eggs, pantry, frozen, other). The requirement `category` column
   is a starting point.
4. Write a sibling file using
   [templates/shopping-list.md](../templates/shopping-list.md) (or update
   a shopping section). Do not put product codes back onto the meal plan.
5. If a grocery provider is configured, resolve products and prices via
   [grocery-search.md](grocery-search.md). Otherwise leave product/price
   columns blank for the user.
6. Include a nutrition summary only when the user asked. The meal plan
   already copies card macros when present; use
   [templates/nutrition-summary.md](../templates/nutrition-summary.md)
   if they want a standalone copy.

## Excess flags

Stores often sell a size far larger than the recipe needs. Flag it; never
quietly add the oversized product.

For each flagged item:

- quantity the plan actually needs (from ingredient requirements)
- smallest shoppable option and how much excess that leaves
- perishable waste vs carries over (shelf-stable / freezable)
- for anything not core (garnish, optional topping), offer to skip it

## Learned mappings

When a product is confirmed in a cart or the user says "this is what I
buy", append a line to `shopping/product-mappings.md`. Never invent a
product code. Never put mappings in this toolkit repo.
