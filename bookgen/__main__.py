"""Command line: python -m bookgen <command>

  doctor                 check that everything is installed
  models                 list Gemini model names available to your key
  theme  --idea "..."    step 1   (--pages N for a small test book)
  cover                  steps 2+3 (--redo for new art, --recompose to rebuild layout only)
  check-cover            step 3 only
  pages                  step 4   (--limit N, --redo 3,7, --hint "...")
  title                  step 5   (--redo)
  clean                  step 7   (run before pdf)
  pdf                    step 6
  package                step 8
  all                    run everything in order (stops for review if pause_for_review is on)
  batch --count N        make N whole books back to back, unattended
  approve theme|cover    mark a review gate as approved
  test-image             make one sample coloring page to test the image engine
  status                 progress and spend
  list                   list books / switch with --book
"""
from __future__ import annotations

import argparse
import os
import sys

from .core import (BOOKS_DIR, FAKE, PipelineError, current_book, font_path, image_provider,
                   load_config, log, set_current)


def doctor(cfg: dict) -> None:
    ok = True
    log(f"Python {sys.version.split()[0]}" + ("  (need 3.10+)" if sys.version_info < (3, 10) else ""))
    ok &= sys.version_info >= (3, 10)
    for mod in ("google.genai", "ollama", "PIL", "yaml", "reportlab", "pypdf", "dotenv"):
        try:
            __import__(mod)
            log(f"  [ok] {mod}")
        except ImportError:
            log(f"  [MISSING] {mod}  -> pip install -r requirements.txt")
            ok = False
    try:
        import ollama
        have = {m.model for m in ollama.list().models}
        log(f"  [ok] Ollama is running ({len(have)} models)")
        for role, r in cfg["roles"].items():
            if r["provider"] == "ollama":
                found = r["model"] in have or f"{r['model']}:latest" in have
                log(f"  [{'ok' if found else 'MISSING'}] {role}: {r['model']}"
                    + ("" if found else f"  -> ollama pull {r['model']}  (or change config.yaml)"))
                ok &= found
    except Exception as e:  # noqa: BLE001
        log(f"  [PROBLEM] Can't reach Ollama ({e}). Open the Ollama app.")
        ok = False
    provider = image_provider(cfg)
    log(f"  Image engine: {provider}")
    if provider == "drawthings":
        from .ai import drawthings_status
        try:
            dt = drawthings_status(cfg)
            loaded = dt.get("model") or "(unknown)"
            log(f"  [ok] Draw Things API server is on. Loaded model: {loaded}")
            want = cfg.get("drawthings", {}).get("model")
            if want and want != loaded:
                log(f"  [note] config.yaml asks for model '{want}' - make sure it's downloaded in Draw Things")
        except Exception as e:  # noqa: BLE001
            log(f"  [PROBLEM] Can't reach Draw Things ({e}).")
            log("            Open Draw Things -> Settings/Advanced -> API Server ON, HTTP, port 7860.")
            ok = False
    uses_gemini = provider == "gemini" or any(r["provider"] == "gemini" for r in cfg["roles"].values())
    key = os.environ.get("GEMINI_API_KEY", "")
    good_key = bool(key) and key != "paste-your-key-here"
    if not uses_gemini:
        log("  [ok] Gemini not needed with these settings")
    else:
        log(f"  [{'ok' if good_key else 'MISSING'}] GEMINI_API_KEY in .env")
        ok &= good_key
    if uses_gemini and good_key and provider == "gemini":
        try:
            from .ai import list_gemini_models
            names = list_gemini_models()
            for m in {cfg["images"]["page_model"], cfg["images"]["cover_model"]}:
                found = m in names
                log(f"  [{'ok' if found else 'CHECK'}] image model {m}"
                    + ("" if found else "  -> run `python -m bookgen models` and fix config.yaml"))
                ok &= found
        except Exception as e:  # noqa: BLE001
            log(f"  [PROBLEM] Gemini key didn't work: {e}")
            ok = False
    fp = font_path(cfg)
    log(f"  [{'ok' if fp else 'WARN'}] font: {fp or 'none found, using a basic font (add one to fonts/)'}")
    log("\nAll good!" if ok else "\nFix the items above, then run doctor again.")


def main() -> int:
    p = argparse.ArgumentParser(prog="bookgen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command")
    p.add_argument("target", nargs="?", help="for approve: theme or cover")
    p.add_argument("--book", help="book folder name under books/ (default: the latest)")
    p.add_argument("--idea", help="step 1: what the book should be about")
    p.add_argument("--pages", type=int, help="step 1: number of coloring pages (default 40)")
    p.add_argument("--limit", type=int, help="step 4: only draw the first N pages")
    p.add_argument("--redo", nargs="?", const="all", help="redo a step; for pages: --redo 3,7")
    p.add_argument("--hint", default="", help="step 4: extra drawing instructions for redone pages")
    p.add_argument("--recompose", action="store_true", help="cover: rebuild layout, no new art")
    p.add_argument("--no-pause", action="store_true", help="all: skip the review stops")
    p.add_argument("--count", type=int, help="batch: how many books to make")
    p.add_argument("--ideas-file", help="batch: text file with one book idea per line")
    a = p.parse_args()
    cfg = load_config()
    if FAKE:
        log("*** FAKE MODE: no AI calls, no cost, placeholder images ***")

    from . import step1_theme, step2_3_cover, step4_5_pages
    from . import step6_7_8_output as outp
    from .ai import list_gemini_models

    try:
        cmd = a.command
        if cmd == "doctor":
            doctor(cfg)
        elif cmd == "test-image":
            from pathlib import Path
            from .ai import generate_image
            from .core import Book
            from .imagecheck import check_raw_page
            from .step4_5_pages import LOCAL_PAGE_PROMPT, PAGE_PROMPT, STYLE
            tmpl = LOCAL_PAGE_PROMPT if image_provider(cfg) == "drawthings" else PAGE_PROMPT
            prompt = tmpl.format(style=STYLE.format(age="4-8"), title="Test", summary="a test page",
                                 setting="a sunny park", id=1, scene_title="Picnic",
                                 description="a happy cartoon bear having a picnic under a tree",
                                 chars="a round friendly bear", hint="").strip()
            out = Path("test_image.png").resolve()
            log(f"Making a test page with {image_provider(cfg)}...")
            generate_image(cfg, Book("_test"), prompt, out, kind="page", aspect="3:4")
            issues = check_raw_page(out, cfg)
            log(f"Saved {out}")
            log("Quality check: " + ("looks like clean line art" if not issues else "; ".join(issues)))
            os.system(f'open "{out}" 2>/dev/null')
        elif cmd == "models":
            for n in list_gemini_models():
                log(("  * " if "image" in n else "    ") + n)
            log("(* = image models)")
        elif cmd == "list":
            for d in sorted(BOOKS_DIR.glob("*/book.yaml")):
                log(f"  {d.parent.name}")
            if a.book:
                set_current(a.book)
                log(f"Current book is now {a.book}")
        elif cmd == "theme":
            step1_theme.run(cfg, a.idea, a.pages)
        elif cmd == "cover":
            step2_3_cover.run(cfg, current_book(a.book), redo=bool(a.redo), recompose=a.recompose)
        elif cmd == "check-cover":
            step2_3_cover.check(cfg, current_book(a.book))
        elif cmd == "pages":
            redo = None
            if a.redo and a.redo != "all":
                redo = [int(x) for x in a.redo.split(",")]
            elif a.redo == "all":
                book = current_book(a.book)
                redo = [s["id"] for s in book.load()["scenes"]]
            step4_5_pages.run_pages(cfg, current_book(a.book), a.limit, redo, a.hint)
        elif cmd == "title":
            step4_5_pages.run_title(cfg, current_book(a.book), redo=bool(a.redo))
        elif cmd == "clean":
            outp.run_clean(cfg, current_book(a.book))
        elif cmd == "pdf":
            outp.run_pdf(cfg, current_book(a.book))
        elif cmd == "package":
            outp.run_package(cfg, current_book(a.book))
        elif cmd == "batch":
            from .batch import run_batch
            if not a.count or a.count < 1:
                raise PipelineError("Say how many books: python -m bookgen batch --count 5")
            return run_batch(cfg, a.count, a.idea, a.ideas_file, a.pages)
        elif cmd == "status":
            outp.status(cfg, current_book(a.book))
        elif cmd == "approve":
            if a.target not in ("theme", "cover"):
                raise PipelineError("Use: python -m bookgen approve theme   (or cover)")
            book = current_book(a.book)
            st = book.state()
            st.setdefault("approved", {})[a.target] = True
            book.save_state(st)
            log(f"Approved {a.target} for {book.slug}")
        elif cmd == "all":
            pause = cfg["pause_for_review"] and not a.no_pause
            if a.idea or not (BOOKS_DIR / ".current").exists():
                book = step1_theme.run(cfg, a.idea, a.pages)
            else:
                book = current_book(a.book)
            st = book.state()
            if pause and not st.get("approved", {}).get("theme"):
                log(f"\nREVIEW: open books/{book.slug}/book.yaml. Edit anything you like, then run:\n"
                    "  python -m bookgen approve theme\n  python -m bookgen all")
                return 0
            step2_3_cover.run(cfg, book)
            st = book.state()
            if pause and not st.get("approved", {}).get("cover"):
                log(f"\nREVIEW: look at books/{book.slug}/cover/cover_full_wrap.jpg, then run:\n"
                    "  python -m bookgen approve cover   (or: python -m bookgen cover --redo)\n"
                    "  python -m bookgen all")
                return 0
            step4_5_pages.run_pages(cfg, book)
            step4_5_pages.run_title(cfg, book)
            result = outp.run_clean(cfg, book)
            if result["missing"] or result["bad"]:
                log("\nStopped: fix the pages listed above, then run `python -m bookgen all` again.")
                return 1
            outp.run_pdf(cfg, book)
            outp.run_package(cfg, book)
            outp.status(cfg, book)
        else:
            p.print_help()
            return 1
    except PipelineError as e:
        log(f"\nSTOPPED: {e}")
        return 2
    except KeyboardInterrupt:
        log("\nInterrupted. Progress is saved; run the same command again to continue.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
