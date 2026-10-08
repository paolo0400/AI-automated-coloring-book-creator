"""Steps 2 & 3 - make the KDP wraparound cover, then check it.

The image engine paints the artwork (no text). Python builds the exact KDP-size cover around it:
back + spine + front + 0.125" bleed, title text inside the safe zone, barcode area kept clear.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .ai import ask_json, generate_image
from .core import (Book, PipelineError, cover_dims, get_font, image_provider,
                   interior_page_count, log, max_attempts)

SHEET_PROMPT = """Character reference sheet for a children's coloring book.
Clean bold black line art on a pure white background. No color, no gray, no shading.
Draw each character once, full body, front view, standing side by side with space between them:
{chars}
Style: cute, simple, rounded cartoon shapes for kids aged {age}.
No text, no labels, no names, no letters anywhere."""

COVER_PROMPT = """Front cover illustration for a children's coloring book.
Theme: {summary}
Scene: {scene}
Characters (match the attached reference sheet's designs, but fully colored): {chars}
Style: bright, cheerful, colorful cartoon illustration with bold outlines, appealing to kids aged {age}.
Color palette: {palette}.
Composition: artwork fills the ENTIRE image edge to edge (full bleed, no border, no frame).
Keep the top 30% of the image simple and uncluttered (sky or soft background) because a title
will be added there later. Place the characters in the middle and lower part, away from the edges.
IMPORTANT: absolutely no text, letters, numbers, words, logos, or signatures anywhere in the image."""

# Shorter prompts for local models (Draw Things): subject first, style after.
LOCAL_SHEET_PROMPT = ("character reference sheet, {chars}, each character standing side by side, "
                      "full body, front view, cute simple cartoon, black and white line art, "
                      "bold clean outlines, pure white background, no shading")
LOCAL_COVER_PROMPT = ("children's book cover illustration, {scene}, featuring {chars}, "
                      "bright cheerful colorful cartoon style, bold outlines, colors: {palette}, "
                      "simple open sky in the top third, characters in the lower half, "
                      "full bleed artwork, kid friendly, high quality")

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "title_readable": {"type": "boolean"},
        "artwork_has_stray_text": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "title_readable", "artwork_has_stray_text", "problems"],
}


def _chars(data: dict) -> str:
    return "; ".join(f"{c['name']}: {c['look']}" for c in data["characters"])


def character_sheet(cfg: dict, book: Book, redo: bool = False) -> Path:
    data = book.load()
    path = book.refs / "character_sheet.png"
    if path.exists() and not redo:
        return path
    log("   Drawing the character reference sheet...")
    local = image_provider(cfg) == "drawthings"
    template = LOCAL_SHEET_PROMPT if local else SHEET_PROMPT
    generate_image(cfg, book, template.format(chars=_chars(data), age=data["age_range"]),
                   path, kind="character_sheet", aspect="4:3")
    return path


# ---------------------------------------------------------------- composition
def _fit_text(draw, text, cfg, max_w, max_h, start_size, max_lines=3):
    """Largest font size where the wrapped text fits in the box."""
    size = start_size
    while size > 20:
        font = get_font(cfg, size)
        for width in range(12, 40, 2):
            lines = textwrap.wrap(text, width=width)
            if len(lines) > max_lines:
                continue
            sw = max(2, size // 12)
            boxes = [draw.textbbox((0, 0), ln, font=font, stroke_width=sw) for ln in lines]
            w = max(b[2] - b[0] for b in boxes)
            ascent, descent = font.getmetrics()
            h = (ascent + descent + 2 * sw) * len(lines) + int(size * 0.12) * (len(lines) - 1)
            if w <= max_w and h <= max_h:
                return font, lines, sw
        size = int(size * 0.92)
    return get_font(cfg, 20), textwrap.wrap(text, 30), 2


def _draw_block(draw, lines, font, sw, center_x, top, fill, stroke):
    """Draw centered lines with even spacing. Returns (box, next_y)."""
    ascent, descent = font.getmetrics()
    lh = ascent + descent + 2 * sw
    gap = int(font.size * 0.12)
    y = top
    xs = []
    for ln in lines:
        b = draw.textbbox((0, 0), ln, font=font, stroke_width=sw)
        w = b[2] - b[0]
        x = center_x - w // 2
        draw.text((x - b[0], y + sw), ln, font=font, fill=fill, stroke_width=sw, stroke_fill=stroke)
        xs.append((x, x + w))
        y += lh + gap
    box = [min(a for a, _ in xs), top, max(b for _, b in xs), y - gap]
    return box, y


def _soft_color(art: Image.Image) -> tuple:
    top = art.crop((0, 0, art.width, art.height // 3)).resize((1, 1))
    r, g, b = top.getpixel((0, 0))[:3]
    return tuple(int(c * 0.75 + 255 * 0.25) for c in (r, g, b))


def compose(cfg: dict, book: Book, art_path: Path) -> dict:
    data = book.load()
    pages = interior_page_count(data["num_pages"], cfg["book"]["blank_backs"])
    d = cover_dims(cfg, pages)
    dpi = cfg["book"]["dpi"]
    margin = int(cfg["book"]["cover_text_margin_in"] * dpi)
    art = Image.open(art_path).convert("RGB")
    bg = _soft_color(art)

    canvas = Image.new("RGB", (d["width_px"], d["height_px"]), bg)
    # front panel = from spine edge to the right bleed edge, full height incl. bleed
    front_w = d["width_px"] - d["front_trim_x0"]
    front_art = ImageOps.fit(art, (front_w, d["height_px"]), Image.LANCZOS)
    canvas.paste(front_art, (d["front_trim_x0"], 0))
    draw = ImageDraw.Draw(canvas)

    # ---- front text (inside trim + margin)
    fx0, fx1 = d["front_trim_x0"] + margin, d["front_trim_x1"] - margin
    fy0, fy1 = d["trim_y0"] + margin, d["trim_y1"] - margin
    cx = (fx0 + fx1) // 2
    title_font, title_lines, sw = _fit_text(draw, data["title"].upper(), cfg, fx1 - fx0,
                                            int((fy1 - fy0) * 0.24), start_size=int(dpi * 1.1))
    title_box, y = _draw_block(draw, title_lines, title_font, sw, cx, fy0, "white", (30, 30, 60))
    sub_font, sub_lines, ssw = _fit_text(draw, data["subtitle"], cfg, fx1 - fx0,
                                         int((fy1 - fy0) * 0.08), start_size=int(dpi * 0.35), max_lines=2)
    sub_box, _ = _draw_block(draw, sub_lines, sub_font, ssw, cx, y + int(dpi * 0.1), "white", (30, 30, 60))
    auth_font = get_font(cfg, int(dpi * 0.28))
    a_h = sum(auth_font.getmetrics()) + 12
    auth_box, _ = _draw_block(draw, [data["author"]], auth_font, 6, cx, fy1 - a_h,
                              "white", (30, 30, 60))

    # ---- back cover: blurb at top, barcode area bottom-right kept empty
    bx0, bx1 = d["back_trim_x0"] + margin, d["back_trim_x1"] - margin
    blurb = data["cover"]["back_blurb"] + f" Includes {data['num_pages']} pages to color."
    b_font, b_lines, bsw = _fit_text(draw, blurb, cfg, bx1 - bx0, int(dpi * 2.5),
                                     start_size=int(dpi * 0.3), max_lines=6)
    back_box, y = _draw_block(draw, b_lines, b_font, bsw, (bx0 + bx1) // 2, fy0, "white", (30, 30, 60))

    # "Meet the characters" panel using the line-art reference sheet
    sheet_path = book.refs / "character_sheet.png"
    if sheet_path.exists():
        panel_w = int((bx1 - bx0) * 0.85)
        sheet = Image.open(sheet_path).convert("RGB")
        k = min((panel_w - 80) / sheet.width, int(dpi * 4.2) / sheet.height)
        sheet = sheet.resize((int(sheet.width * k), int(sheet.height * k)), Image.LANCZOS)
        pw, ph = sheet.width + 80, sheet.height + 80 + int(dpi * 0.45)
        px0 = (bx0 + bx1) // 2 - pw // 2
        py0 = y + int(dpi * 0.3)
        draw.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=50, fill="white",
                               outline=(30, 30, 60), width=8)
        label_font = get_font(cfg, int(dpi * 0.2))
        lb = draw.textbbox((0, 0), "Meet the characters!", font=label_font)
        draw.text(((bx0 + bx1) // 2 - (lb[2] - lb[0]) // 2 - lb[0], py0 + 30 - lb[1]),
                  "Meet the characters!", font=label_font, fill=(30, 30, 60))
        canvas.paste(sheet, (px0 + 40, py0 + 40 + int(dpi * 0.45)))
        back_box = [min(back_box[0], px0), back_box[1], max(back_box[2], px0 + pw), py0 + ph]

    barcode_zone = [d["back_trim_x1"] - margin - int(2.0 * dpi), d["trim_y1"] - margin - int(1.2 * dpi),
                    d["back_trim_x1"] - margin, d["trim_y1"] - margin]

    out = book.cover_dir
    canvas.save(out / "cover_full_wrap.jpg", quality=95, dpi=(dpi, dpi))
    canvas.save(out / "cover_full_wrap.pdf", resolution=dpi)
    front = canvas.crop((d["front_trim_x0"], d["trim_y0"], d["front_trim_x1"], d["trim_y1"]))
    front.save(out / "cover_front.png", dpi=(dpi, dpi))
    art_only = front_art.crop((0, d["trim_y0"], d["front_trim_x1"] - d["front_trim_x0"], d["trim_y1"]))
    art_only.save(out / "cover_front_art_only.png", dpi=(dpi, dpi))
    thumb = front.copy()
    thumb.thumbnail((200, 260))
    thumb.save(out / "cover_thumbnail.png")

    meta = {"dims": d, "art": art_path.name, "text_boxes": {
        "title": title_box, "subtitle": sub_box, "author": auth_box, "back_blurb": back_box},
        "barcode_zone": barcode_zone, "margin_px": margin, "text_contrast": _contrast("white", (30, 30, 60))}
    (out / "cover_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def _lum(c):
    rgb = (255, 255, 255) if c == "white" else c

    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


# ---------------------------------------------------------------- step 3
def check(cfg: dict, book: Book) -> dict:
    """Step 3: does the cover meet KDP specs and look good?"""
    data = book.load()
    meta_path = book.cover_dir / "cover_meta.json"
    if not meta_path.exists():
        raise PipelineError("No cover yet. Run step 2: python -m bookgen cover")
    meta = json.loads(meta_path.read_text())
    d = meta["dims"]
    dpi = cfg["book"]["dpi"]
    problems, notes = [], []  # problems here are layout/spec problems (new art won't fix them)

    img = Image.open(book.cover_dir / "cover_full_wrap.jpg")
    if img.size != (d["width_px"], d["height_px"]):
        problems.append(f"cover is {img.size}, expected {(d['width_px'], d['height_px'])}")
    if round(img.info.get("dpi", (0, 0))[0]) != dpi:
        problems.append("cover is not saved at 300 DPI")

    pages_now = interior_page_count(data["num_pages"], cfg["book"]["blank_backs"])
    if pages_now != d["pages"]:
        problems.append(f"cover was built for {d['pages']} pages but the book now has {pages_now}; "
                        "run: python -m bookgen cover --recompose")

    m = meta["margin_px"]
    for name, (x0, y0, x1, y1) in meta["text_boxes"].items():
        if name == "back_blurb":
            ok = x0 >= d["back_trim_x0"] + m - 2 and x1 <= d["back_trim_x1"] - m + 2
        else:
            ok = x0 >= d["front_trim_x0"] + m - 2 and x1 <= d["front_trim_x1"] - m + 2
        ok = ok and y0 >= d["trim_y0"] + m - 2 and y1 <= d["trim_y1"] - m + 2
        if not ok:
            problems.append(f"{name} text is outside the safe zone")
    bz = meta["barcode_zone"]
    bb = meta["text_boxes"]["back_blurb"]
    if not (bb[2] < bz[0] or bb[3] < bz[1]):
        problems.append("back blurb overlaps the barcode area")
    if meta["text_contrast"] < 4.5:
        problems.append(f"title contrast {meta['text_contrast']} is below 4.5:1")
    notes.append(f"spine {d['spine_in']} in for {d['pages']} pages - "
                 + ("spine text allowed but left blank (spine is thin)" if d["spine_text_allowed"]
                    else "no spine text allowed under 79 pages"))

    # AI judgment: stray text in the art, thumbnail readability, overall look
    stray = ask_json(cfg, "cover_judge",
                     "Look at this children's book cover artwork. Is there ANY text, letters, numbers, "
                     "logos or signatures anywhere in it? Also rate how appealing and professional the "
                     "artwork looks for a kids' coloring book (1-10). title_readable: answer true.",
                     JUDGE_SCHEMA, images=[book.cover_dir / "cover_front_art_only.png"], book=book,
                     temperature=0.1)
    thumb = ask_json(cfg, "cover_judge",
                     f"This is a book cover shown at Amazon search-result size. Can a shopper read the "
                     f"title \"{data['title']}\"? Rate the overall cover 1-10 as a product listing image. "
                     "List any problems (clutter, cut-off characters, weird anatomy, hard-to-read text). "
                     "artwork_has_stray_text: answer false.",
                     JUDGE_SCHEMA, images=[book.cover_dir / "cover_thumbnail.png",
                                           book.cover_dir / "cover_front.png"], book=book, temperature=0.1)
    art_problems = []  # problems that new artwork could fix
    if stray["artwork_has_stray_text"]:
        art_problems.append("artwork contains stray text/letters")
    if not thumb["title_readable"]:
        art_problems.append("title not readable at thumbnail size")
    score = min(stray["score"], thumb["score"])
    if score < cfg["qa"]["min_cover_score"]:
        art_problems.append(f"cover judge score {score} is below {cfg['qa']['min_cover_score']}")
    notes += [f"judge: {p}" for p in stray["problems"] + thumb["problems"]]

    result = {"passed": not (problems or art_problems), "score": score,
              "layout_problems": problems, "art_problems": art_problems,
              "problems": problems + art_problems, "notes": notes, "art": meta["art"]}
    (book.qa / "cover_check.json").write_text(json.dumps(result, indent=2))
    log(f"   Cover check: {'PASSED' if result['passed'] else 'FAILED'} (score {score}/10)")
    for p in result["problems"]:
        log(f"     - {p}")
    return result


# ---------------------------------------------------------------- step 2 driver
def run(cfg: dict, book: Book, redo: bool = False, recompose: bool = False) -> dict:
    data = book.load()
    st = book.state()
    if recompose:
        art = book.cover_dir / st.get("cover", {}).get("art", "")
        if not art.is_file():
            raise PipelineError("No approved cover art to recompose. Run: python -m bookgen cover")
        log("Step 2: rebuilding the cover layout from existing art (no new images)...")
        compose(cfg, book, art)
        return check(cfg, book)

    if st.get("cover", {}).get("status") == "passed" and not redo:
        log("Step 2: cover already done (use --redo to make a new one).")
        return json.loads((book.qa / "cover_check.json").read_text())

    log("Step 2: making the cover...")
    sheet = character_sheet(cfg, book)
    max_tries = max_attempts(cfg)
    template = LOCAL_COVER_PROMPT if image_provider(cfg) == "drawthings" else COVER_PROMPT
    best = None
    start = st.get("cover", {}).get("attempts", 0) if not redo else 0
    attempt = start
    for attempt in range(start + 1, start + max_tries + 1):
        art = book.cover_dir / f"art_a{attempt}.png"
        log(f"   Cover art attempt {attempt}...")
        generate_image(cfg, book, template.format(
            summary=data["theme_summary"], scene=data["cover"]["scene"], chars=_chars(data),
            age=data["age_range"], palette=data["cover"]["palette"]),
            art, kind="cover_art", refs=[sheet], aspect="3:4",
            size=cfg["images"]["cover_size"], model=cfg["images"]["cover_model"])
        compose(cfg, book, art)
        log("Step 3: checking the cover...")
        result = check(cfg, book)
        st["cover"] = {"attempts": attempt, "art": art.name,
                       "status": "passed" if result["passed"] else "failed"}
        book.save_state(st)
        if best is None or (len(result["problems"]), -result["score"]) < (len(best[1]["problems"]), -best[1]["score"]):
            best = (art, result)
        if result["passed"]:
            return result
        if result["layout_problems"]:
            log("   Layout problem - new artwork won't fix this, so not retrying.")
            break
    # none passed: rebuild with the best attempt and flag it
    art, result = best
    compose(cfg, book, art)
    result = check(cfg, book)
    st["cover"] = {"attempts": attempt, "art": art.name, "status": "flagged"}
    book.save_state(st)
    log("   Cover FLAGGED for your review - kept the best attempt.")
    return result
