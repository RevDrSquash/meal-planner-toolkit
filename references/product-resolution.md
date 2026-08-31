# Product resolution

Resolve retailer-independent ingredient requirements into compact grocery
product picks. This workflow is **read-only**: it does not add, remove, or
edit cart items.

Cart fill stays in the parent conversation after the user confirms the
plan, pantry check, excess flags, and the [proposed cart](cart.md). See
[grocery-provider.md](grocery-provider.md).

## Durable shopping knowledge

V2 stores one file in the private workspace:

```text
shopping/
  product-mappings.md   # learned ingredient → preferred product/brand/size
```

That file is enough. Meal-plan shopping lists already live with the plan
under `plans/`. Do not persist raw catalog dumps, generated cart snapshots,
or order history unless a later workflow has a concrete need.

Never write mappings into this toolkit package. Never invent a product id.

## Inputs

- The shopping list / plan **Ingredient requirements** (names, quantities,
  categories — not product IDs)
- Workspace `shopping/product-mappings.md`
- Workspace `preferences.md` (diet, dislikes, optional preferred brands
  and deal notes)
- Optional mocked or subagent search hits (JSON)

Helpers (no retailer network I/O):

```bash
python scripts/product_resolve.py lookup "soy sauce" "milk"
python scripts/product_resolve.py resolve requirements.json
python scripts/product_resolve.py resolve requirements.json --candidates hits.json
python scripts/product_resolve.py rank --needed "2 tbsp soy sauce" hits.json
python scripts/product_resolve.py remember --ingredient "soy sauce" \
  --brand "Example Brand" --name "soy sauce" --size "500 ml" --id EXAMPLE-SOY-500
```

`remember` writes only to the discovered workspace mappings file (or
`--mappings`). It refuses the toolkit tree.

## Procedure

1. Build the to-buy list with [shopping-list.md](shopping-list.md).
2. Look up learned mappings first:

   ```bash
   python scripts/product_resolve.py resolve requirements.json
   ```

   Each row includes `probe`:

   | `probe` | Meaning |
   |---|---|
   | `skip` | Use the mapping as a hint (no id, or no provider) |
   | `details` | Check that product id only — do **not** run a broad search |
   | `search` | No usable mapping; delegate a catalog search |

3. If a grocery provider is available, resolve remaining rows without
   dumping catalog pages into the main thread:

   - `details`: fetch that one product (price, size, availability).
   - `search`: split the leftover list into 2–4 batches and follow
     [grocery-search.md](grocery-search.md) /
     [agents/grocery-search.md](../agents/grocery-search.md).

4. Re-run resolve with the compact candidate JSON the searcher returned:

   ```bash
   python scripts/product_resolve.py resolve requirements.json --candidates hits.json
   ```

   The helper prefers a learned mapping when that product is still
   available, diet-safe, and not a perishable oversupply. It falls back
   to search ranking when the known product is missing, the pack is
   inappropriate, or another option is materially cheaper or a much
   better size.

5. Show compact PICKs, 0–2 ALTs, a one-line why, and **excess flags**.
   Substantial oversupply is never a silent add. For optional garnishes,
   offer to skip.

6. Stop. Build a [proposed cart](cart.md) from the picks and wait for
   approval before any cart mutation. After the user confirms a product
   (or it is actually added to a cart), `remember` that brand/size/id —
   not the raw search response.

## Without a provider

Skip live search. Use mappings as brand/size hints and leave prices
blank. Produce a shopping list the user can take to any store.

## Ranking notes

The helper scores mocked candidates on availability, dietary
constraints, learned mapping / preferred brand, sale flags, unit price,
and package-size waste. It does not call a store. Tests and CI must use
synthetic JSON only.
