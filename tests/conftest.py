"""Test fixtures.

Every test runs offline: the provider is the mock, its simulated latency is
switched off, and the data directory is a tmp path so a test run never touches
a developer's working state.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("MOCK_LATENCY_SCALE", "0")
os.environ.setdefault("DATA_DIR", str(Path(os.environ.get("TMPDIR", "/tmp"))
                                     / "layoutloom-tests"))

import pytest  # noqa: E402

SAMPLES = ROOT / "sample-data"


@pytest.fixture(scope="session", autouse=True)
def _samples_exist():
    missing = [n for n in ("govt-notice.pdf", "research-paper.pdf",
                           "technical-report.pdf", "scanned-invoice.pdf")
               if not (SAMPLES / n).exists()]
    if missing:
        pytest.skip(f"sample data missing: {missing}; run make samples")


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES


@pytest.fixture(scope="session")
def parsed_samples() -> dict:
    """Parse each sample once: it is the most expensive fixture in the suite."""
    from backend.parsers.pdf_parser import PdfParser
    parser = PdfParser()
    out = {}
    for pdf in sorted(SAMPLES.glob("*.pdf")):
        out[pdf.stem] = parser.parse(pdf)
    return out


@pytest.fixture(scope="session")
def expected() -> dict:
    import json
    out = {}
    for path in sorted(SAMPLES.glob("*.expected.json")):
        out[path.name.replace(".expected.json", "")] = json.loads(
            path.read_text(encoding="utf-8"))
    return out


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c
