"""Shared helpers for importing recipes into the workspace recipes/ directory.

A recipe is a normalized dict with these keys:

    name          str
    serves        str | None
    total_time    str | None   (human readable, e.g. "35 min")
    tags          list[str]
    image         str | None   (remote URL, not downloaded)
    ingredients   list[str]
    nutrition     list[dict]   {label, value, estimated}
    instructions  list[str]    each item is safe HTML for one step
    notes         str | None   safe HTML
    source_url    str | None
    source_file   str | None

URL import, markdown conversion, and the HTML template all build this dict
and pass it to render_html() so every card uses the same documented format.
See docs/recipe-format.md.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from nutrition_estimate import estimate_macros

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
JSON_LD_BLOCK = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
HEX_ID_SUFFIX = re.compile(r"-[0-9a-f]{16,}$", re.IGNORECASE)
ISO_DURATION = re.compile(
    r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$",
    re.IGNORECASE,
)
SLUG_STRIP = re.compile(r"[^a-z0-9]+")
H1_BLOCK = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)
SOURCE_URL_BLOCK = re.compile(
    r'<p>\s*Source:\s*<a href="([^"]+)"',
    re.IGNORECASE,
)
SOURCE_FILE_BLOCK = re.compile(
    r"<p>\s*Source file:\s*([^<]+)</p>",
    re.IGNORECASE,
)
TAG_STRIP = re.compile(r"<[^>]+>")

# schema.org NutritionInformation key -> display label, in display order.
NUTRITION_LABELS = [
    ("calories", "Calories"),
    ("proteinContent", "Protein"),
    ("fatContent", "Fat"),
    ("carbohydrateContent", "Carbohydrates"),
    ("saturatedFatContent", "Saturated fat"),
    ("sugarContent", "Sugar"),
    ("fiberContent", "Fiber"),
    ("cholesterolContent", "Cholesterol"),
    ("sodiumContent", "Sodium"),
    ("servingSize", "Serving size"),
]
CORE_MACROS = ("Calories", "Protein", "Fat", "Carbohydrates")
LABEL_TO_SCHEMA = {label: key for key, label in NUTRITION_LABELS}
ESTIMATE_PROPERTY = "nutritionEstimatedLabels"

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
RECIPE_TEMPLATE = TOOLKIT_ROOT / "templates" / "recipe-template.html"


class RecipeExistsError(FileExistsError):
    """Raised when write_recipe would overwrite or collide with a file."""

    def __init__(self, path, same_recipe: bool = False):
        self.path = Path(path)
        self.same_recipe = same_recipe
        if same_recipe:
            message = (
                f"{self.path} already exists (same recipe; "
                "rerun is a no-op, use --force to overwrite)"
            )
        else:
            message = f"{self.path} already exists (use --force to overwrite)"
        super().__init__(message)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/")


def slug_from_url(url: str) -> str:
    path = url.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
    path = HEX_ID_SUFFIX.sub("", path)
    if not path:
        raise ValueError(f"Could not derive a slug from URL: {url}")
    return path


def slugify(text: str) -> str:
    slug = SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"Could not derive a slug from: {text!r}")
    return slug


def fetch_page(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc


def format_duration(iso_duration: str | None) -> str | None:
    if not iso_duration:
        return None
    match = ISO_DURATION.match(iso_duration.strip())
    if not match:
        return iso_duration
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hr" if hours == 1 else f"{hours} hrs")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds and not hours and not minutes:
        parts.append(f"{seconds} sec")
    return " ".join(parts) if parts else None


def iso_duration_from_human(text: str | None) -> str | None:
    """Best-effort conversion of '1 hr 20 min' back to a schema.org duration."""
    if not text:
        return None
    stripped = text.strip()
    if ISO_DURATION.match(stripped):
        return stripped
    hours = re.search(r"(\d+)\s*hrs?", stripped, re.IGNORECASE)
    minutes = re.search(r"(\d+)\s*min", stripped, re.IGNORECASE)
    seconds = re.search(r"(\d+)\s*sec", stripped, re.IGNORECASE)
    h = int(hours.group(1)) if hours else 0
    m = int(minutes.group(1)) if minutes else 0
    s = int(seconds.group(1)) if seconds else 0
    if not (h or m or s):
        return None
    out = "PT"
    if h:
        out += f"{h}H"
    if m:
        out += f"{m}M"
    if s:
        out += f"{s}S"
    return out


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _plain_text(value: str) -> str:
    text = TAG_STRIP.sub(" ", value)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _instruction_html(step) -> str:
    """Return safe HTML for a schema.org recipe instruction step."""
    if isinstance(step, str):
        return html.escape(step)
    if isinstance(step, dict):
        item_type = step.get("@type")
        if item_type == "HowToSection":
            items = _as_list(step.get("itemListElement"))
            inner = "\n".join(_instruction_html(item) for item in items)
            name = step.get("name")
            if name:
                return f"<strong>{html.escape(str(name))}</strong>\n{inner}"
            return inner
        text = step.get("text") or step.get("name") or ""
        return html.escape(str(text)) if text else ""
    return html.escape(str(step))


def _normalize_nutrition_item(item) -> dict[str, object]:
    if isinstance(item, dict):
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        return {
            "label": label,
            "value": value,
            "estimated": bool(item.get("estimated")),
        }
    label, value = item
    return {"label": str(label), "value": str(value), "estimated": False}


def _sort_nutrition(items: list[dict]) -> list[dict]:
    order = {label: index for index, label in enumerate(CORE_MACROS)}
    extras_start = len(CORE_MACROS)

    def key(item: dict) -> tuple[int, str]:
        label = str(item.get("label") or "")
        return (order.get(label, extras_start), label.lower())

    return sorted(items, key=key)


def _nutrition_from_schema(nutrition_src, estimated_labels: set[str]) -> list[dict]:
    nutrition: list[dict] = []
    if not isinstance(nutrition_src, dict):
        return nutrition
    for key, label in NUTRITION_LABELS:
        value = nutrition_src.get(key)
        if value is None or not str(value).strip():
            continue
        nutrition.append(
            {
                "label": label,
                "value": str(value).strip(),
                "estimated": label in estimated_labels,
            }
        )
    return nutrition


def _estimated_labels_from_jsonld(recipe: dict) -> set[str]:
    labels: set[str] = set()
    for prop in _as_list(recipe.get("additionalProperty")):
        if not isinstance(prop, dict):
            continue
        if prop.get("name") != ESTIMATE_PROPERTY:
            continue
        raw = prop.get("value") or ""
        for part in str(raw).split(","):
            name = part.strip()
            if name:
                labels.add(name)
    return labels


def recipe_from_jsonld(page_html: str, source_url: str | None) -> dict:
    """Extract a normalized recipe from a page's schema.org Recipe JSON-LD."""
    recipe = _find_recipe_object(page_html)
    if recipe is None:
        raise RuntimeError(
            "No schema.org Recipe data found on the page. The site may not "
            "publish structured recipe data, or the URL may be wrong."
        )

    image = recipe.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")

    tags: list[str] = []
    for key in ("recipeCuisine", "recipeCategory"):
        for value in _as_list(recipe.get(key)):
            if value and str(value) not in tags:
                tags.append(str(value))

    estimated_labels = _estimated_labels_from_jsonld(recipe)
    nutrition = _nutrition_from_schema(recipe.get("nutrition") or {}, estimated_labels)

    instructions = [
        text
        for text in (_instruction_html(s) for s in _as_list(recipe.get("recipeInstructions")))
        if text
    ]

    serves = recipe.get("recipeYield")
    if isinstance(serves, list):
        serves = serves[0] if serves else None

    source_file = recipe.get("isBasedOn")
    if isinstance(source_file, dict):
        source_file = source_file.get("name") or source_file.get("url")
    if source_file is not None:
        source_file = str(source_file).strip() or None
        if source_file and source_file.startswith(("http://", "https://")):
            # isBasedOn is sometimes a URL; prefer source_url for those.
            if not source_url:
                source_url = source_file
            source_file = None

    json_url = recipe.get("url")
    if json_url and not source_url:
        source_url = str(json_url)

    return {
        "name": recipe.get("name") or "Untitled Recipe",
        "serves": None if serves is None else str(serves),
        "total_time": format_duration(recipe.get("totalTime")),
        "tags": tags,
        "image": str(image) if image else None,
        "ingredients": [str(i) for i in _as_list(recipe.get("recipeIngredient"))],
        "nutrition": nutrition,
        "instructions": instructions,
        "notes": None,
        "source_url": source_url,
        "source_file": source_file,
    }


def _find_recipe_object(page_html: str):
    for match in JSON_LD_BLOCK.finditer(page_html):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found = _search_for_recipe(data)
        if found is not None:
            return found
    return None


def _search_for_recipe(node):
    """Recursively find a Recipe object within JSON-LD (handles @graph)."""
    if isinstance(node, list):
        for item in node:
            found = _search_for_recipe(item)
            if found is not None:
                return found
        return None
    if isinstance(node, dict):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "Recipe" in types:
            return node
        if "@graph" in node:
            return _search_for_recipe(node["@graph"])
    return None


def enrich_nutrition(recipe: dict) -> dict:
    """Fill missing core macros from ingredients when a responsible estimate exists."""
    nutrition = [
        _normalize_nutrition_item(item) for item in (recipe.get("nutrition") or [])
    ]
    nutrition = [item for item in nutrition if item["label"] and item["value"]]
    have = {str(item["label"]).lower() for item in nutrition}
    missing = [label for label in CORE_MACROS if label.lower() not in have]
    if missing:
        estimated = estimate_macros(recipe.get("ingredients") or [], recipe.get("serves"))
        if estimated:
            for label in missing:
                value = estimated.get(label.lower())
                if value:
                    nutrition.append(
                        {"label": label, "value": value, "estimated": True}
                    )
    recipe["nutrition"] = _sort_nutrition(nutrition)
    return recipe


def recipe_filename(recipe: dict) -> str:
    """Stable, human-readable filename from the recipe name (URL slug as fallback)."""
    name = (recipe.get("name") or "").strip()
    if name and name != "Untitled Recipe":
        try:
            return f"{slugify(name)}.html"
        except ValueError:
            pass
    if recipe.get("source_url"):
        return f"{slug_from_url(str(recipe['source_url']))}.html"
    if recipe.get("source_file"):
        stem = Path(str(recipe["source_file"])).stem
        if stem:
            try:
                return f"{slugify(stem)}.html"
            except ValueError:
                pass
    try:
        return f"{slugify(name or 'recipe')}.html"
    except ValueError:
        return "recipe.html"


def peek_recipe_identity(page_html: str) -> dict:
    """Read name / provenance from a canonical card without a full parse."""
    identity = {"name": None, "source_url": None, "source_file": None}
    try:
        parsed = recipe_from_jsonld(page_html, None)
        identity["name"] = parsed.get("name")
        identity["source_url"] = parsed.get("source_url")
        identity["source_file"] = parsed.get("source_file")
    except RuntimeError:
        pass
    if not identity["name"]:
        match = H1_BLOCK.search(page_html)
        if match:
            identity["name"] = _plain_text(match.group(1)) or None
    if not identity["source_url"]:
        match = SOURCE_URL_BLOCK.search(page_html)
        if match:
            identity["source_url"] = match.group(1).strip()
    if not identity["source_file"]:
        match = SOURCE_FILE_BLOCK.search(page_html)
        if match:
            identity["source_file"] = match.group(1).strip()
    return identity


def _sources_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.rstrip("/").lower() == right.rstrip("/").lower()


def is_same_recipe(existing: dict, incoming: dict) -> bool:
    """True when identity fields say these are the same stored recipe."""
    if _sources_match(existing.get("source_url"), incoming.get("source_url")):
        return True
    existing_name = (existing.get("name") or "").strip().lower()
    incoming_name = (incoming.get("name") or "").strip().lower()
    existing_file = existing.get("source_file")
    incoming_file = incoming.get("source_file")
    if existing_file and incoming_file and existing_file == incoming_file:
        # Scratch markdown is often reused (scratch.md); the basename is
        # not identity when the titles differ.
        if existing_name and incoming_name and existing_name != incoming_name:
            return False
        return True
    if existing_name and incoming_name and existing_name == incoming_name:
        return True
    return False


def find_existing_recipe(recipes_dir: Path, recipe: dict) -> Path | None:
    """Return a card that is already this recipe, if one exists."""
    recipes_dir = Path(recipes_dir)
    if not recipes_dir.is_dir():
        return None
    intended = recipes_dir / recipe_filename(recipe)
    if intended.exists():
        try:
            identity = peek_recipe_identity(intended.read_text(encoding="utf-8"))
        except OSError:
            identity = {}
        if is_same_recipe(identity, recipe):
            return intended
    source_url = recipe.get("source_url")
    source_file = recipe.get("source_file")
    if not source_url and not source_file:
        return None
    for path in sorted(recipes_dir.glob("*.html")):
        try:
            identity = peek_recipe_identity(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if source_url and _sources_match(identity.get("source_url"), source_url):
            return path
        if source_file and identity.get("source_file") == source_file:
            if is_same_recipe(identity, recipe):
                return path
    return None


def render_html(recipe: dict, imported_on: str | None = None) -> str:
    recipe = enrich_nutrition(dict(recipe))
    name = recipe.get("name") or "Untitled Recipe"
    escaped_name = html.escape(name)
    imported_on = imported_on or date.today().isoformat()

    meta_parts: list[str] = []
    if recipe.get("serves"):
        meta_parts.append(f"Serves {html.escape(str(recipe['serves']))}")
    if recipe.get("total_time"):
        meta_parts.append(html.escape(str(recipe["total_time"])))
    if recipe.get("tags"):
        meta_parts.append(html.escape(", ".join(recipe["tags"])))
    meta_line = " &middot; ".join(meta_parts)

    image_block = ""
    if recipe.get("image"):
        escaped_image = html.escape(str(recipe["image"]))
        image_block = (
            f'    <img class="hero" src="{escaped_image}" '
            f'alt="{escaped_name}">\n'
        )

    ingredients = recipe.get("ingredients") or []
    ingredient_items = "\n".join(
        f"        <li>{html.escape(str(item))}</li>" for item in ingredients
    ) or "        <li>(none listed)</li>"

    nutrition_section = _nutrition_section_html(recipe.get("nutrition") or [])

    steps = recipe.get("instructions") or []
    step_blocks = []
    for index, step in enumerate(steps, start=1):
        step_blocks.append(
            "      <li class=\"step\">\n"
            f"        <h3>Step {index}</h3>\n"
            f"        <div class=\"step-body\">{step}</div>\n"
            "      </li>"
        )
    steps_html = (
        "\n".join(step_blocks)
        or '      <li class="step"><div class="step-body">(no instructions)</div></li>'
    )

    notes_section = ""
    if recipe.get("notes"):
        notes_section = (
            '    <section class="notes">\n'
            "      <h2>Notes</h2>\n"
            f'      <div class="notes-body">{recipe["notes"]}</div>\n'
            "    </section>\n"
        )

    footer_html = _footer_html(recipe, imported_on)
    json_ld = json.dumps(recipe_jsonld(recipe), indent=2, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name}</title>
  <script type="application/ld+json">
{json_ld}
  </script>
  <style>
    :root {{
      --text: #242424;
      --muted: #676767;
      --border: #e8e8e8;
      --accent: #067646;
      --bg: #fafafa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Helvetica Neue", Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.5;
    }}
    .page {{
      max-width: 860px;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
      background: #fff;
    }}
    h1 {{
      margin: 0 0 0.35rem;
      font-size: 1.85rem;
      line-height: 1.2;
    }}
    .meta {{
      color: var(--muted);
      margin-bottom: 1.25rem;
    }}
    .hero {{
      width: 100%;
      max-height: 420px;
      object-fit: cover;
      border-radius: 8px;
      margin-bottom: 1.75rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 2rem;
      margin-bottom: 2rem;
    }}
    @media (max-width: 700px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
    h2 {{
      margin: 0 0 0.75rem;
      font-size: 1.1rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .ingredients ul {{
      margin: 0;
      padding-left: 1.2rem;
    }}
    .ingredients li {{
      margin-bottom: 0.35rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.95rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.35rem 0;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 500;
      width: 55%;
    }}
    .estimate {{
      color: var(--muted);
      font-weight: 400;
      font-size: 0.85em;
    }}
    .nutrition-note {{
      color: var(--muted);
      font-size: 0.85rem;
      margin-top: 0.75rem;
    }}
    .steps {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .step {{
      margin-bottom: 1.5rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
    }}
    .step:last-child {{
      border-bottom: none;
      padding-bottom: 0;
    }}
    .step h3 {{
      margin: 0 0 0.5rem;
      color: var(--accent);
      font-size: 0.95rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .step-body ul {{
      margin: 0;
      padding-left: 1.2rem;
    }}
    .step-body li {{
      margin-bottom: 0.35rem;
    }}
    .notes {{
      margin-top: 2rem;
    }}
    footer {{
      margin-top: 2rem;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
      color: var(--muted);
      font-size: 0.85rem;
    }}
    footer a {{ color: var(--accent); }}
    @media print {{
      body {{ background: #fff; }}
      .page {{ max-width: none; padding: 0; }}
      .step {{ page-break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <article class="page">
    <header>
      <h1>{escaped_name}</h1>
      <p class="meta">{meta_line}</p>
    </header>
{image_block}    <div class="grid">
      <section class="ingredients">
        <h2>Ingredients</h2>
        <ul>
{ingredient_items}
        </ul>
      </section>
{nutrition_section}
    </div>
    <section class="instructions">
      <h2>Instructions</h2>
      <ol class="steps">
{steps_html}
      </ol>
    </section>
{notes_section}    <footer>
{footer_html}
    </footer>
  </article>
</body>
</html>
"""


def _nutrition_section_html(nutrition: list) -> str:
    items = [_normalize_nutrition_item(item) for item in nutrition]
    items = [item for item in items if item["label"] and item["value"]]
    if not items:
        return ""
    rows = []
    any_estimated = False
    for item in items:
        estimated = bool(item["estimated"])
        any_estimated = any_estimated or estimated
        mark = (
            ' <span class="estimate">(estimated)</span>' if estimated else ""
        )
        attr = ' data-estimated="true"' if estimated else ""
        rows.append(
            f'          <tr{attr}><th scope="row">'
            f"{html.escape(str(item['label']))}</th>"
            f"<td>{html.escape(str(item['value']))}{mark}</td></tr>"
        )
    note = ""
    if any_estimated:
        note = (
            '        <p class="nutrition-note">Values marked estimated were '
            "inferred from ingredients, not provided by the source.</p>\n"
        )
    return (
        '      <section class="nutrition">\n'
        "        <h2>Nutrition</h2>\n"
        "        <p class=\"nutrition-basis\">Per serving</p>\n"
        "        <table>\n"
        f"{chr(10).join(rows)}\n"
        "        </table>\n"
        f"{note}"
        "      </section>"
    )


def _footer_html(recipe: dict, imported_on: str) -> str:
    lines: list[str] = []
    source_url = recipe.get("source_url")
    if source_url:
        escaped_source = html.escape(str(source_url))
        lines.append(
            f'      <p>Source: <a href="{escaped_source}">{escaped_source}</a></p>'
        )
    source_file = recipe.get("source_file")
    if source_file:
        lines.append(f"      <p>Source file: {html.escape(str(source_file))}</p>")
    lines.append(f"      <p>Imported {html.escape(imported_on)}</p>")
    return "\n".join(lines)


def recipe_jsonld(recipe: dict) -> dict:
    """Build the schema.org Recipe object embedded in every card."""
    nutrition_items = [
        _normalize_nutrition_item(item) for item in (recipe.get("nutrition") or [])
    ]
    nutrition_obj: dict[str, object] = {"@type": "NutritionInformation"}
    estimated_labels = []
    for item in nutrition_items:
        key = LABEL_TO_SCHEMA.get(str(item["label"]))
        if not key:
            continue
        nutrition_obj[key] = item["value"]
        if item["estimated"]:
            estimated_labels.append(str(item["label"]))

    payload: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.get("name") or "Untitled Recipe",
    }
    if recipe.get("serves"):
        payload["recipeYield"] = str(recipe["serves"])
    iso_time = iso_duration_from_human(recipe.get("total_time"))
    if iso_time:
        payload["totalTime"] = iso_time
    if recipe.get("tags"):
        payload["recipeCategory"] = list(recipe["tags"])
    if recipe.get("image"):
        payload["image"] = str(recipe["image"])
    payload["recipeIngredient"] = [str(i) for i in (recipe.get("ingredients") or [])]
    payload["recipeInstructions"] = [
        {"@type": "HowToStep", "text": _plain_text(str(step))}
        for step in (recipe.get("instructions") or [])
        if _plain_text(str(step))
    ]
    if len(nutrition_obj) > 1:
        payload["nutrition"] = nutrition_obj
    if estimated_labels:
        payload["additionalProperty"] = {
            "@type": "PropertyValue",
            "name": ESTIMATE_PROPERTY,
            "value": ", ".join(estimated_labels),
        }
    if recipe.get("source_url"):
        payload["url"] = str(recipe["source_url"])
    if recipe.get("source_file"):
        payload["isBasedOn"] = str(recipe["source_file"])
    return payload


def write_recipe(recipe: dict, output_path, force: bool = False):
    """Render and write a recipe, refusing to overwrite unless force.

    Scans the destination directory so a rerun of the same URL or name does
    not create a second file. Raises RecipeExistsError: ``same_recipe`` is
    True when the existing card is this recipe (safe no-op).
    """
    recipe = enrich_nutrition(dict(recipe))
    output_path = Path(output_path)
    recipes_dir = output_path.parent
    existing = find_existing_recipe(recipes_dir, recipe)
    if existing is not None:
        identity = peek_recipe_identity(existing.read_text(encoding="utf-8"))
        same = is_same_recipe(identity, recipe)
        if not force:
            raise RecipeExistsError(existing, same_recipe=same)
        output_path = existing
    elif output_path.exists() and not force:
        raise RecipeExistsError(output_path, same_recipe=False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(recipe), encoding="utf-8")
    return output_path
