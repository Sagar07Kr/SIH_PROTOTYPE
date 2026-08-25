"""Mock determinism, length ratios, term memory, segmentation, language ID."""
from __future__ import annotations

import asyncio

import pytest

from backend.parsers.pdf_parser import detect_language_text
from backend.providers.base import SegmentIn, TranslateRequest
from backend.providers.mock import MockProvider
from backend.translation.segmenter import detect_domain, segment_document
from backend.translation.termmemory import TermMemory
from backend.utils.langs import LANGS

SRC = ("The evaluation corpus consists of synthetic pages generated with known "
       "ground truth, which lets us separate parsing error from translation "
       "error [6].")


def translate(text: str, lang: str, seg_id: str = "seg-1"):
    mp = MockProvider(latency_scale=0)
    return asyncio.run(mp.translate(TranslateRequest(
        segments=[SegmentIn(id=seg_id, text=text)],
        source_lang="en", target_lang=lang))).segments[0]


def test_mock_is_deterministic_across_runs() -> None:
    first = [translate(SRC, "hi").text for _ in range(2)]
    assert first[0] == first[1]
    # and independent of Python's per-process hash seed
    assert translate(SRC, "hi", "other-id").text != first[0]


def test_mock_writes_in_the_target_script() -> None:
    checks = {"hi": (0x0900, 0x097F), "ar": (0x0600, 0x06FF),
              "ja": (0x3040, 0x30FF), "zh": (0x4E00, 0x9FFF)}
    for lang, (lo, hi) in checks.items():
        out = translate(SRC, lang).text
        hits = sum(1 for ch in out if lo <= ord(ch) <= hi)
        assert hits > 10, f"{lang}: only {hits} in-script characters"


def test_mock_length_ratios_are_realistic() -> None:
    # averaged over many segments, the ratio must track the published table
    for lang in ("de", "hi", "ja", "zh", "en"):
        ratios = []
        for i in range(60):
            out = translate(SRC, lang, f"s{i}").text
            ratios.append(len(out) / len(SRC))
        mean = sum(ratios) / len(ratios)
        expected = LANGS[lang].expansion
        assert abs(mean - expected) < 0.12, f"{lang}: {mean:.2f} vs {expected}"
        assert max(ratios) > mean, "no variance: nothing would ever overflow"


def test_mock_preserves_placeholders() -> None:
    text = "Total ⟦P0⟧ units by ⟦P1⟧."
    out = translate(text, "hi").text
    assert out.count("⟦P0⟧") == 1 and out.count("⟦P1⟧") == 1


def test_mock_confidence_distribution() -> None:
    mp = MockProvider(latency_scale=0)
    values = [mp._confidence(f"s{i}", "de") for i in range(2000)]
    mean = sum(values) / len(values)
    low = sum(1 for v in values if v < 0.80) / len(values)
    assert 0.90 < mean < 0.95, mean
    assert 0.02 < low < 0.07, low          # ~4% for the review queue


@pytest.mark.parametrize("text,lang", [
    ("This notification concerns water conservation compliance.", "en"),
    ("Die Bestandsdatenverwaltungsschnittstelle wurde überarbeitet.", "de"),
    ("Cette notification concerne la conservation de l'eau exigée.", "fr"),
    ("Esta notificación se refiere a la conservación del agua exigida.", "es"),
    ("यह अधिसूचना जल संरक्षण अनुपालन से संबंधित है।", "hi"),
    ("هذه الإشعار يتعلق بالامتثال لحفظ المياه في المنطقة.", "ar"),
    ("この通知は水の保全の遵守に関するものです。", "ja"),
    ("本通知涉及节约用水的合规要求。", "zh"),
])
def test_language_detection(text: str, lang: str) -> None:
    got, confidence = detect_language_text(text)
    assert got == lang, f"{lang} detected as {got}"
    assert confidence > 0.3


def test_term_memory_finds_repeated_multiword_terms() -> None:
    memory = TermMemory("de")
    texts = ["The Layout Engine is measured.",
             "Our Layout Engine reports concessions.",
             "Every Layout Engine decision is recorded."]
    candidates = memory.scan(texts)
    assert "Layout Engine" in candidates
    assert candidates["Layout Engine"] >= 3
    assert memory.pending_terms(texts[0]) == ["Layout Engine"]
    memory.lock("Layout Engine", "Layout-Engine")
    assert memory.glossary_for(texts[1]) == {"Layout Engine": "Layout-Engine"}


def test_user_glossary_overrides_the_locked_term() -> None:
    memory = TermMemory("de", user_glossary={"Layout Engine": "Layoutmaschine"})
    memory.lock("Layout Engine", "Layout-Engine")
    assert memory.glossary_for("The Layout Engine")["Layout Engine"] == "Layoutmaschine"


def test_segmentation_never_splits_a_paragraph_by_line(parsed_samples) -> None:
    doc = parsed_samples["research-paper"]
    segments = segment_document(doc)
    assert segments
    assert all("\n" not in s.text for s in segments if s.element_type != "code")
    long_ones = [s for s in segments if s.element_type == "paragraph"
                 and len(s.text) > 200]
    assert long_ones, "paragraphs were split into lines"
    # context comes from neighbouring units
    with_context = [s for s in segments if s.context_before and s.context_after]
    assert len(with_context) > len(segments) // 2
    assert detect_domain(doc) == "academic"


def test_verbatim_segments_are_marked_untranslatable(parsed_samples) -> None:
    report = parsed_samples["technical-report"]
    segments = {s.element_type: s for s in segment_document(report)}
    assert "code" in segments
    assert segments["code"].translatable is False
