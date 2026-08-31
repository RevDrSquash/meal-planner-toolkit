---
name: meal-planner-toolkit
description: Plans meals from a private recipe collection, imports and finds recipes, builds shopping lists, and optionally resolves grocery products or fills a cart. Use when the user asks to plan meals, cook for the week, find or import a recipe, update pantry or staples, shop, or work with PC Express / Superstore.
---

# Meal Planner Toolkit

This package is **read-only application code**. All user state lives in the
**private workspace** that embeds this toolkit (usually as a Git submodule).
Never write preferences, recipes, plans, product mappings, or credentials
into this package.

## 1. Locate the workspace

Find the private workspace root before doing anything else:

1. If `WORKSPACE_ROOT` is set, use it.
2. Walk up from the current working directory looking for `workspace.yaml`
   or `meal-planner.yaml`.
3. Fall back to a directory that contains both `preferences.md` and `recipes/`.

Or run:

```bash
python scripts/workspace.py
python scripts/workspace.py --paths
python scripts/workspace.py --init
python scripts/workspace.py --check-initialized
python scripts/workspace.py --check-onboarding
```

Default paths (overridable in `workspace.yaml`):

| Key | Default |
|---|---|
| preferences | `preferences.md` |
| staples | `staples.md` |
| pantry | `pantry.md` |
| tools | `tools.md` |
| recipes | `recipes/` |
| plans | `plans/` |
| shopping | `shopping/` |

Read [references/workspace-contract.md](references/workspace-contract.md)
if discovery fails or paths look non-standard.

## 2. Onboard if needed

Onboarding is incomplete when `preferences.md` is missing, still the stock
template, or `recipes/` does not exist. Check with
`python scripts/workspace.py --check-onboarding`.

If the layout is missing files, run `python scripts/workspace.py --init`
(never overwrites existing user files), then follow
[references/onboarding.md](references/onboarding.md) and
[agents/onboard.md](agents/onboard.md) **before** planning or shopping.

If onboarding is already complete, do **not** repeat the interview. If the
layout is only partially present, run `--init` to add missing starter
files and continue.

Do not invent a household, store, or diet. Ask.

## 3. Route the request

| User intent | Follow |
|---|---|
| First-time setup / missing workspace files | [references/onboarding.md](references/onboarding.md) |
| Import or add a recipe | [references/recipe-import.md](references/recipe-import.md), [docs/recipe-format.md](docs/recipe-format.md) |
| Find or discover recipes to add | [references/recipe-finder.md](references/recipe-finder.md), [agents/recipe-finder.md](agents/recipe-finder.md) |
| Plan meals / cooking for a period | [references/meal-planning.md](references/meal-planning.md) (one artifact: schedule + cooking sessions) |
| Build a shopping list from a plan | [references/shopping-list.md](references/shopping-list.md) |
| Resolve products / prices (read-only) | [references/product-resolution.md](references/product-resolution.md); search via [references/grocery-search.md](references/grocery-search.md), [agents/grocery-search.md](agents/grocery-search.md) |
| Review or fill a grocery cart | [references/cart.md](references/cart.md) |
| Provider setup | [references/grocery-provider.md](references/grocery-provider.md) and the matching adapter (PC Express: [references/pcexpress.md](references/pcexpress.md)) |

Write generated plans only under the workspace `plans/` path. A V2 plan is
one markdown file with the meal schedule, cooking sessions, deviations,
nutrition, and normalized ingredient requirements (never product IDs).
Helpers: `python scripts/meal_plan.py eligible`, `scale`, `aggregate`,
and `render`. After the plan is confirmed, build the retailer-independent
shopping list with `python scripts/shopping_list.py plan.json` (pantry +
staples; no grocery MCP required). Resolve products with
`python scripts/product_resolve.py lookup|resolve|rank|remember` — that
helper never writes a cart. After products are resolved, build a
proposed cart with `python scripts/cart.py propose` and wait for approval
before any add/remove. Search still works when the provider cannot write
a cart. Write learned product mappings only under the workspace
`shopping/product-mappings.md`. Update `preferences.md`, `staples.md`,
`pantry.md`, and `tools.md` only when the user says the change is lasting.

## 4. Hard rules

- Treat this toolkit directory as read-only during normal use.
- Never commit or paste `.env`, HAR files, or `.pcexpress-mcp/` token state.
- Never place an order or attempt checkout. Cart fill is optional; payment
  stays with the user on the store site.
- Do not assume PC Express, a specific banner, or any product IDs exist.
  Read the workspace files. If a grocery provider is not configured, produce
  a shopping list the user can take to any store.
- Keep bulky product-search results out of the main thread: delegate to a
  grocery-search subagent using [agents/grocery-search.md](agents/grocery-search.md).
- Keep bulky recipe-search results out of the main thread: delegate discovery
  with [agents/recipe-finder.md](agents/recipe-finder.md), then import only
  the recipes the user picks via [references/recipe-import.md](references/recipe-import.md).
