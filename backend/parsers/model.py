"""In-memory layout model: span -> line -> block -> column -> reading order.

These are plain dataclasses rather than ORM rows because the reconstruction
engine walks them thousands of times per document; persistence happens once, at
the edges, in backend/services/documents.py.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

from backend.utils.geometry import Box, height, union, width


class ElementType(str, Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    LIST_MARKER = "list_marker"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    CODE = "code"
    EQUATION = "equation"
    SIGNATURE = "signature"
    FOOTNOTE = "footnote"
    IMAGE = "image"


#: types whose text is placed verbatim rather than translated
VERBATIM_TYPES = {ElementType.CODE, ElementType.EQUATION, ElementType.IMAGE,
                  ElementType.LIST_MARKER}


@dataclass
class Span:
    text: str
    bbox: Box
    font: str
    size: float
    flags: int
    color: int
    ascender: float
    descender: float
    origin: tuple[float, float]
    dir: tuple[float, float] = (1.0, 0.0)

    @property
    def bold(self) -> bool:
        return bool(self.flags & 16)

    @property
    def italic(self) -> bool:
        return bool(self.flags & 2)

    @property
    def mono(self) -> bool:
        return bool(self.flags & 8)

    @property
    def rgb(self) -> tuple[float, float, float]:
        c = int(self.color or 0)
        return (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)


@dataclass
class Line:
    spans: list[Span]
    bbox: Box
    dir: tuple[float, float] = (1.0, 0.0)
    wmode: int = 0

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def size(self) -> float:
        return max((s.size for s in self.spans), default=0.0)

    @property
    def rotated(self) -> bool:
        return abs(self.dir[1]) > 0.15


@dataclass
class BlockStyle:
    font: str = "Helvetica"
    size: float = 10.0
    bold: bool = False
    italic: bool = False
    mono: bool = False
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    align: int = 0                     # fitz.TEXT_ALIGN_*
    leading: float = 1.3
    rotation: int = 0
    line_height: float = 12.0

    def to_dict(self) -> dict:
        return {"font": self.font, "size": round(self.size, 2), "bold": self.bold,
                "italic": self.italic, "mono": self.mono,
                "color": [round(c, 4) for c in self.color], "align": self.align,
                "leading": round(self.leading, 3), "rotation": self.rotation,
                "line_height": round(self.line_height, 2)}


@dataclass
class TextBlock:
    id: str
    type: ElementType
    bbox: Box
    lines: list[Line]
    style: BlockStyle
    column_index: int = 0
    reading_order: int = 0
    list_marker: str | None = None
    marker_bbox: Box | None = None
    parent_id: str | None = None
    table_id: str | None = None
    row: int | None = None
    col: int | None = None
    row_span: int = 1
    col_span: int = 1
    ocr_confidence: float | None = None
    translatable: bool = True
    protected: bool = False
    source_page: int = 0
    cell_align: int | None = None
    numeric: bool = False

    @property
    def text(self) -> str:
        """Block text with line breaks normalised away.

        Line breaks inside a paragraph are layout artefacts, not content; the
        translator must never see them (§5.2).
        """
        parts: list[str] = []
        for ln in self.lines:
            t = ln.text.strip()
            if not t:
                continue
            if parts and parts[-1].endswith("-") and t[:1].islower():
                parts[-1] = parts[-1][:-1] + t        # de-hyphenate
                continue
            parts.append(t)
        joiner = "" if not self.word_spaced else " "
        return joiner.join(parts) if self.type != ElementType.CODE else \
            "\n".join(ln.text for ln in self.lines)

    @property
    def word_spaced(self) -> bool:
        """False for CJK source lines, where a joining space is wrong."""
        sample = "".join(ln.text for ln in self.lines)[:400]
        cjk = sum(1 for ch in sample if "぀" <= ch <= "鿿")
        return cjk < max(4, len(sample) * 0.2)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type.value,
            "bbox": [round(v, 2) for v in self.bbox], "text": self.text,
            "style": self.style.to_dict(), "column_index": self.column_index,
            "reading_order": self.reading_order, "list_marker": self.list_marker,
            "table_id": self.table_id, "row": self.row, "col": self.col,
            "row_span": self.row_span, "col_span": self.col_span,
            "ocr_confidence": self.ocr_confidence,
            "translatable": self.translatable, "protected": self.protected,
            "line_count": self.line_count,
        }


@dataclass
class Table:
    id: str
    bbox: Box
    row_bounds: list[float]
    col_bounds: list[float]
    cells: list[TextBlock] = field(default_factory=list)
    ruled: bool = True
    header_rows: int = 1

    def to_dict(self) -> dict:
        return {"id": self.id, "bbox": [round(v, 2) for v in self.bbox],
                "rows": len(self.row_bounds) - 1, "cols": len(self.col_bounds) - 1,
                "ruled": self.ruled, "cell_count": len(self.cells),
                "header_rows": self.header_rows}


@dataclass
class ImageRegion:
    bbox: Box
    xref: int = 0

    @property
    def area(self) -> float:
        return max(0.0, width(self.bbox)) * max(0.0, height(self.bbox))


@dataclass
class ParsedPage:
    index: int
    width_pt: float
    height_pt: float
    rotation: int = 0
    is_scanned: bool = False
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    images: list[ImageRegion] = field(default_factory=list)
    drawings: list[Box] = field(default_factory=list)
    columns: list[tuple[float, float]] = field(default_factory=list)
    modal_font_size: float = 10.0
    text_area: Box | None = None
    extractable_chars: int = 0
    ocr_mean_confidence: float | None = None

    @property
    def all_blocks(self) -> list[TextBlock]:
        """Blocks plus table cells, in reading order."""
        out = list(self.blocks)
        for t in self.tables:
            out.extend(t.cells)
        return sorted(out, key=lambda b: b.reading_order)

    @property
    def occupied(self) -> list[Box]:
        boxes = [b.bbox for b in self.all_blocks]
        boxes += [i.bbox for i in self.images]
        boxes += list(self.drawings)
        return boxes

    def to_dict(self) -> dict:
        return {"index": self.index, "width_pt": round(self.width_pt, 2),
                "height_pt": round(self.height_pt, 2), "rotation": self.rotation,
                "is_scanned": self.is_scanned,
                "modal_font_size": round(self.modal_font_size, 2),
                "columns": [[round(a, 1), round(b, 1)] for a, b in self.columns],
                "blocks": [b.to_dict() for b in self.all_blocks],
                "tables": [t.to_dict() for t in self.tables],
                "images": [[round(v, 2) for v in i.bbox] for i in self.images],
                "extractable_chars": self.extractable_chars,
                "ocr_mean_confidence": self.ocr_mean_confidence}


@dataclass
class ParsedDocument:
    pages: list[ParsedPage] = field(default_factory=list)
    source_lang: str = "en"
    source_lang_confidence: float = 0.0
    is_scanned: bool = False
    page_count: int = 0
    font_names: list[str] = field(default_factory=list)

    def element_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.pages:
            for b in p.all_blocks:
                counts[b.type.value] = counts.get(b.type.value, 0) + 1
            counts["table"] = counts.get("table", 0) + len(p.tables)
            counts["image"] = counts.get("image", 0) + len(p.images)
        return counts

    def to_dict(self) -> dict:
        return {"page_count": self.page_count, "source_lang": self.source_lang,
                "source_lang_confidence": round(self.source_lang_confidence, 3),
                "is_scanned": self.is_scanned,
                "element_counts": self.element_counts(),
                "pages": [p.to_dict() for p in self.pages]}


def block_bbox(lines: list[Line]) -> Box:
    return union([ln.bbox for ln in lines]) or (0.0, 0.0, 0.0, 0.0)


def element_id(*parts: object) -> str:
    """Stable id derived from page, geometry and content.

    Random ids look harmless and are not: the MockProvider seeds itself from the
    segment id, so a fresh uuid per parse made "deterministic mock" produce a
    different translation on every run, and the golden layout test could never
    hold. Content addressing also means an untouched block keeps its id across
    re-parses, which is what lets a version share unchanged segments.
    """
    payload = "|".join(str(p) for p in parts)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=6).hexdigest()
