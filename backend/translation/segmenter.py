"""Segmentation (§5.2).

The translation unit is a paragraph, a list item or a table cell -- never a
line. A line break inside a paragraph is a layout artefact; translating across
it destroys meaning and produces text that cannot be re-wrapped.

Each unit carries the preceding and following unit as read-only context, plus
the document's detected domain. Context exists for coherence only: the provider
contract says the model returns just the target unit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.parsers.model import ElementType, ParsedDocument, TextBlock

DOMAIN_HINTS = {
    "legal": ("notification", "clause", "section", "act", "hereby", "अधिसूचना",
              "धारा", "अधिनियम", "gemäß", "Absatz"),
    "academic": ("abstract", "we report", "evaluation", "corpus", "et al",
                 "figure", "table", "section"),
    "technical": ("interface", "api", "endpoint", "schnittstelle", "protokoll",
                  "request", "response", "batch"),
    "financial": ("invoice", "vat", "subtotal", "total due", "payment",
                  "rechnung", "betrag"),
}


@dataclass
class Segment:
    id: str                      # block id
    text: str
    element_type: str
    page_index: int
    translatable: bool = True
    context_before: str = ""
    context_after: str = ""
    table_id: str | None = None
    row: int | None = None
    col: int | None = None
    list_marker: str | None = None
    ocr_confidence: float | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "type": self.element_type,
                "page_index": self.page_index, "translatable": self.translatable,
                "context_before": self.context_before[:400],
                "context_after": self.context_after[:400]}


def detect_domain(doc: ParsedDocument) -> str:
    text = " ".join(b.text for p in doc.pages for b in p.all_blocks)[:8000].lower()
    best, score = "general", 0
    for domain, hints in DOMAIN_HINTS.items():
        s = sum(text.count(h.lower()) for h in hints)
        if s > score:
            best, score = domain, s
    return best


def segment_document(doc: ParsedDocument) -> list[Segment]:
    segments: list[Segment] = []
    flat: list[TextBlock] = []
    for page in doc.pages:
        flat.extend(page.all_blocks)
    for i, b in enumerate(flat):
        text = b.text.strip()
        if not text:
            continue
        prev = flat[i - 1].text.strip() if i else ""
        nxt = flat[i + 1].text.strip() if i + 1 < len(flat) else ""
        segments.append(Segment(
            id=b.id, text=text, element_type=b.type.value,
            page_index=b.source_page, translatable=b.translatable,
            context_before=prev, context_after=nxt, table_id=b.table_id,
            row=b.row, col=b.col, list_marker=b.list_marker,
            ocr_confidence=b.ocr_confidence))
    return segments
