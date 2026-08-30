"""Shared helpers for importing recipes into the workspace recipes/ directory.

A "recipe" here is a normalized dict with these keys:

    name          str
    serves        str | None
    total_time    str | None   (human readable, e.g. "35 min")
    tags          list[str]
    image         str | None   (remote URL, not downloaded)
    ingredients   list[str]
    nutrition     list[tuple[str, str]]   (label, value) in display order
    instructions  list[str]    each item is safe HTML for one step
    notes         str | None   safe HTML
    source_url    str | None

Both the URL importer and the markdown converter build this dict and pass it
to render_html() so every recipe card comes out in the same format.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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

# schema.org NutritionInformation key -> display label, in display order.
NUTRITION_LABELS = [
    ("calories", "Calories"),
    ("fatContent", "Fat"),
    ("saturatedFatContent", "Saturated fat"),
    ("carbohydrateContent", "Carbohydrates"),
    ("sugarContent", "Sugar"),
    ("proteinContent", "Protein"),
    ("fiberContent", "Fiber"),
    ("cholesterolContent", "Cholesterol"),
    ("sodiumContent", "Sodium"),
    ("servingSize", "Serving size"),
]


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


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


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _instruction_html(step) -> str:
    """Return safe HTML for a schema.org recipe instruction step."""
    if isinstance(step, str):
        return step
    if isinstance(step, dict):
        item_type = step.get("@type")
        if item_type == "HowToSection":
            items = _as_list(step.get("itemListElement"))
            inner = "\n".join(_instruction_html(item) for item in items)
            name = step.get("name")
            if name:
                return f"<strong>{html.escape(str(name))}</strong>\n{inner}"
            return inner
        return step.get("text") or step.get("name") or ""
    return html.escape(str(step))


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

    nutrition_src = recipe.get("nutrition") or {}
    nutrition: list[tuple[str, str]] = []
    if isinstance(nutrition_src, dict):
        for key, label in NUTRITION_LABELS:
            value = nutrition_src.get(key)
            if value is not None and str(value).strip():
                nutrition.append((label, str(value)))

    instructions = [
        text
        for text in (_instruction_html(s) for s in _as_list(recipe.get("recipeInstructions")))
        if text
    ]

    serves = recipe.get("recipeYield")
    if isinstance(serves, list):
        serves = serves[0] if serves else None

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


def render_html(recipe: dict) -> str:
    name = recipe.get("name") or "Untitled Recipe"
    escaped_name = html.escape(name)

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

    nutrition = recipe.get("nutrition") or []
    if nutrition:
        rows = "\n".join(
            f'          <tr><th scope="row">{html.escape(str(label))}</th>'
            f"<td>{html.escape(str(value))}</td></tr>"
            for label, value in nutrition
        )
        nutrition_section = (
            '      <section class="nutrition">\n'
            "        <h2>Nutrition</h2>\n"
            "        <table>\n"
            f"{rows}\n"
            "        </table>\n"
            "      </section>"
        )
    else:
        nutrition_section = ""

    steps = recipe.get("instructions") or []
    step_blocks = []
    for index, step in enumerate(steps, start=1):
        step_blocks.append(
            "      <li class=\"step\">\n"
            f"        <h3>Step {index}</h3>\n"
            f"        <div class=\"step-body\">{step}</div>\n"
            "      </li>"
        )
    steps_html = "\n".join(step_blocks) or "      <li class=\"step\"><div class=\"step-body\">(no instructions)</div></li>"

    notes_section = ""
    if recipe.get("notes"):
        notes_section = (
            '    <section class="notes">\n'
            "      <h2>Notes</h2>\n"
            f"      <div class=\"notes-body\">{recipe['notes']}</div>\n"
            "    </section>\n"
        )

    footer_lines = []
    source_url = recipe.get("source_url")
    if source_url:
        escaped_source = html.escape(str(source_url))
        footer_lines.append(
            f'      <p>Source: <a href="{escaped_source}">{escaped_source}</a></p>'
        )
    footer_lines.append(f"      <p>Imported {date.today().isoformat()}</p>")
    footer_html = "\n".join(footer_lines)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name}</title>
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


def write_recipe(recipe: dict, output_path, force: bool):
    """Render and write a recipe, refusing to overwrite unless force."""
    if output_path.exists() and not force:
        raise FileExistsError(
            f"{output_path} already exists (use --force to overwrite)"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(recipe), encoding="utf-8")
    return output_path
