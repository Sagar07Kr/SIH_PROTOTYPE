# LayoutLoom

Translate a PDF into another language and get back a PDF that is visually
indistinguishable from the original **except for the words**.

The hard problem is not translation. It is putting translated text of a
different length, script and writing direction back into the exact space the
original text occupied, without touching anything else on the page — and
reporting honestly wherever that could not be done.

```
┌──────────┬────────────────────────────────┬──────────────────┐
│ Pages    │  Comparison viewer             │  Inspector       │
│ thumbs   │  [Side-by-side│Overlay│Text]   │  scores/issues/  │
│ + issue  │  synced scroll & zoom          │  selection/audit │
│ badges   │                                │                  │
└──────────┴────────────────────────────────┴──────────────────┘
```

## Quick start

Nothing below needs an API key or network access.

```bash
make install     # python deps + npm deps
make fonts       # vendor the Noto faces, then verify every script renders
make samples     # generate the four bundled sample PDFs
make dev         # api on :8000, web on :3000
```

Then open <http://localhost:3000>, click **TRY DEMO** on a sample, and watch the
stage machine. Or skip the browser:

```bash
make demo        # all four samples end to end, with measured scores
make test        # full suite, offline, no key
```

With Docker:

```bash
docker compose up --build      # web on :3000, api on :8000
```

## Measured output

`make demo` on this checkout (mock provider, no network):

| Sample | Target | Masked SSIM | Coverage | Overflow | Overlaps | Geometry | Layout score |
|---|---|---|---|---|---|---|---|
| govt-notice (2pp, Hindi, vector seal) | en | 1.000 | 1.00 | 2 | 0 | ✓ | 89.1 |
| research-paper (6pp, 2-col, table) | hi | 1.000 | 1.00 | 0 | 0 | ✓ | 98.6 |
| technical-report (4pp, German, 3 tables) | en | 1.000 | 1.00 | 0 | 0 | ✓ | 98.2 |
| scanned-invoice (1pp, 200 DPI scan) | de | 1.000 | 1.00 | 5 | 0 | ✓ | 92.6 |

Masked SSIM 1.000 means the artwork, ruling lines and background of every page
are pixel-identical to the source: redaction removed text and nothing else.
Overflow counts are blocks that could not fit at the floor size and are
reported rather than clipped. Every number here is computed from pixels and
counters — see [docs/LAYOUT_ENGINE.md §6](docs/LAYOUT_ENGINE.md).

## How it works, briefly

1. **Extract** with `get_text("rawdict")`, keeping per-span font, size, flags,
   colour and baseline. Group spans into lines, lines into blocks, blocks into
   columns, columns into reading order.
2. **Read scans** at the raster's native resolution: deskew, Tesseract with
   per-word confidence, then the same layout pipeline as digital text.
3. **Protect** numbers, URLs, code, citations and glossary terms behind `⟦Pn⟧`
   placeholders before any model sees the text, and verify the round trip after.
4. **Translate** paragraph-by-paragraph with neighbouring units as context and
   recurring terms locked.
5. **Erase** only the text layer — `PDF_REDACT_IMAGE_NONE`,
   `PDF_REDACT_LINE_ART_NONE` — then **place** the translation back into the
   same rectangle, walking a seven-rung ladder of typographic concessions and
   stopping at the first rung that measurably fits.
6. **Measure** the result against the original: masked SSIM, coverage, overflow,
   adjustment budget, overlap sweep, geometry, ink delta.

The interesting details are in [docs/LAYOUT_ENGINE.md](docs/LAYOUT_ENGINE.md);
what does not work is in [docs/LIMITATIONS.md](docs/LIMITATIONS.md), and it is
worth reading before trusting any of this.

## Invariants

These are correctness conditions, asserted in code:

| | | Where |
|---|---|---|
| I1 | output page count == input | `Rebuilder._assert_geometry` (raises) |
| I2 | per-page dimensions == input | `Rebuilder._assert_geometry` (raises) |
| I3 | images and vectors untouched | redaction flags + masked SSIM ≥ 0.98 |
| I4 | nothing clipped or invisible | writer return value; rung 6 records overflow |
| I5 | no placement overlap > 2% | rect trimming + per-page sweep |
| I6 | protected spans verbatim | placeholder verify/repair/fallback |
| I7 | demo works with no key, no network | `make test` and `make demo` |
| I8 | scores are measured, never estimated | every metric carries its derivation |

## Languages

English, German, French, Spanish, Hindi, Arabic, Japanese, Chinese
(Simplified) — as source or target, including the script changes that make it
interesting: Devanagari conjuncts, Arabic joining and bidi, CJK line breaking
without spaces.

## Configuration

Copy `.env.example` to `.env`. The default provider is `mock`, which is
deterministic, writes in the target script, and applies realistic length ratios
so the layout engine is genuinely exercised offline. For a real model:

```bash
AI_PROVIDER=openai
AI_API_KEY=sk-...
AI_BASE_URL=https://api.openai.com/v1   # or any OpenAI-compatible endpoint
AI_MODEL=gpt-4o-mini
```

## Security posture

Prototype architecture designed for future enterprise security controls:
AI calls are server-side only, secrets come from the environment, uploads are
validated by magic bytes and capped by size and page count, temporary files live
under `var/`, and every state change is written to an append-only audit log.
Authentication is an unimplemented interface, not a feature. **No claim is made
about handling classified or regulated data.**

## Licensing note

PyMuPDF is AGPL-3.0. It is the only library that can erase a PDF's text layer in
place while leaving images and vector art untouched, which is the core of this
prototype. Any commercial deployment needs an Artifex commercial licence or an
AGPL-compatible distribution model. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Repository

```
frontend/  app · components · hooks · lib · types
backend/   api · services · models · schemas · providers · parsers · ocr
           translation · reconstruction · validators · fonts · utils · assets
sample-data/  four generated PDFs + .expected.json fixtures
scripts/   fetch_fonts · font_smoke_test · make_samples · demo · update_golden
tests/     unit · integration (incl. the golden layout test) · fixtures
docs/      ARCHITECTURE · API · LAYOUT_ENGINE · LIMITATIONS
```
# Prototype_SIH
