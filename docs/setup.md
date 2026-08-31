# Setup

## Embed this toolkit in a private workspace

From the private workspace root:

```bash
git submodule add https://github.com/RevDrSquash/meal-planner-toolkit.git \
  .agents/skills/meal-planner-toolkit
git submodule update --init --recursive
```

`.agents/skills/` is the portable Agent Skills path (Cursor, Codex, Copilot,
and other hosts that scan that directory). The toolkit does not require
Claude Code, Cursor, or any other specific agent.

Clone later with:

```bash
git clone --recurse-submodules <your-private-workspace>
```

## Onboarding

Ask any agent in the workspace to run meal-planner onboarding, or follow
`references/onboarding.md` yourself.

```bash
python .agents/skills/meal-planner-toolkit/scripts/workspace.py --init
python .agents/skills/meal-planner-toolkit/scripts/workspace.py --check-initialized
python .agents/skills/meal-planner-toolkit/scripts/workspace.py --check-onboarding
```

`--init` creates the default layout (`preferences.md`, `staples.md`,
`pantry.md`, `tools.md`, `recipes/`, `plans/`, `shopping/`) from templates
and never overwrites existing user files. Empty `recipes/` and `plans/`
get a starter `README.md` so Git records those directories. The interview then fills
`preferences.md`. Re-running the entry skill does not repeat onboarding
once preferences contain user answers.

## Recipe tools

Python 3.10+. Recipe import has no third-party dependencies. Every input
becomes one HTML card; see [recipe-format.md](recipe-format.md).

```bash
python .agents/skills/meal-planner-toolkit/scripts/import_recipe.py <url>
python .agents/skills/meal-planner-toolkit/scripts/recipe_from_markdown.py recipe.md
python .agents/skills/meal-planner-toolkit/scripts/recipe_finder.py --check-collection
python .agents/skills/meal-planner-toolkit/scripts/recipe_finder.py --shortlist candidates.json \
  --request "vegetarian weeknight chili"
```

`--shortlist` ranks a JSON array of search hits (use mocked fixtures in
tests; do not call the web from the script). Selected URLs still go through
`import_recipe.py`. See [references/recipe-finder.md](../references/recipe-finder.md).

## Meal + cooking plans

Planning is one workflow: choose meals and group cooking sessions. Helpers
scale servings, merge ingredients, and render `plans/YYYY-MM-DD.md`. See
[references/meal-planning.md](../references/meal-planning.md).

```bash
python .agents/skills/meal-planner-toolkit/scripts/meal_plan.py eligible
python .agents/skills/meal-planner-toolkit/scripts/meal_plan.py scale \
  --from-servings 4 --to-servings 2 "500 g ground beef"
python .agents/skills/meal-planner-toolkit/scripts/meal_plan.py render plan.json \
  -o plans/YYYY-MM-DD.md
```

Agents can also copy `templates/recipe-template.html` into the workspace
`recipes/` directory and fill it in directly.

## Optional PC Express cart

See `references/pcexpress.md`. In short: vendor the reviewed
`FireBall1725/pcexpress-mcp-server` commit in the workspace, copy
`templates/env.example` to `.env`, copy `templates/gitignore.example`
into the workspace-root `.gitignore`, run the **upstream** one-time login
(`python vendor/pcexpress-mcp-server/setup.py`), and point your agent
host at `scripts/pcexpress.py --serve` using an example from
`examples/mcp/`.
