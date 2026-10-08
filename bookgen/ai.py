"""All model calls live here: local Ollama text/vision, Gemini text, Gemini or Draw Things images.

Every paid call goes through the budget ledger first, so a runaway loop can't
spend more than the caps in config.yaml.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .core import FAKE, ROOT, Book, PipelineError, image_provider, log

LEDGER_PATH = ROOT / "spend_ledger.json"


class BudgetExceeded(PipelineError):
    pass


# ================================================================ budget
def _ledger() -> list:
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return []


def spent(book_slug: str | None = None) -> float:
    return round(sum(e["usd"] for e in _ledger() if book_slug in (None, e["book"])), 4)


def _check_budget(cfg: dict, book: Book, est: float) -> None:
    b = cfg["budget"]
    if spent(book.slug) + est > b["max_usd_per_book"]:
        raise BudgetExceeded(
            f"Stopping: this call (~${est:.3f}) would push '{book.slug}' past its "
            f"${b['max_usd_per_book']:.2f} cap (spent ${spent(book.slug):.2f}). "
            "Raise budget.max_usd_per_book in config.yaml if you want to continue.")
    if spent() + est > b["max_usd_total"]:
        raise BudgetExceeded(
            f"Stopping: total spend would pass ${b['max_usd_total']:.2f} "
            f"(spent ${spent():.2f}). Raise budget.max_usd_total in config.yaml to continue.")


def _record(book: Book, kind: str, model: str, usd: float, note: str = "") -> None:
    entries = _ledger()
    entries.append({"time": datetime.now().isoformat(timespec="seconds"), "book": book.slug,
                    "kind": kind, "model": model, "usd": round(usd, 4), "note": note})
    LEDGER_PATH.write_text(json.dumps(entries, indent=1))


def image_price(cfg: dict, model: str, size: str) -> float:
    if image_provider(cfg) == "drawthings":
        return 0.0
    table = cfg["prices_usd_per_image"]
    if model in table and isinstance(table[model], dict):
        return float(table[model].get(size, max(table[model].values())))
    return float(table.get("default", 0.15))


# ================================================================ helpers
def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise PipelineError(f"Model did not return JSON. Got: {text[:300]}")
    return json.loads(text[start:end + 1])


_gemini_client = None


def gemini():
    global _gemini_client
    if _gemini_client is None:
        if not os.environ.get("GEMINI_API_KEY"):
            raise PipelineError("GEMINI_API_KEY is missing. Copy .env.example to .env and add your key.")
        from google import genai
        _gemini_client = genai.Client()
    return _gemini_client


def _retry(fn, what: str):
    """Retry transient API errors (rate limits, overloads) with backoff."""
    for i in range(4):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code in (429, 500, 502, 503, 504) and i < 3:
                wait = 10 * (2 ** i)
                log(f"   {what}: temporary error {code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            raise


# ================================================================ text / vision JSON
def ask_json(cfg: dict, role: str, prompt: str, schema: dict, images: list | None = None,
             book: Book | None = None, temperature: float = 0.7) -> dict:
    """Ask a model for JSON matching `schema`. `role` is a key under config `roles`."""
    r = cfg["roles"][role]
    if FAKE:
        return _fake_json(role, prompt, schema)
    if r["provider"] == "ollama":
        import ollama
        msg = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [str(p) for p in images]
        try:
            resp = ollama.chat(model=r["model"], messages=[msg], format=schema,
                               options={"temperature": temperature, "num_ctx": 16384})
        except Exception as e:  # noqa: BLE001
            raise PipelineError(
                f"Ollama call failed for model '{r['model']}' ({e}). Is the Ollama app running, "
                f"and is the model installed? Try: ollama pull {r['model']}") from e
        return _extract_json(resp.message.content)
    if r["provider"] == "gemini":
        from google.genai import types
        if book:
            _check_budget(cfg, book, cfg["text_call_cost_usd"])
        full = (prompt + "\n\nRespond ONLY with JSON matching this JSON schema:\n"
                + json.dumps(schema))
        contents = [full] + [Image.open(p) for p in (images or [])]
        resp = _retry(lambda: gemini().models.generate_content(
            model=r["model"], contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               temperature=temperature)), role)
        if book:
            _record(book, f"text:{role}", r["model"], cfg["text_call_cost_usd"])
        return _extract_json(resp.text)
    raise PipelineError(f"Unknown provider '{r['provider']}' for role '{role}'")


# ================================================================ images
def generate_image(cfg: dict, book: Book, prompt: str, out_path: Path, *, kind: str,
                   refs: list | None = None, aspect: str = "3:4", size: str | None = None,
                   model: str | None = None) -> Path:
    """Generate one image and save it as PNG. Raises BudgetExceeded before overspending."""
    if image_provider(cfg) == "drawthings" and not FAKE:
        return _drawthings_generate(cfg, book, prompt, out_path, kind=kind, aspect=aspect)
    model = model or cfg["images"]["page_model"]
    size = size or cfg["images"]["page_size"]
    est = image_price(cfg, model, size)
    _check_budget(cfg, book, est)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if FAKE:
        _fake_image(kind, aspect).save(out_path)
        _record(book, kind, "FAKE", 0.0, out_path.name)
        return out_path

    from google.genai import types
    contents = [prompt] + [Image.open(p) for p in (refs or []) if Path(p).exists()]
    config = types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=aspect, image_size=size),
    )
    resp = _retry(lambda: gemini().models.generate_content(
        model=model, contents=contents, config=config), kind)

    img = None
    for cand in (resp.candidates or []):
        for part in ((cand.content.parts if cand.content else None) or []):
            if getattr(part, "inline_data", None) and part.inline_data.data:
                img = Image.open(io.BytesIO(part.inline_data.data))
                break
        if img:
            break
    if img is None:
        _record(book, kind, model, 0.002, f"no image returned for {out_path.name}")
        raise PipelineError(f"Gemini returned no image for {out_path.name} "
                            "(possibly a safety block). Try rewording the scene.")
    _record(book, kind, model, est, out_path.name)
    img.convert("RGB").save(out_path)
    return out_path


# ================================================================ Draw Things (local, free)
DEFAULT_NEGATIVE = ("text, letters, words, numbers, watermark, signature, logo, blurry, "
                    "photo, realistic, deformed, extra limbs, cropped")
LINE_ART_NEGATIVE = "color, colored, shading, gray, grey, gradient, hatching, solid black fill, "


def _dt(cfg: dict) -> dict:
    return cfg.get("drawthings", {})


def _dt_url(cfg: dict) -> str:
    return _dt(cfg).get("url", "http://127.0.0.1:7860").rstrip("/")


def drawthings_status(cfg: dict) -> dict:
    """Returns Draw Things' current settings, or raises if the API server isn't reachable."""
    import urllib.request
    with urllib.request.urlopen(_dt_url(cfg) + "/", timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def _dt_size(cfg: dict, kind: str, aspect: str) -> tuple[int, int]:
    key = "cover_size" if kind.startswith("cover") else "page_size"
    w, h = _dt(cfg).get(key, [960, 1280] if key == "cover_size" else [768, 1024])
    a, b = (int(x) for x in aspect.split(":"))
    if a > b:  # landscape request (character sheet)
        w, h = max(w, h), min(w, h)
    else:
        w, h = min(w, h), max(w, h)
    return int(w) // 64 * 64, int(h) // 64 * 64


def _drawthings_generate(cfg: dict, book: Book, prompt: str, out_path: Path, *,
                         kind: str, aspect: str) -> Path:
    import urllib.error
    import urllib.request

    dt = _dt(cfg)
    w, h = _dt_size(cfg, kind, aspect)
    negative = DEFAULT_NEGATIVE if kind.startswith("cover") else LINE_ART_NEGATIVE + DEFAULT_NEGATIVE
    payload = {"prompt": prompt, "negative_prompt": negative, "width": w, "height": h,
               "seed": -1, "batch_count": 1, "batch_size": 1}
    for k in ("model", "steps", "guidance_scale", "sampler"):
        if dt.get(k) not in (None, ""):
            payload[k] = dt[k]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(_dt_url(cfg) + "/sdapi/v1/txt2img",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=int(dt.get("timeout_seconds", 900))) as r:
            data = json.loads(r.read().decode())
    except urllib.error.URLError as e:
        raise PipelineError(
            f"Can't reach Draw Things at {_dt_url(cfg)} ({e}). Open Draw Things and turn on "
            "its API Server (HTTP, port 7860).") from e
    except TimeoutError as e:
        raise PipelineError("Draw Things took too long. Try fewer steps or a smaller size "
                            "in config.yaml (drawthings section).") from e

    images = data.get("images") or []
    if not images:
        raise PipelineError(f"Draw Things returned no image for {out_path.name}: {str(data)[:200]}")
    b64 = images[0].split(",", 1)[-1]  # strip a "data:image/png;base64," prefix if present
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    img.convert("RGB").save(out_path)
    _record(book, kind, "drawthings", 0.0, f"{out_path.name} {time.time() - started:.0f}s")
    log(f"     (Draw Things: {time.time() - started:.0f}s)")
    return out_path


def list_gemini_models() -> list[str]:
    return sorted(m.name.replace("models/", "") for m in gemini().models.list())


# ================================================================ fake mode (free dry runs)
def _fake_json(role: str, prompt: str, schema: dict) -> dict:
    props = schema.get("properties", {})
    if role == "theme" and "scenes" in props and "title" not in props:
        n = int(re.search(r"Write (\d+)", prompt).group(1))
        start = int(re.search(r"numbered from (\d+)", prompt).group(1))
        return {"scenes": [{"title": f"Test Scene {start + i}",
                            "description": f"Bunny and Fox do activity number {start + i} in the meadow."}
                           for i in range(n)]}
    if role == "theme":
        return {"title": "Meadow Friends Coloring Adventure", "subtitle": "A Test Coloring Book for Kids",
                "theme_summary": "Two woodland friends explore a sunny meadow.",
                "setting": "a sunny meadow with flowers and a small pond",
                "characters": [{"name": "Bunny", "look": "small round bunny with long floppy ears"},
                               {"name": "Fox", "look": "friendly little fox with a big fluffy tail"}],
                "cover_scene": "Bunny and Fox waving in a flower meadow",
                "cover_palette": "sky blue, sunny yellow, grass green, soft pink",
                "back_blurb": "Join Bunny and Fox for a meadow full of fun pages to color!"}
    if role in ("vision_qa",):
        return {k: (False if k.startswith("has_") else True) for k in props} | {"notes": "fake"}
    if role == "cover_judge":
        return {"score": 8, "title_readable": True, "artwork_has_stray_text": False,
                "problems": []}
    return {}


def _fake_image(kind: str, aspect: str) -> Image.Image:
    a, b = (int(x) for x in aspect.split(":"))
    w, h = 900, int(900 * b / a)
    rnd = random.Random(time.time_ns())
    if kind.startswith("cover"):
        img = Image.new("RGB", (w, h), (120, 190, 240))
        d = ImageDraw.Draw(img)
        d.rectangle([0, int(h * .65), w, h], fill=(110, 200, 110))
        for _ in range(12):
            x, y, r = rnd.randint(50, w - 50), rnd.randint(int(h * .4), h - 40), rnd.randint(20, 60)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(rnd.randint(150, 255), rnd.randint(80, 200), 150))
        return img
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    if kind == "title_border":
        d.rounded_rectangle([40, 40, w - 40, h - 40], radius=60, outline="black", width=8)
        for cx, cy in ((90, 90), (w - 90, 90), (90, h - 90), (w - 90, h - 90)):
            d.ellipse([cx - 35, cy - 35, cx + 35, cy + 35], outline="black", width=6)
        return img
    for _ in range(9):
        x, y, r = rnd.randint(150, w - 150), rnd.randint(150, h - 150), rnd.randint(40, 120)
        d.ellipse([x - r, y - r, x + r, y + r], outline="black", width=7)
    d.line([60, h - 200, w - 60, h - 200], fill="black", width=7)
    return img
