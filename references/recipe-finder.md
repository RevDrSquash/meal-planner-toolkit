# Recipe finder (V2 — lightweight discovery)

Help a user grow a small recipe library. This is **not** a recommendation
engine: no collaborative filtering, no long-term scoring, no search index,
and no bulk ingest without review.

When the user asks for a kind of recipe — or meal planning finds too few
local cards — collect public candidates, filter them against the workspace,
show a shortlist, and import only what they pick through
[recipe-import.md](recipe-import.md).

## Inputs

1. The natural-language request (cuisine, protein, time, diet, “something new”).
2. Workspace `preferences.md` — restrictions/allergies, dislikes, likes,
   meals per cycle, time constraints.
3. Workspace `tools.md` when appliance limits matter (for example no oven).
4. Workspace `recipes/*.html` — the existing library. Ignore leftover `.md`
   files; they are inputs, not the collection. See
   [docs/recipe-format.md](../docs/recipe-format.md).

Do not treat `examples/workspace/` as this user's library.

## Procedure

1. Locate the workspace (`python scripts/workspace.py --paths`). If
   onboarding is incomplete, follow [onboarding.md](onboarding.md) first.
2. Restate the request in one line (kind of dish, constraints you will honor).
3. Read preferences and index the local cards:

   ```bash
   python scripts/recipe_finder.py --index
   python scripts/recipe_finder.py --check-collection
   ```

4. Search the public web for candidates (host search, or a delegated turn
   using [agents/recipe-finder.md](../agents/recipe-finder.md)). Prefer sites
   that publish schema.org Recipe data so the importer can read them later.
   Do not scrape paywalled or login-gated pages. Do not copy a full recipe
   from a site that blocks it.
5. Gather a compact JSON array of hits (title, url, summary, and any
   visible tags / time / ingredients from the snippet). Do **not** call
   live search from the helper script. Pass the array through:

   ```bash
   python scripts/recipe_finder.py --shortlist candidates.json \
     --request "the user's request"
   ```

   The script drops near-duplicates of local cards, drops restriction and
   dislike violations, and ranks the rest. Tests use the same path with
   mocked JSON — never live HTTP.
6. Show 3–5 remaining picks. Each line should include the title, source
   link, time/servings/tags when known, a one-line summary, and why it
   survived the filter. Mention anything that was dropped as a duplicate
   or diet miss only if it helps the user understand the shortlist.
7. Stop. The user chooses which recipes to add. Do not import the whole
   list. Do not write cards they did not pick.
8. Import each chosen URL (or dictated family recipe) with
   [recipe-import.md](recipe-import.md) so the collection stays HTML-only:

   ```bash
   python scripts/import_recipe.py <chosen-url>
   ```

9. Confirm the new files are under the workspace `recipes/` directory.
   Offer to plan with them if that was the original ask.

If the user already has a specific URL or wants to dictate a family
recipe, skip search and go straight to import.

## Thin collection (from meal planning)

`--check-collection` compares the number of HTML cards to meals-per-cycle
(default 4). If the library is smaller than that, say so and offer this
workflow before forcing a thin plan. If they want to plan with what they
have, continue.

## Out of scope

- Personalized collaborative filtering or long-term recommendation scores
- Automatic bulk ingestion
- A dedicated recipe search index in this toolkit
