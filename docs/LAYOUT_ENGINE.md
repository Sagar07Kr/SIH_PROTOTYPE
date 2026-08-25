# The layout engine

This is the document that explains why LayoutLoom is a document reconstruction
system rather than a translation API with a PDF wrapper. Everything here is
implemented; where a rule is an approximation, it says so.

---

## 1. The actual problem

Translation is the easy half. The hard half is putting text of a different
length, script and writing direction back into the exact space the original
occupied, without touching anything else on the page.

A German target is about 32% longer than its English source. A Chinese target
is about 38% shorter. Devanagari needs more line height than Latin at the same
point size. Arabic runs right-to-left and joins its letters. None of that is
negotiable by the translator, so all of it has to be absorbed by the typography
— visibly, in a way a reviewer can inspect and argue with.

---

## 2. Extraction: span → line → block → column → reading order

`page.get_text("rawdict")` is the only extraction mode that keeps what matters:
per-span `font`, `size`, `flags`, `color`, `ascender`, `descender` and `origin`.
The baseline in `origin` is what makes registration possible; a plain-text
extraction discards it and the reconstruction is then guesswork.

**Blocks.** Lines join into a block when they are vertically adjacent (gap under
1.7 line heights), horizontally overlapping (>35% of the shorter line), and
typographically similar (size ratio ≤ 1.12, same weight, same monospacing). Two
refinements earn their keep:

- Several streams stay open at once, and each line joins whichever stream it
  best continues. A strictly sequential grouper breaks every paragraph into
  single lines wherever a page is *locally* two-column — a band of two columns
  inside otherwise single-column material, which is extremely common.
- A finished sentence on a short final line, followed by extra leading, ends the
  block. Without it, consecutive paragraphs merge into one translation unit.

**Columns.** Span x-intervals are projected onto the x-axis; a candidate gutter
is a clear corridor at least 7pt wide. Three tests must all pass:

1. *clear* — almost no ink at any x inside it (≤8% of the text height, which
   tolerates one centred author line crossing it);
2. *rarely crossed* — full-width elements may cut across, but over no more than
   40% of the text height;
3. *parallel* — there is text (or artwork) on **both** sides at the same
   heights over at least 40% of the page.

Test 3 is the one that matters. A table's internal whitespace is clear and
uncrossed, and without the parallelism test every table turns its page into a
multi-column layout. Graphics count as occupancy, or a page whose left column
holds a large figure reads as single-column.

**Reading order.** Full-width blocks cut the page into horizontal bands. Inside
a band, columns are read in order (reversed for RTL sources) and blocks
top-to-bottom, ties broken by x0. This is what keeps a two-column paper with a
spanning title and a spanning table from being read as one scrambled stream.

**Classification.** Header/footer requires *both* the top/bottom 8% band and
digit-normalised text recurring on ≥60% of pages — digit **runs** collapse to a
single `#`, so "Page 3 / 12" and "Page 11 / 12" are one key. Headings are
≥1.15× the modal body size, or bold with ≤2 lines and extra trailing space, or
bold, short and centred. Lists capture their marker separately and never send it
to the translator; a marker beside a line also starts a new block, because
markers are drawn as separate text objects interleaved with the body lines they
belong to. Captions are small text within 20pt of a figure or table. Code is
detected from the monospace flag and placed verbatim.

---

## 3. Fonts: solve this first

A Devanagari or Arabic glyph rendered in Helvetica is a row of empty boxes, and
no amount of layout logic saves it. `scripts/fetch_fonts.py` vendors the Noto
set before anything else runs, and `scripts/font_smoke_test.py` is the gate:
it renders every target script through both writers and fails on any missing
glyph.

| Script | Family (sans / serif) | Weights |
|---|---|---|
| Latin, Cyrillic, Greek | Noto Sans / Noto Serif | 400, 700 + italics |
| Devanagari (hi) | Noto Sans Devanagari / Noto Serif Devanagari | 400, 700 |
| Arabic (ar) | Noto Naskh Arabic | 400, 700 |
| Japanese (ja) | Noto Sans JP / Noto Serif JP | 400, 700 |
| Chinese (zh) | Noto Sans SC / Noto Serif SC | 400, 700 |

### Substitution policy

1. Parse the original PostScript name for family, weight, slant and serif class
   (`ABCDEF+NotoSans-BoldItalic`, `TimesNewRomanPS-BoldMT`, `Arial,Bold` and
   `Helvetica-Oblique` all parse correctly; foundry suffixes are stripped).
2. Choose the target-script family with the matching serif class.
3. Map weight: `<550 → 400`, `≥550 → 700`. Weight is never synthesised by
   stroking.
4. Correct the point size by `original_x_height / substitute_x_height`, clamped
   to `[0.85, 1.15]`, so the substituted text carries the same optical weight.
   x-height comes from `OS/2.sxHeight`, or the outline of `x`, or a table of
   the PDF standard-14 metrics for fonts with none.
5. Record the substitution. Every one appears in the validation report and in
   the inspector, with the reason and the correction factor. Substitution is a
   disclosed compromise, never a silent one.

**Glyph coverage.** Before placing, the text is checked against the chosen
face. Where a fallback chain can rescue a missing character the block is routed
through `insert_htmlbox`, which honours one; where it cannot — CJK faces are
17–25MB and MuPDF re-embeds the referenced face on every HTML layout pass — the
block is placed anyway and the specific characters are reported as
`MISSING_GLYPHS`. On the bundled samples this affects exactly one thing: `₹` is
absent from Noto Naskh Arabic and Noto Sans JP.

---

## 4. Reconstruction

Two passes per page, in this order, because they cannot be interleaved:

```python
# 1. ERASE — text only; images and vectors explicitly protected (I3)
page.add_redact_annot(block.bbox)          # for every block, then once:
page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                      graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                      text=fitz.PDF_REDACT_TEXT_REMOVE)

# 2. PLACE — first rung of the ladder that measurably fits wins
for attempt in fit_ladder(block):
    leftover = writer(attempt)             # >= 0 fits, < 0 does not
    if leftover >= 0:
        record(attempt); break
else:
    record_overflow(block)                 # flagged, never clipped (I4)
```

Placing before erasing deletes the text just written; erasing block by block
re-renders the page for every block.

### Vertical registration

The rectangle handed to the writer is derived from **baselines**, not box tops:

```
top    = first_baseline − ascender × size
bottom = max(bbox.y1, last_baseline − descender × size) + 1.2
```

The original span bbox is a glyph bbox; `insert_textbox` measures from the
font's ascender. Matching those two directly is what keeps an identity
round-trip at masked SSIM 1.000 instead of drifting a line down the page. The
1.2pt of slack exists because a block whose original leading equals the
writer's line advance has exactly zero spare height, and floating-point noise
alone would push it onto rung 1 for no visible reason.

### Overflow measurement

`insert_textbox` returns the unused vertical space when the text fits and a
negative number when it does not. That return value is the only overflow oracle
in the codebase — fit is never estimated from character counts.

Measurement runs the *same* writer on a scratch page of identical geometry.
Writing invisibly onto the real page would also answer the question and would
leave an invisible text layer behind, corrupting every later text-coverage
measurement.

### The fit ladder

Each rung is a larger visual concession than the last. The first that fits wins;
concessions are cumulative.

| Rung | Change | Bound | Weight |
|---|---|---|---|
| 0 | original size and leading | — | 0 |
| 1 | tighten leading | to 1.00×, floor 0.95 | 1 |
| 2 | tighten tracking | −2% em (CSS; skipped for CJK) | 2 |
| 3 | reduce font size | 2% steps, floor **0.82×** (**0.75×** in table cells) | 3 |
| 4 | grow the box downward | into verified-empty space, max +25% height | 5 |
| 5 | grow into the outer margin | max 50% of the margin width, not in columns or tables | 8 |
| 6 | give up | record `OVERFLOW` with the measured excess in points | 13 |

Rungs 4 and 5 re-run the overlap sweep; a grown box that collides reverts and
falls through. Nothing goes below 0.82× (0.75× in cells) — smaller reads as a
bug to a human reviewer, and an honest warning beats an illegible page.

The weights are Fibonacci-ish so that a page of small reductions cannot look
cheaper than a single overflow. They are the adjustment budget.

### Keeping placements apart (I5)

Before the ladder runs, the rectangle is trimmed off its neighbours — bottom and
right edges only, since the top edge carries baseline registration. Blocks not
yet placed reserve their original boxes, so a block that grows into rung 4 or 5
cannot claim space a later block is about to use. On the four bundled samples
across six language pairs this brings overlap violations to zero; anything left
over is reported, never hidden.

### Tables

Detection is ranked by evidence strength, because weak evidence produces
spectacular false positives:

1. **Grid** — two or more vertical rules sharing a y-range, crossed by two or
   more horizontal rules, with at least one *interior* vertical. The interior
   requirement is what separates a table from a stroked panel around a code
   block, which is also two verticals crossed by two horizontals.
2. **Rules only** — three or more horizontal rules sharing an x-extent, with
   column boundaries recovered from whitespace. This is the classic three-rule
   table with no verticals at all.
3. **Alignment only** — at least three consecutive rows sharing at least **two**
   interior x-boundaries. Two, not one: a single shared boundary is what a
   two-column page looks like. Boundaries are clustered across rows with
   support counting rather than matched pairwise, which is what recovers all
   five columns of a scanned invoice where pairwise matching found two.

Cells are filled at **span** level, not line level: one text line routinely
crosses several cells, always in OCR output. Row boundaries come from the
vertical centres of spans, because OCR line boxes are tall and loose and
midpoints between them cut through the next row. A missing interior vertical
plus text that crosses it means a merged cell (`col_span`).

Then: translate cell by cell, never move a ruling line, column widths fixed,
font floor 0.75×, numeric cells right-aligned regardless of target language.

### Scanned pages

Trigger per page: fewer than 20 extractable characters and raster coverage over
40% of the area.

```
render at the raster's native DPI → estimate skew (Hough over near-horizontal
lines) → deskew → Tesseract PSM 3, TSV → re-read a Sauvola-binarised copy if
the first pass is weak → words → lines → the same pipeline as §2
```

Two of those choices were measured, not assumed, on the bundled invoice:
rendering a 200 DPI scan at 300 DPI *lowers* mean confidence from 90 to 74, and
Sauvola binarisation lowers it from 90 to 88 on a clean scan while rescuing a
low-contrast one — so it runs second and the better result wins.

Structure is recovered in **deskewed** space and rotated back once the layout is
known. On a page skewed by one degree a table row drifts nine points across its
width, so grouping rows in original-page space assigns the left-hand cells of
one row to the row above. Mapping back moves each box's centre and keeps its
size; taking the bounding box of four rotated corners inflates every box by
~4pt and makes adjacent rows overlap.

Reconstruction differs too: there is no text layer to redact, so each region is
**inpainted** with the median colour of a 3px ring outside its own ink box, and
the translation is drawn on top. The ring is sampled per block because
illumination varies across a real sheet. Only the ink box is covered, never the
whole layout box — painting over a table cell erases the scanned ruling lines,
which is the first thing a reader checks.

Per-word confidences are kept, averaged per block, and any block under 75% is
flagged `LOW_OCR_CONFIDENCE`, outlined in the UI and listed for review. OCR
error is the largest error source in this pipeline; the design makes it visible
rather than laundering it through a confident-looking translation.

---

## 5. Bidi, shaping and line breaking

Writer routing, and why the obvious arrangement does not survive contact with
real fonts:

- Everything is written with `insert_textbox`: fast, predictable, one embed per
  face per document.
- **Arabic** is pre-shaped here (`arabic_reshaper` + the Unicode bidi
  algorithm) because `insert_textbox` does neither, and alignment is flipped —
  source `LEFT` becomes target `RIGHT`, `JUSTIFY` stays, column order reverses.
  Numbers inside RTL text stay LTR runs; the bidi algorithm handles that and it
  is not hand-rolled.
- **CJK** is pre-wrapped here against the real advance widths of the
  substituted face. A UAX #14 approximation finds the break opportunities:
  break between two CJK characters, except before closing punctuation
  `）」』、。，．！？` and except after opening punctuation `（「『`, and never
  inside a Latin word embedded in CJK text.
- **Devanagari** is segmented by extended grapheme cluster (`\X`), never by
  code point, so a conjunct is never split, and gets 8% extra line height.
- `insert_htmlbox` is used only for the tracking rung and for glyph-fallback
  cases. It is deliberately *not* used for CJK: MuPDF re-embeds the referenced
  face on every layout pass, and four calls with a 17MB face produce a 53MB
  document.

The script line-height bonus applies only when the script actually changes. An
identity round-trip, or Hindi to Hindi, would otherwise be pushed down a rung
for nothing.

---

## 6. Validation metrics

Every number is measured. `POST /api/versions/:id/validate` renders both PDFs at
150 DPI and computes:

| Metric | Method | Target |
|---|---|---|
| **Graphics fidelity** | Mask all text bboxes in both renders; SSIM on the remainder | ≥ 0.98 |
| **Text coverage** | translated / translatable segments | 1.00 |
| **Overflow count** | segments that reached rung 6 | 0 |
| **Adjustment budget** | Σ rung weights `(0,1,2,3,5,8,13)`, reported raw | lower |
| **Overlap violations** | per-page bbox sweep, >2% of the smaller box | 0 |
| **Geometry integrity** | page count and per-page size equal (I1, I2) | true |
| **Font substitutions** | distinct original → replacement pairs, with the list | — |
| **Whitespace delta** | mean relative change in ink coverage per page | < 0.15 |
| **Translation confidence** | character-weighted mean segment confidence | — |
| **Protection failures** | placeholder round-trips that could not be repaired | 0 |
| **Low-confidence OCR** | blocks under 75% mean word confidence | — |

Masking is why graphics fidelity means something: it looks only at the parts of
the page that were *not* supposed to change. Text registration is deliberately
not in that number — a substituted face re-wraps lines, so an unmasked
comparison would conflate "the artwork moved" with "the words are different",
and the first is a bug while the second is the entire point.

Composite scores are pure functions of those metrics, with the weights included
in the response so the UI can show the derivation term by term:

```
layout_preservation = 100 × (0.40·ssim + 0.25·(1−overflow_rate)
                             + 0.20·(1−concession_rate) + 0.15·geometry_ok)
text_fidelity       = 100 × (0.70·coverage + 0.30·(1−protection_failure_rate))
typographic_fidelity= 100 × (0.50·(1−overlap_rate) + 0.50·typeface_retention)
```

`Layout Preservation: 98.6%` is clickable down to the four numbers that produced
it. Nothing in this codebase hardcodes a score.

Issue severities: `ERROR` (invariant violated), `WARNING` (rung ≥ 4, low OCR
confidence, font substitution in a heading, missing glyphs, whitespace delta),
`INFO` (rungs 1–3, inpainting, placeholder repair).
