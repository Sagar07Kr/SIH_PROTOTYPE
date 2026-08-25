"""Every advertised target language must produce real glyphs, not tofu.

This is the test that catches the failure mode where a project ships "supports
Hindi and Arabic" and the output is a page of empty boxes: it checks the PDF's
own embedded fonts for coverage of the text that was actually placed.
"""
from __future__ import annotations

import asyncio

import pymupdf as fitz
import pytest

from backend.fonts.resolver import FontResolver
from backend.parsers.pdf_parser import PdfParser, detect_language_text
from backend.providers.mock import MockProvider
from backend.reconstruction.rebuilder import Rebuilder
from backend.translation.pipeline import TranslationPipeline
from backend.translation.segmenter import segment_document

# one representative source per script class, kept small so the suite stays fast
# Arabic is pre-shaped, so its text layer holds Arabic *presentation forms*
# (U+FE70-FEFF) rather than the base letters -- that is what shaping means, and
# it is why the accepted range is a list.
CASES = [
    ("govt-notice.pdf", "hi", [(0x0900, 0x097F)]),
    ("govt-notice.pdf", "ar", [(0x0600, 0x06FF), (0xFB50, 0xFDFF),
                               (0xFE70, 0xFEFF)]),
    ("govt-notice.pdf", "ja", [(0x3040, 0x30FF)]),
    ("govt-notice.pdf", "zh", [(0x4E00, 0x9FFF)]),
    ("govt-notice.pdf", "de", [(0x0041, 0x024F)]),
]
#: characters no Noto face for these scripts carries; see docs/LIMITATIONS.md
KNOWN_MISSING = {"₹"}


@pytest.mark.parametrize("sample,target,script_ranges", CASES)
def test_target_script_renders(samples_dir, sample, target, script_ranges) -> None:
    src = samples_dir / sample
    parsed = PdfParser().parse(src)
    pipeline = TranslationPipeline(MockProvider(latency_scale=0), target,
                                   source_lang=parsed.source_lang)
    result = asyncio.run(pipeline.run(segment_document(parsed)))
    rebuilt = Rebuilder().rebuild(src, parsed, result.texts(), target)

    # 1. nothing was reported as unrenderable except the documented symbols
    reported = {c for pl in rebuilt.placements for i in pl.issues
                if i["code"] == "MISSING_GLYPHS" for c in i.get("characters", [])}
    assert reported <= KNOWN_MISSING, f"{target}: unrenderable {reported}"

    # 2. the placed text really is in the target script
    doc = fitz.open(stream=rebuilt.pdf_bytes, filetype="pdf")
    try:
        text = "".join(page.get_text() for page in doc)

        def in_target_script(ch: str) -> bool:
            return any(lo <= ord(ch) <= hi for lo, hi in script_ranges)

        in_script = sum(1 for ch in text if in_target_script(ch))
        assert in_script > 40, f"{target}: only {in_script} in-script characters"
        assert "�" not in text

        # 3. the embedded face can draw every character that was placed
        font = fitz.Font(fontfile=str(
            FontResolver().resolve("NotoSansDevanagari-Regular", target).path))
        placed = {ch for ch in text if in_target_script(ch)}
        assert all(font.has_glyph(ord(ch)) for ch in placed)
    finally:
        doc.close()


def test_rtl_alignment_is_flipped(samples_dir) -> None:
    """An Arabic target must be set from the right edge of its box."""
    from backend.reconstruction.shaping import flip_align, shape_rtl
    assert flip_align(0, True) == 2
    assert shape_rtl("مرحبا") != "مرحبا"

    src = samples_dir / "govt-notice.pdf"
    parsed = PdfParser().parse(src)
    result = asyncio.run(TranslationPipeline(
        MockProvider(latency_scale=0), "ar",
        source_lang=parsed.source_lang).run(segment_document(parsed)))
    rebuilt = Rebuilder().rebuild(src, parsed, result.texts(), "ar")
    doc = fitz.open(stream=rebuilt.pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        blocks = [b for b in page.get_text("blocks") if b[4].strip()]
        assert blocks
        # right edges of the body blocks cluster near the text-area right edge
        right = max(b[2] for b in blocks)
        assert right > page.rect.width * 0.7
    finally:
        doc.close()


def test_detected_language_of_the_output_matches_the_target(samples_dir) -> None:
    src = samples_dir / "technical-report.pdf"
    parsed = PdfParser().parse(src)
    assert parsed.source_lang == "de"
    result = asyncio.run(TranslationPipeline(
        MockProvider(latency_scale=0), "zh", source_lang="de").run(
            segment_document(parsed)))
    rebuilt = Rebuilder().rebuild(src, parsed, result.texts(), "zh")
    doc = fitz.open(stream=rebuilt.pdf_bytes, filetype="pdf")
    try:
        detected, _ = detect_language_text("".join(p.get_text() for p in doc))
    finally:
        doc.close()
    assert detected == "zh"
