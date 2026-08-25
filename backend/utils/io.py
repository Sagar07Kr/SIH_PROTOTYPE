"""File writing helpers.

Everything that emits a PDF or PNG goes through here. `Document.save()` unlinks
the destination first, which fails on read-only-ish mounts and on Windows when
a viewer holds the file open; serialising to bytes and truncating in place is
both safer and atomic enough for our purposes.
"""
from __future__ import annotations

from pathlib import Path


def write_bytes(path: str | Path, data: bytes) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def save_pdf(doc, path: str | Path, **opts) -> Path:
    """Serialise a PyMuPDF document and write it without unlinking."""
    opts.setdefault("garbage", 3)
    opts.setdefault("deflate", True)
    return write_bytes(path, doc.tobytes(**opts))


def save_pixmap(pix, path: str | Path, fmt: str = "png") -> Path:
    return write_bytes(path, pix.tobytes(fmt))
