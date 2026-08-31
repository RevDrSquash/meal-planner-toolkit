# Plans

Generated meal + cooking plans, shopping-list artifacts, and optional
proposed-cart reviews belong here. A V2 plan is one markdown file:
schedule, cooking sessions, deviations, nutrition, and ingredient
requirements. The shopping list is a sibling JSON + markdown pair from
`scripts/shopping_list.py`. After products are resolved,
`scripts/cart.py propose` writes `YYYY-MM-DD-cart.json` / `.md`.

This file keeps the directory in Git until the first plan is written.
