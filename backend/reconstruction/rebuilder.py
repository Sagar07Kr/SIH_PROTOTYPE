"""Document rebuild orchestration.

`Rebuilder.rebuild` is the whole engine seen from outside: given the parsed
layout and a target string per block, it returns a new PDF whose geometry is
the original's and whose words are the translations, plus a record of every
concession it had to make.

Three behaviours are deliberate:

* Blocks with no translation (code, formulas, list markers, artwork) are not
  redacted at all. Leaving the original glyphs untouched is a stronger
  guarantee than erasing and re-drawing them (I6).
* `pages=` restricts the rebuild to a subset, and `base_pdf=` supplies the PDF
  those pages are spliced into. That is what makes regenerating one paragraph
  re-render one page instead of the document.
* I1 and I2 are asserted on the result, always, and a violation raises.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from backend.config import settings
from backend.parsers.model import (ElementType, ParsedDocument, ParsedPage,
                                   TextBlock)
from backend.reconstruction.inpaint import BackgroundSampler, inpaint
from backend.reconstruction.placer import PageContext, Placement, Placer
from backend.reconstruction.tables import place_table
from backend.utils.errors import InvariantViolation
from backend.utils.geometry import Box, overlap_violations
from backend.utils.io import save_pdf
from backend.utils.langs import script_of as _script_of


@dataclass
class RebuildResult:
    pdf_bytes: bytes
    placements: list[Placement] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    pages_touched: list[int] = field(default_factory=list)
    font_substitutions: list[dict] = field(default_factory=list)
    ms: int = 0

    @property
    def rung_histogram(self) -> dict[int, int]:
        h: dict[int, int] = {}
        for p in self.placements:
            h[p.rung] = h.get(p.rung, 0) + 1
        return dict(sorted(h.items()))

    def overflow_count(self) -> int:
        return sum(1 for p in self.placements if p.rung >= 6)

    def adjustment_budget(self) -> int:
        from backend.reconstruction.fit_ladder import RUNG_WEIGHTS
        return sum(RUNG_WEIGHTS[min(p.rung, 6)] for p in self.placements)


class Rebuilder:
    def __init__(self, placer: Placer | None = None):
        self.placer = placer or Placer()

    def rebuild(self, source: Path, parsed: ParsedDocument,
                translations: dict[str, str], target_lang: str, *,
                pages: set[int] | None = None,
                base_pdf: Path | None = None) -> RebuildResult:
        t0 = time.perf_counter()
        doc = fitz.open(str(source))
        original_geometry = [(p.rect.width, p.rect.height) for p in doc]
        placements: list[Placement] = []
        issues: list[dict] = []
        touched: list[int] = []

        try:
            for parsed_page in parsed.pages:
                if pages is not None and parsed_page.index not in pages:
                    continue
                page = doc[parsed_page.index]
                placements += self._rebuild_page(
                    page, parsed_page, translations, target_lang, issues,
                    script_change=_script_of(parsed.source_lang)
                    != _script_of(target_lang))
                touched.append(parsed_page.index)

            self._assert_geometry(doc, original_geometry)
            issues += self._overlap_sweep(placements, parsed, touched)

            if base_pdf is not None and pages is not None:
                out_bytes = _splice(base_pdf, doc, sorted(touched))
            else:
                out_bytes = doc.tobytes(garbage=3, deflate=True)
        finally:
            doc.close()
            self.placer.close()

        subs = {}
        for p in placements:
            if p.font and p.font.substituted:
                subs[(p.font.original_name, p.font.family)] = {
                    "original": p.font.original_name, "replacement": p.font.family,
                    "file": p.font.path.name, "reason": p.font.reason,
                    "size_factor": round(p.font.size_factor, 3)}
        return RebuildResult(out_bytes, placements, issues, touched,
                            list(subs.values()),
                            int((time.perf_counter() - t0) * 1000))

    # ------------------------------------------------------------------
    def _rebuild_page(self, page: fitz.Page, parsed: ParsedPage,
                      translations: dict[str, str], target_lang: str,
                      issues: list[dict], script_change: bool = True
                      ) -> list[Placement]:
        blocks = [b for b in parsed.all_blocks
                  if b.id in translations and translations[b.id].strip()]
        if not blocks:
            return []
        untouched: list[Box] = [b.bbox for b in parsed.all_blocks
                                if b.id not in translations]
        untouched += [i.bbox for i in parsed.images]
        untouched += [d for d in parsed.drawings]

        if parsed.is_scanned:
            # No text layer exists to redact: cover each region with its own
            # sampled background instead, and leave the rest of the scan alone.
            sampler = BackgroundSampler(page)
            # Cover the ink, not the layout box. A table cell's box spans the
            # grid, and painting over that erases the scanned ruling lines --
            # the very thing the reader checks first.
            fills = inpaint(page, [_ink_box(b) for b in blocks], sampler)
            issues.append({"code": "SCANNED_PAGE_INPAINTED", "severity": "INFO",
                           "page": parsed.index,
                           "message": f"{len(fills)} text regions inpainted on "
                                      "a scanned page",
                           "detail": {"regions": len(fills)}})
        else:
            self.placer.erase(page, blocks)
        ctx = PageContext(page=page, occupied=untouched,
                          reserved={b.id: b.bbox for b in blocks},
                          columns=parsed.columns, text_area=parsed.text_area)
        out: list[Placement] = []
        table_cells = {c.id for t in parsed.tables for c in t.cells}
        for t in parsed.tables:
            out += place_table(self.placer, ctx, t, translations, target_lang,
                               script_change=script_change)
        for b in blocks:
            if b.id in table_cells:
                continue
            out.append(self.placer.place(ctx, b, translations[b.id], target_lang,
                                         script_change=script_change))
        return out

    def _assert_geometry(self, doc: fitz.Document,
                         original: list[tuple[float, float]]) -> None:
        if doc.page_count != len(original):
            raise InvariantViolation(
                "I1 violated: output page count differs from input.",
                {"expected": len(original), "actual": doc.page_count})
        for i, (w, h) in enumerate(original):
            r = doc[i].rect
            if abs(r.width - w) > 0.01 or abs(r.height - h) > 0.01:
                raise InvariantViolation(
                    f"I2 violated: page {i + 1} changed size.",
                    {"page": i, "expected": [w, h],
                     "actual": [r.width, r.height]})

    def _overlap_sweep(self, placements: list[Placement],
                       parsed: ParsedDocument, touched: list[int]) -> list[dict]:
        """I5: no two placed boxes may overlap by more than 2% of the smaller."""
        by_page: dict[int, list[tuple[str, Box]]] = {}
        index = {b.id: b for p in parsed.pages for b in p.all_blocks}
        for p in placements:
            blk = index.get(p.block_id)
            if blk is None:
                continue
            by_page.setdefault(blk.source_page, []).append((p.block_id, p.rect))
        out: list[dict] = []
        for page_index, boxes in by_page.items():
            for a, b, frac in overlap_violations(boxes, settings.overlap_tolerance):
                out.append({"code": "OVERLAP", "severity": "ERROR",
                            "page": page_index, "blocks": [a, b],
                            "fraction": round(frac, 4),
                            "message": f"placed boxes overlap by "
                                       f"{frac * 100:.1f}% of the smaller box"})
        return out


def _ink_box(block: TextBlock) -> Box:
    """Union of the block's line boxes: where the glyphs actually are."""
    from backend.utils.geometry import union
    boxes = [ln.bbox for ln in block.lines] or [block.bbox]
    return union(boxes) or block.bbox


def _splice(base_pdf: Path, rebuilt: fitz.Document, pages: list[int]) -> bytes:
    """Replace `pages` inside a copy of `base_pdf` with the rebuilt versions."""
    out = fitz.open(str(base_pdf))
    try:
        for idx in pages:
            out.delete_page(idx)
            out.insert_pdf(rebuilt, from_page=idx, to_page=idx, start_at=idx)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def identity_translations(parsed: ParsedDocument) -> dict[str, str]:
    """Target == source. The P3 gate: if this round-trip is not near-perfect,
    no translation ever will be."""
    return {b.id: b.text for p in parsed.pages for b in p.all_blocks
            if b.translatable and b.text.strip()}
