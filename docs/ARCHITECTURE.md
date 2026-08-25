# Architecture

## Shape

```
frontend/  Next.js 14 App Router · React 18 · TypeScript strict · Tailwind
           · Radix primitives · pdf.js
backend/   FastAPI · PyMuPDF · Tesseract · Pillow · scikit-image · SQLAlchemy
jobs/      in-process asyncio worker + polling endpoint (no Celery, no Redis)
db/        SQLite by default, PostgreSQL via DATABASE_URL
```

The browser talks to `/api/*` on its own origin; Next rewrites that to FastAPI,
so CORS is not part of the picture in dev or in the compose stack.

## Module map

```
backend/
  config.py          settings; every value has a working offline default
  db.py              engine, session scope, schema creation
  models/            SQLAlchemy rows (§7 of the brief)
  schemas/           wire types, including the JobProgress stage machine
  parsers/
    model.py         Span/Line/TextBlock/Table/ParsedPage/ParsedDocument
    pdf_parser.py    rawdict extraction, grouping, OCR branch, language ID
    columns.py       gutter detection, column assignment, reading order
    classify.py      element classification rules
    tables.py        grid / rules-only / alignment-only table detection
  ocr/
    tesseract.py     native-DPI render, skew, binarise, TSV read
    assemble.py      OCR words -> the shared layout model
  translation/
    protect.py       placeholder masking and verified restore
    segmenter.py     paragraph-level units with neighbour context
    termmemory.py    recurring-term locking
    pipeline.py      batching, retries, repair round, per-segment failure
  providers/         AIProvider protocol + mock + OpenAI-compatible
  reconstruction/
    shaping.py       bidi, CJK breaking, grapheme clusters, writer routing
    fit_ladder.py    the seven rungs and their weights
    placer.py        erase, measure, place, trim, record
    tables.py        per-cell placement at the tighter floor
    inpaint.py       background sampling for scanned pages
    rebuilder.py     page/document orchestration, invariant assertions
  validators/
    metrics.py       masked SSIM and every other measured metric
  fonts/
    resolver.py      FontResolver: substitution policy, x-height correction
    registry.py      PyMuPDF registration, @font-face CSS, fallback chains
  services/          documents, jobs, versions, audit
  api/routes.py      the HTTP surface
```

## Data flow

```
upload → parse (rawdict | OCR) → classify → segment
      → protect → translate (batched) → verify placeholders → restore
      → erase + place (fit ladder) → assert I1/I2 → write version
      → render both PDFs → measure → store ValidationResult
```

The parsed layout model is cached in-process by document sha256. The
reconstruction engine needs spans, baselines and table grids — far more than the
database rows carry — and re-parsing a 40-page document per request would
dominate the runtime.

## Versions

Copy-on-write and immutable. Editing one paragraph writes version N+1 that
copies every segment row by value, rebuilds only the affected page, and splices
that page into a copy of version N's PDF. Rollback is selecting an earlier
version; nothing is ever mutated in place. `changed_pages` on the version row
records what was re-rendered, and the integration test asserts that every other
page is pixel-identical.

## Extension seams

One implementation each, with the future ones named in the docstring:
`DocumentParser` (PDF; DOCX/PPTX/image later), `StorageBackend` (local FS),
`OCREngine` (Tesseract), `AIProvider` (mock + OpenAI-compatible),
`FontResolver` (Noto).

## Failure model

Every error crossing the API is `{code, message, retryable, detail?}`. Handled
explicitly: not-a-PDF (magic bytes), corrupt xref, password-protected, 0-page,
over the page limit, over the size limit, Tesseract missing or a language pack
absent, OCR returning nothing, provider timeout, provider rate limit
(exponential backoff, 3 attempts), malformed provider JSON (one retry with the
schema restated), placeholder round-trip failure, unresolvable overflow.

Partial success is first class. A document where 3 of 40 pages fail produces a
valid PDF with those pages left in the source language, marked in the UI and in
the validation report. Silent partial failure is the worst possible behaviour
here, so a failed segment keeps its source text *and* carries an issue that the
inspector and the report both show.
