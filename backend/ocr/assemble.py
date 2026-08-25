"""Turn OCR words into the same layout model a digital page produces.

Grouping runs on Tesseract's own line ids, then the shared block grouper takes
over, which is what makes a scanned page and a born-digital page reconstruct
through identical code. Font size is estimated from the median glyph height of
the line: a scan has no font metrics, so this is a measurement, not a lookup.
"""
from __future__ import annotations

import statistics
import uuid

from backend.config import settings
from backend.ocr.base import OcrPage
from backend.parsers.model import (BlockStyle, ElementType, Line, Span,
                                   TextBlock, block_bbox)
from backend.utils.geometry import union

#: cap-height fraction used to turn a measured glyph box into a point size
HEIGHT_TO_SIZE = 1.34


def lines_from_ocr(ocr: OcrPage) -> list[Line]:
    buckets: dict[tuple[int, int, int], list] = {}
    for w in ocr.words:
        buckets.setdefault(w.line_id, []).append(w)
    lines: list[Line] = []
    for key in sorted(buckets):
        words = sorted(buckets[key], key=lambda w: w.bbox[0])
        heights = [w.bbox[3] - w.bbox[1] for w in words]
        size = round(statistics.median(heights) * HEIGHT_TO_SIZE, 2) if heights else 10.0
        spans: list[Span] = []
        for i, w in enumerate(words):
            text = w.text if i == len(words) - 1 else w.text + " "
            spans.append(Span(text=text, bbox=w.bbox, font="OCR-Sans",
                              size=size, flags=0, color=0,
                              ascender=0.8, descender=-0.2,
                              origin=(w.bbox[0], w.bbox[3] - 0.18 * size)))
        lines.append(Line(spans=spans, bbox=block_bbox_from(spans)))
    return lines


def block_bbox_from(spans: list[Span]):
    return union([s.bbox for s in spans]) or (0.0, 0.0, 0.0, 0.0)


def confidence_for(block: TextBlock, ocr: OcrPage) -> float:
    """Mean word confidence inside the block's box."""
    vals = [w.confidence for w in ocr.words
            if block.bbox[0] - 1 <= (w.bbox[0] + w.bbox[2]) / 2 <= block.bbox[2] + 1
            and block.bbox[1] - 1 <= (w.bbox[1] + w.bbox[3]) / 2 <= block.bbox[3] + 1]
    return float(statistics.mean(vals)) if vals else ocr.mean_confidence


def annotate_confidence(blocks: list[TextBlock], ocr: OcrPage) -> list[dict]:
    """Attach OCR confidence to blocks and flag the weak ones."""
    issues: list[dict] = []
    for b in blocks:
        b.ocr_confidence = round(confidence_for(b, ocr), 1)
        if b.ocr_confidence < settings.ocr_min_confidence:
            issues.append({
                "code": "LOW_OCR_CONFIDENCE", "severity": "WARNING",
                "block": b.id, "confidence": b.ocr_confidence,
                "message": f"OCR confidence {b.ocr_confidence:.0f}% is below "
                           f"{settings.ocr_min_confidence:.0f}%; review this "
                           "block before trusting its translation"})
    return issues
