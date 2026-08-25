"""Parser interface (§11).

One implementation exists: `PdfParser`. Future implementations -- `DocxParser`,
`PptxParser`, `ImageParser` -- would produce the same ParsedDocument, which is
the only contract the reconstruction engine depends on. Do not add a second
implementation until one is actually needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.parsers.model import ParsedDocument


@runtime_checkable
class DocumentParser(Protocol):
    #: file extensions this parser accepts
    extensions: tuple[str, ...]

    def sniff(self, path: Path) -> bool:
        """Cheap magic-byte check before any heavy work."""

    def parse(self, path: Path, *, password: str | None = None,
              ocr: bool = True) -> ParsedDocument:
        ...
