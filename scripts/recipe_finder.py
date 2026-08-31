#!/usr/bin/env python3
"""Filter and rank recipe-discovery candidates against the local collection.

This is not a search engine or recommender. The agent (or a host web search)
collects public recipe URLs; this script applies deterministic preference
filters, near-duplicate checks, and a small ranking helper so the user can
review a compact shortlist. Selected URLs go through import_recipe.py.

Usage:
    python scripts/recipe_finder.py --index
    python scripts/recipe_finder.py --check-collection
    python scripts/recipe_finder.py --shortlist candidates.json \\
        --request "vegetarian weeknight chili"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from recipe_core import (
    is_same_recipe,
    normalize_url,
    peek_recipe_identity,
    recipe_from_jsonld,
    slugify,
)
from workspace import WorkspaceNotFoundError, find_workspace_root, workspace_paths

DEFAULT_SHORTLIST_LIMIT = 5
DEFAULT_MEALS_PER_CYCLE = 4
NEAR_DUPLICATE_THRESHOLD = 0.7
MIN_SHARED_TITLE_TOKENS = 2

META_LINE = re.compile(r"^-\s*([^:]+?)\s*:\s*(.*)$")
WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?", re.IGNORECASE)
UNDER_MINUTES = re.compile(
    r"(?:under|less than|at most|max(?:imum)?)\s+(\d+)\s*"
    r"(min(?:ute)?s?|hours?|hrs?)\b",
    re.IGNORECASE,
)
HOURS_PART = re.compile(r"(\d+)\s*(?:hrs?|hours?)\b", re.IGNORECASE)
MINUTES_PART = re.compile(r"(\d+)\s*(?:mins?|minutes?)\b", re.IGNORECASE)

NONE_VALUES = frozenset(
    {"", "none", "n/a", "na", "no", "nothing", "-", "nil", "null"}
)
TITLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "best",
        "classic",
        "delicious",
        "easy",
        "from",
        "great",
        "healthy",
        "homemade",
        "my",
        "of",
        "or",
        "our",
        "perfect",
        "quick",
        "recipe",
        "recipes",
        "simple",
        "the",
        "ultimate",
        "with",
        "yummy",
    }
)
REQUEST_FILLER = TITLE_STOPWORDS | frozenset(
    {
        "another",
        "can",
        "find",
        "for",
        "get",
        "i",
        "ideas",
        "looking",
        "me",
        "need",
        "new",
        "please",
        "search",
        "show",
        "some",
        "something",
        "suggest",
        "suggestion",
        "to",
        "want",
        "we",
        "you",
    }
)

MEAT = frozenset(
    {
        "bacon",
        "beef",
        "bison",
        "brisket",
        "chicken",
        "chorizo",
        "duck",
        "goose",
        "ham",
        "lamb",
        "meat",
        "pepperoni",
        "pork",
        "prosciutto",
        "ribs",
        "salami",
        "sausage",
        "steak",
        "turkey",
        "veal",
        "venison",
    }
)
FISH = frozenset(
    {
        "anchovy",
        "bass",
        "cod",
        "fish",
        "halibut",
        "mackerel",
        "salmon",
        "sardine",
        "swordfish",
        "tilapia",
        "trout",
        "tuna",
    }
)
SHELLFISH = frozenset(
    {
        "calamari",
        "clam",
        "crab",
        "lobster",
        "mussel",
        "octopus",
        "oyster",
        "prawn",
        "scallop",
        "shellfish",
        "shrimp",
        "squid",
    }
)
DAIRY = frozenset(
    {
        "butter",
        "cheddar",
        "cheese",
        "cream",
        "dairy",
        "milk",
        "mozzarella",
        "parmesan",
        "whey",
        "yogurt",
        "yoghurt",
    }
)
EGGS = frozenset({"egg", "eggs", "mayonnaise"})
HONEY = frozenset({"honey"})
GLUTEN = frozenset(
    {"barley", "bread", "couscous", "flour", "gluten", "pasta", "rye", "wheat"}
)
NUTS = frozenset(
    {
        "almond",
        "cashew",
        "hazelnut",
        "macadamia",
        "nut",
        "nuts",
        "peanut",
        "pecan",
        "pistachio",
        "walnut",
    }
)
DIETS = {
    "vegetarian": MEAT | FISH | SHELLFISH,
    "vegan": MEAT | FISH | SHELLFISH | DAIRY | EGGS | HONEY,
    "pescatarian": MEAT,
    "gluten-free": GLUTEN,
    "dairy-free": DAIRY,
    "nut-free": NUTS,
}
DIET_ALIASES = {
    "dairy free": "dairy-free",
    "dairy-free": "dairy-free",
    "gf": "gluten-free",
    "gluten free": "gluten-free",
    "gluten-free": "gluten-free",
    "nut free": "nut-free",
    "nut-free": "nut-free",
    "pescatarian": "pescatarian",
    "vegan": "vegan",
    "vegetarian": "vegetarian",
    "veggie": "vegetarian",
}
PLANT_DAIRY_PHRASES = (
    "almond milk",
    "cashew milk",
    "coconut cream",
    "coconut milk",
    "hemp milk",
    "oat cream",
    "oat milk",
    "plant butter",
    "rice milk",
    "soy milk",
    "vegan butter",
)
DAIRY_SCRUB_DIETS = frozenset({"dairy-free", "vegan"})

SCORE_REQUEST_TITLE = 3.0
SCORE_REQUEST_TAG = 1.5
SCORE_REQUEST_BODY = 1.0
SCORE_LIKE = 2.0
SCORE_TIME_FIT = 1.0
SCORE_TIME_OVER = -1.0
SCORE_HAS_URL = 0.25


def empty_preferences() -> dict:
    return {
        "restrictions": [],
        "dislikes": [],
        "likes": [],
        "goals": [],
        "cooking_constraints": [],
        "meals_per_cycle": None,
        "max_minutes": None,
    }


def parse_list_value(value: str) -> list[str]:
    """Split a preferences.md list value into items; treat 'none' as empty."""
    value = value.strip()
    if value.lower() in NONE_VALUES:
        return []
    parts = re.split(r"\s*(?:,|;|/|\band\b)\s*", value)
    items: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned and cleaned.lower() not in NONE_VALUES:
            items.append(cleaned)
    return items


def extract_max_minutes(text: str | None) -> int | None:
    """Read an upper time bound such as 'under 45 minutes'."""
    if not text:
        return None
    match = UNDER_MINUTES.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("hour") or unit.startswith("hr"):
        return amount * 60
    return amount


def parse_minutes(text: str | None) -> int | None:
    """Parse '45 min' or '1 hr 15 min' into an integer minute count."""
    if not text:
        return None
    hours = 0
    minutes = 0
    hour_match = HOURS_PART.search(text)
    minute_match = MINUTES_PART.search(text)
    if hour_match:
        hours = int(hour_match.group(1))
    if minute_match:
        minutes = int(minute_match.group(1))
    if hours or minutes:
        return hours * 60 + minutes
    return None


def parse_preferences(text: str) -> dict:
    """Extract diet, likes/dislikes, and planning hints from preferences.md."""
    prefs = empty_preferences()
    for raw in text.splitlines():
        match = META_LINE.match(raw.strip())
        if not match:
            continue
        label = match.group(1).strip().lower()
        value = match.group(2).strip()
        if "restriction" in label or "allerg" in label:
            prefs["restrictions"] = parse_list_value(value)
        elif "dislike" in label:
            prefs["dislikes"] = parse_list_value(value)
        elif "like" in label or "preference" in label:
            prefs["likes"] = parse_list_value(value)
        elif "goal" in label:
            prefs["goals"] = parse_list_value(value)
        elif "constraint" in label or "appliance" in label:
            prefs["cooking_constraints"] = parse_list_value(value)
            prefs["max_minutes"] = extract_max_minutes(value)
        elif "meals to plan" in label or "meals per" in label:
            numbers = re.findall(r"\d+", value)
            if numbers:
                prefs["meals_per_cycle"] = int(numbers[0])
    return prefs


def load_preferences(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return empty_preferences()
    return parse_preferences(path.read_text(encoding="utf-8"))


def expand_restriction(item: str) -> str:
    """Normalize 'peanut allergy' / 'no pork' / diet names into a match key."""
    raw = item.strip().lower()
    collapsed = raw.replace(" ", "-")
    if raw in DIET_ALIASES:
        return DIET_ALIASES[raw]
    if collapsed in DIET_ALIASES:
        return DIET_ALIASES[collapsed]
    spaced = raw.replace("-", " ")
    if spaced in DIET_ALIASES:
        return DIET_ALIASES[spaced]
    avoid = re.match(r"(?:no|without|avoid)\s+(.+)", raw)
    if avoid:
        return avoid.group(1).strip()
    allergy = re.match(r"(.+?)\s+allerg(?:y|ies)\b", raw)
    if allergy:
        return allergy.group(1).strip()
    allergic = re.match(r"allergic to\s+(.+)", raw)
    if allergic:
        return allergic.group(1).strip()
    return raw


def request_diet_keys(request: str) -> list[str]:
    """Diet words in the user's request, treated as extra restrictions."""
    lowered = f" {request.lower()} "
    found: list[str] = []
    for alias, key in DIET_ALIASES.items():
        needle = f" {alias} "
        hyphen = f" {alias.replace(' ', '-')} "
        if needle in lowered or hyphen in lowered:
            if key not in found:
                found.append(key)
    return found


def words(text: str) -> set[str]:
    return {match.group(0).lower() for match in WORD.finditer(text)}


def text_has_token(haystack: str, needle: str) -> bool:
    """True when *needle* appears as a word or conservative prefix match."""
    needle = needle.lower().strip()
    if not needle:
        return False
    lowered = haystack.lower()
    if " " in needle:
        return needle in lowered
    hay_words = words(haystack)
    if needle in hay_words:
        return True
    if len(needle) < 4:
        return False
    return any(token.startswith(needle) or needle.startswith(token) for token in hay_words if len(token) >= 4)


def scrub_plant_dairy(text: str) -> str:
    out = text.lower()
    for phrase in PLANT_DAIRY_PHRASES:
        out = out.replace(phrase, " ")
    return out


def candidate_blob(candidate: dict) -> str:
    parts = [
        candidate.get("title") or candidate.get("name") or "",
        candidate.get("summary") or "",
        " ".join(candidate.get("tags") or []),
        " ".join(candidate.get("ingredients") or []),
    ]
    return " ".join(str(part) for part in parts)


def restriction_hits(candidate: dict, prefs: dict, extra_restrictions: list[str] | None = None) -> list[str]:
    """Return human-readable reasons this candidate violates diet/dislikes."""
    hits: list[str] = []
    blob = candidate_blob(candidate)
    keys = [expand_restriction(item) for item in prefs.get("restrictions") or []]
    for extra in extra_restrictions or []:
        key = expand_restriction(extra)
        if key not in keys:
            keys.append(key)
    for key in keys:
        if key in DIETS:
            check = scrub_plant_dairy(blob) if key in DAIRY_SCRUB_DIETS else blob
            for food in sorted(DIETS[key]):
                if text_has_token(check, food):
                    hits.append(f"{key}: {food}")
                    break
        elif text_has_token(blob, key):
            hits.append(key)
    for dislike in prefs.get("dislikes") or []:
        if text_has_token(blob, dislike):
            hits.append(f"dislike: {dislike}")
    return hits


def normalize_title_tokens(title: str) -> set[str]:
    return {token for token in words(title) if token not in TITLE_STOPWORDS and len(token) > 1}


def title_jaccard(left: str, right: str) -> float:
    left_tokens = normalize_title_tokens(left)
    right_tokens = normalize_title_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def canonical_candidate_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    trimmed = str(url).strip().split("#")[0].split("?")[0]
    try:
        return normalize_url(trimmed)
    except Exception:
        return trimmed.rstrip("/")


def find_near_duplicate(candidate: dict, local_recipes: list[dict]) -> dict | None:
    """Return the local recipe this candidate duplicates, if any."""
    incoming = {
        "name": candidate.get("title") or candidate.get("name") or "",
        "source_url": canonical_candidate_url(
            candidate.get("url") or candidate.get("source_url")
        ),
        "source_file": candidate.get("source_file"),
    }
    incoming_name = incoming["name"]
    incoming_slug = None
    if incoming_name:
        try:
            incoming_slug = slugify(incoming_name)
        except ValueError:
            incoming_slug = None

    for local in local_recipes:
        comparable = {
            "name": local.get("name") or "",
            "source_url": canonical_candidate_url(local.get("source_url")),
            "source_file": local.get("source_file"),
        }
        if is_same_recipe(comparable, incoming):
            return local
        local_name = comparable["name"]
        if incoming_slug and local_name:
            try:
                if incoming_slug == slugify(local_name):
                    return local
            except ValueError:
                pass
        shared = normalize_title_tokens(incoming_name) & normalize_title_tokens(local_name)
        if len(shared) >= MIN_SHARED_TITLE_TOKENS:
            if title_jaccard(incoming_name, local_name) >= NEAR_DUPLICATE_THRESHOLD:
                return local
    return None


def load_local_recipes(recipes_dir: Path | None) -> list[dict]:
    """Index canonical HTML cards. Ignore leftover markdown inputs."""
    recipes: list[dict] = []
    if recipes_dir is None or not Path(recipes_dir).is_dir():
        return recipes
    for path in sorted(Path(recipes_dir).glob("*.html")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            parsed = recipe_from_jsonld(text, None)
        except RuntimeError:
            identity = peek_recipe_identity(text)
            parsed = {
                "name": identity.get("name") or path.stem,
                "source_url": identity.get("source_url"),
                "source_file": identity.get("source_file"),
                "tags": [],
                "ingredients": [],
                "total_time": None,
                "serves": None,
            }
        recipes.append(
            {
                "path": str(path),
                "filename": path.name,
                "name": parsed.get("name") or path.stem,
                "source_url": parsed.get("source_url"),
                "source_file": parsed.get("source_file"),
                "tags": list(parsed.get("tags") or []),
                "ingredients": list(parsed.get("ingredients") or []),
                "total_time": parsed.get("total_time"),
                "serves": parsed.get("serves"),
            }
        )
    return recipes


def collection_threshold(prefs: dict | None) -> int:
    meals = (prefs or {}).get("meals_per_cycle")
    if isinstance(meals, int) and meals > 0:
        return meals
    return DEFAULT_MEALS_PER_CYCLE


def collection_too_small(count: int, prefs: dict | None = None) -> bool:
    """True when the library is too thin for a useful meal plan."""
    return count < collection_threshold(prefs)


def request_tokens(request: str) -> list[str]:
    tokens: list[str] = []
    for token in words(request):
        if token in REQUEST_FILLER or len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _field_hits(tokens: list[str], text: str) -> list[str]:
    return [token for token in tokens if text_has_token(text, token)]


def score_candidate(
    candidate: dict,
    request: str,
    prefs: dict,
    max_minutes: int | None,
) -> tuple[float, list[str]]:
    """Return (score, why-lines). Higher is a better fit for the shortlist."""
    title = candidate.get("title") or candidate.get("name") or ""
    summary = candidate.get("summary") or ""
    tags = " ".join(candidate.get("tags") or [])
    ingredients = " ".join(candidate.get("ingredients") or [])
    body = f"{summary} {ingredients}"
    tokens = request_tokens(request)
    score = 0.0
    reasons: list[str] = []

    title_hits = _field_hits(tokens, title)
    if title_hits:
        score += SCORE_REQUEST_TITLE * len(title_hits)
        reasons.append("matches request: " + ", ".join(title_hits))

    remaining = [token for token in tokens if token not in title_hits]
    tag_hits = _field_hits(remaining, tags)
    if tag_hits:
        score += SCORE_REQUEST_TAG * len(tag_hits)
        reasons.append("tags: " + ", ".join(tag_hits))

    used = set(title_hits) | set(tag_hits)
    body_hits = _field_hits([token for token in tokens if token not in used], body)
    if body_hits:
        score += SCORE_REQUEST_BODY * len(body_hits)
        reasons.append("summary: " + ", ".join(body_hits))

    like_hits: list[str] = []
    blob = candidate_blob(candidate)
    for like in prefs.get("likes") or []:
        like_tokens = [
            token
            for token in words(like)
            if token not in TITLE_STOPWORDS and (len(token) >= 4 or "-" in token)
        ]
        if any(text_has_token(blob, token) for token in like_tokens) or text_has_token(
            blob, like
        ):
            like_hits.append(like)
    if like_hits:
        score += SCORE_LIKE * len(like_hits)
        reasons.append("fits likes: " + ", ".join(like_hits))

    candidate_minutes = parse_minutes(candidate.get("total_time"))
    if max_minutes and candidate_minutes:
        if candidate_minutes <= max_minutes:
            score += SCORE_TIME_FIT
            reasons.append(f"within {max_minutes} min")
        else:
            score += SCORE_TIME_OVER
            reasons.append(f"over {max_minutes} min")

    if canonical_candidate_url(candidate.get("url") or candidate.get("source_url")):
        score += SCORE_HAS_URL

    return score, reasons


def rank_candidates(
    candidates: list[dict],
    request: str,
    prefs: dict,
    local_recipes: list[dict],
    limit: int = DEFAULT_SHORTLIST_LIMIT,
) -> dict:
    """Filter and rank search hits. Does not fetch the web."""
    extra = request_diet_keys(request)
    max_minutes = extract_max_minutes(request) or prefs.get("max_minutes")
    shortlist: list[dict] = []
    excluded: list[dict] = []

    for raw in candidates:
        candidate = dict(raw)
        title = candidate.get("title") or candidate.get("name") or "Untitled"
        url = candidate.get("url") or candidate.get("source_url")
        duplicate = find_near_duplicate(candidate, local_recipes)
        if duplicate:
            excluded.append(
                {
                    "title": title,
                    "url": url,
                    "reason": f"near-duplicate of {duplicate.get('filename') or duplicate.get('name')}",
                }
            )
            continue
        hits = restriction_hits(candidate, prefs, extra)
        if hits:
            excluded.append(
                {
                    "title": title,
                    "url": url,
                    "reason": "restriction: " + "; ".join(hits),
                }
            )
            continue
        score, reasons = score_candidate(candidate, request, prefs, max_minutes)
        shortlist.append(
            {
                "title": title,
                "url": url,
                "summary": candidate.get("summary") or "",
                "tags": list(candidate.get("tags") or []),
                "ingredients": list(candidate.get("ingredients") or []),
                "total_time": candidate.get("total_time"),
                "serves": candidate.get("serves"),
                "score": score,
                "reasons": reasons,
            }
        )

    shortlist.sort(key=lambda item: (-item["score"], item["title"].lower()))
    return {
        "request": request,
        "local_count": len(local_recipes),
        "threshold": collection_threshold(prefs),
        "collection_too_small": collection_too_small(len(local_recipes), prefs),
        "excluded": excluded,
        "shortlist": shortlist[:limit],
    }


def load_candidates(path: Path) -> list[dict]:
    if str(path) == "-":
        data = json.load(sys.stdin)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]
    if not isinstance(data, list):
        raise ValueError("Candidate file must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def _workspace_paths_or_none() -> dict[str, Path] | None:
    try:
        return workspace_paths(find_workspace_root())
    except WorkspaceNotFoundError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Index local recipes and rank discovery candidates.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--index",
        action="store_true",
        help="Print the local HTML recipe index as JSON",
    )
    group.add_argument(
        "--check-collection",
        action="store_true",
        help="Print whether recipes/ is too small for a useful plan",
    )
    group.add_argument(
        "--shortlist",
        type=Path,
        metavar="CANDIDATES.json",
        help="Rank mocked/search candidates (JSON array; '-' for stdin)",
    )
    parser.add_argument("--request", default="", help="Natural-language recipe request")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SHORTLIST_LIMIT,
        help=f"Shortlist size (default: {DEFAULT_SHORTLIST_LIMIT})",
    )
    parser.add_argument("--preferences", type=Path, default=None)
    parser.add_argument("--recipes-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    paths = _workspace_paths_or_none()
    recipes_dir = args.recipes_dir
    preferences_path = args.preferences
    if paths:
        recipes_dir = recipes_dir or paths["recipes"]
        preferences_path = preferences_path or paths["preferences"]

    prefs = load_preferences(preferences_path)
    local_recipes = load_local_recipes(recipes_dir)

    if args.index:
        json.dump({"count": len(local_recipes), "recipes": local_recipes}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.check_collection:
        threshold = collection_threshold(prefs)
        too_small = collection_too_small(len(local_recipes), prefs)
        print(f"recipes\t{len(local_recipes)}")
        print(f"threshold\t{threshold}")
        print(f"too_small\t{str(too_small).lower()}")
        return 0

    if not args.request.strip():
        print("--request is required with --shortlist", file=sys.stderr)
        return 2
    try:
        candidates = load_candidates(args.shortlist)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error reading candidates: {exc}", file=sys.stderr)
        return 1
    result = rank_candidates(
        candidates,
        args.request,
        prefs,
        local_recipes,
        limit=args.limit,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
