# Meal planning (V2)

Choose meals for a requested period **and** decide what should be cooked
together. That is one workflow, not two subsystems.

Read workspace files only. This toolkit has no user preferences of its own.
Most meal selection is agent reasoning; use the helpers below for scaling,
aggregation, eligibility, and the plan artifact.

## Inputs

1. The request — number of days, which meals (dinner / lunch / both),
   servings, and any one-off constraints (include this recipe, use up
   these ingredients).
2. `preferences.md` — household, diet, store, cooking constraints
3. `staples.md` — recurring items to consider restocking (shopping step)
4. `pantry.md` — assumed on-hand stock (shopping step)
5. `tools.md` — equipment and capacity notes that affect **recipe choice
   and cooking-session grouping** (oven size, burners, mixing-bowl
   capacity, specialty appliances)
6. `recipes/` — candidate meals (canonical HTML cards; see
   [docs/recipe-format.md](../docs/recipe-format.md)). Read the visible
   sections or the embedded JSON-LD. Do not look for a Markdown sibling.
7. `shopping/product-mappings.md` — known preferred products (hints only;
   not plan output)

If onboarding is incomplete, stop and follow [onboarding.md](onboarding.md).

## Thin recipe collection

If `recipes/` has fewer HTML cards than the requested meals (or the
household's meals-per-cycle, default 4), the library is too small for a
useful plan. Check with `python scripts/recipe_finder.py --check-collection`
and `python scripts/meal_plan.py eligible`.

**Say so and offer [recipe-finder.md](recipe-finder.md).** Do not invent
unsupported recipes to fill the week. If the user wants to plan with what
they have (repeats or leftover nights), continue and record that in
library notes.

## Procedure

Keep selection and cooking-session design in the same pass.

1. **Restate the request.** Days, meal slots, servings, must-include
   recipes, ingredients to use up.
2. **Index the library and drop hard-constraint misses.**

   ```bash
   python scripts/recipe_finder.py --index
   python scripts/recipe_finder.py --check-collection
   python scripts/meal_plan.py eligible
   ```

   `eligible` applies diet/allergy/dislike filters from `preferences.md`.
   Also skip recipes that `tools.md` cannot support (no oven → no sheet-pan
   roast). Soft preferences (likes, time, one-pot) rank candidates; they
   do not silently drop a viable card.
3. **Select meals with variety.** Rotate proteins, cuisines, and formats
   across the period when the library allows. Prefer leftover-friendly
   dishes when the user wants fewer cook days. Every scheduled meal must
   point at a real card in `recipes/`.
4. **Adapt only in small, explicit ways.** Protein swaps (beef → turkey),
   omitting a disliked garnish, or using a pantry oil are fine. Ask
   before dropping something core to the dish. Record every intentional
   deviation — never silently rewrite a source recipe.
5. **Group cooking sessions in the same plan.** Look for meals that share
   ingredients or prep (onion, garlic, a pot of grains) and that do **not**
   fight over the same scarce equipment. Helpers:

   ```bash
   python scripts/meal_plan.py aggregate --source "Chili" "1 onion" "1 onion"
   ```

   `scripts/meal_plan.py` also exposes `suggest_cook_together()` (shared
   significant ingredients plus light equipment hints). The agent still
   assigns days: two sheet-pan dinners do not share a compact oven on the
   same night; one pressure cooker means one pressure-cooker recipe per
   session; mixing-bowl capacity in `tools.md` limits batched doughs.
6. **Scale and aggregate ingredients for what you will cook**, not leftover
   reheats. Mark leftover / reheat slots so they appear on the eat
   schedule without double-counting groceries.

   ```bash
   python scripts/meal_plan.py scale --from-servings 4 --to-servings 2 \
     "500 g ground beef"
   ```

7. **Write one plan artifact** to the workspace `plans/YYYY-MM-DD.md`
   (add a short suffix if a second plan is written the same day). Use
   [templates/meal-plan.md](../templates/meal-plan.md) or render a JSON
   plan:

   ```bash
   python scripts/meal_plan.py render plan.json -o plans/YYYY-MM-DD.md
   ```

   The file must include all of:
   - meal schedule (what to eat, which day / slot, servings)
   - cooking sessions / prep plan (what to cook, why together, equipment)
   - recipe references (`recipes/<slug>.html`) and planned servings
   - explicit deviations / substitutions
   - nutrition summary where the cards have macros (blank if missing)
   - **normalized ingredient requirements** for the shopping-list step
     (names, quantities, categories — never retailer product IDs)

8. **Show the plan and wait.** Confirm meals, sessions, deviations, and
   leftover flow before [shopping-list.md](shopping-list.md). Excess flags
   and pantry subtraction belong to shopping, which starts from the
   ingredient-requirements table.

A separate cooking-plan file is optional. Prefer the cooking-sessions
section of the same artifact. [templates/cooking-plan.md](../templates/cooking-plan.md)
is only an excerpt if the user wants a print-out.

## Cooking-session rules of thumb

- Shared produce, aromatics, or a pot of grains are good reasons to batch.
- Leftovers count as meals on later days; they are not a second cook.
- Honor `tools.md`: compact oven, burner count, bowl capacity, specialty
  appliances.
- Non-conflicting equipment can run in parallel (chili on the stove while
  broccoli roasts, if the oven fits).
- If two meals both need the same scarce tool, schedule them on different
  days or cook only one.

## Recipe deviations

Write a line for every change from the source card, with the reason:

- Weeknight Chili: ground turkey instead of ground beef (preference)
- Tomato Skillet Pasta: omit the optional chili flake (dislike)

If you pass `replace` / `ingredient` + `replacement` into `build_plan()`,
the helper applies the swap before scaling and aggregation.

## Nutrition

Copy per-serving Calories / Protein / Fat / Carbohydrates from the HTML
card (sourced or estimated). Leave cells blank when the card has no
figure. Do not invent lab-precise numbers. A standalone
[templates/nutrition-summary.md](../templates/nutrition-summary.md) is
optional; the plan already has this table.

## Lasting changes

When the user says a staple, pantry item, or kitchen-tool note changed
permanently, update `staples.md`, `pantry.md`, or `tools.md` in the same
conversation. Do not rewrite those files just because one week's plan
skipped an item.
