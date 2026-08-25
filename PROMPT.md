# Build Prompt — LayoutLoom: AI PDF Translation with Layout Preservation

## 0. Role & Mandate

You are the sole engineer on this project: document-processing engineer, full-stack architect, and UX designer.

Build a **runnable full-stack monorepo** that takes a PDF in one language and emits a new PDF in another language that is **visually indistinguishable from the original except for the words**.

The hard problem is not translation. It is **putting translated text of a different length, script, and writing direction back into the exact space the original text occupied, without touching anything else on the page.** Every decision below serves that.

**Work autonomously.** Do not ask for confirmation between phases. Do not stop to summarize progress. Run the gate tests, fix what fails, continue. Report once at the end.

---

## 1. Non-Negotiable Invariants

These are correctness conditions, not preferences. A build that violates any of them is a failed build.

| # | Invariant | How it is enforced |
|---|---|---|
| I1 | Output page count == input page count | Assertion in exporter; hard fail |
| I2 | Output page dimensions (pts) == input, per page | Assertion in exporter; hard fail |
| I3 | Raster images, vector graphics, and drawings are byte-identical in position and content | Redaction must use `PDF_REDACT_IMAGE_NONE`; verified by masked SSIM ≥ 0.98 |
| I4 | No translated text is clipped or invisible | `insert_textbox` return value ≥ 0 for every placement, or the block is recorded as an unresolved overflow issue |
| I5 | No two placed text boxes overlap by > 2% of the smaller box's area | Post-placement bbox intersection sweep |
| I6 | Protected spans (numbers, URLs, emails, code, proper nouns, formulas) appear verbatim in output | Placeholder round-trip assertion per segment |
| I7 | Demo mode works with zero API keys and zero network access | CI runs the full E2E test with `AI_PROVIDER=mock` and no egress |
| I8 | Quality scores are computed from measurements, never hardcoded or estimated | Every score traces to a counter or pixel metric in `ValidationResult` |

**I8 is the one most likely to be quietly violated. Do not fake the numbers.** A validation panel showing `Layout Preservation: 97%` that is not derived from actual pixel and geometry comparison is worse than showing nothing.

---

## 2. Anti-Goals — Do Not Build

Every hour here is stolen from layout fidelity.

- Chatbot, conversational UI, or any chat affordance
- Authentication flows, RBAC, user management, billing, teams
- Summarization, content generation, rewriting, "improve my document"
- Analytics dashboards, usage charts, admin panels
- Microservices, message brokers, Kubernetes manifests, Terraform
- DOCX / PPTX / image input (leave interface seams only — see §11)
- Cloud storage backends (leave a `StorageBackend` interface with one local implementation)
- Model fine-tuning or embeddings
- Gradients, glow effects, robot mascots, animated AI orbs, marketing hero sections

If a requirement in this document conflicts with an invariant in §1, the invariant wins. If you find yourself building something in this list, stop and return to §1.

---

## 3. Stack

```
frontend/   Next.js 14 (App Router) · React 18 · TypeScript (strict) · Tailwind · shadcn/ui · pdf.js
backend/    Python 3.11 · FastAPI · PyMuPDF (fitz) · Tesseract (pytesseract) · Pillow · scikit-image
db/         SQLAlchemy 2.0 · SQLite by default · PostgreSQL via DATABASE_URL
jobs/       In-process asyncio worker + polling endpoint (no Celery, no Redis)
```

PyMuPDF is mandatory, not a suggestion. It is the only library in this space that can erase text in place while leaving images and vectors untouched (`add_redact_annot` + `apply_redactions`) and then write shaped, wrapped, right-to-left text back into an exact rectangle (`insert_textbox` / `insert_htmlbox`). Do not substitute pdfplumber, PyPDF2, or reportlab for the reconstruction path.

---

## 4. The Reconstruction Engine — Specified Precisely

This is the core of the deliverable. Implement it exactly.

### 4.1 Extraction

Use `page.get_text("rawdict")`. Preserve at the **span** level: `bbox`, `font`, `size`, `flags` (bold/italic bits), `color`, `ascender`, `descender`, `origin`, `dir`.

Build the hierarchy bottom-up:

```
span → line → block → column → reading order
```

- **Columns**: project span x-intervals onto the x-axis, find gutters as x-ranges with zero coverage spanning > 60% of the page's text height. Assign blocks to columns; order columns left-to-right (or right-to-left for RTL source).
- **Reading order**: within a column, top-to-bottom by `bbox.y0`, ties broken by `bbox.x0` (reversed for RTL).
- **Headers / footers**: a block whose y-center is in the top or bottom 8% of the page **and** whose normalized text (digits → `#`) recurs on ≥ 60% of pages. Page numbers are the digit-only case — translate the digits into target-locale numerals only if the target locale uses different numerals, otherwise leave verbatim.
- **Headings**: font size ≥ 1.15× the page's modal body size, OR bold with ≤ 2 lines and trailing whitespace below > 1.5× line height.
- **Lists**: line begins with a bullet glyph (`•◦▪-–*`) or an ordinal pattern (`1.` `a)` `i.` `(1)`). Capture the marker separately; **never send the marker to the translator**.
- **Captions**: text block within 20pt of an image bbox, font size < modal body size.
- **Tables**: see §4.5.

### 4.2 Fonts — Solve This First

This blocks everything else. A Devanagari or Arabic glyph rendered in Helvetica is a row of empty boxes, and no amount of layout logic saves it.

Vendor the fonts. `scripts/fetch_fonts.py` downloads to `backend/assets/fonts/` (OFL-1.1, attribution in `THIRD_PARTY_NOTICES.md`):

| Script | Family | Weights |
|---|---|---|
| Latin, Cyrillic, Greek | Noto Sans | 400, 700 (+ italics) |
| Devanagari (hi) | Noto Sans Devanagari | 400, 700 |
| Arabic (ar) | Noto Naskh Arabic | 400, 700 |
| Japanese (ja) | Noto Sans JP | 400, 700 |
| Simplified Chinese (zh) | Noto Sans SC | 400, 700 |

Also vendor Noto Serif equivalents where they exist, so a serif source maps to a serif target.

**Substitution policy** — `FontResolver.resolve(original_font, target_lang) -> ResolvedFont`:

1. Parse the original PostScript name for family, weight, slant, and serif/sans classification.
2. Select the target-script family with matching serif class.
3. Map weight: `< 550 → 400`, `≥ 550 → 700`. Never synthesize a weight by stroking.
4. Compute an **x-height correction factor** = `original_x_height / substitute_x_height`, clamped to `[0.85, 1.15]`, and apply it to the point size so the substituted text has the same optical weight on the page.
5. Record every substitution in `ValidationResult.font_substitutions` with the original and replacement names. Surface these in the UI. Substitution is a known, disclosed compromise — never a silent one.

### 4.3 Text Replacement — The Core Loop

For each text block, in reading order:

```python
# 1. ERASE — text only, graphics untouched
page.add_redact_annot(block.bbox)  # no fill, no text
page.apply_redactions(
    images=fitz.PDF_REDACT_IMAGE_NONE,      # I3: never touch rasters
    graphics=fitz.PDF_REDACT_LINE_ART_NONE, # I3: never touch vectors
    text=fitz.PDF_REDACT_TEXT_REMOVE,
)

# 2. PLACE — shrink-to-fit ladder, first rung that returns >= 0 wins
for attempt in fit_ladder(block):
    leftover = page.insert_textbox(
        attempt.rect, attempt.text,
        fontname=attempt.font, fontsize=attempt.size,
        lineheight=attempt.leading, align=attempt.align,
        color=block.style.color, rotate=block.rotation,
    )
    if leftover >= 0:
        record(attempt); break
else:
    record_overflow_issue(block)  # I4: flag, never clip
```

`insert_textbox` returns the unused vertical space when the text fits and a **negative** number when it does not. This return value is the overflow oracle — do not estimate fit from character counts.

**The fit ladder, in strict order.** Each rung is a larger visual concession than the last; stop at the first that fits.

| Rung | Change | Bound |
|---|---|---|
| 0 | Original size, original leading | — |
| 1 | Tighten leading | to 1.00× font size, floor 0.95 |
| 2 | Tighten tracking | to −2% em |
| 3 | Reduce font size | 2% steps, floor **0.82×** original |
| 4 | Grow the box downward | only into whitespace verified empty by a bbox sweep against all other elements on the page, max +25% of original height |
| 5 | Grow the box into the outer margin | horizontally, max 50% of the margin width, only if the block is not part of a column or table |
| 6 | Give up | record an `OVERFLOW` issue with the measured excess in points |

Rungs 4 and 5 must **re-run the overlap sweep (I5)** after growing. A grown box that collides reverts and falls through to rung 6.

Never go below 0.82× — smaller than that reads as a bug to a human reviewer, and an honest warning beats an illegible page.

### 4.4 Bidi, Shaping, and Line Breaking

Their absence is the most common way a project like this ships broken output for half its advertised languages.

- **Arabic (RTL)**: shape with `arabic_reshaper`, then apply the Unicode bidi algorithm with `python-bidi`. **Flip block alignment**: source `LEFT` becomes target `RIGHT`; `JUSTIFY` stays. Column order reverses. Numbers inside RTL text stay LTR runs — the bidi algorithm handles this; do not hand-roll it.
- **CJK (ja, zh)**: no spaces to break on. Implement a UAX #14 approximation: break between any two Han/Kana characters **except** before closing punctuation `）」』、。，．！？` and except after opening punctuation `（「『`. Never break inside a Latin word embedded in CJK text.
- **Devanagari (hi)**: do not break inside a grapheme cluster. Segment with `regex`'s `\X` (extended grapheme cluster), never by code point. Devanagari ascenders exceed Latin ones — add 8% to line height for Devanagari targets.
- **Prefer `insert_htmlbox`** over `insert_textbox` for RTL and complex-script blocks: it routes through HarfBuzz and handles shaping and `dir="rtl"` correctly. Keep `insert_textbox` for simple LTR blocks where it is faster and more predictable.

### 4.5 Tables

Detect via ruling lines (`page.get_drawings()`, filter to axis-aligned strokes) with a whitespace-alignment fallback for borderless tables (≥ 3 rows sharing ≥ 2 consistent x-boundaries).

Then: **translate cell by cell, and never move a ruling line.** Column widths are fixed. The fit ladder runs inside each cell with the font-size floor raised to **0.75×** — cells are tighter than prose and readers tolerate smaller table type. Row height may grow, which pushes subsequent rows and the table's lower boundary down; if the table would then exceed the page, split it at a row boundary and repeat the header row, matching the original header styling.

Preserve merged cells, cell background fills, and per-cell alignment. Numeric cells stay right-aligned regardless of target language.

### 4.6 OCR Fallback

Trigger per page, not per document: if a page has < 20 extractable characters but has raster content covering > 40% of its area, it is scanned.

```
render page @ 300 DPI → deskew (Hough) → binarize (Sauvola)
→ tesseract with the source language pack, PSM 3, TSV output
→ word boxes → line grouping → block grouping → same pipeline as §4.1
```

For scanned pages the reconstruction differs: there is no text layer to redact. Instead, **inpaint** — fill each detected text region with the locally sampled background color (median of a 3px ring outside the text bbox), then draw the translated text on top. Estimate the original font size from the text region's height. Keep the page's raster background intact everywhere else.

Store per-word Tesseract confidence. Any block whose mean confidence < 75% is flagged `LOW_OCR_CONFIDENCE`, rendered with a warning outline in the UI, and listed in the review queue. OCR errors are the largest error source in this pipeline — make them visible rather than laundering them through a confident-looking translation.

---

## 5. Translation Layer

### 5.1 Protection Before Translation

Mask protected content into placeholder tokens **before** the text reaches any model, restore after:

```
PROTECT_PATTERNS = [URL, EMAIL, IPV4, FILE_PATH, ISO_DATE, LOCALE_DATE,
                    NUMBER_WITH_UNIT, PERCENTAGE, CURRENCY, CITATION,
                    INLINE_CODE, LATEX_MATH, ACRONYM_3PLUS_CAPS,
                    GLOSSARY_LOCKED_TERM]
```

Placeholder format: `⟦P{n}⟧` — chosen because no natural language contains these brackets, so no model will translate or reorder them away.

After translation, assert every placeholder issued is present exactly once. On failure, retry the segment once with an explicit repair instruction; on second failure, fall back to the source text for that segment and record a `PROTECTION_FAILURE` issue. Never ship a segment with a mangled URL or an altered number.

### 5.2 Segmentation & Context

Translation unit = one paragraph (or one list item, or one table cell). Never a raw line — line breaks are layout artifacts and translating across them destroys meaning.

Each request carries: the preceding and following unit as read-only context, the document's detected domain, the running glossary, and the target style. Context is for coherence only — the model returns **only** the target unit.

### 5.3 Terminology Consistency

Build a `TermMemory` per document. First translation of a term ≥ 2 words that recurs ≥ 3 times in the document is recorded and injected as a locked glossary entry for all subsequent segments. This is what stops "Artificial Intelligence" from becoming three different Hindi phrases across 38 pages.

User glossary entries always override. A glossary entry may map a term to itself (`API → API`), which promotes it to a protected span.

### 5.4 Provider Abstraction

```python
class AIProvider(Protocol):
    async def detect_language(self, req: DetectRequest) -> DetectResponse: ...
    async def translate(self, req: TranslateRequest) -> TranslateResponse: ...
    async def review(self, req: ReviewRequest) -> ReviewResponse: ...
```

Two implementations: `MockProvider` and `OpenAIProvider` (also usable against any OpenAI-compatible endpoint via `AI_BASE_URL`). Provider code touches nothing outside `backend/providers/`. All responses are Pydantic-validated; a malformed response is a typed `ProviderResponseError`, retried once with the schema restated, then surfaced as a per-segment failure — never a crashed job.

### 5.5 MockProvider Must Stress the Layout Engine

**This is the single most important detail in the mock, and the easiest to get wrong.** A mock that returns same-length placeholder text means the fit ladder, the overflow detector, and the whole validation layer are never exercised — and the prototype will look perfect right up until someone uses a real model.

`MockProvider` therefore:

- Is **deterministic**: seeded by `hash(segment_id + target_lang)`. Same input, same output, always — tests depend on this.
- Produces text in the **target script**, generated by transliterating source syllables into plausible target-script glyph sequences. Output must be non-Latin for hi/ar/ja/zh so real shaping and line-breaking paths execute.
- Applies **empirically realistic length ratios** with variance, so some blocks overflow and some underflow:

| Target | Mean char ratio | σ |
|---|---|---|
| German | 1.32 | 0.15 |
| Hindi | 1.18 | 0.12 |
| French | 1.22 | 0.12 |
| Spanish | 1.20 | 0.12 |
| Arabic | 1.05 | 0.14 |
| English | 1.00 | 0.10 |
| Chinese | 0.62 | 0.10 |
| Japanese | 0.68 | 0.10 |

- Preserves all `⟦P{n}⟧` placeholders exactly.
- Returns confidences drawn from a seeded beta distribution centered at 0.93, with ~4% of segments below 0.80 so the low-confidence review UI has something to show.
- Simulates latency: `40ms + 8ms × word_count`, so the progress UI is honest in demo mode.

At least one bundled sample PDF must, in demo mode with target German, produce **two genuine rung-3 font reductions and one genuine rung-6 overflow warning**. Those are the demo's proof that the layout engine is real.

---

## 6. Validation — Measured, Not Asserted

`POST /api/versions/:id/validate` renders both PDFs to PNG at 150 DPI and computes:

| Metric | Method |
|---|---|
| **Graphics fidelity** | Mask out all text bboxes in both renders; SSIM on the remainder. Target ≥ 0.98. Catches displaced images and clobbered vectors (I3). |
| **Text coverage** | `translated_segments / translatable_segments`. Must be 1.00. Anything less means content was dropped. |
| **Overflow count** | Blocks that reached rung 6. |
| **Adjustment budget** | Σ of per-block visual concession, weighted by rung (0 pt, then 1/2/3/5/8/13). Lower is better. Reported as a raw number, not a percentage. |
| **Overlap violations** | Bbox intersection sweep, > 2% of smaller box's area (I5). |
| **Geometry integrity** | Page count and per-page dimensions equal (I1, I2). Boolean. |
| **Font substitutions** | Count + the full list. |
| **Whitespace delta** | Ink coverage ratio per page, original vs translated. Flag pages differing by > 15%. |
| **Translation confidence** | Mean segment confidence, weighted by character count. |

Composite scores are pure functions of these numbers, and the UI shows the derivation on hover. `Layout Preservation: 97%` must be clickable down to the metrics that produced 97%. Hardcoding this number, or computing it from anything other than real measurements, defeats the entire point of the prototype.

Issue severities: `ERROR` (invariant violated), `WARNING` (rung ≥ 4, low OCR confidence, font substitution in a heading), `INFO` (rungs 1–3).

---

## 7. Data Model

```
Project(id, name, created_at)
Document(id, project_id, filename, sha256, page_count, source_lang, is_scanned, status)
DocumentPage(id, document_id, index, width_pt, height_pt, rotation, is_scanned, render_path)
DocumentElement(id, page_id, type, reading_order, bbox, text, style_json,
                column_index, parent_id, ocr_confidence, is_protected)
Glossary(id, project_id, source_term, target_term, target_lang, locked)
TranslationJob(id, project_id, document_id, target_lang, style, options_json,
               status, progress_json, provider, started_at, finished_at,
               input_tokens, output_tokens, ocr_ms, translate_ms, reconstruct_ms)
TranslationVersion(id, job_id, number, parent_version_id, pdf_path, created_at)
TranslationSegment(id, version_id, element_id, source_text, translated_text,
                   confidence, fit_rung, applied_font, applied_size,
                   final_bbox, edited_by_user, placeholders_json)
ValidationResult(id, version_id, metrics_json, issues_json, computed_at)
AuditLog(id, project_id, event, payload_json, created_at)
```

`fit_rung` and `final_bbox` on every segment are what make the inspection panel and partial regeneration possible. Persist them.

Versions are **copy-on-write and immutable**. Regenerating one paragraph creates version N+1 that shares every unchanged segment row by reference and re-renders only the affected pages. Rollback is selecting an earlier version, never mutating a stored one.

---

## 8. API

```
POST   /api/documents                        multipart upload → document
POST   /api/documents/:id/analyze            → job (parse, OCR, layout, language detect)
GET    /api/documents/:id                    → document + pages + elements
GET    /api/documents/:id/pages/:n/render     → PNG (cached)

POST   /api/projects
POST   /api/projects/:id/translate            body: {target_lang, style, glossary, options}
                                              → {job_id}
GET    /api/jobs/:id                          → typed progress (see §9)
DELETE /api/jobs/:id                          cancel

GET    /api/projects/:id/versions
GET    /api/versions/:id                      → version + segments
GET    /api/versions/:id/pdf                  → translated PDF
GET    /api/versions/:id/diff/:other_id       → changed pages, blocks, layout deltas
POST   /api/versions/:id/validate             → ValidationResult
POST   /api/versions/:id/export               body: {format: pdf|txt|md|json}

PATCH  /api/segments/:id                      manual edit → new version
POST   /api/segments/:id/regenerate           → new version, that segment only
POST   /api/pages/:id/regenerate              → new version, that page only
```

Job progress is a typed stage machine, not a percentage:

```ts
type Stage = "UPLOADED" | "PARSING" | "OCR" | "LANG_DETECT" | "LAYOUT"
           | "SEGMENTING" | "TRANSLATING" | "RECONSTRUCTING"
           | "VALIDATING" | "GENERATING" | "DONE" | "FAILED";

interface JobProgress {
  stage: Stage;
  stages: { stage: Stage; status: "pending"|"active"|"done"|"skipped"|"failed";
            ms?: number }[];
  current_page?: number;
  total_pages: number;
  message?: string;          // "Translating page 12 / 38"
  error?: { code: string; message: string; retryable: boolean };
}
```

The frontend renders this state machine directly. **No generic spinner anywhere in the application.**

---

## 9. Frontend

**Two screens.** Resist adding a third.

### Dashboard
Drop zone → detected source language (with confidence, overridable) → target language with a swap control → collapsed settings (style, glossary, preservation toggles, all on by default) → Translate. Below: four bundled sample PDFs with one-click `TRY DEMO`.

### Workspace — three panels
```
┌──────────┬────────────────────────────────┬──────────────────┐
│ Pages    │  Comparison Viewer             │  Inspector       │
│ thumbs   │  [Side-by-side│Overlay│Text]   │  scores/issues/  │
│ + issue  │  synced scroll & zoom          │  selection/audit │
│ badges   │                                │                  │
└──────────┴────────────────────────────────┴──────────────────┘
```

- **Side-by-side**: locked scroll and zoom, both rendered via pdf.js.
- **Overlay**: translated over original with an opacity slider, plus a difference-blend toggle. This one view makes every layout shift obvious at a glance — it is the most persuasive thing in the demo. Build it well.
- **Text**: aligned source/target segment list, colored by fit rung and confidence.
- Clicking any block anywhere selects it everywhere: thumbnail badge, viewer highlight, inspector detail.

Inspector on selection shows source, target, page, bbox, confidence, fit rung with the concession made in plain words ("font reduced 6% to fit"), font substitution, protected spans, and Edit / Regenerate / Reset.

**Visual register**: a document workbench — dense, neutral, typographic. Light and dark themes, both explicit. Restrained motion. No gradients, no glow, no mascot, no marketing copy.

---

## 10. Sample PDFs

Generate with `scripts/make_samples.py` into `sample-data/`. Fictional content only — no real names, agencies, or logos.

1. `govt-notice.pdf` — 2pp, single column, letterhead, seal (vector), numbered clauses, signature block, footer page numbers. **Source: Hindi.**
2. `research-paper.pdf` — 6pp, two-column, abstract, embedded equations, a figure with caption, a 4×5 results table, footnotes, bracketed citations. **Source: English.**
3. `technical-report.pdf` — 4pp, mixed one/two column, three tables incl. merged cells, code block, bulleted and numbered lists, headers and footers. **Source: German** (long compounds stress-test contraction).
4. `scanned-invoice.pdf` — 1pp, image-only at 200 DPI with realistic skew and speckle, a bordered line-item table. **Forces the OCR path. Source: English.**

Each has a `.expected.json` recording page count, page sizes, element counts by type, and detected source language — the fixtures for the parsing tests.

---

## 11. Extension Seams (interfaces only, one implementation each)

`DocumentParser` (PDF impl; DOCX/PPTX/image are future subclasses) · `StorageBackend` (local FS impl) · `OCREngine` (Tesseract impl) · `AIProvider` (mock + OpenAI) · `FontResolver` (Noto impl).

Write the interface, one implementation, and a docstring naming the future implementations. **Do not write a second implementation of anything.**

---

## 12. Errors

Every failure returns `{code, message, detail?, retryable}`. Never a stack trace, never a raw exception string.

Handle explicitly: not-a-PDF, corrupt xref, password-protected (prompt for password, retry once), 0-page, > 200pp (refuse with a clear limit), > 50MB, no embedded font metrics, Tesseract missing or language pack absent, OCR returns nothing, provider timeout, provider rate limit (exponential backoff, 3 attempts), malformed provider JSON, placeholder round-trip failure, unresolvable overflow, page render OOM.

Partial success is a first-class outcome: a document where 3 of 40 pages failed produces a valid PDF with those pages left in the source language, clearly marked in the UI and in the validation report. Silent partial failure is the worst possible behavior here — the user must be able to see exactly which pages are untranslated.

---

## 13. Tests

**Unit** — parsing against `.expected.json` fixtures · column detection on the two-column sample · header/footer recurrence · list-marker exclusion · language detection (all 8) · segmentation · placeholder mask/restore round-trip incl. adversarial input · glossary and TermMemory precedence · font resolution and x-height correction · fit-ladder monotonicity (each rung is a weaker concession than the next) · CJK break rules · Arabic bidi + shaping · Devanagari grapheme integrity · overlap sweep · table structure preservation · geometry invariants I1/I2 · mock determinism (identical bytes across two runs).

**Integration** — one test per sample PDF: `upload → analyze → translate → reconstruct → validate → export`, asserting I1–I8 on the output. The scanned invoice must exercise the OCR branch. The German report must produce at least one rung-3 reduction.

**Golden layout test** — commit a reference render of `research-paper.pdf` translated to Hindi in mock mode. Fail if masked SSIM against it drops below 0.995. This is the regression net for the reconstruction engine; without it, refactors silently degrade layout fidelity.

`make test` runs everything offline, no keys, no network.

---

## 14. Repository

```
frontend/{app,components,hooks,lib,types}
backend/
  api/ services/ models/ schemas/ providers/
  parsers/ ocr/ translation/ reconstruction/ validators/ fonts/ utils/
  assets/fonts/
sample-data/  scripts/  tests/{unit,integration,fixtures}
docs/{ARCHITECTURE.md,API.md,LAYOUT_ENGINE.md,LIMITATIONS.md}
docker-compose.yml  Dockerfile.{frontend,backend}  Makefile
.env.example  README.md  THIRD_PARTY_NOTICES.md  LICENSE
```

`docs/LAYOUT_ENGINE.md` documents the fit ladder, the font substitution table, the bidi and line-breaking rules, and the validation metric definitions. This is the document that proves the system understands documents rather than merely calling a translation API.

`docs/LIMITATIONS.md` states plainly what does not work: font substitution is visible on branded documents, OCR accuracy caps scanned-page quality, heavily designed layouts (magazines, infographics) will show reflow, ligature-heavy scripts may break at extreme size reductions. **Write this file honestly.** An engineering reviewer trusts a prototype that names its own failure modes far more than one that claims none.

Security posture in the README: server-side AI calls only, env-based secrets, MIME + magic-byte validation, size caps, temp-file cleanup on job completion, audit logging, auth as an unimplemented interface. Describe it as *"prototype architecture designed for future enterprise security controls."* Make no claim about handling classified or regulated data.

---

## 15. Build Order — Gates Are Mandatory

Do not begin a phase until the previous gate passes. The gates exist because every later phase depends on the earlier one being correct, and layout bugs are nearly impossible to diagnose once stacked.

| Phase | Work | Gate |
|---|---|---|
| **P0** | Repo, Docker, DB, `make dev` runs both services | Health checks green |
| **P1** | Font vendoring + `FontResolver` | Script renders "नमस्ते / مرحبا / こんにちは / 你好 / Größe" correctly to a test PDF. **Nothing proceeds until this is visually verified.** |
| **P2** | Parser: spans → blocks, columns, classification, `.expected.json` fixtures | Unit tests pass on samples 1–3 |
| **P3** | Redact + re-place round-trip, **identity translation** (target == source) | Output is pixel-near-identical to input: masked SSIM ≥ 0.995, I1–I3 hold. **This gate is the whole project in miniature — if identity round-trip is not near-perfect, translation will never be.** |
| **P4** | MockProvider with realistic length ratios; fit ladder; overflow detection | German target produces ≥ 1 rung-3 and ≥ 1 rung-6 event; zero clipping |
| **P5** | Bidi + CJK breaking + Devanagari clusters | Arabic and Japanese outputs render correctly; alignment flipped for RTL |
| **P6** | Tables | Sample 3's tables keep every ruling line and column width |
| **P7** | OCR path + inpainting | Sample 4 round-trips; confidences persisted and surfaced |
| **P8** | Validation engine | All §6 metrics computed from real measurements; no constants |
| **P9** | Frontend: dashboard, side-by-side, thumbnails, progress state machine | Full flow clickable end to end |
| **P10** | Overlay + text comparison + inspector | Opacity and difference blend working |
| **P11** | Partial regeneration, versions, diff, rollback | Regenerating one paragraph re-renders one page, not the document |
| **P12** | Glossary, styles, export formats, audit timeline, observability panel | — |
| **P13** | Golden layout test, E2E suite, docs, `LIMITATIONS.md` | `make test` green offline |

If time runs short, **stop after P8 and ship a thin frontend**. A working reconstruction engine with a plain UI is a real prototype. A beautiful UI over a fake engine is a mockup, and any reviewer who opens the output PDF will know within seconds.

---

## 16. Acceptance

With no API key set, a reviewer can: open the app · click `TRY DEMO` on the scanned invoice · watch real per-stage progress · get a translated PDF whose table, borders, and background survived · open the same for the two-column research paper into Hindi · drag the overlay slider and see near-perfect registration of images and rules · click a block flagged with a font reduction and read exactly what was changed and why · edit that translation and regenerate only it · watch the version counter increment and only one page re-render · read a validation panel whose every number traces to a measurement · download a PDF that opens in Preview and Acrobat with correct page count and page size.

Then they run `make test` on a laptop with Wi-Fi off, and it passes.

**One-line summary of intent:** this is not a text translator with a PDF wrapper — it is a document reconstruction system whose translated output preserves the original's design, geometry, and typographic identity, and which reports honestly wherever it could not.

---

## 17. Deliverable

The complete monorepo, runnable via `docker compose up` and `make dev`, with `.env.example`, all four sample PDFs, both providers, full tests, and the four docs.

**Final self-check before you report done** — run this and confirm each line:

```
□ make test          passes, network disabled, no API key
□ make demo          all 4 samples: upload → export, no errors
□ I1–I8              asserted in code and green
□ Every score        traceable to a measurement (grep for hardcoded percentages)
□ Hindi/Arabic/JP/ZH outputs open and render real glyphs, not tofu boxes
□ Overlay slider     images and ruling lines register within 1pt
□ LIMITATIONS.md     written honestly, names real failure modes
□ Zero generic spinners; zero chat UI; zero items from §2
```

Report at the end: what works, what does not, which invariants hold, and the measured scores for all four samples. Be specific about shortfalls — an accurate account of a partially complete system is more useful than an optimistic one.
