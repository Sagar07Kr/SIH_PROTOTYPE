"""Golden layout test -- the regression net for the reconstruction engine.

A committed reference render of research-paper.pdf translated to Hindi in mock
mode. If a refactor moves text, changes a fit decision, or perturbs the
substituted metrics, the masked SSIM against this reference drops and the test
fails. Without it, layout fidelity degrades silently: every other test would
still pass while the pages slowly stopped matching.

Regenerate deliberately, never casually:

    python scripts/update_golden.py

and read the reported delta before committing the new reference.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pymupdf as fitz
import pytest
from skimage.metrics import structural_similarity as ssim

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
SAMPLE = "research-paper.pdf"
TARGET = "hi"
DPI = 100
THRESHOLD = 0.995


def render_translation(sample: Path, target: str) -> list[np.ndarray]:
    """Deterministic mock translation + reconstruction, rendered to greyscale."""
    from backend.parsers.pdf_parser import PdfParser
    from backend.providers.mock import MockProvider
    from backend.reconstruction.rebuilder import Rebuilder
    from backend.translation.pipeline import TranslationPipeline
    from backend.translation.segmenter import segment_document

    parsed = PdfParser().parse(sample)
    pipeline = TranslationPipeline(MockProvider(latency_scale=0), target,
                                  source_lang=parsed.source_lang)
    result = asyncio.run(pipeline.run(segment_document(parsed)))
    rebuilt = Rebuilder().rebuild(sample, parsed, result.texts(), target)
    doc = fitz.open(stream=rebuilt.pdf_bytes, filetype="pdf")
    try:
        pages = []
        for page in doc:
            pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
            pages.append(np.frombuffer(pix.samples, dtype=np.uint8)
                         .reshape(pix.height, pix.width))
        return pages
    finally:
        doc.close()


def test_reconstruction_matches_the_golden_render(samples_dir) -> None:
    if not GOLDEN_DIR.exists() or not list(GOLDEN_DIR.glob("*.png")):
        pytest.skip("no golden render committed; run scripts/update_golden.py")
    from PIL import Image

    rendered = render_translation(samples_dir / SAMPLE, TARGET)
    references = sorted(GOLDEN_DIR.glob("page-*.png"))
    assert len(rendered) == len(references), \
        f"{len(rendered)} pages rendered, {len(references)} in the golden set"

    scores = []
    for page, (got, ref_path) in enumerate(zip(rendered, references)):
        ref = np.asarray(Image.open(ref_path).convert("L"))
        assert got.shape == ref.shape, f"page {page}: {got.shape} vs {ref.shape}"
        score = float(ssim(got, ref, data_range=255))
        scores.append(score)
        assert score >= THRESHOLD, (
            f"page {page + 1} drifted: SSIM {score:.5f} < {THRESHOLD}. "
            "Either a layout regression, or an intended change that needs "
            "scripts/update_golden.py")
    assert min(scores) >= THRESHOLD


def test_reconstruction_is_deterministic(samples_dir) -> None:
    """Two runs must agree exactly; the golden test is meaningless otherwise."""
    first = render_translation(samples_dir / SAMPLE, TARGET)
    second = render_translation(samples_dir / SAMPLE, TARGET)
    for page, (a, b) in enumerate(zip(first, second)):
        assert np.array_equal(a, b), f"page {page + 1} differed between runs"
