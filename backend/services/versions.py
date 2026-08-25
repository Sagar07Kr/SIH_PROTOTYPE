"""Versions: copy-on-write, partial regeneration, diff, export (§7, §8).

A new version copies every segment row by value and never mutates an older
one, so rollback is a selection rather than an edit. Regeneration rebuilds only
the pages that actually changed and splices them into the previous version's
PDF, which is what makes "edit one paragraph" cost one page.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import (Document, TranslationJob, TranslationSegment,
                            TranslationVersion, ValidationResult)
from backend.parsers.model import ParsedDocument
from backend.reconstruction.rebuilder import Rebuilder
from backend.services import audit, documents
from backend.utils.errors import BadRequest, NotFound
from backend.utils.io import write_bytes


def get_version(db: Session, version_id: str) -> TranslationVersion:
    v = db.get(TranslationVersion, version_id)
    if v is None:
        raise NotFound("No such version.", {"version_id": version_id})
    return v


def segments_of(db: Session, version_id: str) -> list[TranslationSegment]:
    return (db.query(TranslationSegment)
            .filter(TranslationSegment.version_id == version_id)
            .order_by(TranslationSegment.page_index).all())


def versions_for_project(db: Session, project_id: str) -> list[TranslationVersion]:
    return (db.query(TranslationVersion)
            .join(TranslationJob, TranslationJob.id == TranslationVersion.job_id)
            .filter(TranslationJob.project_id == project_id)
            .order_by(TranslationVersion.created_at.desc()).all())


def context_for(db: Session, version: TranslationVersion
                ) -> tuple[TranslationJob, Document, ParsedDocument]:
    job = db.get(TranslationJob, version.job_id)
    if job is None:
        raise NotFound("The job for this version is gone.")
    doc = documents.get_document(db, job.document_id)
    return job, doc, documents.parsed_for(doc)


def regenerate(db: Session, version: TranslationVersion, *,
               segment_id: str | None = None, page_index: int | None = None,
               new_text: str | None = None, label: str = "") -> TranslationVersion:
    """Create version N+1. Exactly one of segment_id / page_index is required."""
    if (segment_id is None) == (page_index is None):
        raise BadRequest("Regeneration needs either a segment or a page.")
    job, doc, parsed = context_for(db, version)
    rows = segments_of(db, version.id)
    if not rows:
        raise BadRequest("This version has no segments to regenerate.")

    target_pages: set[int]
    edited: TranslationSegment | None = None
    if segment_id is not None:
        edited = next((r for r in rows if r.element_id == segment_id), None)
        if edited is None:
            raise NotFound("No such segment in this version.",
                           {"segment_id": segment_id})
        target_pages = {edited.page_index}
    else:
        target_pages = {int(page_index)}
        if not any(r.page_index in target_pages for r in rows):
            raise NotFound(f"Page {page_index} has no segments.")

    nxt = (db.query(TranslationVersion)
           .filter(TranslationVersion.job_id == job.id).count()) + 1
    new_version = TranslationVersion(
        job_id=job.id, number=nxt, parent_version_id=version.id,
        label=label or ("segment edit" if segment_id else "page regenerated"),
        changed_pages=sorted(target_pages))
    db.add(new_version)
    db.flush()

    # copy every row by value; apply the edit to its own copy only
    copies: dict[str, TranslationSegment] = {}
    for r in rows:
        text = r.translated_text
        edited_flag = r.edited_by_user
        if edited is not None and r.id == edited.id and new_text is not None:
            text = new_text
            edited_flag = True
        copy = TranslationSegment(
            version_id=new_version.id, element_id=r.element_id,
            page_index=r.page_index, source_text=r.source_text,
            translated_text=text, confidence=r.confidence, fit_rung=r.fit_rung,
            applied_font=r.applied_font, applied_size=r.applied_size,
            original_size=r.original_size, final_bbox=r.final_bbox,
            edited_by_user=edited_flag, placeholders_json=r.placeholders_json,
            issues_json=r.issues_json,
            font_substitution_json=r.font_substitution_json, status=r.status)
        db.add(copy)
        copies[r.element_id] = copy
    db.flush()

    translations = {r.element_id: r.translated_text for r in copies.values()
                    if r.translated_text.strip()}
    rebuilt = Rebuilder().rebuild(
        Path(doc.path), parsed, translations, job.target_lang,
        pages=target_pages, base_pdf=Path(version.pdf_path))
    out = settings.output_dir / f"{new_version.id}.pdf"
    write_bytes(out, rebuilt.pdf_bytes)
    new_version.pdf_path = str(out)
    db.add(new_version)

    # refresh placement facts for the rebuilt pages only
    for pl in rebuilt.placements:
        row = copies.get(pl.block_id)
        if row is None:
            continue
        row.fit_rung = pl.rung
        row.applied_size = pl.size
        row.applied_font = pl.font.family if pl.font else None
        row.final_bbox = [round(v, 2) for v in pl.rect]
        row.issues_json = pl.issues
        row.font_substitution_json = (pl.font.to_dict()
                                      if pl.font and pl.font.substituted else None)
        db.add(row)
    db.flush()
    audit.record(db, "version.regenerated",
                 {"from": version.id, "to": new_version.id,
                  "pages": sorted(target_pages),
                  "segment": segment_id, "rungs": rebuilt.rung_histogram},
                 project_id=job.project_id)
    return new_version


def diff(db: Session, a_id: str, b_id: str) -> dict:
    """What changed between two versions: text, fit rung, geometry, pages."""
    a, b = get_version(db, a_id), get_version(db, b_id)
    rows_a = {r.element_id: r for r in segments_of(db, a.id)}
    rows_b = {r.element_id: r for r in segments_of(db, b.id)}
    changed_blocks = []
    for key, rb in rows_b.items():
        ra = rows_a.get(key)
        if ra is None:
            changed_blocks.append({"element_id": key, "kind": "added",
                                   "page": rb.page_index})
            continue
        text_changed = ra.translated_text != rb.translated_text
        rung_changed = ra.fit_rung != rb.fit_rung
        moved = _bbox_delta(ra.final_bbox, rb.final_bbox)
        if text_changed or rung_changed or moved > 0.5:
            changed_blocks.append({
                "element_id": key, "kind": "changed", "page": rb.page_index,
                "text_changed": text_changed,
                "before": ra.translated_text, "after": rb.translated_text,
                "rung_before": ra.fit_rung, "rung_after": rb.fit_rung,
                "size_before": ra.applied_size, "size_after": rb.applied_size,
                "bbox_delta_pt": round(moved, 2)})
    removed = [{"element_id": k, "kind": "removed", "page": r.page_index}
               for k, r in rows_a.items() if k not in rows_b]
    pages = sorted({c["page"] for c in changed_blocks + removed})
    return {"from": a.id, "to": b.id, "from_number": a.number,
            "to_number": b.number, "changed_pages": pages,
            "changed_blocks": changed_blocks + removed,
            "layout_deltas": {"blocks_moved": sum(
                1 for c in changed_blocks if c.get("bbox_delta_pt", 0) > 0.5),
                "rungs_changed": sum(1 for c in changed_blocks
                                     if c.get("rung_before") != c.get("rung_after"))}}


def _bbox_delta(a, b) -> float:
    if not a or not b:
        return 0.0
    return max(abs(x - y) for x, y in zip(a, b))


def export(db: Session, version: TranslationVersion, fmt: str) -> tuple[Path, str]:
    """Returns (path, media type). PDF is the version itself; the text formats
    are generated from the segment rows in reading order."""
    if fmt == "pdf":
        if not version.pdf_path:
            raise NotFound("This version has no PDF.")
        return Path(version.pdf_path), "application/pdf"
    job, doc, parsed = context_for(db, version)
    rows = segments_of(db, version.id)
    order = {b.id: (b.source_page, b.reading_order)
             for p in parsed.pages for b in p.all_blocks}
    rows.sort(key=lambda r: order.get(r.element_id, (r.page_index, 0)))
    types = {b.id: b.type.value for p in parsed.pages for b in p.all_blocks}
    out_dir = settings.output_dir
    if fmt == "txt":
        body = "\n\n".join(r.translated_text for r in rows if r.translated_text)
        path = out_dir / f"{version.id}.txt"
        write_bytes(path, body.encode("utf-8"))
        return path, "text/plain; charset=utf-8"
    if fmt == "md":
        lines: list[str] = [f"# {doc.filename} — {job.target_lang}", ""]
        page = -1
        for r in rows:
            if r.page_index != page:
                page = r.page_index
                lines += [f"", f"<!-- page {page + 1} -->", ""]
            kind = types.get(r.element_id, "paragraph")
            text = r.translated_text
            if not text:
                continue
            if kind == "heading":
                lines.append(f"## {text}")
            elif kind in ("list_item",):
                lines.append(f"- {text}")
            elif kind == "code":
                lines += ["```", text, "```"]
            elif kind == "table_cell":
                lines.append(f"| {text} |")
            else:
                lines.append(text)
            lines.append("")
        path = out_dir / f"{version.id}.md"
        write_bytes(path, "\n".join(lines).encode("utf-8"))
        return path, "text/markdown; charset=utf-8"
    if fmt == "json":
        payload = {
            "document": doc.filename, "target_lang": job.target_lang,
            "version": version.number,
            "segments": [{
                "element_id": r.element_id, "page": r.page_index,
                "type": types.get(r.element_id), "source": r.source_text,
                "target": r.translated_text, "confidence": r.confidence,
                "fit_rung": r.fit_rung, "applied_size": r.applied_size,
                "original_size": r.original_size, "bbox": r.final_bbox,
                "status": r.status, "issues": r.issues_json,
                "font_substitution": r.font_substitution_json}
                for r in rows]}
        path = out_dir / f"{version.id}.json"
        write_bytes(path, json.dumps(payload, indent=2,
                                     ensure_ascii=False).encode("utf-8"))
        return path, "application/json"
    raise BadRequest(f"Unsupported export format '{fmt}'.")


def validation_for(db: Session, version_id: str) -> dict | None:
    row = (db.query(ValidationResult)
           .filter(ValidationResult.version_id == version_id).one_or_none())
    if row is None:
        return None
    return {"version_id": version_id, **(row.metrics_json or {}),
            "issues": row.issues_json or [],
            "computed_at": row.computed_at.isoformat()}
