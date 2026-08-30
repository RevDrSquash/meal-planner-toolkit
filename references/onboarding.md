# Onboarding

Run this when the workspace is missing required files, or when the user
asks to set up meal planning for the first time.

Use [agents/onboard.md](../agents/onboard.md) if you are delegating the
interview to a subagent. The parent still applies the file writes.

## Required result

Create these in the **workspace root** (never inside this toolkit):

1. `workspace.yaml` — copy [templates/workspace.yaml](../templates/workspace.yaml)
2. `preferences.md` — start from [templates/preferences.md](../templates/preferences.md)
3. `staples.md` — start from [templates/staples.md](../templates/staples.md)
4. `pantry.md` — start from [templates/pantry.md](../templates/pantry.md)
5. `recipes/` directory (empty is fine)
6. `plans/` directory
7. `shopping/` directory, with an empty or header-only
   `shopping/product-mappings.md` from
   [templates/product-mappings.md](../templates/product-mappings.md)

Ask the user; do not invent household size, diet, store, or budget.

## Interview

Collect at least:

- Number of people and how many dinners (or meals) to plan per cycle
- Dietary restrictions, allergies, strong likes/dislikes
- Nutrition goals if any
- Weekly grocery budget if they have one
- Preferred store / banner / fulfillment (pickup, delivery, in-person)
- Whether they want optional grocery-provider integration now

Then fill `preferences.md` in their words.

## Optional provider setup

If they use a PC Express banner (Superstore, Loblaws, No Frills, Zehrs,
Independent, T&T), follow [pcexpress.md](pcexpress.md) after the files
exist. That includes adding `templates/gitignore.example` entries to the
workspace `.gitignore`. Otherwise stop at a file-based shopping list.

## Done when

`python scripts/workspace.py --check-onboarding` exits 0, and the user has
reviewed `preferences.md`.
