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
`references/onboarding.md` yourself. Required result: `workspace.yaml`,
`preferences.md`, and a `recipes/` directory.

```bash
python .agents/skills/meal-planner-toolkit/scripts/workspace.py --check-onboarding
```

## Recipe tools

Python 3.10+. Recipe import has no third-party dependencies.

```bash
python .agents/skills/meal-planner-toolkit/scripts/import_recipe.py <url>
python .agents/skills/meal-planner-toolkit/scripts/recipe_from_markdown.py recipe.md
```

## Optional PC Express cart

See `references/pcexpress.md`. In short: vendor the reviewed
`FireBall1725/pcexpress-mcp-server` commit in the workspace, copy
`templates/env.example` to `.env`, run the **upstream** one-time login
(`python vendor/pcexpress-mcp-server/setup.py`), and point your agent
host at `scripts/pcexpress.py --serve` using an example from
`examples/mcp/`.
