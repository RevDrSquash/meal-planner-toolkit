# Onboarding

Run this when the workspace is uninitialized, when `preferences.md` is
still the stock template, or when the user asks to set up meal planning
for the first time.

Use [agents/onboard.md](../agents/onboard.md) if you are delegating the
interview to a subagent. The parent still applies the file writes.

Do not repeat the full interview when `preferences.md` already contains
user answers. If only a couple of required fields are blank, ask just
those. Learn additional preferences during normal planning.

## Initialize the layout first

From the **private workspace** root (never this toolkit package):

```bash
python scripts/workspace.py --init --root .
python scripts/workspace.py --check-initialized
```

That creates any missing default-layout files from `templates/` and does
**not** overwrite existing user files.

## Required result

These must exist in the workspace root after onboarding:

1. `workspace.yaml` — copy [templates/workspace.yaml](../templates/workspace.yaml)
2. `preferences.md` — filled from the interview, starting at
   [templates/preferences.md](../templates/preferences.md)
3. `staples.md` — starter guidance from
   [templates/staples.md](../templates/staples.md)
4. `pantry.md` — starter guidance from
   [templates/pantry.md](../templates/pantry.md)
5. `tools.md` — starter guidance from
   [templates/tools.md](../templates/tools.md); add only notable
   kitchen-tool exceptions the user mentioned
6. `recipes/` directory with a starter `README.md` so Git records it
   (empty of recipes is fine)
7. `plans/` directory with a starter `README.md`
8. `shopping/` directory, with header-only
   `shopping/product-mappings.md` from
   [templates/product-mappings.md](../templates/product-mappings.md)

Ask the user; do not invent household size, diet, store, or constraints.

## Interview

Keep this short. Collect only what is needed for a useful first meal plan:

1. Household size / number of people being fed
2. Location and preferred grocery store / provider
3. Dietary restrictions / allergies
4. Important food dislikes and preferences
5. Basic nutrition or meal-planning goals, if any
6. Cooking constraints that materially affect planning (available
   appliances that matter, or strong time constraints)
7. Only **notable exceptions** to a normal home-kitchen tool set (for
   example no oven, unusually small oven, no stovetop). Do not turn this
   into a full equipment inventory.

Optional, only if they volunteer or it is needed to write the files:

- meals to plan per cycle
- whether they want optional grocery-provider integration now

Do **not** front-load budget, a full staples list, a pantry inventory, or
an appliance catalog. Those accumulate during normal use.

Write answers primarily into `preferences.md`. Put durable equipment notes
in `tools.md`. Leave `staples.md` and `pantry.md` as starter guidance
unless the user already named specific items.

## Optional provider setup

If they use a PC Express banner (Superstore, Loblaws, No Frills, Zehrs,
Independent, T&T) **and** they asked for integration now, follow
[pcexpress.md](pcexpress.md) after the files exist. That includes adding
`templates/gitignore.example` entries to the workspace `.gitignore`.
Otherwise stop at a file-based shopping list.

Store/banner preference stays in `preferences.md`. Credentials stay in
`.env` / MCP config, not in Markdown.

## Done when

`python scripts/workspace.py --check-initialized` and
`python scripts/workspace.py --check-onboarding` both exit 0, and the user
has reviewed `preferences.md`.
