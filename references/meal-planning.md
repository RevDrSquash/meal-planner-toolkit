# Meal planning

Read workspace files only. This toolkit has no user preferences of its own.

## Inputs

1. `preferences.md` — household, diet, budget, cadence, store notes
2. `staples.md` — recurring items to consider restocking
3. `pantry.md` — assumed on-hand stock (exclude from to-buy)
4. `recipes/` — candidate meals
5. `shopping/product-mappings.md` — known preferred products (hints only)

If onboarding is incomplete, stop and follow [onboarding.md](onboarding.md).

## Procedure

1. Pick meals that fit preferences (servings, diet, protein-repeat limits,
   leftover/overlap goals).
2. Note recipe modifications that reduce waste or avoid one-off specialty
   items, using pantry staples where reasonable. Ask before dropping
   something core to the dish.
3. Collect ingredients. Split **to buy** vs **assumed pantry stock**.
4. Write the plan to the workspace `plans/YYYY-MM-DD.md` using
   [templates/meal-plan.md](../templates/meal-plan.md). Include:
   - meals and why they fit
   - recipe modifications
   - pantry check (every pantry item the recipes need, with quantity)
   - excess flags (see below)
   - a shopping list section (fill prices later if a provider is available)
5. Show the plan and wait for confirmation of the pantry check and excess
   flags before any cart writes.

Also write a cooking plan when the user wants prep order / leftover flow,
using [templates/cooking-plan.md](../templates/cooking-plan.md).

## Excess flags

Stores often sell a size far larger than the recipe needs. Flag it; never
quietly add the oversized product.

For each flagged item:

- quantity the recipe actually needs
- smallest shoppable option and how much excess that leaves
- perishable waste vs carries over (shelf-stable / freezable)
- for anything not core (garnish, optional topping), offer to skip it

## Lasting changes

When the user says a staple or pantry item changed permanently, update
`staples.md` or `pantry.md` in the same conversation. Do not rewrite those
files just because one week's order skipped an item.
