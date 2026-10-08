"""Step 1 - a free local model invents the book: title, characters, setting, 40 scenes."""
from __future__ import annotations

from .ai import ask_json
from .core import Book, PipelineError, interior_page_count, log, set_current, slugify

THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "theme_summary": {"type": "string"},
        "setting": {"type": "string"},
        "characters": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "look": {"type": "string"}},
            "required": ["name", "look"]}},
        "cover_scene": {"type": "string"},
        "cover_palette": {"type": "string"},
        "back_blurb": {"type": "string"},
    },
    "required": ["title", "subtitle", "theme_summary", "setting", "characters",
                 "cover_scene", "cover_palette", "back_blurb"],
}

SCENES_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
        "required": ["title", "description"]}}},
    "required": ["scenes"],
}


def run(cfg: dict, idea: str | None, num_pages: int | None = None) -> Book:
    n = num_pages or cfg["book"]["default_num_pages"]
    age = cfg["book"]["age_range"]
    log("Step 1: creating the book theme (local model)...")

    theme = ask_json(cfg, "theme", f"""You are planning a children's coloring book to sell on Amazon KDP.
Readers are kids aged {age}.
Idea from the publisher: {idea or "none - invent a fresh, cozy, marketable theme kids love"}

Create:
- title: short, catchy, easy to read (max 6 words)
- subtitle: describes the book for Amazon search (for example "A Coloring Book for Kids Ages {age}")
- theme_summary: 2 sentences
- setting: where the pictures take place
- characters: 2 to 4 recurring characters, each with a name and a precise visual description
  (species, body shape, clothing, one signature accessory) so an artist can draw them the same way every time
- cover_scene: one exciting scene for the front cover with the main characters
- cover_palette: 4-5 bright colors for the cover
- back_blurb: 1-2 cheerful sentences for the back cover
Keep everything original: no famous characters, brands, or trademarks.""", THEME_SCHEMA)

    scenes: list[dict] = []
    seen: set[str] = set()
    tries = 0
    while len(scenes) < n and tries < 12:
        tries += 1
        k = min(10, n - len(scenes))
        used = "; ".join(s["title"] for s in scenes) or "none yet"
        batch = ask_json(cfg, "theme", f"""Coloring book: "{theme['title']}" - {theme['theme_summary']}
Setting: {theme['setting']}
Characters: {', '.join(c['name'] + ' (' + c['look'] + ')' for c in theme['characters'])}

Write {k} NEW coloring page scenes, numbered from {len(scenes) + 1}.
Each needs a short title and a one-sentence description of exactly what is drawn
(which characters, what they are doing, 2-3 background objects).
Keep each scene simple enough to color for ages {age}. Vary the activities and places.
Do not repeat these scenes: {used}""", SCENES_SCHEMA, temperature=0.9)
        for s in batch.get("scenes", []):
            key = s["title"].strip().lower()
            if key and key not in seen and len(scenes) < n:
                seen.add(key)
                scenes.append({"id": len(scenes) + 1, "title": s["title"].strip(),
                               "description": s["description"].strip()})
    if len(scenes) < n:
        raise PipelineError(f"Only got {len(scenes)} of {n} scenes. Run step 1 again.")

    slug = slugify(theme["title"])
    book = Book(slug)
    i = 2
    while book.exists():
        book = Book(f"{slug}-{i}")
        i += 1

    data = {
        "title": theme["title"].strip(),
        "subtitle": theme["subtitle"].strip(),
        "author": cfg["book"]["author_name"],
        "age_range": age,
        "idea": idea or "",
        "num_pages": n,
        "interior_page_count": interior_page_count(n, cfg["book"]["blank_backs"]),
        "theme_summary": theme["theme_summary"],
        "setting": theme["setting"],
        "characters": theme["characters"],
        "cover": {"scene": theme["cover_scene"], "palette": theme["cover_palette"],
                  "back_blurb": theme["back_blurb"]},
        "scenes": scenes,
    }
    book.save(data)
    set_current(book.slug)
    log(f"   Title: {data['title']} - {data['subtitle']}")
    log(f"   Characters: {', '.join(c['name'] for c in data['characters'])}")
    log(f"   {n} scenes written. Interior will be {data['interior_page_count']} pages.")
    log(f"   Saved to books/{book.slug}/book.yaml (edit it freely before the next step)")
    return book
