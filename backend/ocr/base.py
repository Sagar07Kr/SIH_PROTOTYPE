"""OCR interface (§11). One implementation: Tesseract.

Future implementations -- a cloud OCR service, or a layout-aware model -- must
return the same `OcrPage`: word boxes in PDF points with per-word confidence.
Everything downstream (line grouping, block grouping, classification) is shared
with the digital-text path, so an OCR'd page and a born-digital page are
reconstructed by exactly the same code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from backend.utils.geometry import Box


@dataclass
class OcrWord:
    text: str
    bbox: Box              # PDF points, original (un-deskewed) page space
    confidence: float      # 0-100
    line_id: tuple[int, int, int]   # block, paragraph, line as reported


@dataclass
class OcrPage:
    words: list[OcrWord] = field(default_factory=list)
    angle_deg: float = 0.0
    dpi: int = 300
    engine: str = "tesseract"
    language: str = "eng"
    mean_confidence: float = 0.0
    ms: int = 0


@runtime_checkable
class OCREngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def languages(self) -> list[str]: ...

    def read_page(self, page, *, lang: str = "en", dpi: int = 300) -> OcrPage: ...
