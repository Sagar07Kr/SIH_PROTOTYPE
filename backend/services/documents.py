"""Upload, parse and persist a document, and render its pages.

The parse result is cached in-process keyed by the document's sha256: the
reconstruction engine needs the full layout model (spans, baselines, table
grids), which is far richer than what the database rows carry, and re-parsing a
40-page document for every request would dominate the runtime.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf as fitz
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Document, DocumentElement, DocumentPage
from backend.parsers.model import ParsedDocument
from backend.parsers.pdf_parser import PdfParser
from backend.utils.errors import FileTooLarge, NotAPdf, NotFound
from backend.utils.io import save_pixmap, write_bytes

_PARSE_CACHE: dict[str, ParsedDocument] = {}
_CACHE_LIMIT = 8


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_upload(db: Session, filename: str, data: bytes,
                 project_id: str | None = None) -> Document:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise FileTooLarge(
            f"That file is {len(data) / 1e6:.1f}MB; the limit is "
            f"{settings.max_upload_mb}MB.")
    # extension is a hint; the magic bytes are the check
    if data[:1024].find(b"%PDF-") < 0:
        raise NotAPdf("Only PDF files are accepted in this prototype.",
                      {"filename": filename})
    digest = sha256_bytes(data)
    safe = Path(filename or "document.pdf").name
    path = settings.upload_dir / f"{digest[:16]}-{safe}"
    write_bytes(path, data)
    doc = Document(project_id=project_id, filename=safe, path=str(path),
                   sha256=digest, size_bytes=len(data), status="UPLOADED")
    db.add(doc)
    db.flush()
    return doc


def parsed_for(doc: Document, *, ocr: bool = True,
               password: str | None = None) -> ParsedDocument:
    key = f"{doc.sha256}:{int(ocr)}"
    hit = _PARSE_CACHE.get(key)
    if hit is not None:
        return hit
    parsed = PdfParser().parse(Path(doc.path), ocr=ocr, password=password)
    if len(_PARSE_CACHE) >= _CACHE_LIMIT:
        _PARSE_CACHE.pop(next(iter(_PARSE_CACHE)))
    _PARSE_CACHE[key] = parsed
    return parsed


def persist_analysis(db: Session, doc: Document, parsed: ParsedDocument) -> None:
    doc.page_count = parsed.page_count
    doc.source_lang = parsed.source_lang
    doc.source_lang_confidence = parsed.source_lang_confidence
    doc.is_scanned = parsed.is_scanned
    doc.status = "ANALYZED"
    for page in list(doc.pages):
        db.delete(page)
    db.flush()
    for p in parsed.pages:
        row = DocumentPage(document_id=doc.id, index=p.index, width_pt=p.width_pt,
                           height_pt=p.height_pt, rotation=p.rotation,
                           is_scanned=p.is_scanned,
                           modal_font_size=p.modal_font_size,
                           column_count=max(1, len(p.columns)))
        db.add(row)
        db.flush()
        for b in p.all_blocks:
            db.add(DocumentElement(
                page_id=row.id, type=b.type.value, reading_order=b.reading_order,
                bbox=[round(v, 2) for v in b.bbox], text=b.text,
                style_json=b.style.to_dict(), column_index=b.column_index,
                ocr_confidence=b.ocr_confidence, is_protected=b.protected,
                translatable=b.translatable))
    db.flush()


def render_page(doc: Document, index: int, dpi: int | None = None) -> Path:
    dpi = dpi or settings.render_dpi_thumb
    out = settings.render_dir / f"{doc.sha256[:16]}-p{index}-{dpi}.png"
    if out.exists():
        return out
    with fitz.open(doc.path) as pdf:
        if index < 0 or index >= pdf.page_count:
            raise NotFound(f"Page {index + 1} does not exist in this document.")
        save_pixmap(pdf[index].get_pixmap(dpi=dpi), out)
    return out


def render_pdf_page(pdf_path: str | Path, index: int, dpi: int, tag: str) -> Path:
    out = settings.render_dir / f"{tag}-p{index}-{dpi}.png"
    if out.exists():
        return out
    with fitz.open(str(pdf_path)) as pdf:
        if index < 0 or index >= pdf.page_count:
            raise NotFound(f"Page {index + 1} does not exist.")
        save_pixmap(pdf[index].get_pixmap(dpi=dpi), out)
    return out


def get_document(db: Session, document_id: str) -> Document:
    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFound("No such document.", {"document_id": document_id})
    return doc
