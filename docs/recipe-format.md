# Canonical recipe format

Every recipe in a workspace `recipes/` directory is a single HTML card.
That card is the only stored representation. Do not keep a second Markdown
copy of the same recipe unless the user explicitly wants a source archive.

Meal-planning skills read these cards. They should not grow source-specific
parsers for HelloFresh, blogs, family notebooks, or anything else. All of
that happens at ingest time.

## What a card contains

The importer and [templates/recipe-template.html](../templates/recipe-template.html)
use the same structure:

| Field | Where to read it | Required |
|---|---|---|
| Name | `h1` and JSON-LD `name` | yes |
| Servings | `.meta` (`Serves N`) and JSON-LD `recipeYield` | no |
| Time | `.meta` and JSON-LD `totalTime` | no |
| Tags | `.meta` (comma-separated) and JSON-LD `recipeCategory` | no |
| Ingredients | `section.ingredients li` and JSON-LD `recipeIngredient` | yes |
| Instructions | `ol.steps .step-body` and JSON-LD `recipeInstructions` | yes |
| Nutrition | `section.nutrition table` and JSON-LD `nutrition` | no, but expected when possible |
| Notes | `section.notes .notes-body` | no |
| Image | `img.hero` and JSON-LD `image` | no |
| Provenance | footer `Source` / `Source file`; JSON-LD `url` / `isBasedOn` | when known |

Nutrition figures are **per serving**. The four macros planning cares about
are Calories, Protein, Fat, and Carbohydrates, in that order. Extra rows
(fiber, sodium, …) are allowed after those.

When a value was inferred from ingredients rather than taken from the
source, the table row has `data-estimated="true"` and a
`<span class="estimate">(estimated)</span>`, and the JSON-LD lists that
label on `additionalProperty` `nutritionEstimatedLabels`. Missing macros
are allowed when a responsible estimate is not possible.

## How recipes get into `recipes/`

All of these produce the same HTML:

1. **URL** — `python scripts/import_recipe.py <url>`  
   Sites that publish schema.org Recipe JSON-LD (most recipe blogs, HelloFresh).
2. **Markdown** — copy [templates/recipe-template.md](../templates/recipe-template.md),
   fill it in, then `python scripts/recipe_from_markdown.py my-recipe.md`  
   (or pass the `.md` file to `import_recipe.py`).
3. **Hand-authored HTML** — copy [templates/recipe-template.html](../templates/recipe-template.html)
   into `recipes/<slug>.html` and replace the example values. Keep the JSON-LD
   block in sync with the visible card.
4. **Discovery** — [references/recipe-finder.md](../references/recipe-finder.md)
   shortlists public URLs against preferences and the local collection.
   Chosen recipes still go through `import_recipe.py`; there is no second
   storage format.

PDF files are not ingested. Convert them to markdown or HTML first.

Filenames are a slug of the recipe name (`Weeknight Chili` →
`weeknight-chili.html`). Re-running an import is a no-op when the same
recipe (same name or same source URL/file) is already present. Use
`--force` to overwrite.

## What planning should do

1. List `recipes/*.html`.
2. Read each card’s visible sections or its JSON-LD Recipe block. Both
   describe the same recipe.
3. Ignore any leftover `.md` files; they are inputs, not the collection.
4. Scale, adapt, and aggregate with `scripts/meal_plan.py` /
   `scripts/ingredients.py`. Write one plan artifact under `plans/`.

See [references/meal-planning.md](../references/meal-planning.md) and
[references/recipe-import.md](../references/recipe-import.md).
