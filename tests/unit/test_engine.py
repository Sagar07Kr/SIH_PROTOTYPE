"""Fonts, fit ladder, shaping, geometry -- the parts that make or break layout."""
from __future__ import annotations

import pymupdf as fitz
import pytest

from backend.config import settings
from backend.fonts.resolver import FontResolver, measure_x_height, parse_font_name
from backend.reconstruction import shaping
from backend.reconstruction.fit_ladder import RUNG_WEIGHTS, fit_ladder, monotonic
from backend.utils.geometry import overlap_fraction, overlap_violations
from backend.utils.langs import LANGS


# ------------------------------------------------------------------- fonts
@pytest.mark.parametrize("raw,family,weight,italic,serif", [
    ("ABCDEF+NotoSans-BoldItalic", "NotoSans", 700, True, False),
    ("TimesNewRomanPS-BoldMT", "TimesNewRoman", 700, False, True),
    ("Helvetica-Oblique", "Helvetica", 400, True, False),
    ("Arial,Bold", "Arial", 700, False, False),
    ("Courier", "Courier", 400, False, False),
])
def test_font_name_parsing(raw, family, weight, italic, serif) -> None:
    got_family, got_weight, got_italic, got_serif, _mono = parse_font_name(raw)
    assert got_family == family
    assert got_weight == weight
    assert got_italic is italic
    assert got_serif is serif


def test_resolver_covers_every_target_script() -> None:
    resolver = FontResolver()
    assert resolver.missing_scripts(list(LANGS)) == []
    for code in LANGS:
        rf = resolver.resolve("Helvetica", code)
        assert rf.path.exists()
        font = fitz.Font(fontfile=str(rf.path))
        probe = {"hi": "न", "ar": "م", "ja": "こ", "zh": "好"}.get(code, "A")
        assert font.has_glyph(ord(probe)), f"{code}: no glyph for {probe}"


def test_x_height_correction_is_clamped_and_directional() -> None:
    resolver = FontResolver()
    # Times has a small x-height; substituting a larger-x-height face must
    # shrink the point size, never grow it past the clamp.
    times = resolver.resolve("Times-Roman", "en")
    helv = resolver.resolve("Helvetica", "en")
    assert 0.85 <= times.size_factor <= 1.15
    assert 0.85 <= helv.size_factor <= 1.15
    assert times.size_factor < helv.size_factor
    xh = measure_x_height(str(times.path))
    assert 0.3 < xh < 0.8


def test_weight_is_mapped_not_synthesised() -> None:
    resolver = FontResolver()
    assert resolver.resolve("Helvetica-Light", "en").weight == 400
    assert resolver.resolve("Helvetica-Semibold", "en").weight == 700
    assert resolver.resolve("Helvetica-Black", "en").weight == 700


def test_substitution_is_recorded_not_silent() -> None:
    rf = FontResolver().resolve("Helvetica", "hi")
    assert rf.substituted and rf.reason
    assert rf.to_dict()["original"] == "Helvetica"


# -------------------------------------------------------------- fit ladder
def test_ladder_is_monotonic_in_concession() -> None:
    attempts = fit_ladder((0, 0, 200, 40), 10.0, 1.35, "textbox",
                          grow_down=8, grow_right=30, margin_width=40)
    assert monotonic(attempts)
    assert [a.rung for a in attempts][0] == 0
    assert max(a.rung for a in attempts) == 5
    assert RUNG_WEIGHTS == (0, 1, 2, 3, 5, 8, 13)


def test_ladder_respects_the_size_floor() -> None:
    attempts = fit_ladder((0, 0, 200, 40), 10.0, 1.3, "textbox")
    sizes = [a.size for a in attempts if a.rung == 3]
    assert sizes and min(sizes) >= 10.0 * settings.prose_size_floor - 1e-6
    cells = fit_ladder((0, 0, 200, 40), 10.0, 1.3, "textbox",
                       size_floor_factor=settings.cell_size_floor)
    assert min(a.size for a in cells if a.rung == 3) < min(sizes)


def test_ladder_skips_growth_when_there_is_no_room() -> None:
    attempts = fit_ladder((0, 0, 200, 40), 10.0, 1.3, "textbox",
                          grow_down=0, grow_right=0)
    assert max(a.rung for a in attempts) == 3


def test_cjk_ladder_skips_the_tracking_rung() -> None:
    assert shaping.supports_tracking("de")
    assert not shaping.supports_tracking("ja")
    attempts = fit_ladder((0, 0, 200, 40), 10.0, 1.3, "textbox",
                          allow_tracking=False)
    assert all(a.rung != 2 for a in attempts)
    assert all(a.tracking == 0.0 for a in attempts)


# ------------------------------------------------------------- shaping etc.
def test_cjk_break_rules() -> None:
    z = shaping.ZWSP
    out = shaping.cjk_break_text("日本語の組版")
    assert z in out
    # no break before closing punctuation, none after opening punctuation
    assert "語" + z + "。" not in shaping.cjk_break_text("日本語。次")
    assert "「" + z not in shaping.cjk_break_text("「引用」")
    # never inside a Latin word embedded in CJK
    assert z not in shaping.cjk_break_text("PDF")
    assert "P" + z not in shaping.cjk_break_text("日本PDF語")


def test_cjk_wrap_respects_the_measured_width() -> None:
    rf = FontResolver().resolve("Helvetica", "ja")
    font = fitz.Font(fontfile=str(rf.path))
    text = "日本語の組版処理系統情報管理報告書資料確認対応実施検討結果" * 2
    wrapped = shaping.cjk_wrap(text, font, 10.0, 100.0)
    assert "\n" in wrapped
    for line in wrapped.split("\n"):
        assert font.text_length(line, fontsize=10.0) <= 100.5, line


def test_arabic_is_shaped_and_reordered() -> None:
    src = "مرحبا بالعالم"
    shaped = shaping.shape_rtl(src)
    assert shaped != src                        # presentation forms applied
    assert len(shaped) >= len(src) - 2
    assert shaping.flip_align(0, True) == 2     # LEFT becomes RIGHT
    assert shaping.flip_align(3, True) == 3     # JUSTIFY stays
    assert shaping.flip_align(0, False) == 0


def test_devanagari_grapheme_integrity() -> None:
    text = "नमस्ते दुनिया"
    clusters = shaping.grapheme_clusters(text)
    assert len("".join(clusters)) == len(text)
    # a cluster is never split: truncation lands on a boundary
    for n in range(1, len(clusters) + 1):
        cut = shaping.safe_truncate(text, n)
        assert text.startswith(cut)
    assert shaping.line_height_factor("hi") > 1.0
    assert shaping.line_height_factor("en") == 1.0


# ---------------------------------------------------------------- geometry
def test_overlap_sweep_uses_the_smaller_box() -> None:
    a = (0, 0, 100, 100)
    small = (90, 90, 110, 110)          # 100/400 of the smaller box
    assert overlap_fraction(a, small) == pytest.approx(0.25)
    assert overlap_violations([("a", a), ("b", small)], 0.02)
    assert not overlap_violations([("a", a), ("b", (200, 200, 300, 300))], 0.02)


def test_overlap_sweep_ignores_touching_edges() -> None:
    assert not overlap_violations(
        [("a", (0, 0, 100, 100)), ("b", (100, 0, 200, 100))], 0.02)
