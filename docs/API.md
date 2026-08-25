# API

Base path `/api`. All errors are `{code, message, retryable, detail?}`.

## Health and samples

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | provider, font coverage, OCR availability, limits, languages |
| GET | `/samples` | the bundled sample PDFs with their fixtures |

`GET /health` is the fastest way to see whether a checkout is usable:
`fonts.missing_scripts` non-empty means those targets would render empty boxes.

## Documents

| Method | Path | Notes |
|---|---|---|
| POST | `/documents` | multipart upload → document |
| POST | `/documents/from-sample?name=` | load a bundled sample |
| POST | `/documents/:id/analyze` | parse, OCR, layout, language detect |
| GET | `/documents/:id` | document + full analysis |
| GET | `/documents/:id/pdf` | the original file |
| GET | `/documents/:id/pages/:n/render?dpi=` | cached PNG |

`analyze` runs inline and returns a **completed** job object. Parsing including
OCR takes a couple of seconds, so there is nothing to poll; the shape matches a
translation job so the frontend has one renderer for both. This is the one
deliberate divergence from the brief's endpoint table.

## Projects and translation

| Method | Path | Notes |
|---|---|---|
| POST | `/projects` | `{name}` |
| GET | `/projects` | recent projects |
| POST | `/projects/:id/translate` | `{document_id, target_lang, style, glossary, options}` → `{job_id}` |
| GET | `/projects/:id/versions` | every version in the project |
| GET | `/projects/:id/audit` | append-only event timeline |
| GET/POST | `/projects/:id/glossary` | locked term pairs |

## Jobs

| Method | Path | Notes |
|---|---|---|
| GET | `/jobs/:id` | typed progress (below) |
| DELETE | `/jobs/:id` | cancel |

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
  message?: string;               // "Translating segment 84 of 121"
  error?: { code: string; message: string; retryable: boolean };
  segments_done: number;
  segments_total: number;
  version_id?: string;            // set when stage === "DONE"
}
```

The frontend renders this state machine directly. There is no generic spinner
anywhere in the application.

## Versions

| Method | Path | Notes |
|---|---|---|
| GET | `/versions/:id` | version + job + document + segments + validation |
| GET | `/versions/:id/pdf` | the translated PDF |
| GET | `/versions/:id/pages/:n/render?dpi=` | cached PNG of a translated page |
| POST | `/versions/:id/validate` | recompute and store the measured report |
| GET | `/versions/:id/diff/:other` | changed pages, blocks, rungs, geometry |
| POST | `/versions/:id/export` | `{format: pdf\|txt\|md\|json}` |
| POST | `/versions/:id/pages/:n/regenerate` | new version, that page only |

Each segment carries `fit_rung`, `applied_size`, `original_size`, `final_bbox`,
`confidence`, `status`, `issues`, `placeholders` and `font_substitution` — which
is what makes the inspector able to say *"font reduced 6% to fit"* rather than
*"adjusted"*.

## Segments

| Method | Path | Notes |
|---|---|---|
| PATCH | `/segments/:id` | `{text}` → new version, that page re-rendered |
| POST | `/segments/:id/regenerate` | new version, that segment only |

Both return `{version, validation}` so the UI can move to the new version and
show fresh measurements without a second round trip.

## Validation payload

```jsonc
{
  "metrics": [
    { "key": "graphics_fidelity", "label": "Graphics fidelity", "value": 1.0,
      "target": ">= 0.98",
      "derivation": "mean masked SSIM over 6 page pairs rendered at 150 DPI, text bboxes masked out",
      "detail": { "per_page": [1.0, 1.0, ...], "min": 1.0 } }
  ],
  "scores": {
    "layout_preservation": {
      "value": 98.6,
      "terms": [ { "name": "graphics fidelity (masked SSIM)", "weight": 0.4,
                   "value": 1.0, "contribution": 40.0 }, ... ]
    }
  },
  "per_page": [ { "page": 0, "masked_ssim": 1.0, "ink_delta": 0.19, ... } ],
  "issues": [ { "code": "OVERFLOW", "severity": "ERROR", "page": 1,
                "message": "text does not fit; 12pt of overflow left unresolved" } ]
}
```

Every metric carries its own derivation string, and every score carries the
terms it was computed from. Nothing is hardcoded.
