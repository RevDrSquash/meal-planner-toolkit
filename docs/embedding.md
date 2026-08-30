# Embedding this toolkit

This repository is meant to be added as a Git submodule of a **private**
personal workspace. Other users can embed the same public repo without
modification.

## Recommended path

```
.agents/skills/meal-planner-toolkit
```

That path is agent-agnostic. Hosts that only scan a vendor directory can
add a thin pointer in the **workspace** (not in this repo). Example for
Claude Code: a small `SKILL.md` at `.claude/skills/meal-planner-toolkit/`
that tells the agent to follow this package's `SKILL.md`.

Do not relocate user data into this package to make a host "see" files.

## What the workspace owns

Preferences, staples, pantry, recipes, plans, shopping mappings, `.env`,
PC Express token state (`.pcexpress-mcp/`), and MCP config. Updating this
submodule must not overwrite those files.

## What this package owns

Skills, agent instructions, templates, schemas, scripts, synthetic
examples, and tests. Treat it as read-only during normal meal planning.

## Discovery

Scripts and skills locate the workspace by walking up from the current
working directory to `workspace.yaml` (or `preferences.md` + `recipes/`).
They do not assume they live next to those files.
