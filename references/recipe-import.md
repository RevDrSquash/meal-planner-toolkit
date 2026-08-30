# Recipe import

Normalize recipes into the workspace `recipes/` directory so planning can
read a consistent card format.

Scripts live in this toolkit. Output always goes to the **workspace**
`recipes/` path (`scripts/workspace.py --paths`).

## From a URL

Works with sites that publish schema.org/Recipe JSON-LD (HelloFresh and
most recipe blogs):

```bash
python scripts/import_recipe.py <recipe-url> [more-urls...]
python scripts/import_recipe.py --force <recipe-url>
```

`--recipes-dir` overrides the workspace default if needed.

## From markdown

For family or cookbook recipes that are not online. Copy
[templates/recipe-template.md](../templates/recipe-template.md) into a
scratch file (workspace or /tmp), fill it in, then:

```bash
python scripts/recipe_from_markdown.py path/to/recipe.md
```

Only `# Name`, `## Ingredients`, and `## Instructions` are required.

## After import

- Confirm the new file is under the workspace `recipes/` directory.
- Do not commit the scratch markdown unless the user wants a source copy
  kept next to the HTML card.
- Offer to plan with the new recipe if that was the original ask.
