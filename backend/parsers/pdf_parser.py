"""PDF parser: spans -> lines -> blocks -> columns -> reading order (§4.1).

Extraction uses `get_text("rawdict")` so that per-span font, size, flags,
colour, ascender/descender and origin survive into the layout model. Those
values are what let the reconstruction engine put text back with the same
optical weight; `get_text("text")` throws all of it away.
"""
from __future__ import annotations

import statistics
from pathlib import Path

import pymupdf as fitz

from backend.config import settings
from backend.parsers import classify as C
from backend.parsers.columns import assign_columns, detect_columns, reading_order
from backend.parsers.model import (BlockStyle, ElementType, ImageRegion, Line,
                                   ParsedDocument, ParsedPage, Span, TextBlock,
                                   block_bbox, element_id)
from backend.parsers.tables import detect_tables
from backend.utils.errors import (CorruptPdf, EmptyDocument, NotAPdf,
                                  PasswordProtected, TooManyPages)
from backend.utils.geometry import Box, area, union

ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT, ALIGN_JUSTIFY = 0, 1, 2, 3
EDGE_TOL = 2.0
SCANNED_CHAR_LIMIT = 20
SCANNED_IMAGE_COVERAGE = 0.40


class PdfParser:
    extensions = (".pdf",)

    def sniff(self, path: Path) -> bool:
        try:
            with open(path, "rb") as fh:
                head = fh.read(1024)
        except OSError:
            return False
        return b"%PDF-" in head

    # ------------------------------------------------------------------
    def open(self, path: Path, password: str | None = None) -> fitz.Document:
        if not self.sniff(path):
            raise NotAPdf("That file is not a PDF (missing %PDF- header).",
                          {"filename": Path(path).name})
        try:
            doc = fitz.open(str(path))
        except Exception as exc:
            raise CorruptPdf("The PDF could not be opened; its cross-reference "
                             "table may be damaged.", {"reason": str(exc)[:200]})
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                doc.close()
                raise PasswordProtected(
                    "This PDF is password protected. Supply the open password "
                    "and try again.", retryable=True)
        if doc.page_count == 0:
            doc.close()
            raise EmptyDocument("The PDF has no pages.")
        if doc.page_count > settings.max_pages:
            n = doc.page_count
            doc.close()
            raise TooManyPages(
                f"This prototype accepts up to {settings.max_pages} pages; "
                f"the file has {n}.", {"page_count": n, "limit": settings.max_pages})
        return doc

    # ------------------------------------------------------------------
    def parse(self, path: Path, *, password: str | None = None,
              ocr: bool = True, ocr_lang: str | None = None) -> ParsedDocument:
        doc = self.open(path, password)
        try:
            out = ParsedDocument(page_count=doc.page_count)
            fonts: set[str] = set()
            for pno in range(doc.page_count):
                page = doc[pno]
                parsed = self.parse_page(page, ocr=ocr,
                                         ocr_lang=ocr_lang or "en")
                out.pages.append(parsed)
                for b in parsed.blocks:
                    fonts.add(b.style.font)
            C.mark_headers_footers(out.pages)
            for p in out.pages:
                for b in p.all_blocks:
                    C.finalise_flags(b)
            out.is_scanned = any(p.is_scanned for p in out.pages)
            out.font_names = sorted(fonts)
            lang, conf = detect_language(out)
            out.source_lang, out.source_lang_confidence = lang, conf

            # A scanned page had to be read before its language could be
            # detected, so the first pass used the caller's hint. If the text
            # says otherwise and the right pack is installed, read it again --
            # OCR with the wrong language model is the single cheapest way to
            # ruin a scanned document.
            if ocr and out.is_scanned and lang != (ocr_lang or "en"):
                from backend.ocr.tesseract import default_engine
                engine = default_engine()
                try:
                    wanted = engine.resolve_lang(lang)
                except Exception:
                    wanted = None
                first = engine.resolve_lang(ocr_lang or "en") if wanted else None
                if wanted and wanted != first:
                    for i, p in enumerate(out.pages):
                        if p.is_scanned:
                            out.pages[i] = self.parse_page(doc[i], ocr=True,
                                                           ocr_lang=lang)
                    C.mark_headers_footers(out.pages)
                    for p in out.pages:
                        for b in p.all_blocks:
                            C.finalise_flags(b)
            return out
        finally:
            doc.close()

    # ------------------------------------------------------------------
    def parse_page(self, page: fitz.Page, *, ocr: bool = True,
                   ocr_lang: str = "en") -> ParsedPage:
        rect = page.rect
        raw = page.get_text("rawdict")
        lines = _lines_from_rawdict(raw)
        images = _images(page)
        drawings = page.get_drawings()
        draw_boxes = [(_r(d["rect"])) for d in drawings if d.get("rect")]
        chars = len(page.get_text().strip())
        img_cov = sum(i.area for i in images) / max(1.0, area(_r(rect)))
        is_scanned = chars < SCANNED_CHAR_LIMIT and img_cov > SCANNED_IMAGE_COVERAGE

        parsed = ParsedPage(index=page.number, width_pt=rect.width,
                            height_pt=rect.height, rotation=page.rotation,
                            is_scanned=is_scanned, images=images,
                            drawings=draw_boxes, extractable_chars=chars)
        if is_scanned and ocr:
            return self._parse_scanned(page, parsed, ocr_lang)
        if is_scanned or not lines:
            parsed.modal_font_size = 10.0
            return parsed

        modal = C.modal_font_size(lines)
        parsed.modal_font_size = modal

        tables, consumed = detect_tables(lines, drawings, page.number,
                                         rect.width, modal)
        parsed.tables = tables
        body = [ln for ln in lines if id(ln) not in consumed]

        graphics = C.graphic_regions(images, draw_boxes)
        layout = detect_columns(body, rect.width, rect.height,
                                graphics=graphics)
        parsed.columns = layout.columns
        parsed.text_area = layout.text_area

        blocks = group_lines(body, layout.columns, page.number)
        assign_columns(blocks, layout)
        C.attach_markers(blocks)

        rules = [d for d in draw_boxes if (d[3] - d[1]) <= 3.0]
        caption_anchors = graphics + [t.bbox for t in tables]
        ordered = reading_order(blocks, layout)
        for i, b in enumerate(ordered):
            gap = _gap_below(b, ordered, rect.height)
            if b.type in (ElementType.LIST_MARKER,):
                continue
            b.type = C.classify_block(b, modal=modal, page_height=rect.height,
                                      page_width=rect.width,
                                      graphics=caption_anchors,
                                      rules=rules, gap_below=gap)
        # table cells continue the page's reading order after the body blocks
        n = len(ordered)
        for t in tables:
            for cell in sorted(t.cells, key=lambda c: (c.row or 0, c.col or 0)):
                cell.reading_order = n
                n += 1
        parsed.blocks = ordered
        return parsed


    # ------------------------------------------------------------------
    def _parse_scanned(self, page: fitz.Page, parsed: ParsedPage,
                       ocr_lang: str) -> ParsedPage:
        """Read a scanned page, then run the *same* layout pipeline on it."""
        from backend.ocr.assemble import (annotate_confidence, lines_from_ocr)
        from backend.ocr.tesseract import default_engine

        engine = default_engine()
        ocr_page = engine.read_page(page, lang=ocr_lang)
        lines = lines_from_ocr(ocr_page)
        parsed.ocr_mean_confidence = round(ocr_page.mean_confidence, 1)
        if not lines:
            parsed.modal_font_size = 10.0
            return parsed
        rect = page.rect
        modal = C.modal_font_size(lines)
        parsed.modal_font_size = modal
        drawings = page.get_drawings()
        tables, consumed = detect_tables(lines, drawings, page.number,
                                         rect.width, modal)
        parsed.tables = tables
        body = [ln for ln in lines if id(ln) not in consumed]
        layout = detect_columns(body, rect.width, rect.height)
        parsed.columns = layout.columns
        parsed.text_area = layout.text_area
        blocks = group_lines(body, layout.columns, page.number)
        assign_columns(blocks, layout)
        C.attach_markers(blocks)
        graphics = C.graphic_regions(parsed.images, parsed.drawings)
        rules = [d for d in parsed.drawings if (d[3] - d[1]) <= 3.0]
        ordered = reading_order(blocks, layout)
        anchors = graphics + [t.bbox for t in tables]
        for b in ordered:
            if b.type is ElementType.LIST_MARKER:
                continue
            b.type = C.classify_block(b, modal=modal, page_height=rect.height,
                                      page_width=rect.width, graphics=anchors,
                                      rules=rules,
                                      gap_below=_gap_below(b, ordered,
                                                           rect.height))
        n = len(ordered)
        for t in tables:
            for cell in sorted(t.cells, key=lambda c: (c.row or 0, c.col or 0)):
                cell.reading_order = n
                n += 1
        parsed.blocks = ordered
        annotate_confidence(parsed.all_blocks, ocr_page)
        if abs(ocr_page.angle_deg) > 0.08:
            # OCR boxes are in deskewed space; the inverse of the straightening
            # rotation puts them back on the skewed page. The sign was verified
            # against the raster: the recovered box for the invoice's letterhead
            # lands within a point of the ink's own tight bounding box.
            _unskew(parsed, ocr_page.angle_deg, rect)
        return parsed


# ---------------------------------------------------------------- helpers

def _r(rect) -> Box:
    if isinstance(rect, (tuple, list)):
        return tuple(float(v) for v in rect)  # type: ignore[return-value]
    return (rect.x0, rect.y0, rect.x1, rect.y1)


def _images(page: fitz.Page) -> list[ImageRegion]:
    out: list[ImageRegion] = []
    try:
        for info in page.get_image_info(xrefs=True):
            out.append(ImageRegion(bbox=_r(info["bbox"]), xref=info.get("xref", 0)))
    except Exception:
        pass
    return out


def _lines_from_rawdict(raw: dict) -> list[Line]:
    lines: list[Line] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            spans: list[Span] = []
            for sp in ln.get("spans", []):
                text = sp.get("text")
                if text is None:
                    text = "".join(ch.get("c", "") for ch in sp.get("chars", []))
                if not text:
                    continue
                spans.append(Span(
                    text=text, bbox=_r(sp["bbox"]), font=sp.get("font", ""),
                    size=float(sp.get("size", 10.0)),
                    flags=int(sp.get("flags", 0)), color=int(sp.get("color", 0)),
                    ascender=float(sp.get("ascender", 0.8)),
                    descender=float(sp.get("descender", -0.2)),
                    origin=tuple(sp.get("origin", (0.0, 0.0))),
                    dir=tuple(ln.get("dir", (1.0, 0.0)))))
            if not spans:
                continue
            lines.append(Line(spans=spans, bbox=block_bbox_from_spans(spans),
                              dir=tuple(ln.get("dir", (1.0, 0.0))),
                              wmode=int(ln.get("wmode", 0))))
    return lines


def block_bbox_from_spans(spans: list[Span]) -> Box:
    return union([s.bbox for s in spans]) or (0.0, 0.0, 0.0, 0.0)


def group_lines(lines: list[Line], columns: list[tuple[float, float]],
                page_index: int) -> list[TextBlock]:
    """Cluster lines into paragraph blocks.

    Lines join when they are vertically adjacent (gap under ~1.7 line heights),
    horizontally overlapping, and typographically similar.

    The grouping keeps several *streams* open at once and appends each line to
    whichever open stream it best continues. That is what makes it survive
    locally two-column material -- a band of two columns inside an otherwise
    single-column page -- where a strictly sequential grouper would break every
    paragraph into single lines because consecutive lines by y belong to
    different columns.

    Rotated lines are kept in their own single-line blocks: a rotated stamp is
    not part of the paragraph it happens to sit beside. List markers are lifted
    out first (they interleave with the body lines they belong to) and a marker
    beside a line means a new item starts there.
    """
    marker_lines = [ln for ln in lines if C.is_marker_only(ln.text)]
    marker_ids = {id(ln) for ln in marker_lines}
    marker_centres = [(ln.bbox[1] + ln.bbox[3]) / 2 for ln in marker_lines]
    body = [ln for ln in lines if id(ln) not in marker_ids]

    streams: list[list[Line]] = []
    for ln in sorted(body, key=lambda l: (round(l.bbox[1], 1), l.bbox[0])):
        starts_item = any(ln.bbox[1] - 2 <= mc <= ln.bbox[3] + 2
                          for mc in marker_centres)
        best, best_overlap = None, 0.0
        if not starts_item and not ln.rotated:
            for st in streams:
                prev = st[-1]
                if prev.rotated:
                    continue
                gap = ln.bbox[1] - prev.bbox[3]
                lh = max(prev.size, ln.size) * 1.7
                if gap > lh or ln.bbox[1] < prev.bbox[1] - 0.5:
                    continue
                overlap = (min(prev.bbox[2], ln.bbox[2])
                           - max(prev.bbox[0], ln.bbox[0]))
                min_w = max(1.0, min(prev.bbox[2] - prev.bbox[0],
                                     ln.bbox[2] - ln.bbox[0]))
                size_ratio = (max(prev.size, ln.size)
                              / max(0.1, min(prev.size, ln.size)))
                same_style = (prev.spans[0].bold == ln.spans[0].bold
                              and prev.spans[0].mono == ln.spans[0].mono)
                # 1.12, not 1.25: an 8pt caption under 9.4pt body text is a
                # different element, and merging them loses the caption.
                # A finished sentence on a short final line, followed by extra
                # leading, is a paragraph break -- the strongest signal
                # available without semantics.
                stream_right = max(l.bbox[2] for l in st)
                ends_para = (C.SENTENCE_END.search(prev.text.strip() or " ")
                             and prev.bbox[2] < 0.94 * stream_right
                             and gap > 0.35 * prev.size)
                if overlap > 0.35 * min_w and size_ratio <= 1.12 and same_style \
                        and not ends_para and overlap > best_overlap:
                    best, best_overlap = st, overlap
        if best is None:
            streams.append([ln])
        else:
            best.append(ln)

    groups = streams + [[ln] for ln in marker_lines]
    blocks: list[TextBlock] = []
    for g in groups:
        style = _style_of(g, columns)
        bbox = block_bbox(g)
        text = " ".join(ln.text.strip() for ln in g)[:160]
        blocks.append(TextBlock(
            id=element_id(page_index, [round(v, 1) for v in bbox], text),
            type=ElementType.PARAGRAPH, bbox=bbox, lines=g, style=style,
            source_page=page_index))
    return blocks


def _style_of(lines: list[Line], columns: list[tuple[float, float]]) -> BlockStyle:
    spans = [sp for ln in lines for sp in ln.spans if sp.text.strip()]
    if not spans:
        return BlockStyle()
    dominant = max(spans, key=lambda s: len(s.text))
    sizes = [s.size for s in spans]
    baselines = [ln.spans[0].origin[1] for ln in lines]
    deltas = [b - a for a, b in zip(baselines, baselines[1:]) if b - a > 0.5]
    line_height = statistics.median(deltas) if deltas else dominant.size * 1.3
    leading = line_height / max(1.0, dominant.size)
    dirx, diry = lines[0].dir
    rotation = 0
    if abs(diry) > 0.5:
        rotation = 90 if diry < 0 else 270
    elif dirx < -0.5:
        rotation = 180
    return BlockStyle(font=dominant.font, size=max(sizes),
                      bold=dominant.bold, italic=dominant.italic,
                      mono=dominant.mono, color=dominant.rgb,
                      align=_alignment(lines, columns), leading=round(leading, 3),
                      rotation=rotation, line_height=round(line_height, 2))


def _alignment(lines: list[Line], columns: list[tuple[float, float]]) -> int:
    if len(lines) < 2:
        # single line: guess from its position inside the column
        ln = lines[0]
        for a, b in columns:
            if a - 2 <= ln.bbox[0] and ln.bbox[2] <= b + 2:
                left = ln.bbox[0] - a
                right = b - ln.bbox[2]
                if abs(left - right) <= 3 and left > 6:
                    return ALIGN_CENTER
                if right < 2 and left > 6:
                    return ALIGN_RIGHT
                break
        return ALIGN_LEFT
    lefts = [ln.bbox[0] for ln in lines]
    rights = [ln.bbox[2] for ln in lines]
    left_ragged = (max(lefts) - min(lefts)) > EDGE_TOL
    # the last line of a justified paragraph is short by design
    right_body = rights[:-1] if len(rights) > 2 else rights
    right_ragged = (max(right_body) - min(right_body)) > EDGE_TOL
    if not left_ragged and not right_ragged:
        return ALIGN_JUSTIFY
    if not left_ragged and right_ragged:
        return ALIGN_LEFT
    if left_ragged and not right_ragged:
        return ALIGN_RIGHT
    centers = [(ln.bbox[0] + ln.bbox[2]) / 2 for ln in lines]
    if (max(centers) - min(centers)) <= EDGE_TOL:
        return ALIGN_CENTER
    return ALIGN_LEFT


def _gap_below(block: TextBlock, blocks: list[TextBlock], page_height: float) -> float:
    below = [b for b in blocks
             if b is not block and b.bbox[1] >= block.bbox[3] - 1
             and min(b.bbox[2], block.bbox[2]) - max(b.bbox[0], block.bbox[0]) > 0]
    if not below:
        return max(0.0, page_height * 0.92 - block.bbox[3])
    return min(b.bbox[1] - block.bbox[3] for b in below)


def _unskew(parsed: ParsedPage, angle_deg: float, page_rect) -> None:
    """Rotate a page's recovered geometry back onto the skewed original.

    Text is still drawn horizontally: neither `insert_textbox` nor
    `insert_htmlbox` accepts an arbitrary rotation, only quarter turns. The
    residual error is the slant across one block -- about 4pt on a 200pt block
    at one degree -- and is recorded in docs/LIMITATIONS.md.
    """
    import math

    rad = math.radians(angle_deg)
    cx, cy = page_rect.width / 2.0, page_rect.height / 2.0
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    def pt(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - cx, y - cy
        return (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)

    def box(b):
        # Rotate the centre and keep the size. Taking the bounding box of the
        # four rotated corners instead inflates every box by tan(angle) x its
        # width -- about 4pt on a table cell at one degree -- which is enough to
        # make adjacent rows overlap and trip the I5 sweep.
        cx0, cy0 = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        w, h = b[2] - b[0], b[3] - b[1]
        mx, my = pt(cx0, cy0)
        return (mx - w / 2, my - h / 2, mx + w / 2, my + h / 2)

    for blk in parsed.all_blocks:
        blk.bbox = box(blk.bbox)
        if blk.marker_bbox:
            blk.marker_bbox = box(blk.marker_bbox)
        for ln in blk.lines:
            ln.bbox = box(ln.bbox)
            for sp in ln.spans:
                sp.bbox = box(sp.bbox)
                sp.origin = pt(*sp.origin)
    for tbl in parsed.tables:
        tbl.bbox = box(tbl.bbox)


def detect_language(doc: ParsedDocument) -> tuple[str, float]:
    """Script-first detection, then langdetect for Latin-script disambiguation.

    Script evidence is decisive and cheap: a page of Devanagari is Hindi with no
    statistical model required. langdetect only has to separate en/de/fr/es.
    """
    text = " ".join(b.text for p in doc.pages for b in p.all_blocks
                    if b.translatable)[:20000]
    return detect_language_text(text)


def detect_language_text(text: str) -> tuple[str, float]:
    sample = (text or "").strip()
    if not sample:
        return "en", 0.0
    counts = {"devanagari": 0, "arabic": 0, "kana": 0, "han": 0, "latin": 0}
    for ch in sample:
        o = ord(ch)
        if 0x0900 <= o <= 0x097F:
            counts["devanagari"] += 1
        elif 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
            counts["arabic"] += 1
        elif 0x3040 <= o <= 0x30FF:
            counts["kana"] += 1
        elif 0x4E00 <= o <= 0x9FFF:
            counts["han"] += 1
        elif ch.isalpha():
            counts["latin"] += 1
    total = max(1, sum(counts.values()))
    if counts["devanagari"] / total > 0.25:
        return "hi", min(0.99, counts["devanagari"] / total + 0.2)
    if counts["arabic"] / total > 0.25:
        return "ar", min(0.99, counts["arabic"] / total + 0.2)
    if counts["kana"] / total > 0.05:
        return "ja", min(0.99, (counts["kana"] + counts["han"]) / total + 0.2)
    if counts["han"] / total > 0.20:
        return "zh", min(0.99, counts["han"] / total + 0.2)
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        best = detect_langs(sample)[0]
        code = best.lang.split("-")[0]
        from backend.utils.langs import LANGS
        if code in LANGS:
            return code, float(best.prob)
        return "en", 0.35
    except Exception:
        return "en", 0.25
