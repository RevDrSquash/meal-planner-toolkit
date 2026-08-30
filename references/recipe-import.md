# Recipe import

Normalize every recipe into one HTML card in the workspace `recipes/`
directory. Planning then reads that card format only — never a
source-specific parser, and never a second Markdown copy of the same
recipe.

The format is documented in [docs/recipe-format.md](../docs/recipe-format.md).
The fill-in HTML example is
[templates/recipe-template.html](../templates/recipe-template.html).

Scripts live in this toolkit. Output always goes to the **workspace**
`recipes/` path (`scripts/workspace.py --paths`).

## From a URL

Works with sites that publish schema.org/Recipe JSON-LD (HelloFresh and
most recipe blogs):

```bash
python scripts/import_recipe.py <recipe-url> [more-urls...]
python scripts/import_recipe.py --force <recipe-url>
```

`--recipes-dir` overrides the workspace default if needed. A local HTML
page that already contains Recipe JSON-LD can be passed as a file path
(useful for fixtures; do not fetch live sites in tests).

## From markdown

For family or cookbook recipes that are not online. Copy
[templates/recipe-template.md](../templates/recipe-template.md) into a
scratch file (workspace or /tmp), fill it in, then:

```bash
python scripts/recipe_from_markdown.py path/to/recipe.md
python scripts/import_recipe.py path/to/recipe.md
```

Only `# Name`, `## Ingredients`, and `## Instructions` are required. An
optional `- Source: https://...` line in the preamble is stored as
provenance. After conversion, discard the scratch markdown unless the
user wants it kept as an archive **outside** the canonical card.

## Hand-authored HTML

Copy [templates/recipe-template.html](../templates/recipe-template.html)
to `recipes/<slug>.html` and replace the example values. Keep the JSON-LD
block aligned with the visible sections.

## Nutrition

During ingest, the scripts keep sourced Calories / Protein / Fat /
Carbohydrates when the page or markdown provides them. If any of those
four are missing, they try a conservative ingredient-based estimate and
mark those rows as estimated. If the ingredients cannot be estimated
responsibly (no servings, too many unknown items), the macros stay
blank — do not invent numbers.

## Filenames and reruns

The output filename is a slug of the recipe name
(`Weeknight Chili` → `weeknight-chili.html`). Importing the same URL or
the same named recipe again does not create a second file. `--force`
overwrites the existing card.

## PDF

Not supported. Ask the user for a URL, markdown, or a filled HTML card.

## After import

- Confirm the new file is under the workspace `recipes/` directory and
  ends in `.html`.
- Confirm provenance (source URL or source filename) is in the footer
  when it was available.
- Offer to plan with the new recipe if that was the original ask.
