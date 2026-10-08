# bookgen: Coloring Book Pipeline for Amazon KDP

Turn a one-line idea into a print-ready Amazon KDP coloring book: a full wraparound cover and an
interior PDF, with every page checked for print quality. Runs entirely on a Mac with free local
models (Ollama + Draw Things), or optionally with Google Gemini for images.

```
idea ──► theme (Ollama) ──► cover art + KDP wrap ──► cover check ──► 40 coloring pages + QA
                                                                           │
   output/<book>/  ◄── package ◄── interior PDF ◄── cleanup (B/W, 300 DPI) ◄┘  + "This book belongs to" page
```

## What it does

1. **Theme.** A local Ollama model invents the title, subtitle, recurring characters, setting, and 40 scenes.
2. **Cover.** An image engine paints colorful cover art; Python builds the exact KDP wraparound
   (back + spine + front + bleed) and sets the title text in the safe zone.
3. **Cover check.** Pixel size, DPI, safe zones, barcode area, text contrast, plus a vision-model
   judgment of the art and its readability at Amazon thumbnail size.
4. **Pages.** 40 coloring pages in a consistent style, each checked (color, gray shading, solid
   fills, ink coverage, plus a vision model) and retried automatically.
5. **Title page.** A "This Book Belongs To: ____" page with a decorative border.
6. **PDF.** Title page + 40 pages, each with a blank back, verified for page count and trim size.
7. **Cleanup** (runs before the PDF). Every page is made pure black and white, 300 DPI, centered
   inside the safe area, with thin lines thickened to KDP's 0.75 pt minimum.
8. **Package.** `output/<book>/interior/` and `output/<book>/cover/`, plus a `READ_ME_FIRST.md`
   with flagged pages and the exact KDP settings to choose.

Other features:

- **Batch mode:** make several complete books back to back, unattended.
- **Budget guards** (Gemini): hard per-book and total caps, a spend ledger, and a retry cap.
- **Resumable:** finished images are never regenerated, so any interrupted run can be restarted.
- **Free dry run:** `BOOKGEN_FAKE=1` runs the whole pipeline with placeholder images and no AI calls.
- **Agent-ready:** `CLAUDE.md` tells Claude Code (including Claude Code running on Ollama models)
  how to drive the pipeline, with review stops after the theme and the cover.

## Requirements

- macOS on Apple Silicon (Linux works for the dry run and with Gemini)
- Python 3.10+ (tested up to 3.14)
- [Ollama](https://ollama.com) with a text model and a vision model
- An image engine, either:
  - [Draw Things](https://drawthings.ai) (free, local) with FLUX.1 [schnell] or SDXL, API Server on, or
  - a Gemini API key with billing enabled (the account holder must be 18+)

Default models:

| Job | Model | Runs in |
|---|---|---|
| Title, characters, 40 scenes | `qwen3:14b` | Ollama |
| Page QA and cover judge | `gemma3:12b` (must support images) | Ollama |
| All images | FLUX.1 [schnell] | Draw Things |

On a 16 GB Mac, `qwen3:8b` and `gemma3:4b` are lighter alternatives.

---

## Setup

### 1. Get the code

```bash
git clone https://github.com/paolo0400/AI-automated-coloring-book-creator.git
cd AI-automated-coloring-book-creator
```

### 2. Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run `source .venv/bin/activate` in every new Terminal window before using the pipeline.

### 3. Check your Ollama models

With the Ollama app running:

```bash
ollama list
```

Either pull the defaults (`ollama pull qwen3:14b`, `ollama pull gemma3:12b`) or change the names
under `roles:` in `config.yaml` to models you already have, typed exactly as `ollama list` shows them.

### 4. Choose your image engine

`config.yaml` defaults to `images: provider: drawthings`.

**Draw Things (free, local):**

1. Open Draw Things and load a model that allows commercial use. FLUX.1 [schnell] or SDXL are
   good choices.
2. Make one image by hand in the app to confirm the model works.
3. Turn on the API Server (Settings or the Advanced tab): protocol **HTTP** (not gRPC),
   port **7860**, IP localhost, TLS off.
4. Keep Draw Things open whenever the pipeline runs.

Steps, sampler, and similar settings come from whatever is selected in the app. Image sizes are in
the `drawthings:` section of `config.yaml`.

**Gemini (paid, cloud):** set `provider: gemini`, then:

1. Create a key at https://aistudio.google.com/apikey and turn on billing for its project
   (Gemini's image models have no free tier).
2. Set a budget alert in Google Cloud Console (Billing → Budgets & alerts).
3. `cp .env.example .env` and paste your key into `.env`. Never commit `.env`.

### 5. Edit settings

Open `config.yaml` and at minimum set `author_name:` (it's printed on the cover). Other useful settings:

- `pause_for_review: true` stops `all` after the theme and after the cover so you can look.
- `budget:` sets hard spending caps for Gemini (default $15 per book, $50 total).
- `qa: vision_mode:` `strict` retries on vision-model complaints, `advisory` only notes them.

**Optional font:** drop a bold, kid-friendly `.ttf` (for example Fredoka or Baloo 2 from Google
Fonts, both OFL-licensed) into `fonts/`. Otherwise Arial Rounded Bold from macOS is used.

### 6. Health check and test image

```bash
python -m bookgen doctor
python -m bookgen test-image
```

`doctor` checks packages, Ollama models, the image engine connection, and the font. `test-image`
makes one sample page, opens it, and says whether it came out as clean line art.

---

## Usage

### Free dry run (no AI, $0)

```bash
BOOKGEN_FAKE=1 python -m bookgen all --idea test --pages 4 --no-pause
open output
rm -rf books output spend_ledger.json   # clean up afterwards
```

### One book, with review stops

```bash
python -m bookgen all --idea "cozy forest animals having a picnic"
# review and edit books/<slug>/book.yaml, then:
python -m bookgen approve theme
caffeinate -i python -m bookgen all
# review books/<slug>/cover/cover_full_wrap.jpg, then:
python -m bookgen approve cover
caffeinate -i python -m bookgen all
open output
```

`caffeinate -i` keeps the Mac awake during long runs. Start with `--pages 4` the first time.
If a run stops for any reason, run the same command again; finished pages are kept.

Type quote marks yourself in Terminal. Curly quotes pasted from email or chat cause a `dquote>`
prompt (press Ctrl+C to escape). Underscores avoid quotes entirely: `--idea ocean_friends`.

### Step by step

```bash
python -m bookgen theme --idea "..." --pages 4   # step 1
python -m bookgen cover                          # steps 2 + 3
python -m bookgen pages                          # step 4
python -m bookgen title                          # step 5
python -m bookgen clean                          # step 7 (always before pdf)
python -m bookgen pdf                            # step 6
python -m bookgen package                        # step 8
```

### Fixing things

| Problem | Command |
|---|---|
| Don't like the cover art | `python -m bookgen cover --redo` |
| Cover text or layout needs rebuilding (free) | `python -m bookgen cover --recompose` |
| Bad pages, e.g. 5 and 12 | `python -m bookgen pages --redo 5,12 --hint "simpler background, bigger shapes"` |
| Rebuild PDF and folders after fixes | `python -m bookgen all` |
| Check progress and spend | `python -m bookgen status` |
| Switch between books | `python -m bookgen list --book <folder-name>` |

### Writing your own theme

Run `python -m bookgen theme --idea placeholder --pages 1` to create a book folder, then replace
`books/<slug>/book.yaml` with your own title, characters, cover, and scenes. Rules:

- `num_pages` must equal the number of scenes, numbered 1, 2, 3... with no gaps.
- Use spaces, not tabs; wrap any sentence containing a colon in single quotes.
- Write very specific character `look` descriptions; they are what keep characters consistent.
- Keep everything original (no famous characters or brands).

Check it loads with `python -m bookgen status`, then `approve theme` and continue.

### Several books at once

```bash
# the model invents a theme for each book
caffeinate -i python -m bookgen batch --count 5

# one subject for every book (expect variations on a theme)
caffeinate -i python -m bookgen batch --count 3 --idea ocean_animals

# a different idea per book, one per line
caffeinate -i python -m bookgen batch --count 5 --ideas-file examples/halloween_ideas.txt

# quick test
python -m bookgen batch --count 2 --pages 3
```

Or pass each idea directly, one book per line, without an ideas file:

```bash
caffeinate -i python -m bookgen all --no-pause --idea "a friendly little witch and her clumsy black cat in a pumpkin village"
caffeinate -i python -m bookgen all --no-pause --idea "silly costumed kids trick-or-treating down a decorated street at dusk"
```

Batch mode has no review stops. A failed book is logged and the batch moves on. `batch_report.md`
lists each book, its run time, and any flagged pages. Review every book before publishing.

### Monitoring a long run

From a second Terminal window in the project folder:

```bash
ollama ps                                         # during step 1: is the text model loaded?
ls -lt books/*/pages_raw | head                   # during pages: are new files appearing?
while true; do clear; python -m bookgen status; sleep 30; done
```

Step 1 typically takes 5–15 minutes (`qwen3` thinks before answering and prints nothing while it
works). Each Draw Things image prints a line like `(Draw Things: 45s)`. If memory pressure is high
in Activity Monitor, quit Draw Things during step 1 or use a smaller theme model.

---

## Let an AI agent run it

The pipeline works without an agent. An agent adds judgment: it reads QA results, rewrites scenes
that keep failing, and redoes them, following the rules in `CLAUDE.md`.

```bash
curl -fsSL https://claude.ai/install.sh | bash      # install Claude Code
launchctl setenv OLLAMA_CONTEXT_LENGTH 64000         # local models need ~64k context; restart Ollama
cd AI-automated-coloring-book-creator
ollama launch claude                                 # pick a strong tool-calling model
```

Then: `Read CLAUDE.md. Make a 4-page test coloring book about dinosaurs going to school.`

Small local models can struggle to drive an agent. If it loops, press Esc and run the commands yourself.

---

## Upload to Amazon KDP (manual)

Open `output/<book>/READ_ME_FIRST.md`, which lists flagged pages and settings.

1. Trim 8.5 × 11 in, **no bleed**, black & white interior, white paper.
2. Manuscript: `interior/interior.pdf`.
3. Cover: `cover/cover_full_wrap.pdf` ("Upload a cover you already have"), or
   `cover/cover_full_wrap.jpg` in Cover Creator (second design on the top row).
4. Confirm the spine width with KDP's cover calculator.
5. Check every page and the cover in KDP's Print Previewer.
6. Answer **Yes** to the AI-generated content question.
7. Order a proof copy before publishing.

---

## Command reference

| Command | What it does |
|---|---|
| `python -m bookgen doctor` | Check setup |
| `python -m bookgen test-image` | Make one sample page with your image engine |
| `python -m bookgen models` | List Gemini model names |
| `python -m bookgen theme --idea "..." [--pages N]` | Step 1 |
| `python -m bookgen cover [--redo] [--recompose]` | Steps 2 + 3 |
| `python -m bookgen check-cover` | Step 3 only |
| `python -m bookgen pages [--limit N] [--redo 3,7] [--hint "..."]` | Step 4 |
| `python -m bookgen title [--redo]` | Step 5 |
| `python -m bookgen clean` | Step 7 (before the PDF) |
| `python -m bookgen pdf` | Step 6 |
| `python -m bookgen package` | Step 8 |
| `python -m bookgen all [--idea "..."] [--pages N] [--no-pause]` | Everything |
| `python -m bookgen batch --count N [--idea ...] [--ideas-file f] [--pages N]` | N whole books, unattended |
| `python -m bookgen approve theme` / `approve cover` | Pass a review stop |
| `python -m bookgen status` | Progress and spend |
| `python -m bookgen list [--book name]` | List or switch books |

## Troubleshooting

- **"Ollama call failed"**: open the Ollama app; check `config.yaml` model names against `ollama list`.
- **"Can't reach Draw Things" / "remote end closed connection"**: API Server off, or set to gRPC
  instead of HTTP. Test with `curl http://127.0.0.1:7860/`.
- **Draw Things pages come out colored or shaded**: try another model, more steps, or a
  coloring-book LoRA (check its license before selling).
- **Draw Things is very slow**: lower `drawthings.page_size` (e.g. `[640, 832]`) or use FLUX.1
  schnell at ~4 steps.
- **Pages keep failing QA**: set `qa.vision_mode: advisory`; small local vision models can be picky.
- **"GEMINI_API_KEY is missing"**: fill in `.env`, or switch to Draw Things.
- **Gemini "no image returned"**: usually a safety filter; reword the scene in `book.yaml`.
- **"STOPPED: ... cap"**: the budget guard worked. Check `status`, then raise the cap if intended.
- **`command not found: python`**: run `source .venv/bin/activate`.
- **Unknown command prints the help text**: check spelling, e.g. `test-image` with a dash.

## Costs

With Draw Things and Ollama, every book is free (electricity only). With Gemini
(September 2026 estimates): about $0.07 per image at 1K and $0.10 at 2K on
`gemini-3.1-flash-image`; a 40-page book uses ~43 images plus retries, roughly $5–15.
Every paid call is logged in `spend_ledger.json`. Update `prices_usd_per_image` in
`config.yaml` if pricing changes.

## Project layout

```
bookgen/
  __main__.py          CLI: all commands, doctor, test-image, approvals
  core.py              config, book folders, state, KDP math (spine, cover size, margins), fonts
  ai.py                Ollama / Gemini text+vision, Gemini and Draw Things images, budget ledger, fake mode
  imagecheck.py        pixel QA and B/W + 300 DPI + centering cleanup
  step1_theme.py       step 1
  step2_3_cover.py     steps 2 and 3
  step4_5_pages.py     steps 4 and 5
  step6_7_8_output.py  steps 7, 6, 8 and status
  batch.py             multi-book runs
scripts/mock_drawthings.py   fake Draw Things server for testing without the app
examples/halloween_ideas.txt sample ideas file for batch mode
CLAUDE.md                    instructions for an AI agent driving the pipeline
config.yaml                  all settings
```

Generated at runtime (git-ignored): `books/`, `output/`, `spend_ledger.json`, `batch_report.md`, `.env`.

## Testing without Draw Things

```bash
python scripts/mock_drawthings.py &      # serves placeholder images on port 7860
python -m bookgen test-image
kill %1
```

Combine with `BOOKGEN_FAKE=1` to also skip Ollama (fake mode skips the image engine too).

## Notes

- KDP requires disclosing AI-generated images when you publish.
- Use image models whose licenses allow commercial use.
- KDP allows spine text only at 79+ pages; at 82 pages the spine is ~0.18 in, so it's left blank.
