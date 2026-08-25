"""SQLAlchemy 2.0 session plumbing. SQLite by default; set DATABASE_URL for
PostgreSQL. No migrations tool -- schema is created on startup, which is
honest for a prototype and stated as such in the README."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.config import settings

_url = settings.sqlalchemy_url
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, future=True, connect_args=_connect_args)

if _url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - driver glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True)


def init_db() -> None:
    from backend.models import Base  # noqa: WPS433 - avoid circular import
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
