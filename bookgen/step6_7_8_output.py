"""Step 7 (clean every page), step 6 (build the interior PDF), step 8 (package folders)."""
from __future__ import annotations

import json
import shutil
from datetime import date

from pypdf import PdfReader
from reportlab.pdfgen import canvas as pdfcanvas

from .ai import spent
from .core import (OUTPUT_DIR, Book, PipelineError, cover_dims, interior_page_count, log,
                   min_gutter_in)
from .imagecheck import clean_page, verify_final


def _page_keys(book: Book) -> list[str]:
    return [f"page_{s['id']:02d}" for s in book.load()["scenes"]]


# ---------------------------------------------------------------- step 7
def run_clean(cfg: dict, book: Book) -> dict:
    log("Step 7: making every page pure black/white, 300 DPI, centered in the safe area...")
    pages = interior_page_count(book.num_pages(), cfg["book"]["blank_backs"])
    if cfg["book"]["safe_margin_in"] < min_gutter_in(pages):
        raise PipelineError(f"safe_margin_in must be at least {min_gutter_in(pages)} for {pages} pages")
    report, missing = {}, []
    for key in ["title_page"] + _page_keys(book):
        src = book.raw / f"{key}.png"
        if not src.exists():
            missing.append(key)
            continue
        dst = book.final / f"{key}.png"
        if key == "title_page":
            # already laid out on the page; just verify it (re-fitting would shrink the text)
            shutil.copy(src, dst)
            report[key] = {"problems": verify_final(dst, cfg), "thickened": False}
        else:
            report[key] = clean_page(src, dst, cfg)
        mark = "OK" if not report[key]["problems"] else "PROBLEM: " + "; ".join(report[key]["problems"])
        extra = " (lines thickened)" if report[key].get("thickened") else ""
        log(f"   {key}: {mark}{extra}")
    (book.qa / "cleanup_report.json").write_text(json.dumps(
        {"pages": report, "missing": missing}, indent=2))
    bad = [k for k, v in report.items() if v["problems"]]
    if missing:
        log(f"   Missing (run steps 4/5 first): {', '.join(missing)}")
    if bad:
        log(f"   Pages with problems: {', '.join(bad)}")
    return {"missing": missing, "bad": bad}


# ---------------------------------------------------------------- step 6
def run_pdf(cfg: dict, book: Book) -> None:
    log("Step 6: building the interior PDF...")
    data = book.load()
    b = cfg["book"]
    keys = ["title_page"] + _page_keys(book)
    missing = [k for k in keys if not (book.final / f"{k}.png").exists()]
    if missing:
        raise PipelineError(f"Can't build the PDF yet, missing cleaned pages: {', '.join(missing)}. "
                            "Run steps 4, 5 and 7 (pages, title, clean).")
    bad = [k for k in keys if verify_final(book.final / f"{k}.png", cfg)]
    if bad:
        raise PipelineError(f"These pages fail the print checks: {', '.join(bad)}. Run step 7 (clean).")

    W, H = b["trim_width_in"] * 72, b["trim_height_in"] * 72
    expected = interior_page_count(data["num_pages"], b["blank_backs"])
    c = pdfcanvas.Canvas(str(book.interior_pdf), pagesize=(W, H), pageCompression=1)
    c.setTitle(data["title"])
    c.setAuthor(data["author"])
    c.setCreator("")
    n = 0

    def blank():
        nonlocal n
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, W, H, stroke=0, fill=1)  # explicit white page
        c.showPage()
        n += 1

    for k in keys:
        c.drawImage(str(book.final / f"{k}.png"), 0, 0, width=W, height=H)
        c.showPage()
        n += 1
        if b["blank_backs"]:
            blank()
    while n < expected:
        blank()
    c.save()

    reader = PdfReader(str(book.interior_pdf))
    got = len(reader.pages)
    sizes = {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in reader.pages}
    if got != expected or sizes != {(round(W), round(H))}:
        raise PipelineError(f"PDF check failed: {got} pages (expected {expected}), sizes {sizes}")
    cover_meta = book.cover_dir / "cover_meta.json"
    if cover_meta.exists():
        cp = json.loads(cover_meta.read_text())["dims"]["pages"]
        if cp != got:
            raise PipelineError(f"Cover was built for {cp} pages but the PDF has {got}. "
                                "Run: python -m bookgen cover --recompose")
    log(f"   interior.pdf: {got} pages at {b['trim_width_in']} x {b['trim_height_in']} in - OK")


# ---------------------------------------------------------------- step 8
def run_package(cfg: dict, book: Book) -> None:
    log("Step 8: packaging the finished book...")
    data = book.load()
    st = book.state()
    if not book.interior_pdf.exists():
        raise PipelineError("No interior.pdf yet. Run step 6: python -m bookgen pdf")
    cover_files = ["cover_full_wrap.pdf", "cover_full_wrap.jpg", "cover_front.png",
                   "cover_front_art_only.png", "cover_thumbnail.png"]
    for f in cover_files:
        if not (book.cover_dir / f).exists():
            raise PipelineError(f"Missing cover/{f}. Run step 2: python -m bookgen cover")

    out = OUTPUT_DIR / book.slug
    if out.exists():
        shutil.rmtree(out)
    (out / "interior" / "pages").mkdir(parents=True)
    (out / "cover").mkdir(parents=True)
    for k in ["title_page"] + _page_keys(book):
        shutil.copy(book.final / f"{k}.png", out / "interior" / "pages" / f"{k}.png")
    shutil.copy(book.interior_pdf, out / "interior" / "interior.pdf")
    for f in cover_files:
        shutil.copy(book.cover_dir / f, out / "cover" / f)
    shutil.copy(book.yaml_path, out / "book.yaml")

    pages = interior_page_count(data["num_pages"], cfg["book"]["blank_backs"])
    d = cover_dims(cfg, pages)
    cover_check = {}
    if (book.qa / "cover_check.json").exists():
        cover_check = json.loads((book.qa / "cover_check.json").read_text())
    flagged = {k: v for k, v in st["pages"].items() if v["status"] != "approved"}

    lines = [
        f"# {data['title']}", f"*{data['subtitle']}*", "",
        f"Built {date.today()} - image spend for this book: ${spent(book.slug):.2f}", "",
        "## Review before uploading",
        f"- Cover check: {'passed' if cover_check.get('passed') else 'NEEDS REVIEW'} "
        f"(score {cover_check.get('score', '?')}/10)",
    ]
    lines += [f"  - {p}" for p in cover_check.get("problems", []) + cover_check.get("notes", [])]
    lines.append(f"- Pages flagged by QA: {', '.join(flagged) if flagged else 'none'}")
    for k, v in flagged.items():
        lines.append(f"  - {k}: {'; '.join(v.get('issues', []))}")
    lines += [
        "- Flip through interior/interior.pdf and look at every page yourself.", "",
        "## KDP settings to choose",
        f"- Trim size: {cfg['book']['trim_width_in']} x {cfg['book']['trim_height_in']} in",
        "- Bleed: No bleed (interior)",
        f"- Interior: Black & white, {cfg['book']['paper']} paper",
        "- Cover finish: your choice (matte is popular for coloring books)",
        f"- Page count KDP should detect: {pages}",
        "",
        "## Cover files",
        f"- cover_full_wrap.pdf - print-ready wrap ({d['width_in']} x {d['height_in']} in, "
        f"spine {d['spine_in']} in). Use 'Upload a cover you already have'.",
        "- cover_full_wrap.jpg - same wrap as JPEG for Cover Creator "
        "(From My Computer -> choose the second design on the top row).",
        "- cover_front_art_only.png - artwork without text, if you want Cover Creator to add the title.",
        "- cover_front.png / cover_thumbnail.png - for previews and listings.",
        "",
        "## Final checks on KDP",
        "- Use KDP's cover calculator to confirm the spine width matches the number above.",
        "- Open KDP's Print Previewer and check every page and the cover.",
        "- Answer YES to the AI-generated content question (images were made with AI).",
        "- Order a proof copy before publishing.",
    ]
    (out / "READ_ME_FIRST.md").write_text("\n".join(lines) + "\n")
    log(f"   Done: output/{book.slug}/")
    log("     interior/  -> interior.pdf + all page images")
    log("     cover/     -> full wrap PDF/JPG + front images")
    log("     READ_ME_FIRST.md -> review list and KDP settings")


# ---------------------------------------------------------------- status
def status(cfg: dict, book: Book) -> None:
    data = book.load()
    st = book.state()
    counts = {}
    for v in st["pages"].values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    log(f"Book: {data['title']}  (books/{book.slug})")
    log(f"  Theme approved: {st.get('approved', {}).get('theme', False)}")
    log(f"  Cover: {st.get('cover', {}).get('status', 'not started')}"
        f"  approved: {st.get('approved', {}).get('cover', False)}")
    log(f"  Pages ({data['num_pages']} planned): {counts or 'not started'}")
    log(f"  Title page: {st.get('title_page', {}).get('status', 'not started')}")
    log(f"  Cleaned pages: {len(list(book.final.glob('*.png')))}")
    log(f"  Interior PDF: {'yes' if book.interior_pdf.exists() else 'no'}")
    log(f"  Spend: this book ${spent(book.slug):.2f} / cap ${cfg['budget']['max_usd_per_book']:.2f}"
        f"   all books ${spent():.2f} / cap ${cfg['budget']['max_usd_total']:.2f}")
