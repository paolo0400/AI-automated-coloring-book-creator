"""Pixel-level checks and the black/white + 300 DPI + centering cleanup (step 7)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from .core import page_px, safe_box_px

THRESHOLD = 150  # grayscale value: darker -> black, lighter -> white


def to_bw(img: Image.Image) -> Image.Image:
    """Pure black (0) / white (255) grayscale image."""
    return img.convert("L").point(lambda v: 0 if v < THRESHOLD else 255)


def black_count(bw: Image.Image) -> int:
    return bw.histogram()[0]


def gray_fraction(img: Image.Image) -> float:
    h = img.convert("L").histogram()
    return sum(h[40:216]) / max(1, sum(h))


def color_fraction(img: Image.Image) -> float:
    hsv = img.convert("RGB").convert("HSV")
    s, v = hsv.split()[1], hsv.split()[2]
    colored = Image.eval(s, lambda x: 255 if x > 70 else 0)
    bright = Image.eval(v, lambda x: 255 if x > 70 else 0)
    both = Image.composite(colored, Image.new("L", s.size, 0), bright)
    return both.histogram()[255] / (s.size[0] * s.size[1])


def thin_line_fraction(bw: Image.Image) -> float:
    """Share of ink in strokes thinner than ~3 px (0.75 pt at 300 DPI is ~3.1 px)."""
    black = black_count(bw)
    if not black:
        return 0.0
    opened = bw.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return (black - black_count(opened)) / black


def solid_fill_fraction(bw: Image.Image) -> float:
    """Share of ink that sits in big solid blobs (hard for kids to color around)."""
    small = bw.resize((bw.width // 4, bw.height // 4))
    small = small.point(lambda v: 0 if v < 128 else 255)
    black = black_count(small)
    if not black:
        return 0.0
    opened = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(7))
    return black_count(opened) / black


def check_raw_page(path: Path, cfg: dict) -> list[str]:
    """Fast checks on a freshly generated coloring page. Returns a list of problems."""
    q = cfg["qa"]
    img = Image.open(path)
    issues = []
    cf = color_fraction(img)
    if cf > q["max_color_fraction"]:
        issues.append(f"page is in color ({cf:.0%} colored pixels)")
    gf = gray_fraction(img)
    if gf > q["max_gray_fraction"]:
        issues.append(f"too much gray shading ({gf:.0%})")
    bw = to_bw(img)
    total = bw.width * bw.height
    bf = black_count(bw) / total
    if bf > q["max_black_fraction"]:
        issues.append(f"too much black ink ({bf:.0%})")
    if bf < q["min_black_fraction"]:
        issues.append(f"page is nearly empty ({bf:.1%} ink)")
    sf = solid_fill_fraction(bw)
    if sf > q["max_solid_fill_fraction"]:
        issues.append(f"large solid black areas ({sf:.0%} of ink)")
    return issues


def center_is_empty(path: Path, max_ink: float = 0.01) -> bool:
    bw = to_bw(Image.open(path))
    w, h = bw.size
    box = bw.crop((int(w * .25), int(h * .25), int(w * .75), int(h * .75)))
    return black_count(box) / (box.width * box.height) <= max_ink


# ---------------------------------------------------------------- step 7
def clean_page(src: Path, dst: Path, cfg: dict) -> dict:
    """Make a page pure B/W, exactly trim-size at 300 DPI, art centered inside the safe box."""
    q = cfg["qa"]
    page_w, page_h = page_px(cfg)
    l, t, r, b = safe_box_px(cfg)
    box_w, box_h = r - l, b - t

    gray = Image.open(src).convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=1)
    bbox = ImageOps.invert(to_bw(gray)).getbbox()
    if bbox is None:
        raise ValueError(f"{src.name} is blank")
    gray = gray.crop(bbox)

    # leave a few pixels of slack so thickening lines can't push ink past the safe box
    scale = min((box_w - 8) / gray.width, (box_h - 8) / gray.height)
    new_size = (max(1, int(gray.width * scale)), max(1, int(gray.height * scale)))
    gray = gray.resize(new_size, Image.LANCZOS)
    bw = ImageOps.expand(to_bw(gray), border=4, fill=255)

    thickened = False
    thin = thin_line_fraction(bw)
    if thin > q["thin_line_fraction"]:
        bw = bw.filter(ImageFilter.MinFilter(3))  # grow black strokes by ~1 px each side
        thickened = True

    canvas = Image.new("L", (page_w, page_h), 255)
    x = l + (box_w - bw.width) // 2
    y = t + (box_h - bw.height) // 2
    canvas.paste(bw, (x, y))
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst, dpi=(cfg["book"]["dpi"], cfg["book"]["dpi"]))
    return {"thin_line_fraction": round(thin, 3), "thickened": thickened,
            "problems": verify_final(dst, cfg)}


def verify_final(path: Path, cfg: dict) -> list[str]:
    """Final gate for an interior page file."""
    problems = []
    img = Image.open(path)
    want = page_px(cfg)
    if img.size != want:
        problems.append(f"size {img.size} should be {want}")
    dpi = img.info.get("dpi", (0, 0))
    if round(dpi[0]) != cfg["book"]["dpi"]:
        problems.append(f"DPI is {dpi[0]}, should be {cfg['book']['dpi']}")
    colors = {c for _, c in (img.convert("L").getcolors(256) or [])}
    if not colors <= {0, 255}:
        problems.append("contains gray pixels (not pure black and white)")
    bbox = ImageOps.invert(img.convert("L")).getbbox()
    if bbox is None:
        problems.append("page is blank")
    else:
        l, t, r, b = safe_box_px(cfg)
        if bbox[0] < l or bbox[1] < t or bbox[2] > r or bbox[3] > b:
            problems.append(f"art {bbox} extends outside the safe area {(l, t, r, b)}")
    return problems
