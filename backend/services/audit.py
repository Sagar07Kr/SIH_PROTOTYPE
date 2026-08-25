"""Append-only audit log. Every state change a reviewer might ask about."""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import AuditLog


def record(db: Session, event: str, payload: dict | None = None,
           project_id: str | None = None) -> AuditLog:
    row = AuditLog(project_id=project_id, event=event, payload_json=payload or {})
    db.add(row)
    db.flush()
    return row


def timeline(db: Session, project_id: str | None = None, limit: int = 200
             ) -> list[dict]:
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if project_id:
        q = q.filter(AuditLog.project_id == project_id)
    return [{"id": r.id, "event": r.event, "payload": r.payload_json,
             "at": r.created_at.isoformat()} for r in q.all()]
