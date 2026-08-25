"""Block classification (§4.1).

Each rule is written so it can be unit-tested in isolation and so a wrong
answer is visible in the UI rather than silently changing what gets translated.
The one rule with document-wide scope is header/footer recurrence, which needs
every page before it can decide; `mark_headers_footers` runs after all pages
are parsed.
"""
from __future__ import annotations

import re
from collections import Counter

import regex as re2

from backend.parsers.model import (ElementType, ImageRegion, Line, TextBlock,
                                   VERBATIM_TYPES)
from backend.utils.geometry import Box, area, height, width

BULLET_GLYPHS = "•◦▪▫‣⁃∙·-–—*"
BULLET_RE = re.compile(rf"^\s*([{re.escape(BULLET_GLYPHS)}])\s+(?=\S)")
ORDINAL_RE = re.compile(
    r"^\s*(\(?(?:\d{1,3}|[ivxlcdm]{1,6}|[a-zA-Z])\)|"          # (1) a) iv)
    r"(?:\d{1,3}|[ivxlcdm]{1,6}|[a-zA-Z])[.)]|"                # 1. a. iv)
    r"\d{1,3}(?:\.\d{1,3}){1,3}\.?)\s+(?=\S)")
# A bare number is a page number far more often than a list marker, so digits
# and letters must carry punctuation to count as a marker; bullets stand alone.
MARKER_ONLY_RE = re.compile(
    rf"^\s*(?:[{re.escape(BULLET_GLYPHS)}]|\(\d{{1,3}}\)|\d{{1,3}}[.)]|"
    r"[ivxlcdm]{1,6}[.)]|[a-zA-Z][.)])\s*$")
MATH_CHARS = set("=+−-±×÷≤≥≠≈∑∏∫√∞∂∇αβγδθλμπσφω^_/|·")
DIGIT_RE = re.compile(r"\d+")
SENTENCE_END = re.compile(r"[.!?।؟。！？]\s*$")


def modal_font_size(lines: list[Line]) -> float:
    """Character-weighted modal size: the body text size of the page."""
    c: Counter = Counter()
    for ln in lines:
        for sp in ln.spans:
            t = sp.text.strip()
            if t:
                c[round(sp.size * 2) / 2] += len(t)
    if not c:
        return 10.0
    return c.most_common(1)[0][0]


def split_marker(text: str) -> tuple[str | None, str]:
    """Pull a leading list marker off the text. The marker is never translated."""
    m = BULLET_RE.match(text) or ORDINAL_RE.match(text)
    if not m:
        return None, text
    marker = m.group(1) if m.re is BULLET_RE else m.group(1)
    return marker.strip(), text[m.end():].lstrip()


def is_marker_only(text: str) -> bool:
    t = text.strip()
    return bool(t) and len(t) <= 6 and bool(MARKER_ONLY_RE.match(t))


def normalise_recurrence_key(text: str) -> str:
    """Digit *runs* -> '#', so "Page 3 / 12" and "Page 11 / 12" collapse to the
    same key. Replacing digits one by one looks equivalent and is not: it turns
    those two into "page # / ##" and "page ## / ##", and every footer in a
    document longer than nine pages stops being recognised as recurring."""
    t = DIGIT_RE.sub("#", text.strip().lower())
    return re.sub(r"\s+", " ", t)


def graphic_regions(images: list[ImageRegion], drawings: list[Box],
                    min_area: float = 1800.0) -> list[Box]:
    """Images plus clusters of vector art big enough to carry a caption."""
    out = [i.bbox for i in images]
    big = [d for d in drawings if area(d) >= min_area
           and width(d) > 30 and height(d) > 30]
    return out + big


def classify_block(block: TextBlock, *, modal: float, page_height: float,
                   page_width: float, graphics: list[Box],
                   rules: list[Box], gap_below: float) -> ElementType:
    text = block.text.strip()
    if not text:
        return ElementType.PARAGRAPH
    style = block.style
    size = style.size

    if block.style.mono and block.line_count >= 2:
        return ElementType.CODE
    if _looks_like_equation(text, block):
        return ElementType.EQUATION
    if is_marker_only(text):
        return ElementType.LIST_MARKER

    # caption: near a graphic, smaller than body text
    for g in graphics:
        vertical_gap = min(abs(block.bbox[1] - g[3]), abs(g[1] - block.bbox[3]))
        overlaps_x = min(block.bbox[2], g[2]) - max(block.bbox[0], g[0]) > 0
        if overlaps_x and vertical_gap <= 20.0 and size < modal - 0.2:
            return ElementType.CAPTION

    # A numbered note under a short separator rule is a footnote, even though
    # it carries a list marker; footnotes are numbered by convention.
    if size <= 0.88 * modal:
        for r in rules:
            if 40 <= width(r) <= 140 and 0 <= block.bbox[1] - r[3] <= 18 \
                    and abs(block.bbox[0] - r[0]) < 24:
                return ElementType.FOOTNOTE

    if block.list_marker:
        return ElementType.LIST_ITEM

    # signature: a body-sized block of a line or three, sitting just under a
    # short rule. Position on the page is not part of the test -- a signature
    # block lands high on a sparse final page.
    if block.line_count <= 3 and size >= 0.9 * modal:
        for r in rules:
            rule_w = width(r)
            if 60 <= rule_w <= 300 and 0 <= block.bbox[1] - r[3] <= 14 and \
                    abs(block.bbox[0] - r[0]) < 40 and not SENTENCE_END.search(text):
                return ElementType.SIGNATURE

    if size >= 1.15 * modal:
        return ElementType.HEADING
    if style.bold and block.line_count <= 2 and gap_below > 1.5 * style.line_height:
        return ElementType.HEADING
    if style.bold and block.line_count == 1 and len(text) <= 48 and \
            style.align in (1, 2) and not SENTENCE_END.search(text):
        return ElementType.HEADING          # e.g. a centred "Abstract" 

    if size <= 0.88 * modal:
        if block.bbox[1] > page_height * 0.72:
            return ElementType.FOOTNOTE
        for r in rules:          # footnotes sit under a short separator rule
            if 40 <= width(r) <= 140 and 0 <= block.bbox[1] - r[3] <= 16 \
                    and abs(block.bbox[0] - r[0]) < 24:
                return ElementType.FOOTNOTE
    return ElementType.PARAGRAPH


def _looks_like_equation(text: str, block: TextBlock) -> bool:
    if block.line_count > 2 or len(text) > 160:
        return False
    stripped = text.replace(" ", "")
    if not stripped:
        return False
    math = sum(1 for ch in stripped if ch in MATH_CHARS)
    letters = sum(1 for ch in stripped if ch.isalpha())
    if math == 0:
        return False
    # 0.08 with a mandatory relation sign: a displayed formula is mostly
    # identifiers, so the operator density is lower than intuition suggests.
    return math / len(stripped) >= 0.08 and ("=" in text or math >= 4) and \
        letters < len(stripped) * 0.75


def mark_headers_footers(pages: list, band: float = 0.08,
                         recurrence: float = 0.60) -> None:
    """A block is a header/footer only if it sits in the top/bottom band *and*
    its digit-normalised text recurs on at least 60% of pages (§4.1)."""
    n = len(pages)
    if n == 0:
        return
    counts: Counter = Counter()
    candidates: list[tuple[object, str, bool]] = []
    for page in pages:
        seen_keys = set()
        for b in page.blocks:
            cy = (b.bbox[1] + b.bbox[3]) / 2
            top = cy <= page.height_pt * band
            bottom = cy >= page.height_pt * (1 - band)
            if not (top or bottom):
                continue
            key = normalise_recurrence_key(b.text)
            if not key:
                continue
            candidates.append((b, key, top))
            if key not in seen_keys:
                counts[key] += 1
                seen_keys.add(key)
    threshold = max(2, int(round(recurrence * n))) if n > 1 else 1
    for b, key, top in candidates:
        if counts[key] < threshold:
            continue
        digits_only = bool(re.fullmatch(r"[^\w]*[\d\s/.,#-]+[^\w]*", b.text.strip()))
        if digits_only:
            b.type = ElementType.PAGE_NUMBER
        else:
            b.type = ElementType.HEADER if top else ElementType.FOOTER


def attach_markers(blocks: list[TextBlock]) -> None:
    """Marker-only blocks drawn beside their text (common in generated PDFs and
    after OCR) are folded into the following block as `list_marker`, and the
    marker itself is never sent to the translator."""
    markers = [b for b in blocks if is_marker_only(b.text)]
    bodies = [b for b in blocks if b not in markers]
    for m in markers:
        my = (m.bbox[1] + m.bbox[3]) / 2
        best, best_dx = None, 1e9
        for b in bodies:
            if b.bbox[0] < m.bbox[2] - 1:
                continue
            if not (b.bbox[1] - 4 <= my <= b.bbox[3] + 4):
                continue
            dx = b.bbox[0] - m.bbox[2]
            if 0 <= dx < best_dx and dx < 60:
                best, best_dx = b, dx
        if best is not None and not best.list_marker:
            best.list_marker = m.text.strip()
            best.marker_bbox = m.bbox
            best.type = ElementType.LIST_ITEM
            m.translatable = False
            m.type = ElementType.LIST_MARKER
    # in-block markers, e.g. a paragraph that literally starts with "• "
    for b in bodies:
        if b.list_marker:
            continue
        marker, rest = split_marker(b.text)
        if marker and rest:
            b.list_marker = marker
            b.type = ElementType.LIST_ITEM


def finalise_flags(block: TextBlock) -> None:
    if block.type in VERBATIM_TYPES:
        block.translatable = False
        block.protected = True
    if block.type == ElementType.PAGE_NUMBER:
        # digits are re-rendered in the target numeral system, or left verbatim
        block.translatable = True
    if not block.text.strip():
        block.translatable = False


def grapheme_len(text: str) -> int:
    """Extended grapheme clusters, not code points -- the only correct length
    measure for Devanagari (§4.4)."""
    return len(re2.findall(r"\X", text))
