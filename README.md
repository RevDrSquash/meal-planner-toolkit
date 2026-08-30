# Meal Planner Toolkit

Shareable skills, scripts, and templates for AI-assisted meal planning.
Personal recipes, preferences, store history, and credentials stay in a
**private workspace** that embeds this repository as a Git submodule.

This package is agent-agnostic. It uses the [Agent Skills](https://agentskills.io)
`SKILL.md` format and the portable `.agents/skills/` embed path. It is not
tied to Claude Code, Cursor, or any other single host.

## What this repo contains

- Orchestrator skill (`SKILL.md`)
- Onboarding, planning, shopping, and grocery-search workflows
- Recipe import / markdown conversion scripts
- Output templates (meal plan, cooking plan, shopping list, nutrition)
- Grocery-provider contract and an optional PC Express adapter
- Synthetic example workspace and tests (no personal data)

## What this repo does not contain

- Anyone's `preferences.md`, recipes, plans, or product mappings
- Store account IDs, tokens, or provider session state
- A requirement to use a specific grocery chain

## Quick start (new private workspace)

```bash
mkdir my-meals && cd my-meals
git init
git submodule add https://github.com/RevDrSquash/meal-planner-toolkit.git \
  .agents/skills/meal-planner-toolkit
```

Then ask an agent to run meal-planner onboarding, or copy files from
`templates/` and `examples/workspace/`. Full steps: [docs/setup.md](docs/setup.md).

Entry point for any agent: read `SKILL.md`, locate the workspace, onboard
if needed, then route to the matching file under `references/`.

## Layout

```
SKILL.md                 # orchestrator
agents/                  # subagent instructions (host-neutral)
references/              # workflows and contracts
scripts/                 # import, workspace locator, PC Express pin/launcher
templates/               # empty/skeleton user files and plan formats
examples/workspace/      # synthetic household for docs/tests
tests/                   # synthetic fixtures only
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT. The optional PC Express MCP server is a separate project with its own
license; vendor the reviewed commit in the private workspace if you use
that adapter. See `references/pcexpress.md`.
