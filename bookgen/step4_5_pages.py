"""Step 4 (40 coloring pages) and step 5 (the "This book belongs to" title page)."""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from .ai import BudgetExceeded, ask_json, generate_image
from .core import (Book, PipelineError, get_font, image_provider, log, max_attempts, page_px,
                   safe_box_px)
from .imagecheck import center_is_empty, check_raw_page, clean_page
from .step2_3_cover import character_sheet

STYLE = """Children's coloring book page for kids aged {age}.
Clean, bold, smooth black outlines on a pure white background.
Black and white line art ONLY: no color, no gray, no shading, no hatching, no gradients,
no large solid black areas. Large, simple, closed shapes that are easy to color.
The whole drawing must fit inside the image with a clear white margin on every side;
nothing touches or is cut off at the edges. No border or frame.
Absolutely no text, letters, numbers, words, or signatures."""

PAGE_PROMPT = """{style}

Book: "{title}" - {summary}
Setting: {setting}
Scene {id}: {scene_title} - {description}
Characters (draw them exactly like the attached reference sheet): {chars}
{hint}"""

# Shorter prompts for local models (Draw Things): subject first, style after.
LOCAL_PAGE_PROMPT = ("coloring book page for kids, {scene_title}: {description}, "
                     "characters: {chars}, setting: {setting}, "
                     "black and white line art, bold clean outlines, pure white background, "
                     "no shading, no color, large simple shapes, whole drawing centered with a "
                     "white margin, nothing cut off. {hint}")

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_black_and_white_line_art": {"type": "boolean"},
        "has_gray_shading_or_solid_fills": {"type": "boolean"},
        "has_letters_or_words": {"type": "boolean"},
        "has_deformed_characters": {"type": "boolean"},
        "matches_description": {"type": "boolean"},
        "kid_friendly": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["is_black_and_white_line_art", "has_gray_shading_or_solid_fills",
                 "has_letters_or_words", "has_deformed_characters", "matches_description",
                 "kid_friendly", "notes"],
}


def vision_issues(cfg: dict, book: Book, path: Path, description: str) -> list[str]:
    if cfg["qa"]["vision_mode"] == "off":
        return []
    v = ask_json(cfg, "vision_qa",
                 "You are checking a page for a children's coloring book.\n"
                 f"It should show: {description}\n"
                 "Answer honestly about what you see in the image.",
                 VISION_SCHEMA, images=[path], book=book, temperature=0.1)
    issues = []
    if not v["is_black_and_white_line_art"]:
        issues.append("vision: not clean black-and-white line art")
    if v["has_gray_shading_or_solid_fills"]:
        issues.append("vision: shading or solid fills")
    if v["has_letters_or_words"]:
        issues.append("vision: contains letters/words")
    if v["has_deformed_characters"]:
        issues.append("vision: deformed characters")
    if not v["kid_friendly"]:
        issues.append("vision: not kid friendly")
    if not v["matches_description"]:
        issues.append("vision (minor): may not match the scene")
    return issues


def _blocking(issues: list[str], cfg: dict) -> list[str]:
    """Which issues force a retry."""
    out = []
    for i in issues:
        if i.startswith("vision (minor)"):
            continue
        if i.startswith("vision") and cfg["qa"]["vision_mode"] != "strict":
            continue
        out.append(i)
    return out


# ---------------------------------------------------------------- step 4
def run_pages(cfg: dict, book: Book, limit: int | None = None, redo: list[int] | None = None,
              hint: str = "") -> None:
    data = book.load()
    st = book.state()
    sheet = character_sheet(cfg, book)
    chars = "; ".join(f"{c['name']}: {c['look']}" for c in data["characters"])
    style = STYLE.format(age=data["age_range"])
    max_tries = max_attempts(cfg)
    local = image_provider(cfg) == "drawthings"
    scenes = data["scenes"][:limit] if limit else data["scenes"]
    redo = set(redo or [])
    log(f"Step 4: drawing {len(scenes)} coloring pages...")

    for sc in scenes:
        key = f"page_{sc['id']:02d}"
        rec = st["pages"].get(key, {})
        if rec.get("status") in ("approved", "flagged") and sc["id"] not in redo:
            continue
        if sc["id"] in redo:
            rec = {"attempts": rec.get("attempts", 0)}
            (book.final / f"{key}.png").unlink(missing_ok=True)
        base = rec.get("attempts", 0)

        anchor = st.get("style_anchor")
        refs = [sheet] + ([book.raw / anchor] if anchor and anchor != f"{key}.png" else [])
        template = LOCAL_PAGE_PROMPT if local else PAGE_PROMPT
        prompt = template.format(style=style, title=data["title"], summary=data["theme_summary"],
                                 setting=data["setting"], id=sc["id"], scene_title=sc["title"],
                                 description=sc["description"], chars=chars,
                                 hint=(f"Extra instructions: {hint}" if hint else "")).strip()
        if anchor and not local:
            prompt += "\nMatch the line style of the second attached image (an approved page)."

        best = None
        attempt = base
        for attempt in range(base + 1, base + max_tries + 1):
            out = book.raw / f"{key}_a{attempt}.png"
            log(f"   {key} '{sc['title']}' attempt {attempt}...")
            try:
                generate_image(cfg, book, prompt, out, kind="page", refs=refs, aspect="3:4")
            except BudgetExceeded:
                st["pages"][key] = rec | {"attempts": attempt - 1}
                book.save_state(st)
                raise
            except PipelineError as e:
                log(f"     {e}")
                continue
            issues = check_raw_page(out, cfg)
            if not issues:  # only spend a vision call on pages that pass the pixel checks
                issues = vision_issues(cfg, book, out, sc["description"])
            blocking = _blocking(issues, cfg)
            log(f"     {'OK' if not blocking else 'retry: ' + '; '.join(blocking)}")
            if best is None or len(blocking) < len(best[1]):
                best = (out, blocking, issues)
            if not blocking:
                break

        if best is None:
            st["pages"][key] = {"status": "missing", "attempts": base + max_tries,
                                "issues": ["no image could be generated"]}
        else:
            shutil.copy(best[0], book.raw / f"{key}.png")
            status = "approved" if not best[1] else "flagged"
            st["pages"][key] = {"status": status, "attempts": attempt, "file": best[0].name,
                                "issues": best[2]}
            if status == "approved" and not st.get("style_anchor"):
                st["style_anchor"] = f"{key}.png"
        book.save_state(st)

    counts = {}
    for k, v in st["pages"].items():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    log(f"   Pages: {counts}")
    flagged = [k for k, v in st["pages"].items() if v["status"] != "approved"]
    if flagged:
        log(f"   Needs your review: {', '.join(flagged)} (see books/{book.slug}/pages_raw/)")


# ---------------------------------------------------------------- step 5
BORDER_PROMPT = """Decorative page frame for the first page of a children's coloring book.
Black and white line art only: bold clean outlines, pure white background, no shading, no gray.
A cheerful border around the edges of the page featuring {chars} and small objects from {setting},
peeking in from the corners and edges.
The whole CENTER of the page (about 60% of the width and height) must be completely EMPTY white space.
No text, letters, numbers, or words anywhere."""

LOCAL_BORDER_PROMPT = ("decorative page border frame for a kids coloring book, {chars} peeking in "
                       "from the corners, small objects from {setting} along the edges, "
                       "completely empty white center, black and white line art, bold clean "
                       "outlines, pure white background, no shading")


def _code_border(cfg: dict) -> Image.Image:
    w, h = page_px(cfg)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    l, t, r, b = safe_box_px(cfg)
    d.rounded_rectangle([l + 10, t + 10, r - 10, b - 10], radius=120, outline=0, width=14)
    d.rounded_rectangle([l + 60, t + 60, r - 60, b - 60], radius=90, outline=0, width=6)
    for cx, cy in ((l + 60, t + 60), (r - 60, t + 60), (l + 60, b - 60), (r - 60, b - 60)):
        d.ellipse([cx - 55, cy - 55, cx + 55, cy + 55], fill=255, outline=0, width=10)
    return img


def run_title(cfg: dict, book: Book, redo: bool = False) -> Path:
    data = book.load()
    st = book.state()
    out = book.raw / "title_page.png"
    if out.exists() and not redo:
        log("Step 5: title page already done (use --redo to remake it).")
        return out
    log("Step 5: making the 'This book belongs to' page...")
    chars = ", ".join(c["name"] + " (" + c["look"] + ")" for c in data["characters"])
    border_src = None
    local = image_provider(cfg) == "drawthings"
    border_template = LOCAL_BORDER_PROMPT if local else BORDER_PROMPT
    looks = ", ".join(c["look"] for c in data["characters"])
    for attempt in range(1, max_attempts(cfg) + 1):
        p = book.raw / f"title_border_a{attempt}.png"
        try:
            generate_image(cfg, book, border_template.format(
                               chars=looks if local else chars, setting=data["setting"]),
                           p, kind="title_border", refs=[book.refs / "character_sheet.png"], aspect="3:4")
        except BudgetExceeded:
            raise
        except PipelineError as e:
            log(f"   {e}")
            continue
        issues = check_raw_page(p, cfg)
        if center_is_empty(p) and not [i for i in issues if "nearly empty" not in i]:
            border_src = p
            break
        log(f"   border attempt {attempt} rejected (center not empty or quality issues)")

    w, h = page_px(cfg)
    if border_src:
        tmp = book.raw / "_title_border_clean.png"
        clean_page(border_src, tmp, cfg)
        page = Image.open(tmp).convert("L")
    else:
        log("   Using a simple drawn border instead.")
        page = _code_border(cfg)

    d = ImageDraw.Draw(page)
    dpi = cfg["book"]["dpi"]
    cx = w // 2
    # white panel so text never collides with border art
    panel = [int(w * .22), int(h * .30), int(w * .78), int(h * .70)]
    d.rounded_rectangle(panel, radius=60, fill=255)
    f_big = get_font(cfg, int(dpi * 0.55))
    f_small = get_font(cfg, int(dpi * 0.22))
    y = int(h * .34)
    for text, font in (("This Book", f_big), ("Belongs To:", f_big)):
        bb = d.textbbox((0, 0), text, font=font)
        d.text((cx - (bb[2] - bb[0]) // 2 - bb[0], y - bb[1]), text, font=font, fill=0)
        y += (bb[3] - bb[1]) + int(dpi * 0.15)
    y += int(dpi * 0.9)
    line_w = int(w * .46)
    d.rectangle([cx - line_w // 2, y, cx + line_w // 2, y + 8], fill=0)
    y += int(dpi * 0.6)
    bb = d.textbbox((0, 0), data["title"], font=f_small)
    d.text((cx - (bb[2] - bb[0]) // 2 - bb[0], y - bb[1]), data["title"], font=f_small, fill=0)

    page = page.point(lambda v: 0 if v < 150 else 255)
    page.save(out, dpi=(dpi, dpi))
    st["title_page"] = {"status": "done", "border": border_src.name if border_src else "code"}
    book.save_state(st)
    return out
