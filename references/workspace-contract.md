# Workspace contract

File-based. No database.

The private workspace uses a **default layout**. Agents must locate state
through this contract (or `workspace.yaml` path overrides), not by asking
the user to invent a repository structure.

## Roles

| Location | Role |
|---|---|
| This toolkit package | Read-only skills, scripts, templates, provider adapters |
| Parent workspace repository | User data and generated artifacts |

A typical embed path is `.agents/skills/meal-planner-toolkit` so any agent
that scans the portable Agent Skills location can load `SKILL.md`. The
toolkit must not require that path; discovery is by walking up to
`workspace.yaml` (or `preferences.md` + `recipes/`).

## Default layout

```text
workspace.yaml
preferences.md
staples.md
pantry.md
tools.md
recipes/
plans/
shopping/
  product-mappings.md
.agents/
  skills/
    meal-planner-toolkit/   # this toolkit, usually a submodule
```

Create missing files with:

```bash
python scripts/workspace.py --init
python scripts/workspace.py --check-initialized
python scripts/workspace.py --check-onboarding
```

`--init` never overwrites an existing user file.

## Config file

`workspace.yaml` at the workspace root is a small machine-readable locator.
User preferences stay in the Markdown files below, not in this YAML.

```yaml
version: 2
paths:
  preferences: preferences.md
  staples: staples.md
  pantry: pantry.md
  tools: tools.md
  recipes: recipes
  plans: plans
  shopping: shopping
```

Omit `paths` (or any key) to use the defaults above. `meal-planner.yaml` is
an accepted alias for the same file.

## File semantics

- `workspace.yaml` — locator/configuration for workspace paths and toolkit
  discovery. Not a preferences store.
- `preferences.md` — household size, location, store/banner, dietary
  restrictions, likes/dislikes, nutrition or planning goals, and cooking
  constraints that affect a first plan.
- `staples.md` — recurring non-recipe groceries to consider for purchase
  independently of the meal plan.
- `pantry.md` — ingredients the planner can normally assume are already on
  hand, subject to pantry checks where appropriate.
- `tools.md` — durable notes about cooking equipment and capacity that
  affect recipe selection and cooking-session planning. Starts mostly empty
  and accumulates details (unusual oven size, missing stovetop, specialty
  appliances). Not a full equipment inventory.
- `recipes/` — the user's normalized recipe collection (HTML cards or
  markdown). One file per recipe. `--init` writes a starter `README.md`
  so Git records the directory before any recipes exist.
- `plans/` — generated meal plans, cooking plans, shopping plans.
  `--init` writes a starter `README.md` so Git records the directory.
- `shopping/` — durable retailer/product knowledge. V2 keeps this minimal:
  `shopping/product-mappings.md` holds learned ingredient → preferred
  product, brand, and size. Generated cart or order records may appear
  later; they are not required. Never store credentials here.

## Provider / store configuration

- Human preferences (preferred store, banner, fulfillment) live in
  `preferences.md`.
- Tokens, account IDs, and runtime MCP config stay outside versioned
  Markdown (workspace `.env` and local MCP configuration).

## Secrets (workspace only, gitignored)

- `.env` — provider tokens and store/customer IDs
- `.browser-profile/` — saved browser session for token refresh
- `*.har` — captured network logs

The toolkit never stores these and never documents real values.

## Agent procedure

1. Locate the workspace root (see `scripts/workspace.py`).
2. If the layout is incomplete, run `--init` (or the onboarding workflow,
   which calls it). Existing files are left untouched.
3. If onboarding is incomplete (`preferences.md` missing or still the stock
   template), follow [onboarding.md](onboarding.md). Do not repeat the
   interview when preferences already contain user answers.
4. Read and write user state only under the workspace paths.
5. Leave this toolkit tree unchanged unless the user is developing the
   toolkit itself.
