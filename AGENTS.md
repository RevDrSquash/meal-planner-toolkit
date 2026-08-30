# Meal Planner Toolkit (this repository)

This repository is the **shareable toolkit**, not a household workspace.

- User state belongs in the private parent that embeds this package.
- During normal meal planning, treat this tree as read-only.
- Entry point: `SKILL.md`. Workflows live under `references/`.
- Scripts locate the parent workspace via `scripts/workspace.py`.
- Do not add real preferences, recipes, product IDs, or credentials here.
  Tests and `examples/workspace/` must stay synthetic.

If you opened this folder directly (not as a submodule), you are developing
the toolkit. See `README.md` and `docs/embedding.md`.
