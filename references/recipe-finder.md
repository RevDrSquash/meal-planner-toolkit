# Recipe finder (V2 — minimal)

This toolkit does not ship a recipe search index or recommendation engine.

When the user's `recipes/` collection cannot satisfy the request:

1. Say what is missing (cuisine, protein, time, diet).
2. Ask whether they want to import a specific URL, dictate a family
   recipe, or have you suggest public recipe URLs to import.
3. If they pick URLs or dictate a family recipe, import them with
   [recipe-import.md](recipe-import.md) so the collection stays HTML-only.
4. Do not scrape paywalled or login-gated content. Do not copy a full
   recipe from a site that blocks it; prefer the importer + the site's
   own structured data.

Keep suggestions generic. Do not assume the example recipes in
`examples/workspace/` belong to this user.
