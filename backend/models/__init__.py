"""Persistence model (§7).

Two things here are load-bearing for the product rather than for the database:

* `TranslationSegment.fit_rung` / `final_bbox` -- without these persisted, the
  inspector cannot say "font reduced 6% to fit" and partial regeneration cannot
  know which page to re-render.
* Versions are copy-on-write. A new version shares unchanged segment rows by
  value (they are copied with `parent_segment_id` set) and never mutates an
  older version's rows, so rollback is a selection, not an edit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), default="Untitled project")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    documents: Mapped[list["Document"]] = relationship(back_populates="project")
    glossary: Mapped[list["GlossaryEntry"]] = relationship(back_populates="project")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    filename: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    source_lang: Mapped[str | None] = mapped_column(String(8))
    source_lang_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    project: Mapped[Project | None] = relationship(back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document", cascade="all, delete-orphan",
        order_by="DocumentPage.index")


class DocumentPage(Base):
    __tablename__ = "document_pages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    width_pt: Mapped[float] = mapped_column(Float)
    height_pt: Mapped[float] = mapped_column(Float)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    is_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    render_path: Mapped[str | None] = mapped_column(String(1024))
    modal_font_size: Mapped[float] = mapped_column(Float, default=0.0)
    column_count: Mapped[int] = mapped_column(Integer, default=1)
    ink_ratio: Mapped[float] = mapped_column(Float, default=0.0)

    document: Mapped[Document] = relationship(back_populates="pages")
    elements: Mapped[list["DocumentElement"]] = relationship(
        back_populates="page", cascade="all, delete-orphan",
        order_by="DocumentElement.reading_order")

    __table_args__ = (UniqueConstraint("document_id", "index"),)


class DocumentElement(Base):
    __tablename__ = "document_elements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    page_id: Mapped[str] = mapped_column(ForeignKey("document_pages.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("document_elements.id"))
    type: Mapped[str] = mapped_column(String(24))       # see schemas.ElementType
    reading_order: Mapped[int] = mapped_column(Integer, default=0)
    bbox: Mapped[list] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text, default="")
    style_json: Mapped[dict] = mapped_column(JSON, default=dict)
    column_index: Mapped[int] = mapped_column(Integer, default=0)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    translatable: Mapped[bool] = mapped_column(Boolean, default=True)

    page: Mapped[DocumentPage] = relationship(back_populates="elements")


class GlossaryEntry(Base):
    __tablename__ = "glossary"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_term: Mapped[str] = mapped_column(String(512))
    target_term: Mapped[str] = mapped_column(String(512))
    target_lang: Mapped[str] = mapped_column(String(8))
    locked: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    project: Mapped[Project] = relationship(back_populates="glossary")


class TranslationJob(Base):
    __tablename__ = "translation_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    target_lang: Mapped[str] = mapped_column(String(8))
    style: Mapped[str] = mapped_column(String(32), default="neutral")
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    error_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ocr_ms: Mapped[int] = mapped_column(Integer, default=0)
    translate_ms: Mapped[int] = mapped_column(Integer, default=0)
    reconstruct_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TranslationVersion(Base):
    __tablename__ = "translation_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("translation_jobs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("translation_versions.id"))
    pdf_path: Mapped[str | None] = mapped_column(String(1024))
    label: Mapped[str] = mapped_column(String(255), default="")
    changed_pages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    segments: Mapped[list["TranslationSegment"]] = relationship(
        back_populates="version", cascade="all, delete-orphan")


class TranslationSegment(Base):
    __tablename__ = "translation_segments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("translation_versions.id"), index=True)
    element_id: Mapped[str] = mapped_column(String(32), index=True)
    page_index: Mapped[int] = mapped_column(Integer, default=0)
    source_text: Mapped[str] = mapped_column(Text, default="")
    translated_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    fit_rung: Mapped[int | None] = mapped_column(Integer)
    applied_font: Mapped[str | None] = mapped_column(String(128))
    applied_size: Mapped[float | None] = mapped_column(Float)
    original_size: Mapped[float | None] = mapped_column(Float)
    final_bbox: Mapped[list | None] = mapped_column(JSON)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    placeholders_json: Mapped[dict] = mapped_column(JSON, default=dict)
    issues_json: Mapped[list] = mapped_column(JSON, default=list)
    font_substitution_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="OK")

    version: Mapped[TranslationVersion] = relationship(back_populates="segments")


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("translation_versions.id"), index=True)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    issues_json: Mapped[list] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    event: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


__all__ = ["Base", "Project", "Document", "DocumentPage", "DocumentElement",
           "GlossaryEntry", "TranslationJob", "TranslationVersion",
           "TranslationSegment", "ValidationResult", "AuditLog"]
