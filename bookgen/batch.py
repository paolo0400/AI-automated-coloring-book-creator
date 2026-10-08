"""Make several books in one run, one after another."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import step1_theme, step2_3_cover, step4_5_pages
from . import step6_7_8_output as outp
from .ai import BudgetExceeded, spent
from .core import ROOT, Book, PipelineError, log

REPORT = ROOT / "batch_report.md"


def run_one_book(cfg: dict, idea: str | None, pages: int | None) -> Book:
    """Every step for a single book, with no review stops."""
    book = step1_theme.run(cfg, idea, pages)
    step2_3_cover.run(cfg, book)
    step4_5_pages.run_pages(cfg, book)
    step4_5_pages.run_title(cfg, book)
    result = outp.run_clean(cfg, book)
    if result["missing"] or result["bad"]:
        raise PipelineError("pages failed the print checks: "
                            + ", ".join(result["missing"] + result["bad"]))
    outp.run_pdf(cfg, book)
    outp.run_package(cfg, book)
    return book


def run_batch(cfg: dict, count: int, idea: str | None = None,
              ideas_file: str | None = None, pages: int | None = None) -> int:
    ideas: list[str | None] = [idea] * count
    if ideas_file:
        lines = [ln.strip() for ln in Path(ideas_file).read_text().splitlines() if ln.strip()]
        if not lines:
            raise PipelineError(f"{ideas_file} has no ideas in it (one per line).")
        ideas = [lines[i % len(lines)] for i in range(count)]

    started = time.time()
    done: list[tuple[str, str, str]] = []  # slug, title, result
    log(f"Batch: making {count} books. This runs unattended - no review stops.\n")

    for i, this_idea in enumerate(ideas, 1):
        log(f"=========== Book {i} of {count} ===========")
        t0 = time.time()
        try:
            book = run_one_book(cfg, this_idea, pages)
            data = book.load()
            st = book.state()
            flagged = [k for k, v in st["pages"].items() if v["status"] != "approved"]
            note = f"done in {(time.time() - t0) / 60:.0f} min"
            if flagged:
                note += f", {len(flagged)} page(s) flagged: {', '.join(flagged)}"
            done.append((book.slug, data["title"], note))
            log(f"Book {i} finished: {data['title']}\n")
        except BudgetExceeded as e:
            log(f"\nBatch stopped at book {i}: {e}")
            done.append(("-", this_idea or "(model's own idea)", f"STOPPED: {e}"))
            break
        except PipelineError as e:
            log(f"Book {i} failed: {e}\nMoving on to the next one.\n")
            done.append(("-", this_idea or "(model's own idea)", f"FAILED: {e}"))
        except KeyboardInterrupt:
            log("\nBatch interrupted. Finished books are saved in output/.")
            break

    lines = [f"# Batch run {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Books attempted: {len(done)} of {count}",
             f"Total time: {(time.time() - started) / 60:.0f} minutes",
             f"Spend this batch: see spend_ledger.json (total so far ${spent():.2f})", "",
             "| # | Folder | Title | Result |", "|---|---|---|---|"]
    lines += [f"| {n} | {slug} | {title} | {note} |" for n, (slug, title, note) in enumerate(done, 1)]
    REPORT.write_text("\n".join(lines) + "\n")

    ok = sum(1 for _, _, n in done if n.startswith("done"))
    log(f"\nBatch finished: {ok} of {count} books made in "
        f"{(time.time() - started) / 60:.0f} minutes.")
    log("Each finished book is in its own folder under output/.")
    log(f"Summary written to {REPORT.name}. Review every book before publishing.")
    return 0 if ok else 1
