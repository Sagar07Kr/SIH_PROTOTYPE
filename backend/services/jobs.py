"""Job orchestration: parse -> detect -> segment -> translate -> reconstruct ->
validate -> generate.

The worker is an in-process asyncio task and the client polls `GET /api/jobs/:id`
(no Celery, no Redis -- §3). Progress is a typed stage machine, not a
percentage, because that is what the UI renders: a reader can see that OCR ran,
that translation is on page 12 of 38, and that validation has not started.

Partial success is a first-class outcome. A segment that fails leaves the source
text in place and is recorded as such; the job still produces a valid PDF and
the failures are visible in the validation report rather than silently shipped.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.db import session_scope
from backend.models import (Document, GlossaryEntry, Project, TranslationJob,
                            TranslationSegment, TranslationVersion,
                            ValidationResult)
from backend.parsers.model import ParsedDocument
from backend.providers import get_provider
from backend.reconstruction.rebuilder import Rebuilder
from backend.schemas import STAGES, JobProgress, StageState, TranslateBody
from backend.services import audit, documents
from backend.translation.pipeline import TranslationPipeline
from backend.translation.segmenter import detect_domain, segment_document
from backend.utils.errors import AppError, NotFound
from backend.utils.io import write_bytes
from backend.validators.metrics import validate

_TASKS: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------- progress
@dataclass
class ProgressWriter:
    job_id: str
    total_pages: int = 0
    _state: dict | None = None

    def init(self, total_pages: int) -> None:
        self.total_pages = total_pages
        self._state = JobProgress(
            stage="UPLOADED", total_pages=total_pages,
            stages=[StageState(stage=s) for s in STAGES]).model_dump()
        self._flush()

    def stage(self, name: str, *, message: str | None = None,
              status: str = "active", ms: int | None = None) -> None:
        st = self._state or {}
        st["stage"] = name
        st["message"] = message
        for entry in st.get("stages", []):
            if entry["stage"] == name:
                entry["status"] = status
                if ms is not None:
                    entry["ms"] = ms
            elif entry["status"] == "active":
                entry["status"] = "done"
        self._state = st
        self._flush()

    def finish_stage(self, name: str, ms: int) -> None:
        for entry in (self._state or {}).get("stages", []):
            if entry["stage"] == name:
                entry["status"] = "done"
                entry["ms"] = ms
        self._flush()

    def skip(self, name: str, message: str | None = None) -> None:
        for entry in (self._state or {}).get("stages", []):
            if entry["stage"] == name:
                entry["status"] = "skipped"
        if message:
            self._state["message"] = message
        self._flush()

    def page(self, index: int, message: str | None = None) -> None:
        st = self._state or {}
        st["current_page"] = index
        if message:
            st["message"] = message
        self._flush()

    def segments(self, done: int, total: int) -> None:
        st = self._state or {}
        st["segments_done"] = done
        st["segments_total"] = total
        st["message"] = f"Translating segment {done} of {total}"
        self._flush()

    def fail(self, err: AppError) -> None:
        st = self._state or {}
        st["stage"] = "FAILED"
        st["error"] = {"code": err.code, "message": err.message,
                       "retryable": err.retryable, "detail": err.detail}
        for entry in st.get("stages", []):
            if entry["status"] == "active":
                entry["status"] = "failed"
        self._flush(status="failed")

    def done(self, version_id: str) -> None:
        st = self._state or {}
        st["stage"] = "DONE"
        st["version_id"] = version_id
        st["message"] = "Complete"
        for entry in st.get("stages", []):
            if entry["stage"] == "DONE":
                entry["status"] = "done"
            elif entry["status"] == "active":
                entry["status"] = "done"
        self._flush(status="done")

    def _flush(self, status: str | None = None) -> None:
        with session_scope() as db:
            job = db.get(TranslationJob, self.job_id)
            if job is None:
                return
            job.progress_json = self._state or {}
            if status:
                job.status = status
                if status in ("done", "failed"):
                    job.finished_at = datetime.now(timezone.utc)
            db.add(job)


# ---------------------------------------------------------------- public API
def create_job(db: Session, project: Project, doc: Document,
               body: TranslateBody) -> TranslationJob:
    job = TranslationJob(
        project_id=project.id, document_id=doc.id, target_lang=body.target_lang,
        style=body.style, options_json=body.options.model_dump(),
        provider=(body.provider or settings.ai_provider), status="queued",
        progress_json=JobProgress(
            stage="UPLOADED", total_pages=doc.page_count,
            stages=[StageState(stage=s) for s in STAGES]).model_dump())
    db.add(job)
    db.flush()
    audit.record(db, "job.created", {"job_id": job.id, "target": body.target_lang,
                                     "document": doc.filename},
                 project_id=project.id)
    return job


def start(job_id: str, glossary: dict[str, str] | None = None) -> None:
    """Fire the worker.

    Must be called from async context (the route is `async def`): a sync route
    runs in a worker thread where there is no event loop to attach the task to.
    """
    loop = asyncio.get_running_loop()
    task = loop.create_task(run_job(job_id, glossary or {}))
    _TASKS[job_id] = task
    task.add_done_callback(lambda t: _TASKS.pop(job_id, None))


def cancel(job_id: str) -> bool:
    task = _TASKS.get(job_id)
    if task and not task.done():
        task.cancel()
        with session_scope() as db:
            job = db.get(TranslationJob, job_id)
            if job:
                job.status = "cancelled"
                db.add(job)
        return True
    return False


async def run_job(job_id: str, glossary: dict[str, str]) -> None:
    progress: ProgressWriter | None = None
    try:
        with session_scope() as db:
            job = db.get(TranslationJob, job_id)
            if job is None:
                raise NotFound("Job disappeared before it started.")
            doc = documents.get_document(db, job.document_id)
            target = job.target_lang
            style = job.style
            provider_name = job.provider
            options = dict(job.options_json or {})
            project_id = job.project_id
            doc_path = Path(doc.path)
            doc_id = doc.id
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            db.add(job)
            user_glossary = dict(glossary)
            for row in db.query(GlossaryEntry).filter(
                    GlossaryEntry.project_id == project_id,
                    GlossaryEntry.target_lang == target).all():
                user_glossary.setdefault(row.source_term, row.target_term)

        progress = ProgressWriter(job_id)
        progress.init(0)

        # -- PARSING (+ OCR inside the parser, reported separately)
        t0 = time.perf_counter()
        progress.stage("PARSING", message="Reading the document")
        with session_scope() as db:
            doc = documents.get_document(db, doc_id)
            parsed = documents.parsed_for(
                doc, ocr=bool(options.get("ocr_scanned_pages", True)))
            documents.persist_analysis(db, doc, parsed)
            ocr_ms = 0
        progress.total_pages = parsed.page_count
        progress.init(parsed.page_count)
        progress.finish_stage("PARSING", int((time.perf_counter() - t0) * 1000))

        if parsed.is_scanned:
            scanned = [p.index for p in parsed.pages if p.is_scanned]
            progress.stage("OCR", status="done",
                           message=f"Read {len(scanned)} scanned page(s) with "
                                   "Tesseract")
        else:
            progress.skip("OCR", "No scanned pages")

        progress.stage("LANG_DETECT", status="done",
                       message=f"Source detected as {parsed.source_lang} "
                               f"({parsed.source_lang_confidence:.0%})")
        progress.stage("LAYOUT", status="done",
                       message=f"{sum(len(p.all_blocks) for p in parsed.pages)} "
                               "blocks in reading order")

        # -- SEGMENTING
        progress.stage("SEGMENTING")
        segments = segment_document(parsed)
        domain = detect_domain(parsed)
        progress.stage("SEGMENTING", status="done",
                       message=f"{len(segments)} translation units ({domain})")

        # -- TRANSLATING
        progress.stage("TRANSLATING")
        provider = get_provider(provider_name)
        pipeline = TranslationPipeline(provider, target,
                                      source_lang=parsed.source_lang,
                                      style=style, domain=domain,
                                      user_glossary=user_glossary)
        result = await pipeline.run(
            segments, on_progress=lambda d, t: progress.segments(d, t))
        progress.finish_stage("TRANSLATING", result.ms)

        # -- RECONSTRUCTING
        progress.stage("RECONSTRUCTING", message="Placing translated text")
        rebuilt = Rebuilder().rebuild(doc_path, parsed, result.texts(), target)
        progress.finish_stage("RECONSTRUCTING", rebuilt.ms)

        # -- GENERATING (write the PDF, create the version)
        progress.stage("GENERATING")
        with session_scope() as db:
            version = TranslationVersion(job_id=job_id, number=1, label="initial",
                                         changed_pages=rebuilt.pages_touched)
            db.add(version)
            db.flush()
            out_path = settings.output_dir / f"{version.id}.pdf"
            write_bytes(out_path, rebuilt.pdf_bytes)
            version.pdf_path = str(out_path)
            db.add(version)
            _write_segments(db, version, parsed, result, rebuilt)
            job = db.get(TranslationJob, job_id)
            job.input_tokens = result.input_tokens
            job.output_tokens = result.output_tokens
            job.translate_ms = result.ms
            job.reconstruct_ms = rebuilt.ms
            job.ocr_ms = ocr_ms
            db.add(job)
            audit.record(db, "version.created",
                         {"version": version.id, "number": 1,
                          "rungs": rebuilt.rung_histogram,
                          "overflow": rebuilt.overflow_count()},
                         project_id=project_id)
            version_id = version.id
        progress.finish_stage("GENERATING", 0)

        # -- VALIDATING
        progress.stage("VALIDATING", message="Measuring layout fidelity")
        t_val = time.perf_counter()
        with session_scope() as db:
            report = validate_version(db, version_id)
        progress.finish_stage("VALIDATING", int((time.perf_counter() - t_val) * 1000))
        _ = report
        progress.done(version_id)

    except asyncio.CancelledError:
        if progress:
            progress.fail(AppError("The job was cancelled.", code="CANCELLED"))
        raise
    except AppError as exc:
        if progress:
            progress.fail(exc)
    except Exception as exc:                       # pragma: no cover - safety net
        if progress:
            progress.fail(AppError(f"Unexpected failure: {exc}",
                                   code="INTERNAL"))


def _write_segments(db: Session, version: TranslationVersion,
                    parsed: ParsedDocument, result, rebuilt) -> None:
    placements = {p.block_id: p for p in rebuilt.placements}
    index = {b.id: b for p in parsed.pages for b in p.all_blocks}
    for ts in result.segments:
        block = index.get(ts.id)
        pl = placements.get(ts.id)
        db.add(TranslationSegment(
            version_id=version.id, element_id=ts.id,
            page_index=block.source_page if block else 0,
            source_text=ts.source, translated_text=ts.target,
            confidence=ts.confidence, fit_rung=pl.rung if pl else None,
            applied_font=pl.font.family if (pl and pl.font) else None,
            applied_size=pl.size if pl else None,
            original_size=block.style.size if block else None,
            final_bbox=[round(v, 2) for v in pl.rect] if pl else
            ([round(v, 2) for v in block.bbox] if block else None),
            placeholders_json=ts.placeholders,
            issues_json=(ts.issues + (pl.issues if pl else [])),
            font_substitution_json=(pl.font.to_dict()
                                    if pl and pl.font and pl.font.substituted
                                    else None),
            status=ts.status))
    db.flush()


def validate_version(db: Session, version_id: str) -> dict:
    """Render both PDFs and measure. Stored so the UI can show derivations."""
    version = db.get(TranslationVersion, version_id)
    if version is None or not version.pdf_path:
        raise NotFound("No such version, or it has no PDF yet.")
    job = db.get(TranslationJob, version.job_id)
    doc = documents.get_document(db, job.document_id)
    parsed = documents.parsed_for(doc)
    segments = [{
        "segment_id": s.element_id, "rung": s.fit_rung, "rect": s.final_bbox,
        "confidence": s.confidence, "translated": s.translated_text,
        "translatable": s.status != "VERBATIM", "page_index": s.page_index,
        "status": s.status, "concession": None,
        "font_substitution": s.font_substitution_json,
    } for s in db.query(TranslationSegment).filter(
        TranslationSegment.version_id == version_id).all()]
    report = validate(Path(doc.path), Path(version.pdf_path), parsed, segments)
    row = db.query(ValidationResult).filter(
        ValidationResult.version_id == version_id).one_or_none()
    payload = report.to_dict()
    if row is None:
        row = ValidationResult(version_id=version_id)
    row.metrics_json = {"metrics": payload["metrics"], "scores": payload["scores"],
                        "per_page": payload["per_page"]}
    row.issues_json = payload["issues"]
    db.add(row)
    db.flush()
    return payload
