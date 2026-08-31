"""Estimate per-serving calories, protein, fat, and carbohydrates.

Used when a recipe source does not provide complete macros. Values are
approximate household figures (typical raw weights), not lab measurements.
Callers must mark any values produced here as estimates.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# kcal, protein g, fat g, carbohydrate g — per 100 g, typical raw weights.
FOODS: dict[str, tuple[float, float, float, float]] = {
    # proteins
    "bacon": (541, 37.0, 42.0, 1.4),
    "canned tuna": (116, 26.0, 0.8, 0.0),
    "chicken": (165, 31.0, 3.6, 0.0),
    "chicken breast": (120, 22.5, 2.6, 0.0),
    "chicken thigh": (180, 19.0, 11.0, 0.0),
    "cod": (82, 18.0, 0.7, 0.0),
    "egg": (143, 12.6, 9.5, 0.7),
    "ground beef": (254, 17.2, 20.0, 0.0),
    "ground chicken": (143, 17.4, 8.1, 0.0),
    "ground pork": (263, 16.9, 21.2, 0.0),
    "ground turkey": (203, 18.7, 13.3, 0.0),
    "ham": (145, 21.0, 6.0, 1.5),
    "lean ground beef": (215, 26.0, 12.0, 0.0),
    "pork": (242, 27.0, 14.0, 0.0),
    "pork chop": (231, 24.0, 14.0, 0.0),
    "salmon": (208, 20.0, 13.0, 0.0),
    "sausage": (301, 12.0, 27.0, 2.0),
    "shrimp": (99, 24.0, 0.3, 0.2),
    "steak": (271, 25.0, 19.0, 0.0),
    "tempeh": (193, 20.0, 11.0, 8.0),
    "tilapia": (96, 20.0, 1.7, 0.0),
    "tofu": (76, 8.0, 4.8, 1.9),
    "tuna": (132, 28.0, 1.3, 0.0),
    "turkey breast": (135, 30.0, 1.0, 0.0),
    "white fish": (90, 18.0, 1.5, 0.0),
    # dairy
    "butter": (717, 0.9, 81.0, 0.1),
    "cheddar": (403, 25.0, 33.0, 1.3),
    "cheese": (350, 22.0, 28.0, 2.4),
    "cream": (340, 2.8, 36.0, 2.8),
    "cream cheese": (342, 6.2, 34.0, 4.1),
    "feta": (264, 14.0, 21.0, 4.1),
    "greek yogurt": (97, 9.0, 5.0, 3.9),
    "heavy cream": (340, 2.8, 36.0, 2.8),
    "milk": (61, 3.2, 3.3, 4.8),
    "mozzarella": (280, 28.0, 17.0, 3.1),
    "parmesan": (431, 38.0, 29.0, 4.1),
    "skim milk": (34, 3.4, 0.1, 5.0),
    "sour cream": (198, 2.4, 19.0, 4.6),
    "whole milk": (61, 3.2, 3.3, 4.8),
    "yogurt": (61, 3.5, 3.3, 4.7),
    # grains / starches
    "bread": (265, 9.0, 3.2, 49.0),
    "brown rice": (360, 7.5, 2.7, 76.0),
    "couscous": (376, 13.0, 0.6, 77.0),
    "flour": (364, 10.3, 1.0, 76.0),
    "noodles": (138, 4.5, 2.1, 25.0),
    "oats": (389, 16.9, 6.9, 66.0),
    "pasta": (371, 13.0, 1.5, 75.0),
    "potato": (77, 2.0, 0.1, 17.0),
    "quinoa": (368, 14.0, 6.1, 64.0),
    "rice": (365, 7.1, 0.7, 80.0),
    "spaghetti": (371, 13.0, 1.5, 75.0),
    "sweet potato": (86, 1.6, 0.1, 20.0),
    "tortilla": (312, 8.0, 7.0, 52.0),
    "white rice": (365, 7.1, 0.7, 80.0),
    # vegetables
    "avocado": (160, 2.0, 15.0, 9.0),
    "bell pepper": (31, 1.0, 0.3, 6.0),
    "broccoli": (34, 2.8, 0.4, 7.0),
    "cabbage": (25, 1.3, 0.1, 6.0),
    "carrot": (41, 0.9, 0.2, 10.0),
    "cauliflower": (25, 1.9, 0.3, 5.0),
    "celery": (16, 0.7, 0.2, 3.0),
    "corn": (86, 3.3, 1.4, 19.0),
    "cucumber": (15, 0.7, 0.1, 3.6),
    "garlic": (149, 6.4, 0.5, 33.0),
    "ginger": (80, 1.8, 0.8, 18.0),
    "green beans": (31, 1.8, 0.2, 7.0),
    "kale": (49, 4.3, 0.9, 9.0),
    "lettuce": (15, 1.4, 0.2, 2.9),
    "mushroom": (22, 3.1, 0.3, 3.3),
    "onion": (40, 1.1, 0.1, 9.3),
    "peas": (81, 5.4, 0.4, 14.0),
    "spinach": (23, 2.9, 0.4, 3.6),
    "tomato": (18, 0.9, 0.2, 3.9),
    "zucchini": (17, 1.2, 0.3, 3.1),
    # canned / pantry produce
    "canned tomato": (32, 1.6, 0.2, 7.0),
    "canned tomatoes": (32, 1.6, 0.2, 7.0),
    "crushed tomato": (32, 1.6, 0.2, 7.0),
    "diced tomato": (32, 1.6, 0.2, 7.0),
    "diced tomatoes": (32, 1.6, 0.2, 7.0),
    "tomato paste": (82, 4.3, 0.5, 19.0),
    "tomato sauce": (29, 1.3, 0.2, 6.7),
    # legumes
    "black beans": (132, 8.9, 0.5, 24.0),
    "chickpea": (164, 8.9, 2.6, 27.0),
    "chickpeas": (164, 8.9, 2.6, 27.0),
    "kidney bean": (127, 8.7, 0.5, 23.0),
    "kidney beans": (127, 8.7, 0.5, 23.0),
    "lentils": (116, 9.0, 0.4, 20.0),
    "pinto beans": (143, 9.0, 0.7, 26.0),
    "red kidney beans": (127, 8.7, 0.5, 23.0),
    # fruit
    "apple": (52, 0.3, 0.2, 14.0),
    "banana": (89, 1.1, 0.3, 23.0),
    "lemon": (29, 1.1, 0.3, 9.3),
    "lime": (30, 0.7, 0.2, 11.0),
    # fats / pantry
    "broth": (4, 0.4, 0.2, 0.3),
    "brown sugar": (380, 0.1, 0.0, 98.0),
    "canola oil": (884, 0.0, 100.0, 0.0),
    "chicken broth": (4, 0.4, 0.2, 0.3),
    "coconut milk": (197, 2.0, 21.0, 3.0),
    "coconut oil": (892, 0.0, 99.0, 0.0),
    "honey": (304, 0.3, 0.0, 82.0),
    "maple syrup": (260, 0.0, 0.1, 67.0),
    "oil": (884, 0.0, 100.0, 0.0),
    "olive oil": (884, 0.0, 100.0, 0.0),
    "sesame oil": (884, 0.0, 100.0, 0.0),
    "soy sauce": (53, 8.1, 0.1, 4.9),
    "stock": (4, 0.4, 0.2, 0.3),
    "sugar": (387, 0.0, 0.0, 100.0),
    "vegetable oil": (884, 0.0, 100.0, 0.0),
    # seasonings (small mass, included so they match rather than block)
    "basil": (23, 3.2, 0.6, 2.7),
    "black pepper": (251, 10.0, 3.3, 64.0),
    "chili powder": (282, 13.5, 14.3, 50.0),
    "cinnamon": (247, 4.0, 1.2, 81.0),
    "cumin": (375, 18.0, 22.0, 44.0),
    "oregano": (265, 9.0, 4.3, 69.0),
    "paprika": (282, 14.0, 13.0, 54.0),
    "pepper": (251, 10.0, 3.3, 64.0),
    "salt": (0, 0.0, 0.0, 0.0),
}

# Typical grams when the recipe gives a count instead of a weight.
COUNT_GRAMS: dict[str, float] = {
    "apple": 180,
    "avocado": 150,
    "banana": 120,
    "bell pepper": 120,
    "carrot": 60,
    "celery": 40,
    "egg": 50,
    "garlic": 3,
    "lemon": 60,
    "lime": 50,
    "onion": 150,
    "potato": 170,
    "sweet potato": 170,
    "tomato": 120,
    "tortilla": 45,
}

# Cup weights that differ enough from 240 g water to be worth special-casing.
CUP_GRAMS: dict[str, float] = {
    "broth": 240,
    "brown sugar": 220,
    "butter": 227,
    "cheese": 110,
    "flour": 120,
    "milk": 244,
    "oats": 80,
    "oil": 218,
    "olive oil": 218,
    "rice": 185,
    "sugar": 200,
    "yogurt": 245,
}

ALIASES: dict[str, str] = {
    "all purpose flour": "flour",
    "beef": "ground beef",
    "black pepper": "pepper",
    "canola oil": "oil",
    "celery stalk": "celery",
    "chicken stock": "chicken broth",
    "clove garlic": "garlic",
    "cloves garlic": "garlic",
    "extra virgin olive oil": "olive oil",
    "garlic clove": "garlic",
    "garlic cloves": "garlic",
    "ground black pepper": "pepper",
    "kidney bean": "kidney beans",
    "large egg": "egg",
    "lean ground beef": "lean ground beef",
    "olive oil": "olive oil",
    "red kidney bean": "kidney beans",
    "red onion": "onion",
    "roma tomato": "tomato",
    "spaghetti": "pasta",
    "unsalted butter": "butter",
    "vegetable oil": "oil",
    "yellow onion": "onion",
}

PREP_WORDS = {
    "and",
    "boneless",
    "canned",
    "chopped",
    "cooked",
    "crushed",
    "cubed",
    "diced",
    "divided",
    "drained",
    "dried",
    "fine",
    "finely",
    "fresh",
    "freshly",
    "frozen",
    "grated",
    "halved",
    "julienned",
    "large",
    "lean",
    "medium",
    "minced",
    "optional",
    "peeled",
    "plus",
    "raw",
    "rinsed",
    "seeded",
    "shredded",
    "sliced",
    "small",
    "softened",
    "to",
    "trimmed",
    "uncooked",
    "undrained",
    "virgin",
    "whole",
}

SPICES = {
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

SKIP_PHRASES = (
    "to taste",
    "for serving",
    "for garnish",
    "as needed",
    "optional",
)

UNIT_GRAMS = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "lb": 453.6,
    "lbs": 453.6,
    "pound": 453.6,
    "pounds": 453.6,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "ml": 1.0,
    "l": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "litre": 1000.0,
    "litres": 1000.0,
    "tsp": 5.0,
    "teaspoon": 5.0,
    "teaspoons": 5.0,
    "tbsp": 15.0,
    "tablespoon": 15.0,
    "tablespoons": 15.0,
}

CAN_DEFAULT_GRAMS = 400.0
CLOVE_GRAMS = 3.0
DEFAULT_CUP_GRAMS = 240.0

FRACTIONS = {
    "½": 0.5,
    "¼": 0.25,
    "¾": 0.75,
    "⅓": 1.0 / 3.0,
    "⅔": 2.0 / 3.0,
    "⅛": 0.125,
}

NUMBER_TOKEN = re.compile(
    r"(?P<mixed>\d+\s+\d+\s*/\s*\d+)"
    r"|(?P<mixed_unicode>\d+\s*[½¼¾⅓⅔⅛])"
    r"|(?P<frac>\d+\s*/\s*\d+)"
    r"|(?P<decimal>\d+(?:\.\d+)?)"
    r"|(?P<unicode>[½¼¾⅓⅔⅛])"
)
# Longer tokens first; \b so "l"/"g"/"can" do not eat "large"/"garlic"/"canned".
UNIT_TOKEN = re.compile(
    r"(kg|mg|g|lbs|pounds?|lb|ounces?|oz|ml|litres?|liters?|l|"
    r"cups?|tbsp|tablespoons?|tsp|teaspoons?|"
    r"cans?|cloves?|bunches|bunch|slices?)\b",
    re.IGNORECASE,
)
PAREN_QTY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kg|g|mg|lb|lbs|oz|ml|l)\b",
    re.IGNORECASE,
)


class ParsedIngredient(NamedTuple):
    grams: float | None
    food: str
    original: str
    macros: tuple[float, float, float, float] | None


def parse_servings(serves: str | None) -> float | None:
    """Return the first positive number in a serves/yield string."""
    if serves is None:
        return None
    text = str(serves).strip()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def parse_number(token: str) -> float | None:
    token = token.strip()
    if not token:
        return None
    if token in FRACTIONS:
        return FRACTIONS[token]
    mixed_unicode = re.match(r"^(\d+)\s*([½¼¾⅓⅔⅛])$", token)
    if mixed_unicode:
        return int(mixed_unicode.group(1)) + FRACTIONS[mixed_unicode.group(2)]
    mixed = re.match(r"^(\d+)\s+(\d+)\s*/\s*(\d+)$", token)
    if mixed:
        whole, num, den = (int(p) for p in mixed.groups())
        if den == 0:
            return None
        return whole + (num / den)
    frac = re.match(r"^(\d+)\s*/\s*(\d+)$", token)
    if frac:
        num, den = (int(p) for p in frac.groups())
        if den == 0:
            return None
        return num / den
    try:
        return float(token)
    except ValueError:
        return None


def lookup_food(name: str) -> tuple[str, tuple[float, float, float, float]] | None:
    """Return (canonical name, per-100g macros) or None."""
    lightly = _lighten_food_name(name)
    stripped = _clean_food_name(name)
    if not lightly and not stripped:
        return None
    for candidate in [
        *_lookup_candidates(lightly),
        *_lookup_candidates(stripped),
    ]:
        resolved = _resolve_food_key(candidate)
        if resolved:
            return resolved
    keys = sorted(set(FOODS) | set(ALIASES), key=len, reverse=True)
    for key in keys:
        if _phrase_in(key, lightly) or _phrase_in(key, stripped):
            resolved = _resolve_food_key(key)
            if resolved:
                return resolved
    return None


def parse_ingredient(line: str) -> ParsedIngredient:
    original = line.strip()
    lowered = original.lower()
    if not original or any(phrase in lowered for phrase in SKIP_PHRASES):
        return ParsedIngredient(None, original, original, None)

    rest = original
    amount = None
    number_match = NUMBER_TOKEN.search(rest)
    if number_match:
        amount = parse_number(number_match.group(0))
        rest = rest[number_match.end() :].strip()

    unit = None
    unit_match = UNIT_TOKEN.match(rest)
    if unit_match:
        unit = unit_match.group(0).lower()
        rest = rest[unit_match.end() :].strip()

    paren_grams = None
    paren = re.match(r"^\(([^)]+)\)\s*", rest)
    if paren:
        inner = paren.group(1)
        qty = PAREN_QTY.search(inner)
        if qty:
            paren_val = float(qty.group(1))
            paren_unit = qty.group(2).lower()
            paren_grams = paren_val * UNIT_GRAMS.get(paren_unit, 1.0)
        rest = rest[paren.end() :].strip()

    # "2 (796 ml) cans ..." — unit can sit after the parenthetical size.
    if unit is None:
        unit_match = UNIT_TOKEN.match(rest)
        if unit_match:
            unit = unit_match.group(0).lower()
            rest = rest[unit_match.end() :].strip()

    # Drop trailing prep after a comma: "onion, diced"
    food = rest.split(",")[0].strip()
    food = re.sub(r"\s+", " ", food)
    match = lookup_food(food)
    macros = match[1] if match else None
    canonical = match[0] if match else food

    grams = _to_grams(amount, unit, canonical, paren_grams)
    return ParsedIngredient(grams, canonical, original, macros)


def estimate_macros(
    ingredients: list[str],
    serves: str | None,
) -> dict[str, str] | None:
    """Return per-serving Calories/Protein/Fat/Carbohydrates strings, or None.

    Returns None when a responsible estimate is not possible (unknown servings,
    too few recognized ingredients, or the unmatched remainder looks substantial).
    """
    servings = parse_servings(serves)
    if servings is None:
        return None

    parsed = [parse_ingredient(item) for item in ingredients if str(item).strip()]
    if not parsed:
        return None

    matched = [item for item in parsed if item.macros is not None and item.grams]
    if len(matched) < 2:
        return None

    unmatched_substantial = [
        item
        for item in parsed
        if item not in matched and _looks_substantial(item)
    ]
    if unmatched_substantial:
        return None

    totals = [0.0, 0.0, 0.0, 0.0]  # kcal, protein, fat, carbs
    for item in matched:
        factor = item.grams / 100.0
        for index, value in enumerate(item.macros or ()):
            totals[index] += value * factor

    if totals[0] < 50:
        return None

    per = [value / servings for value in totals]
    return {
        "calories": f"{_round_calories(per[0])} kcal",
        "protein": f"{_round_grams(per[1])} g",
        "fat": f"{_round_grams(per[2])} g",
        "carbohydrates": f"{_round_grams(per[3])} g",
    }


def _lighten_food_name(name: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    return re.sub(r"\s+", " ", text).strip()


def _clean_food_name(name: str) -> str:
    tokens = [tok for tok in _lighten_food_name(name).split() if tok not in PREP_WORDS]
    return " ".join(tokens)


def _resolve_food_key(candidate: str) -> tuple[str, tuple[float, float, float, float]] | None:
    if candidate in FOODS:
        return candidate, FOODS[candidate]
    aliased = ALIASES.get(candidate)
    if aliased and aliased in FOODS:
        return aliased, FOODS[aliased]
    return None


def _phrase_in(needle: str, haystack: str) -> bool:
    """True when needle's words appear contiguously in haystack (not substrings)."""
    if not needle or not haystack:
        return False
    needle_words = needle.split()
    hay_words = haystack.split()
    size = len(needle_words)
    if size == 0 or size > len(hay_words):
        return False
    for index in range(len(hay_words) - size + 1):
        if hay_words[index : index + size] == needle_words:
            return True
    return False


def _lookup_candidates(cleaned: str) -> list[str]:
    candidates = [cleaned]
    if cleaned.endswith("es") and len(cleaned) > 4:
        candidates.append(cleaned[:-2])
    if cleaned.endswith("s") and len(cleaned) > 3:
        candidates.append(cleaned[:-1])
    return candidates


def _to_grams(
    amount: float | None,
    unit: str | None,
    food: str,
    paren_grams: float | None,
) -> float | None:
    if paren_grams is not None:
        # "1 can (796 ml) ..." or "2 (796 ml) cans ..." — size is the useful mass.
        if amount is None:
            return paren_grams
        if _is_can_unit(unit, food):
            return paren_grams * amount
        return paren_grams

    if amount is None:
        return None

    if unit is None:
        if food in COUNT_GRAMS:
            return amount * COUNT_GRAMS[food]
        return None

    if _is_can_unit(unit, food):
        return amount * CAN_DEFAULT_GRAMS
    if unit.startswith("clove"):
        return amount * COUNT_GRAMS.get(food, CLOVE_GRAMS)
    if unit.startswith("cup"):
        return amount * CUP_GRAMS.get(food, DEFAULT_CUP_GRAMS)
    if unit.startswith("bunch") or unit.startswith("slice"):
        return None
    if unit in UNIT_GRAMS:
        return amount * UNIT_GRAMS[unit]
    return None


def _is_can_unit(unit: str | None, food: str) -> bool:
    if unit and unit.startswith("can"):
        return True
    first = _lighten_food_name(food).split()[:1]
    return first == ["can"] or first == ["cans"]


def _looks_substantial(item: ParsedIngredient) -> bool:
    if any(phrase in item.original.lower() for phrase in SKIP_PHRASES):
        return False
    if item.food in SPICES or _clean_food_name(item.food) in SPICES:
        return False
    if item.grams is not None and item.grams >= 50:
        return True
    # A counted/weighed line we could not match, e.g. "400 g seitan".
    return bool(NUMBER_TOKEN.match(item.original.strip()))


def _round_calories(value: float) -> int:
    return int(round(value / 10.0) * 10)


def _round_grams(value: float) -> int:
    return max(0, int(round(value)))
