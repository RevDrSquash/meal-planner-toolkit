# Shopping-list generation

The shopping list is the handoff between meal planning and grocery
providers. Planning finishes without a store integration. Shopping starts
from the plan's ingredient requirements, then adds staples and pantry
checks. The result is a retailer-independent list a human can take to any
store, and a grocery adapter can consume without meal-plan internals.

Do not put PC Express (or any other store) product IDs on this list.

## Intermediate representation

`scripts/shopping_list.py` writes a JSON artifact (`kind: shopping-list`)
and a human-readable markdown sibling. The JSON is the contract for
grocery search. Keep it small:

| Field | Meaning |
|---|---|
| `name` | Canonical ingredient name |
| `display` / `amount` / `unit` | Required quantity when known |
| `quantity_status` | `exact`, `approximate` (unit conversion), or `uncertain` |
| `parts` | Present only when amounts could not be combined |
| `role` | `essential`, `optional`, or `garnish` when known |
| `sources` | Recipe names that need this item |
| `pantry_status` | `buy`, `assumed_in_pantry`, or `needs_confirmation` |
| `origin` | `recipe` or `staple` |
| `substitutions` | Explicit planning swaps (`from` → `to`) |
| `notes` | Staple restock hints, temporary "we're out", etc. |

`quantity_status: uncertain` means "do not invent a single number." A
provider should search from `name` + `display` (and `parts` when present).

Forbidden on this artifact: `code`, `product_id`, `sku`, `upc`, `price`,
and any PC Express / offer identifiers.

## Inputs

- The plan JSON from `scripts/meal_plan.py` (ingredient requirements
  already scaled, aggregated, leftover meals excluded, deviations applied)
- `pantry.md` — assumed on-hand stock
- `staples.md` — recurring items added here, not baked into recipes
- Temporary "we're out" / "please confirm" overrides for this order only
- `shopping/product-mappings.md` — brand/size hints **after** the list
  exists, during [grocery-search.md](grocery-search.md)

## Procedure

1. Confirm the meal plan first ([meal-planning.md](meal-planning.md)).
2. Build the shopping list (no grocery MCP required):

   ```bash
   python scripts/shopping_list.py plans/YYYY-MM-DD.json \
     --pantry pantry.md --staples staples.md \
     -o plans/YYYY-MM-DD-shopping.json \
     --markdown-out plans/YYYY-MM-DD-shopping.md
   ```

   Workspace `pantry.md` and `staples.md` are used when those flags are
   omitted. Repeat `--pantry-out "soy sauce"` for this-order overrides.
3. Review the markdown with the user: to-buy, confirm-before-skipping,
   assumed pantry stock, uncertain quantities, and recorded substitutions.
4. If a grocery provider is configured, resolve **to-buy** items via
   [product-resolution.md](product-resolution.md) (search is delegated;
   the cart is not written). Leave `needs_confirmation` items unresolved
   until the user says they are actually out. Do not write product codes
   back onto the meal plan.
5. Excess flags (shoppable size far larger than needed) belong after
   product resolution, not in this intermediate list.

## Pantry and staple rules

- A clearly listed pantry item is `assumed_in_pantry` — show it on the
  pantry check, do not add it to to-buy.
- "When low" / "when below" / other uncertain wording is
  `needs_confirmation`. Do not silently skip or silently buy.
- Temporary "we're out" forces `buy` for this list only. Edit `pantry.md`
  only when the user says the change is lasting.
- Staples are attached at this step. A staple that matches a recipe item
  becomes a note on that row. A staple that is not in the recipes is a
  new row (`origin: staple`).
- Generic pantry "cooking oil" covers olive / vegetable / canola oil, not
  sesame or coconut oil.

## Learned mappings

When a product is confirmed in a cart or the user says "this is what I
buy", persist it with `python scripts/product_resolve.py remember` (or
append a line to `shopping/product-mappings.md`). Never invent a
product code. Never put mappings in this toolkit repo. Do not store raw
search results or cart snapshots; the shopping list lives with the plan.
