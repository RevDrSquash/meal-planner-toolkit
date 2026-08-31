"""Scale, normalize, and aggregate recipe ingredients for meal plans.

Nutrition estimates convert lines to grams. Planning and shopping need the
original culinary units (cups, cans, counts), then scale and merge them.
Downstream shopping receives these normalized requirements — never retailer
product IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable

from nutrition_estimate import (
    NUMBER_TOKEN,
    PAREN_QTY,
    SKIP_PHRASES,
    UNIT_TOKEN,
    _clean_food_name,
    _lighten_food_name,
    lookup_food,
    parse_number,
    parse_servings,
)

UNIT_CANON = {
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "mg": "mg",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "ml": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "cup": "cup",
    "cups": "cup",
    "can": "can",
    "cans": "can",
    "clove": "clove",
    "cloves": "clove",
    "bunch": "bunch",
    "bunches": "bunch",
    "slice": "slice",
    "slices": "slice",
}

# Convert within a family so "500 g" + "1 lb" can merge.
MASS_TO_G = {
    "mg": 0.001,
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.35,
    "lb": 453.6,
}
VOLUME_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "tsp": 5.0,
    "tbsp": 15.0,
    "cup": 240.0,
}
SPOON_UNITS = frozenset({"tsp", "tbsp"})

UNIT_DISPLAY = {
    "g": ("g", "g"),
    "kg": ("kg", "kg"),
    "mg": ("mg", "mg"),
    "lb": ("lb", "lb"),
    "oz": ("oz", "oz"),
    "ml": ("ml", "ml"),
    "l": ("l", "l"),
    "tsp": ("tsp", "tsp"),
    "tbsp": ("tbsp", "tbsp"),
    "cup": ("cup", "cups"),
    "can": ("can", "cans"),
    "clove": ("clove", "cloves"),
    "bunch": ("bunch", "bunches"),
    "slice": ("slice", "slices"),
}

FRACTION_CHARS = {
    0.125: "⅛",
    0.25: "¼",
    1.0 / 3.0: "⅓",
    0.5: "½",
    2.0 / 3.0: "⅔",
    0.75: "¾",
}

CATEGORY_ORDER = (
    "produce",
    "proteins",
    "dairy",
    "pantry",
    "frozen",
    "other",
)

PRODUCE = frozenset(
    {
        "apple",
        "avocado",
        "banana",
        "bell pepper",
        "broccoli",
        "cabbage",
        "carrot",
        "cauliflower",
        "celery",
        "corn",
        "cucumber",
        "garlic",
        "ginger",
        "green beans",
        "kale",
        "lemon",
        "lettuce",
        "lime",
        "mushroom",
        "onion",
        "peas",
        "potato",
        "spinach",
        "sweet potato",
        "tomato",
        "zucchini",
    }
)
PROTEINS = frozenset(
    {
        "bacon",
        "canned tuna",
        "chicken",
        "chicken breast",
        "chicken thigh",
        "cod",
        "egg",
        "ground beef",
        "ground chicken",
        "ground pork",
        "ground turkey",
        "ham",
        "lean ground beef",
        "pork",
        "pork chop",
        "salmon",
        "sausage",
        "shrimp",
        "steak",
        "tempeh",
        "tilapia",
        "tofu",
        "tuna",
        "turkey breast",
        "white fish",
    }
)
DAIRY = frozenset(
    {
        "butter",
        "cheddar",
        "cheese",
        "cream",
        "cream cheese",
        "feta",
        "greek yogurt",
        "heavy cream",
        "milk",
        "mozzarella",
        "parmesan",
        "skim milk",
        "sour cream",
        "whole milk",
        "yogurt",
    }
)
SPICES = frozenset(
    {
        "basil",
        "black pepper",
        "chili powder",
        "cinnamon",
        "cumin",
        "oregano",
        "paprika",
        "pepper",
        "salt",
    }
)

PRODUCT_ID_KEYS = frozenset(
    {"code", "product_id", "product-id", "sku", "upc", "item_code"}
)


@dataclass(frozen=True)
class IngredientQty:
    amount: float | None
    unit: str | None
    unit_size: str | None
    name: str
    notes: str
    original: str
    scalable: bool


class IngredientError(ValueError):
    """Invalid serving counts or ingredient input."""


def serving_scale(
    recipe_serves: str | float | None,
    planned_servings: float,
) -> tuple[float, str | None]:
    """Return (factor, note). Missing source servings leaves amounts unscaled."""
    if planned_servings < 0:
        raise IngredientError("planned servings must be non-negative")
    if isinstance(recipe_serves, (int, float)):
        base = float(recipe_serves) if recipe_serves > 0 else None
    else:
        base = parse_servings(recipe_serves)
    if base is None:
        return 1.0, "source recipe has no serving count; ingredients left unscaled"
    return planned_servings / base, None


def scale_amount(
    amount: float,
    from_servings: float,
    to_servings: float,
) -> float:
    if from_servings <= 0:
        raise IngredientError("from_servings must be a positive number")
    if to_servings < 0:
        raise IngredientError("to_servings must be non-negative")
    return amount * (to_servings / from_servings)


def normalize_name(name: str) -> str:
    """Canonical food name when known; otherwise a cleaned lowercase phrase."""
    text = (name or "").strip()
    if not text:
        return ""
    match = lookup_food(text)
    if match:
        return match[0]
    cleaned = _clean_food_name(text)
    return cleaned or _lighten_food_name(text) or text.lower()


def parse_ingredient_qty(line: str) -> IngredientQty:
    """Parse a recipe line into amount, culinary unit, and normalized name."""
    original = str(line).strip()
    if not original:
        return IngredientQty(None, None, None, "", "", original, False)

    lowered = original.lower()
    skip = any(phrase in lowered for phrase in SKIP_PHRASES)

    rest = original
    amount = None
    number_match = NUMBER_TOKEN.search(rest)
    if number_match:
        amount = parse_number(number_match.group(0))
        rest = rest[number_match.end() :].strip()

    unit = None
    unit_match = UNIT_TOKEN.match(rest)
    if unit_match:
        unit = _canon_unit(unit_match.group(0))
        rest = rest[unit_match.end() :].strip()

    unit_size = None
    paren = re.match(r"^\(([^)]+)\)\s*", rest)
    if paren:
        inner = paren.group(1).strip()
        qty = PAREN_QTY.search(inner)
        if qty:
            unit_size = f"{qty.group(1)} {qty.group(2).lower()}"
        else:
            unit_size = inner
        rest = rest[paren.end() :].strip()

    if unit is None:
        unit_match = UNIT_TOKEN.match(rest)
        if unit_match:
            unit = _canon_unit(unit_match.group(0))
            rest = rest[unit_match.end() :].strip()

    if unit is None:
        clove = re.search(r"\bcloves?\b", rest, re.IGNORECASE)
        if clove:
            unit = "clove"
            rest = (rest[: clove.start()] + rest[clove.end() :]).strip()

    notes = ""
    if "," in rest:
        food_part, notes_part = rest.split(",", 1)
        rest = food_part.strip()
        notes = notes_part.strip()

    name = normalize_name(rest) if rest else ""
    scalable = amount is not None and not skip
    return IngredientQty(amount, unit, unit_size, name, notes, original, scalable)


def scale_ingredient(
    qty: IngredientQty,
    from_servings: float,
    to_servings: float,
) -> IngredientQty:
    if not qty.scalable or qty.amount is None:
        return qty
    return replace(qty, amount=scale_amount(qty.amount, from_servings, to_servings))


def scale_ingredient_line(
    line: str,
    from_servings: float,
    to_servings: float,
) -> str:
    """Scale one recipe line. Unquantified lines (to taste) are unchanged."""
    qty = parse_ingredient_qty(line)
    if not qty.scalable:
        return str(line).strip()
    return format_ingredient_line(scale_ingredient(qty, from_servings, to_servings))


def format_amount(amount: float, unit: str | None = None) -> str:
    """Pretty-print a scaled quantity (unicode fractions for cups/spoons)."""
    if amount < 0:
        raise IngredientError("amount must be non-negative")
    metric = unit in {"g", "kg", "mg", "ml", "l", "oz", "lb"}
    if metric:
        if unit in {"g", "ml", "mg"} and abs(amount - round(amount)) < 0.05:
            return str(int(round(amount)))
        if abs(amount - round(amount)) < 1e-6:
            return str(int(round(amount)))
        text = f"{amount:.2f}".rstrip("0").rstrip(".")
        return text

    whole = int(amount)
    frac = amount - whole
    nearest = _nearest_fraction(frac)
    if nearest is None:
        if abs(amount - round(amount)) < 1e-6:
            return str(int(round(amount)))
        return f"{amount:.2f}".rstrip("0").rstrip(".")
    symbol = FRACTION_CHARS[nearest]
    if whole == 0:
        return symbol
    return f"{whole}{symbol}"


def format_ingredient_line(qty: IngredientQty) -> str:
    if qty.amount is None:
        return qty.original
    quantity = display_quantity(qty)
    name = qty.name or qty.original
    if qty.notes:
        return f"{quantity} {name}, {qty.notes}"
    return f"{quantity} {name}"


def display_quantity(qty: IngredientQty) -> str:
    """Quantity cell for the plan table (no product identifiers)."""
    if qty.amount is None:
        lowered = qty.original.lower()
        if "to taste" in lowered:
            return "to taste"
        if any(phrase in lowered for phrase in SKIP_PHRASES):
            return "as needed"
        return qty.original or "as needed"

    amount_text = format_amount(qty.amount, qty.unit)
    if qty.unit == "can" and qty.unit_size:
        noun = _unit_noun(qty.unit, qty.amount)
        return f"{amount_text} {noun} ({qty.unit_size})"
    if qty.unit:
        noun = _unit_noun(qty.unit, qty.amount)
        return f"{amount_text} {noun}"
    return amount_text


def categorize_ingredient(qty: IngredientQty) -> str:
    original = qty.original.lower()
    if "frozen" in original:
        return "frozen"
    name = qty.name
    if name in PRODUCE:
        return "produce"
    if name in PROTEINS:
        return "proteins"
    if name in DAIRY or name == "egg":
        return "dairy"
    if name in SPICES:
        return "pantry"
    if name:
        return "pantry"
    return "other"


def apply_ingredient_replacement(line: str, old: str, new: str) -> str:
    """Swap one ingredient name, keeping the scaled quantity and notes."""
    qty = parse_ingredient_qty(line)
    old_name = normalize_name(old)
    if qty.name and qty.name == old_name:
        replaced = replace(qty, name=normalize_name(new) or new.strip())
        return format_ingredient_line(replaced)
    pattern = re.compile(re.escape(old), re.IGNORECASE)
    if pattern.search(line):
        return pattern.sub(new, line, count=1)
    return line


def aggregate_ingredients(
    entries: Iterable[dict | tuple[str, str] | str],
) -> list[dict]:
    """Merge duplicate ingredients. Each entry is a line plus optional source.

    Entry shapes:
      ``"1 onion"``
      ``("1 onion", "Weeknight Chili")``
      ``{"line": "1 onion", "source": "Weeknight Chili"}``
    """
    buckets: dict[tuple[str, str | None, str | None], _Bucket] = {}
    passthrough: list[_Bucket] = []

    for entry in entries:
        line, source = _unpack_entry(entry)
        if not str(line).strip():
            continue
        qty = parse_ingredient_qty(str(line))
        if qty.amount is None or not qty.name:
            passthrough.append(
                _Bucket(
                    name=qty.name or str(line).strip(),
                    amount=None,
                    unit=qty.unit,
                    unit_size=qty.unit_size,
                    notes=[qty.notes] if qty.notes else [],
                    originals=[qty.original],
                    sources=[source] if source else [],
                    category=categorize_ingredient(qty),
                )
            )
            continue
        key = (qty.name, qty.unit, qty.unit_size)
        merged = False
        if key in buckets:
            buckets[key].add(qty, source)
            merged = True
        else:
            for existing_key, bucket in list(buckets.items()):
                if existing_key[0] != qty.name:
                    continue
                if existing_key[2] != qty.unit_size:
                    continue
                converted = _convert_amount(qty.amount, qty.unit, bucket.unit)
                if converted is None:
                    continue
                compatible = replace(qty, amount=converted, unit=bucket.unit)
                bucket.add(compatible, source)
                merged = True
                break
        if not merged:
            buckets[key] = _Bucket.from_qty(qty, source)

    combined = list(buckets.values()) + passthrough
    combined.sort(
        key=lambda item: (
            CATEGORY_ORDER.index(item.category)
            if item.category in CATEGORY_ORDER
            else len(CATEGORY_ORDER),
            item.name.lower(),
        )
    )
    return [item.as_dict() for item in combined]


def significant_names(lines: Iterable[str]) -> set[str]:
    """Normalized names that matter for cook-together overlap (not spices)."""
    names: set[str] = set()
    for line in lines:
        qty = parse_ingredient_qty(str(line))
        if not qty.name or qty.name in SPICES:
            continue
        names.add(qty.name)
    return names


def shared_ingredient_names(left: Iterable[str], right: Iterable[str]) -> list[str]:
    return sorted(significant_names(left) & significant_names(right))


def overlap_score(left: Iterable[str], right: Iterable[str]) -> float:
    """Jaccard overlap of significant ingredient names (0–1)."""
    names_left = significant_names(left)
    names_right = significant_names(right)
    if not names_left and not names_right:
        return 0.0
    union = names_left | names_right
    if not union:
        return 0.0
    return len(names_left & names_right) / len(union)


def assert_no_product_ids(requirement: dict) -> None:
    extra = PRODUCT_ID_KEYS & set(requirement)
    if extra:
        raise IngredientError(
            "ingredient requirements must not include product identifiers: "
            + ", ".join(sorted(extra))
        )


def _canon_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    return UNIT_CANON.get(raw.strip().lower(), raw.strip().lower())


def _unit_noun(unit: str, amount: float) -> str:
    singular, plural = UNIT_DISPLAY.get(unit, (unit, unit))
    if abs(amount - 1.0) < 1e-6:
        return singular
    return plural


def _nearest_fraction(frac: float, tolerance: float = 0.03) -> float | None:
    if frac < 1e-6:
        return 0.0 if frac >= 0 else None
    best = None
    best_delta = tolerance
    for value in FRACTION_CHARS:
        delta = abs(frac - value)
        if delta <= best_delta:
            best = value
            best_delta = delta
    return best


def _convert_amount(
    amount: float,
    from_unit: str | None,
    to_unit: str | None,
) -> float | None:
    if from_unit == to_unit:
        return amount
    if from_unit in MASS_TO_G and to_unit in MASS_TO_G:
        grams = amount * MASS_TO_G[from_unit]
        return grams / MASS_TO_G[to_unit]
    if from_unit in SPOON_UNITS and to_unit in SPOON_UNITS:
        ml = amount * VOLUME_TO_ML[from_unit]
        return ml / VOLUME_TO_ML[to_unit]
    if from_unit in {"ml", "l"} and to_unit in {"ml", "l"}:
        ml = amount * VOLUME_TO_ML[from_unit]
        return ml / VOLUME_TO_ML[to_unit]
    return None


def _unpack_entry(entry: dict | tuple | str) -> tuple[str, str | None]:
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, tuple):
        line = entry[0]
        source = entry[1] if len(entry) > 1 else None
        return str(line), str(source) if source else None
    if isinstance(entry, dict):
        line = entry.get("line") or entry.get("original") or ""
        source = entry.get("source") or entry.get("used_in")
        return str(line), str(source) if source else None
    return str(entry), None


@dataclass
class _Bucket:
    name: str
    amount: float | None
    unit: str | None
    unit_size: str | None
    notes: list[str]
    originals: list[str]
    sources: list[str]
    category: str

    @classmethod
    def from_qty(cls, qty: IngredientQty, source: str | None) -> _Bucket:
        return cls(
            name=qty.name,
            amount=qty.amount,
            unit=qty.unit,
            unit_size=qty.unit_size,
            notes=[qty.notes] if qty.notes else [],
            originals=[qty.original],
            sources=[source] if source else [],
            category=categorize_ingredient(qty),
        )

    def add(self, qty: IngredientQty, source: str | None) -> None:
        if self.amount is None or qty.amount is None:
            return
        self.amount += qty.amount
        if qty.notes and qty.notes not in self.notes:
            self.notes.append(qty.notes)
        if qty.original not in self.originals:
            self.originals.append(qty.original)
        if source and source not in self.sources:
            self.sources.append(source)

    def as_qty(self) -> IngredientQty:
        notes = ", ".join(self.notes)
        return IngredientQty(
            amount=self.amount,
            unit=self.unit,
            unit_size=self.unit_size,
            name=self.name,
            notes=notes,
            original=self.originals[0] if self.originals else self.name,
            scalable=self.amount is not None,
        )

    def as_dict(self) -> dict:
        qty = self.as_qty()
        payload = {
            "name": self.name,
            "amount": self.amount,
            "unit": self.unit,
            "unit_size": self.unit_size,
            "display": display_quantity(qty),
            "category": self.category,
            "used_in": list(self.sources),
        }
        assert_no_product_ids(payload)
        return payload
