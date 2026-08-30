# Workspace contract

File-based. No database.

## Roles

| Location | Role |
|---|---|
| This toolkit package | Read-only skills, scripts, templates, provider adapters |
| Parent workspace repository | User data and generated artifacts |

A typical embed path is `.agents/skills/meal-planner-toolkit` so any agent
that scans the portable Agent Skills location can load `SKILL.md`. The
toolkit must not require that path; discovery is by walking up to
`workspace.yaml` (or `preferences.md` + `recipes/`).

## Config file

Optional `workspace.yaml` at the workspace root:

```yaml
version: 2
paths:
  preferences: preferences.md
  staples: staples.md
  pantry: pantry.md
  recipes: recipes
  plans: plans
  shopping: shopping
```

Omit `paths` (or any key) to use the defaults above. `meal-planner.yaml` is
an accepted alias for the same file.

## What each path holds

- `preferences.md` — household size, location/store preferences, dietary
  restrictions, nutrition goals, likes/dislikes, budget, planning cadence.
- `staples.md` — recurring grocery items and usual quantities (replenish
  these even when not in a recipe).
- `pantry.md` — optional current or assumed on-hand stock. Items here are
  excluded from the to-buy list and listed in a pantry-check section.
- `recipes/` — the user's normalized recipe collection (HTML cards or
  markdown). One file per recipe.
- `plans/` — generated meal plans, cooking plans, shopping plans.
- `shopping/` — learned ingredient → preferred product mappings, brands,
  sizes, and any past-order notes the user wants kept. Not credentials.

## Secrets (workspace only, gitignored)

- `.env` — provider tokens and store/customer IDs
- `.browser-profile/` — saved browser session for token refresh
- `*.har` — captured network logs

The toolkit never stores these and never documents real values.

## Agent procedure

1. Locate the workspace root (see `scripts/workspace.py`).
2. Check onboarding (`preferences.md` and `recipes/` must exist).
3. If incomplete, run onboarding into the **workspace**, not this package.
4. Read and write user state only under the workspace paths.
5. Leave this toolkit tree unchanged unless the user is developing the
   toolkit itself.
