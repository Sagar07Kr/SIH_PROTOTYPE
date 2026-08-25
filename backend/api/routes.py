"""HTTP surface (§8).

Two conventions worth stating:

* Errors are always `{code, message, retryable, detail?}` -- never a stack
  trace. See backend/utils/errors.py and the handler in main.py.
* `POST /api/documents/:id/analyze` returns a *completed* job object rather
  than a queued one. Parsing (including OCR) takes a couple of seconds, so it
  runs inline and the response carries the finished stage machine; the shape is
  identical to a translation job so the frontend has one renderer for both.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db import get_session
from backend.models import (Document, GlossaryEntry, Project, TranslationJob,
                            TranslationSegment, TranslationVersion)
from backend.providers import available_providers
from backend.schemas import (STAGES, ExportBody, GlossaryBody, JobProgress,
                             ProjectBody, SegmentPatch, StageState,
                             TranslateBody)
from backend.services import audit, documents, jobs, versions
from backend.utils.errors import BadRequest, NotFound
from backend.utils.langs import LANGS

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ health
@router.get("/health")
def health() -> dict:
    from backend.fonts.resolver import FontResolver
    from backend.ocr.tesseract import default_engine
    resolver = FontResolver()
    engine = default_engine()
    missing = resolver.missing_scripts(list(LANGS))
    return {
        "status": "ok" if not missing else "degraded",
        "provider": settings.ai_provider,
        "providers_available": available_providers(),
        "fonts": {"dir": str(settings.fonts_dir), "faces": len(resolver.available()),
                  "missing_scripts": missing},
        "ocr": {"available": engine.available(), "languages": engine.languages()},
        "limits": {"max_upload_mb": settings.max_upload_mb,
                   "max_pages": settings.max_pages},
        "languages": {code: {"name": l.name, "script": l.script, "rtl": l.rtl,
                             "expansion": l.expansion}
                      for code, l in LANGS.items()},
    }


@router.get("/samples")
def samples() -> dict:
    import json
    out = []
    for pdf in sorted(Path(settings.sample_dir).glob("*.pdf")):
        meta_path = pdf.with_suffix(".expected.json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                meta = {}
        out.append({"name": pdf.name, "stem": pdf.stem,
                    "size_bytes": pdf.stat().st_size,
                    "page_count": meta.get("page_count"),
                    "source_lang": meta.get("source_lang"),
                    "is_scanned": bool(meta.get("is_scanned")),
                    "notes": meta.get("notes", "")})
    return {"samples": out}


# --------------------------------------------------------------- documents
@router.post("/documents")
async def upload_document(file: UploadFile = File(...),
                          db: Session = Depends(get_session)) -> dict:
    data = await file.read()
    doc = documents.store_upload(db, file.filename or "document.pdf", data)
    audit.record(db, "document.uploaded",
                 {"document": doc.id, "filename": doc.filename,
                  "bytes": doc.size_bytes})
    db.commit()
    return _document_dict(doc)


@router.post("/documents/from-sample")
def upload_sample(name: str = Query(...),
                  db: Session = Depends(get_session)) -> dict:
    path = Path(settings.sample_dir) / name
    if not path.exists() or path.suffix.lower() != ".pdf":
        raise NotFound(f"No bundled sample named '{name}'.")
    doc = documents.store_upload(db, path.name, path.read_bytes())
    audit.record(db, "document.sample_loaded", {"document": doc.id, "name": name})
    db.commit()
    return _document_dict(doc)


@router.post("/documents/{document_id}/analyze")
def analyze(document_id: str, ocr: bool = True,
            db: Session = Depends(get_session)) -> dict:
    doc = documents.get_document(db, document_id)
    t0 = time.perf_counter()
    parsed = documents.parsed_for(doc, ocr=ocr)
    documents.persist_analysis(db, doc, parsed)
    audit.record(db, "document.analyzed",
                 {"document": doc.id, "pages": parsed.page_count,
                  "source_lang": parsed.source_lang,
                  "scanned": parsed.is_scanned})
    db.commit()
    ms = int((time.perf_counter() - t0) * 1000)
    stages = []
    for s in STAGES:
        if s in ("PARSING", "LANG_DETECT", "LAYOUT"):
            stages.append(StageState(stage=s, status="done", ms=ms // 3))
        elif s == "OCR":
            stages.append(StageState(stage=s,
                                     status="done" if parsed.is_scanned else "skipped",
                                     ms=ms // 3 if parsed.is_scanned else None))
        elif s == "DONE":
            stages.append(StageState(stage=s, status="done"))
        else:
            stages.append(StageState(stage=s, status="pending"))
    progress = JobProgress(stage="DONE", stages=stages,
                           total_pages=parsed.page_count,
                           message=f"Analyzed in {ms}ms")
    return {"document": _document_dict(doc), "progress": progress.model_dump(),
            "analysis": parsed.to_dict()}


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_session)) -> dict:
    doc = documents.get_document(db, document_id)
    parsed = documents.parsed_for(doc)
    return {"document": _document_dict(doc), "analysis": parsed.to_dict()}


@router.get("/documents/{document_id}/pdf")
def document_pdf(document_id: str, db: Session = Depends(get_session)):
    doc = documents.get_document(db, document_id)
    if not Path(doc.path).exists():
        raise NotFound("The uploaded file is no longer on disk.")
    return FileResponse(doc.path, media_type="application/pdf",
                        filename=doc.filename)


@router.get("/documents/{document_id}/pages/{index}/render")
def render_page(document_id: str, index: int, dpi: int = 110,
                db: Session = Depends(get_session)):
    doc = documents.get_document(db, document_id)
    path = documents.render_page(doc, index, dpi=max(36, min(300, dpi)))
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------- projects
@router.post("/projects")
def create_project(body: ProjectBody,
                   db: Session = Depends(get_session)) -> dict:
    project = Project(name=body.name)
    db.add(project)
    db.flush()
    audit.record(db, "project.created", {"project": project.id}, project.id)
    db.commit()
    return {"id": project.id, "name": project.name,
            "created_at": project.created_at.isoformat()}


@router.get("/projects")
def list_projects(db: Session = Depends(get_session)) -> dict:
    rows = db.query(Project).order_by(Project.created_at.desc()).limit(50).all()
    return {"projects": [{"id": p.id, "name": p.name,
                          "created_at": p.created_at.isoformat()} for p in rows]}


@router.post("/projects/{project_id}/translate")
async def translate(project_id: str, body: TranslateBody, request: Request,
                    db: Session = Depends(get_session)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFound("No such project.")
    if body.target_lang not in LANGS:
        raise BadRequest(f"Unsupported target language '{body.target_lang}'.",
                         {"supported": sorted(LANGS)})
    if not body.document_id:
        raise BadRequest("A document_id is required.")
    doc = documents.get_document(db, body.document_id)
    if doc.project_id != project.id:
        doc.project_id = project.id
        db.add(doc)
    job = jobs.create_job(db, project, doc, body)
    db.commit()
    jobs.start(job.id, body.glossary)
    return {"job_id": job.id, "status": job.status}


@router.get("/projects/{project_id}/versions")
def project_versions(project_id: str, db: Session = Depends(get_session)) -> dict:
    rows = versions.versions_for_project(db, project_id)
    return {"versions": [_version_dict(db, v) for v in rows]}


@router.get("/projects/{project_id}/audit")
def project_audit(project_id: str, db: Session = Depends(get_session)) -> dict:
    return {"events": audit.timeline(db, project_id)}


@router.get("/projects/{project_id}/glossary")
def get_glossary(project_id: str, db: Session = Depends(get_session)) -> dict:
    rows = db.query(GlossaryEntry).filter(
        GlossaryEntry.project_id == project_id).all()
    return {"entries": [{"id": r.id, "source_term": r.source_term,
                         "target_term": r.target_term,
                         "target_lang": r.target_lang, "locked": r.locked}
                        for r in rows]}


@router.post("/projects/{project_id}/glossary")
def add_glossary(project_id: str, body: GlossaryBody,
                 db: Session = Depends(get_session)) -> dict:
    if db.get(Project, project_id) is None:
        raise NotFound("No such project.")
    row = GlossaryEntry(project_id=project_id, source_term=body.source_term,
                        target_term=body.target_term,
                        target_lang=body.target_lang, locked=body.locked)
    db.add(row)
    audit.record(db, "glossary.added",
                 {"term": body.source_term, "target": body.target_term},
                 project_id)
    db.commit()
    return {"id": row.id}


# -------------------------------------------------------------------- jobs
@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_session)) -> dict:
    job = db.get(TranslationJob, job_id)
    if job is None:
        raise NotFound("No such job.")
    return _job_dict(job)


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, db: Session = Depends(get_session)) -> dict:
    job = db.get(TranslationJob, job_id)
    if job is None:
        raise NotFound("No such job.")
    cancelled = jobs.cancel(job_id)
    return {"cancelled": cancelled, "status": job.status}


# ---------------------------------------------------------------- versions
@router.get("/versions/{version_id}")
def get_version(version_id: str, db: Session = Depends(get_session)) -> dict:
    v = versions.get_version(db, version_id)
    rows = versions.segments_of(db, version_id)
    job, doc, parsed = versions.context_for(db, v)
    types = {b.id: b.type.value for p in parsed.pages for b in p.all_blocks}
    markers = {b.id: b.list_marker for p in parsed.pages for b in p.all_blocks}
    return {
        "version": _version_dict(db, v),
        "job": _job_dict(job),
        "document": _document_dict(doc),
        "segments": [{
            "id": r.id, "element_id": r.element_id, "page": r.page_index,
            "type": types.get(r.element_id, "paragraph"),
            "list_marker": markers.get(r.element_id),
            "source": r.source_text, "target": r.translated_text,
            "confidence": r.confidence, "fit_rung": r.fit_rung,
            "applied_font": r.applied_font, "applied_size": r.applied_size,
            "original_size": r.original_size, "bbox": r.final_bbox,
            "edited": r.edited_by_user, "status": r.status,
            "issues": r.issues_json, "placeholders": r.placeholders_json,
            "font_substitution": r.font_substitution_json} for r in rows],
        "validation": versions.validation_for(db, version_id),
    }


@router.get("/versions/{version_id}/pdf")
def version_pdf(version_id: str, db: Session = Depends(get_session)):
    v = versions.get_version(db, version_id)
    if not v.pdf_path or not Path(v.pdf_path).exists():
        raise NotFound("This version has no PDF on disk.")
    return FileResponse(v.pdf_path, media_type="application/pdf",
                        filename=f"translated-v{v.number}.pdf")


@router.get("/versions/{version_id}/pages/{index}/render")
def version_page_render(version_id: str, index: int, dpi: int = 110,
                        db: Session = Depends(get_session)):
    v = versions.get_version(db, version_id)
    if not v.pdf_path:
        raise NotFound("This version has no PDF.")
    path = documents.render_pdf_page(v.pdf_path, index, max(36, min(300, dpi)),
                                     tag=f"v{v.id[:12]}")
    return FileResponse(path, media_type="image/png")


@router.post("/versions/{version_id}/validate")
def validate_version(version_id: str, db: Session = Depends(get_session)) -> dict:
    payload = jobs.validate_version(db, version_id)
    db.commit()
    return payload


@router.get("/versions/{version_id}/diff/{other_id}")
def diff_versions(version_id: str, other_id: str,
                  db: Session = Depends(get_session)) -> dict:
    return versions.diff(db, version_id, other_id)


@router.post("/versions/{version_id}/export")
def export_version(version_id: str, body: ExportBody,
                   db: Session = Depends(get_session)):
    v = versions.get_version(db, version_id)
    path, media = versions.export(db, v, body.format)
    db.commit()
    return FileResponse(path, media_type=media, filename=path.name)


@router.post("/versions/{version_id}/pages/{index}/regenerate")
def regenerate_page(version_id: str, index: int,
                    db: Session = Depends(get_session)) -> dict:
    v = versions.get_version(db, version_id)
    nv = versions.regenerate(db, v, page_index=index)
    payload = jobs.validate_version(db, nv.id)
    db.commit()
    return {"version": _version_dict(db, nv), "validation": payload}


# ---------------------------------------------------------------- segments
@router.patch("/segments/{segment_id}")
def edit_segment(segment_id: str, body: SegmentPatch,
                 db: Session = Depends(get_session)) -> dict:
    row = db.get(TranslationSegment, segment_id)
    if row is None:
        raise NotFound("No such segment.")
    v = versions.get_version(db, row.version_id)
    nv = versions.regenerate(db, v, segment_id=row.element_id,
                             new_text=body.text, label="manual edit")
    payload = jobs.validate_version(db, nv.id)
    db.commit()
    return {"version": _version_dict(db, nv), "validation": payload}


@router.post("/segments/{segment_id}/regenerate")
def regenerate_segment(segment_id: str, db: Session = Depends(get_session)) -> dict:
    row = db.get(TranslationSegment, segment_id)
    if row is None:
        raise NotFound("No such segment.")
    v = versions.get_version(db, row.version_id)
    nv = versions.regenerate(db, v, segment_id=row.element_id,
                             new_text=row.translated_text,
                             label="segment regenerated")
    payload = jobs.validate_version(db, nv.id)
    db.commit()
    return {"version": _version_dict(db, nv), "validation": payload}


# ------------------------------------------------------------------ shapes
def _document_dict(doc: Document) -> dict:
    return {"id": doc.id, "filename": doc.filename, "sha256": doc.sha256,
            "size_bytes": doc.size_bytes, "page_count": doc.page_count,
            "source_lang": doc.source_lang,
            "source_lang_confidence": doc.source_lang_confidence,
            "is_scanned": doc.is_scanned, "status": doc.status,
            "project_id": doc.project_id,
            "created_at": doc.created_at.isoformat()}


def _job_dict(job: TranslationJob) -> dict:
    return {"id": job.id, "project_id": job.project_id,
            "document_id": job.document_id, "target_lang": job.target_lang,
            "style": job.style, "status": job.status, "provider": job.provider,
            "progress": job.progress_json or {},
            "options": job.options_json or {},
            "tokens": {"input": job.input_tokens, "output": job.output_tokens},
            "timings_ms": {"ocr": job.ocr_ms, "translate": job.translate_ms,
                           "reconstruct": job.reconstruct_ms},
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": (job.finished_at.isoformat()
                            if job.finished_at else None)}


def _version_dict(db: Session, v: TranslationVersion) -> dict:
    job = db.get(TranslationJob, v.job_id)
    return {"id": v.id, "job_id": v.job_id, "number": v.number,
            "parent_version_id": v.parent_version_id, "label": v.label,
            "changed_pages": v.changed_pages or [],
            "target_lang": job.target_lang if job else None,
            "document_id": job.document_id if job else None,
            "has_pdf": bool(v.pdf_path),
            "created_at": v.created_at.isoformat()}
