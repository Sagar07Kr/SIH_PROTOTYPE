"""Wire schemas. The stage machine (§8) is the important one: the frontend
renders it directly, which is why there is no generic spinner anywhere."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Stage = Literal["UPLOADED", "PARSING", "OCR", "LANG_DETECT", "LAYOUT",
                "SEGMENTING", "TRANSLATING", "RECONSTRUCTING", "VALIDATING",
                "GENERATING", "DONE", "FAILED"]
STAGES: tuple[str, ...] = ("UPLOADED", "PARSING", "OCR", "LANG_DETECT", "LAYOUT",
                           "SEGMENTING", "TRANSLATING", "RECONSTRUCTING",
                           "VALIDATING", "GENERATING", "DONE")
StageStatus = Literal["pending", "active", "done", "skipped", "failed"]


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    detail: dict | None = None


class StageState(BaseModel):
    stage: str
    status: StageStatus = "pending"
    ms: int | None = None


class JobProgress(BaseModel):
    stage: str = "UPLOADED"
    stages: list[StageState] = Field(default_factory=list)
    current_page: int | None = None
    total_pages: int = 0
    message: str | None = None
    error: ErrorBody | None = None
    segments_done: int = 0
    segments_total: int = 0
    version_id: str | None = None


class TranslateOptions(BaseModel):
    preserve_tables: bool = True
    preserve_lists: bool = True
    preserve_headers_footers: bool = True
    protect_numbers: bool = True
    ocr_scanned_pages: bool = True


class TranslateBody(BaseModel):
    document_id: str | None = None
    target_lang: str
    style: str = "neutral"
    glossary: dict[str, str] = Field(default_factory=dict)
    options: TranslateOptions = Field(default_factory=TranslateOptions)
    provider: str | None = None


class ProjectBody(BaseModel):
    name: str = "Untitled project"


class SegmentPatch(BaseModel):
    text: str


class ExportBody(BaseModel):
    format: Literal["pdf", "txt", "md", "json"] = "pdf"


class GlossaryBody(BaseModel):
    source_term: str
    target_term: str
    target_lang: str
    locked: bool = True
