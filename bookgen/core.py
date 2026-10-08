"""Shared helpers: config, book folders, saved state, KDP math, fonts."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv
from PIL import ImageFont

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
BOOKS_DIR = ROOT / "books"
OUTPUT_DIR = ROOT / "output"
CURRENT_FILE = BOOKS_DIR / ".current"

load_dotenv(ROOT / ".env")

FAKE = os.environ.get("BOOKGEN_FAKE") == "1"   # free dry-run mode, no API calls


class PipelineError(Exception):
    """A problem the user (or agent) needs to fix before continuing."""


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- config
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def image_provider(cfg: dict) -> str:
    """'drawthings' (free, local) or 'gemini' (paid, cloud)."""
    return cfg.get("images", {}).get("provider", "gemini")


def max_attempts(cfg: dict) -> int:
    """Retries per image: more for free local generation, fewer for paid."""
    if image_provider(cfg) == "drawthings":
        return int(cfg.get("drawthings", {}).get("max_attempts", 5))
    return int(cfg["budget"]["max_attempts_per_image"])


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:50] or "book"


# ---------------------------------------------------------------- books
class Book:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = BOOKS_DIR / slug
        self.yaml_path = self.dir / "book.yaml"
        self.state_path = self.dir / "state.json"
        self.refs = self.dir / "refs"
        self.cover_dir = self.dir / "cover"
        self.raw = self.dir / "pages_raw"
        self.final = self.dir / "pages_final"
        self.qa = self.dir / "qa"
        self.interior_pdf = self.dir / "interior.pdf"

    # book.yaml -----------------------------------------------------------
    def exists(self) -> bool:
        return self.yaml_path.exists()

    def load(self) -> dict:
        if not self.exists():
            raise PipelineError(f"No book.yaml for '{self.slug}'. Run step 1: python -m bookgen theme")
        with open(self.yaml_path) as f:
            return yaml.safe_load(f)

    def save(self, data: dict) -> None:
        for d in (self.dir, self.refs, self.cover_dir, self.raw, self.final, self.qa):
            d.mkdir(parents=True, exist_ok=True)
        with open(self.yaml_path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)

    # state.json ----------------------------------------------------------
    def state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"pages": {}, "approved": {}}

    def save_state(self, st: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st, indent=2))

    def num_pages(self) -> int:
        return int(self.load().get("num_pages", 40))


def current_book(slug: str | None = None) -> Book:
    if slug:
        return Book(slug)
    if CURRENT_FILE.exists():
        return Book(CURRENT_FILE.read_text().strip())
    raise PipelineError("No book selected. Run step 1 (theme) first or pass --book <slug>.")


def set_current(slug: str) -> None:
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(slug)


# ---------------------------------------------------------------- KDP math
PAPER_THICKNESS_IN = {"white": 0.002252, "cream": 0.0025}  # per page, B&W interior
BLEED_IN = 0.125
KDP_MIN_PAGES = 24


def interior_page_count(num_pages: int, blank_backs: bool) -> int:
    """Title page + coloring pages (+ a blank back behind each), padded to KDP rules."""
    n = 1 + num_pages
    if blank_backs:
        n *= 2
    n = max(n, KDP_MIN_PAGES)
    if n % 2:
        n += 1
    return n


def min_gutter_in(pages: int) -> float:
    for limit, g in ((150, 0.375), (300, 0.5), (500, 0.625), (700, 0.75), (828, 0.875)):
        if pages <= limit:
            return g
    raise PipelineError("KDP paperbacks max out at 828 pages")


def cover_dims(cfg: dict, pages: int) -> dict:
    b = cfg["book"]
    dpi = b["dpi"]
    spine = pages * PAPER_THICKNESS_IN[b["paper"]]
    w_in = BLEED_IN + b["trim_width_in"] + spine + b["trim_width_in"] + BLEED_IN
    h_in = BLEED_IN + b["trim_height_in"] + BLEED_IN
    px = lambda inches: int(round(inches * dpi))  # noqa: E731
    return {
        "pages": pages,
        "spine_in": round(spine, 4),
        "width_in": round(w_in, 4),
        "height_in": h_in,
        "width_px": px(w_in),
        "height_px": px(h_in),
        "back_trim_x0": px(BLEED_IN),
        "back_trim_x1": px(BLEED_IN + b["trim_width_in"]),
        "spine_x0": px(BLEED_IN + b["trim_width_in"]),
        "spine_x1": px(BLEED_IN + b["trim_width_in"] + spine),
        "front_trim_x0": px(BLEED_IN + b["trim_width_in"] + spine),
        "front_trim_x1": px(w_in - BLEED_IN),
        "trim_y0": px(BLEED_IN),
        "trim_y1": px(h_in - BLEED_IN),
        "spine_text_allowed": pages >= 79,
    }


def page_px(cfg: dict) -> tuple[int, int]:
    b = cfg["book"]
    return int(round(b["trim_width_in"] * b["dpi"])), int(round(b["trim_height_in"] * b["dpi"]))


def safe_box_px(cfg: dict) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the area art must stay inside."""
    w, h = page_px(cfg)
    m = int(round(cfg["book"]["safe_margin_in"] * cfg["book"]["dpi"]))
    return m, m, w - m, h - m


# ---------------------------------------------------------------- fonts
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Rounded Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font_path(cfg: dict) -> str | None:
    if cfg.get("fonts", {}).get("title"):
        return cfg["fonts"]["title"]
    for p in sorted((ROOT / "fonts").glob("*")):
        if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
            return str(p)
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def get_font(cfg: dict, size: int) -> ImageFont.FreeTypeFont:
    p = font_path(cfg)
    if p:
        return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)
