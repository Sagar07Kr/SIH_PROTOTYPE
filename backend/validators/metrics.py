"""Validation engine (§6): every number is measured, none is asserted.

The rule this module exists to enforce is I8. A panel that reads
`Layout Preservation: 97%` is worse than no panel at all unless the 97 can be
traced to pixels and counters, so each metric carries its own derivation and
the composite scores are pure functions of the metrics with the weights
included in the output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf as fitz
from skimage.metrics import structural_similarity as ssim

from backend.config import settings
from backend.parsers.model import ParsedDocument
from backend.reconstruction.fit_ladder import RUNG_WEIGHTS
from backend.utils.geometry import Box, overlap_violations

INK_THRESHOLD = 245          # 8-bit grey below this counts as ink
MASK_PAD_PT = 1.5


@dataclass
class Metric:
    key: str
    label: str
    value: float | bool | int
    unit: str = ""
    target: str = ""
    derivation: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        v = self.value
        if isinstance(v, float):
            v = round(v, 4)
        return {"key": self.key, "label": self.label, "value": v,
                "unit": self.unit, "target": self.target,
                "derivation": self.derivation, "detail": self.detail}


@dataclass
class ValidationReport:
    metrics: list[Metric]
    issues: list[dict]
    scores: dict
    per_page: list[dict]

    def to_dict(self) -> dict:
        return {"metrics": [m.to_dict() for m in self.metrics],
                "issues": self.issues, "scores": self.scores,
                "per_page": self.per_page,
                "metrics_by_key": {m.key: m.to_dict() for m in self.metrics}}

    def metric(self, key: str) -> Metric | None:
        for m in self.metrics:
            if m.key == key:
                return m
        return None


def _render_grey(page: fitz.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _mask(shape: tuple[int, int], boxes: list[Box], scale: float) -> np.ndarray:
    """True where text is (and therefore where SSIM must not look)."""
    m = np.zeros(shape, dtype=bool)
    h, w = shape
    for b in boxes:
        x0 = max(0, int((b[0] - MASK_PAD_PT) * scale))
        y0 = max(0, int((b[1] - MASK_PAD_PT) * scale))
        x1 = min(w, int((b[2] + MASK_PAD_PT) * scale) + 1)
        y1 = min(h, int((b[3] + MASK_PAD_PT) * scale) + 1)
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True
    return m


def validate(original: Path, translated: Path, parsed: ParsedDocument,
             segments: list[dict], *, dpi: int | None = None) -> ValidationReport:
    """Compare two PDFs and the segment record that produced the second.

    `segments` entries use the keys written by the job service: rung, rect,
    confidence, status, translated, translatable, page_index, font_substitution.
    """
    dpi = dpi or settings.render_dpi_validate
    scale = dpi / 72.0
    src = fitz.open(str(original))
    dst = fitz.open(str(translated))
    issues: list[dict] = []
    per_page: list[dict] = []
    try:
        geometry_ok = src.page_count == dst.page_count
        if not geometry_ok:
            issues.append({"code": "PAGE_COUNT_MISMATCH", "severity": "ERROR",
                           "message": f"{src.page_count} pages in, "
                                      f"{dst.page_count} out"})
        ssim_values: list[float] = []
        ink_deltas: list[float] = []
        for i in range(min(src.page_count, dst.page_count)):
            sp, dp = src[i], dst[i]
            if abs(sp.rect.width - dp.rect.width) > 0.01 or \
                    abs(sp.rect.height - dp.rect.height) > 0.01:
                geometry_ok = False
                issues.append({"code": "PAGE_SIZE_MISMATCH", "severity": "ERROR",
                               "page": i,
                               "message": f"page {i + 1} geometry changed"})
            a = _render_grey(sp, dpi)
            b = _render_grey(dp, dpi)
            if a.shape != b.shape:
                h = min(a.shape[0], b.shape[0])
                w = min(a.shape[1], b.shape[1])
                a, b = a[:h, :w], b[:h, :w]
            text_boxes = [blk.bbox for blk in parsed.pages[i].all_blocks] \
                if i < len(parsed.pages) else []
            text_boxes += [tuple(s["rect"]) for s in segments
                           if s.get("page_index") == i and s.get("rect")]
            mask = _mask(a.shape, text_boxes, scale)
            am = np.where(mask, np.uint8(255), a)
            bm = np.where(mask, np.uint8(255), b)
            value = float(ssim(am, bm, data_range=255))
            ssim_values.append(value)
            ink_a = float((a < INK_THRESHOLD).mean())
            ink_b = float((b < INK_THRESHOLD).mean())
            delta = abs(ink_b - ink_a) / max(1e-6, ink_a)
            ink_deltas.append(delta)
            page_issues = []
            if value < settings.graphics_ssim_target:
                page_issues.append("graphics_shifted")
                issues.append({
                    "code": "GRAPHICS_FIDELITY", "severity": "ERROR", "page": i,
                    "message": f"masked SSIM {value:.4f} on page {i + 1} is "
                               f"below the {settings.graphics_ssim_target} target",
                    "value": round(value, 4)})
            if delta > 0.15:
                page_issues.append("ink_delta")
                issues.append({
                    "code": "WHITESPACE_DELTA", "severity": "WARNING", "page": i,
                    "message": f"ink coverage on page {i + 1} differs by "
                               f"{delta * 100:.0f}%",
                    "value": round(delta, 4)})
            per_page.append({"page": i, "masked_ssim": round(value, 5),
                             "ink_original": round(ink_a, 5),
                             "ink_translated": round(ink_b, 5),
                             "ink_delta": round(delta, 5),
                             "issues": page_issues})
    finally:
        src.close()
        dst.close()

    translatable = [s for s in segments if s.get("translatable", True)]
    translated_ok = [s for s in translatable if (s.get("translated") or "").strip()]
    coverage = len(translated_ok) / len(translatable) if translatable else 1.0
    rungs = [int(s.get("rung") or 0) for s in translated_ok]
    overflow = sum(1 for r in rungs if r >= 6)
    budget = sum(RUNG_WEIGHTS[min(r, 6)] for r in rungs)
    grown = sum(1 for r in rungs if r in (4, 5))
    reduced = sum(1 for r in rungs if r == 3)
    subs = [s["font_substitution"] for s in translated_ok
            if s.get("font_substitution")]
    sub_pairs = sorted({(x.get("original", "?"),
                         x.get("replacement") or x.get("family", "?"))
                        for x in subs})
    protection_failures = [s for s in segments
                           if s.get("status") == "PROTECTION_FAILURE"]
    low_ocr = [s for s in segments if s.get("status") == "LOW_OCR_CONFIDENCE"]
    conf_num = sum((s.get("confidence") or 0.0) * max(1, len(s.get("translated") or ""))
                   for s in translated_ok)
    conf_den = sum(max(1, len(s.get("translated") or "")) for s in translated_ok)
    confidence = conf_num / conf_den if conf_den else 0.0

    # The sweep is per page: two boxes on different pages cannot overlap, and
    # comparing them produced a spectacular crop of phantom violations.
    overlaps: list[tuple[str, str, float]] = []
    by_page_boxes: dict[int, list[tuple[str, Box]]] = {}
    for s in translated_ok:
        if s.get("rect"):
            by_page_boxes.setdefault(int(s.get("page_index") or 0), []).append(
                (s["segment_id"], tuple(s["rect"])))
    for _pg, boxes in sorted(by_page_boxes.items()):
        overlaps += overlap_violations(boxes, settings.overlap_tolerance)
    for a, b, frac in overlaps:
        issues.append({"code": "OVERLAP", "severity": "ERROR",
                       "blocks": [a, b], "fraction": round(frac, 4),
                       "message": f"placed boxes overlap by {frac * 100:.1f}%"})
    if coverage < 1.0:
        issues.append({"code": "TEXT_COVERAGE", "severity": "ERROR",
                       "message": f"{len(translatable) - len(translated_ok)} of "
                                  f"{len(translatable)} translatable segments "
                                  f"carry no text",
                       "value": round(coverage, 4)})
    for s in translated_ok:
        if int(s.get("rung") or 0) >= 6:
            issues.append({"code": "OVERFLOW", "severity": "ERROR",
                           "page": s.get("page_index"),
                           "segment": s.get("segment_id"),
                           "message": s.get("concession")
                           or "text does not fit its frame"})

    mean_ssim = float(np.mean(ssim_values)) if ssim_values else 0.0
    min_ssim = float(np.min(ssim_values)) if ssim_values else 0.0
    n = max(1, len(translated_ok))
    metrics = [
        Metric("graphics_fidelity", "Graphics fidelity", mean_ssim,
               target=f">= {settings.graphics_ssim_target}",
               derivation=f"mean masked SSIM over {len(ssim_values)} page pairs "
                          f"rendered at {dpi} DPI, text bboxes masked out",
               detail={"per_page": [round(v, 5) for v in ssim_values],
                       "min": round(min_ssim, 5)}),
        Metric("text_coverage", "Text coverage", coverage, target="1.00",
               derivation=f"{len(translated_ok)} translated / "
                          f"{len(translatable)} translatable segments"),
        Metric("overflow_count", "Overflow blocks", overflow, target="0",
               derivation=f"segments that reached rung 6 of the fit ladder "
                          f"({overflow} of {n})"),
        Metric("adjustment_budget", "Adjustment budget", budget,
               derivation="sum of fit-ladder rung weights "
                          f"{RUNG_WEIGHTS} over {n} placed segments",
               detail={"rung_histogram": {str(r): rungs.count(r)
                                          for r in sorted(set(rungs))},
                       "size_reductions": reduced, "boxes_grown": grown}),
        Metric("overlap_violations", "Overlap violations", len(overlaps),
               target="0", derivation="pairs of placed boxes overlapping by "
                                      f"more than {settings.overlap_tolerance:.0%} "
                                      "of the smaller box"),
        Metric("geometry_integrity", "Geometry integrity", bool(geometry_ok),
               target="true", derivation="page count and per-page width/height "
                                         "equal to the source (I1, I2)"),
        Metric("font_substitutions", "Font substitutions", len(sub_pairs),
               derivation="distinct original -> replacement pairs",
               detail={"pairs": [{"original": a, "replacement": b}
                                 for a, b in sub_pairs]}),
        Metric("whitespace_delta", "Whitespace delta",
               float(np.mean(ink_deltas)) if ink_deltas else 0.0,
               target="< 0.15",
               derivation="mean relative change in ink coverage per page",
               detail={"pages_over_15pct": [i for i, d in enumerate(ink_deltas)
                                            if d > 0.15]}),
        Metric("translation_confidence", "Translation confidence", confidence,
               derivation="character-weighted mean of per-segment provider "
                          "confidence"),
        Metric("protection_failures", "Protection failures",
               len(protection_failures), target="0",
               derivation="segments whose placeholder round-trip could not be "
                          "repaired"),
        Metric("low_ocr_confidence", "Low-confidence OCR blocks", len(low_ocr),
               derivation=f"OCR blocks with mean confidence below "
                          f"{settings.ocr_min_confidence:.0f}%"),
    ]
    scores = composite_scores(mean_ssim, coverage, overflow, budget, n,
                              len(overlaps), geometry_ok, len(sub_pairs),
                              len(protection_failures), confidence)
    return ValidationReport(metrics, issues, scores, per_page)


def composite_scores(mean_ssim: float, coverage: float, overflow: int,
                     budget: int, segments: int, overlaps: int,
                     geometry_ok: bool, substitutions: int,
                     protection_failures: int, confidence: float) -> dict:
    """Pure function. The UI shows this derivation on hover, term by term."""
    n = max(1, segments)
    overflow_rate = overflow / n
    concession_rate = budget / (RUNG_WEIGHTS[6] * n)
    overlap_rate = overlaps / n
    layout_terms = [
        ("graphics fidelity (masked SSIM)", 0.40, mean_ssim),
        ("blocks that fit", 0.25, 1.0 - overflow_rate),
        ("typographic concession budget", 0.20, 1.0 - concession_rate),
        ("geometry invariants", 0.15, 1.0 if geometry_ok else 0.0),
    ]
    text_terms = [
        ("segments carrying text", 0.70, coverage),
        ("protected spans intact", 0.30,
         1.0 - (protection_failures / n)),
    ]
    typo_terms = [
        ("no overlapping boxes", 0.50, 1.0 - min(1.0, overlap_rate)),
        ("original typeface retained", 0.50,
         1.0 if substitutions == 0 else max(0.0, 1.0 - 0.1 * substitutions)),
    ]

    def score(terms: list[tuple[str, float, float]]) -> dict:
        total = sum(w * max(0.0, min(1.0, v)) for _, w, v in terms)
        return {"value": round(100 * total, 1),
                "terms": [{"name": nm, "weight": w,
                           "value": round(max(0.0, min(1.0, v)), 4),
                           "contribution": round(100 * w * max(0.0, min(1.0, v)), 2)}
                          for nm, w, v in terms]}

    return {
        "layout_preservation": score(layout_terms),
        "text_fidelity": score(text_terms),
        "typographic_fidelity": score(typo_terms),
        "translation_confidence": {"value": round(100 * confidence, 1),
                                   "terms": [{"name": "character-weighted mean "
                                              "provider confidence",
                                              "weight": 1.0,
                                              "value": round(confidence, 4),
                                              "contribution": round(100 * confidence, 2)}]},
    }
