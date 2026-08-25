#!/usr/bin/env python3
"""P1 gate. Writes var/font_smoke.pdf + .png with one line per target script
through both PyMuPDF text writers, then counts unrenderable glyphs.

A pass means the vendored faces really do shape Devanagari conjuncts, join
Arabic letters and place kana/hanzi. Nothing downstream is meaningful until
this is visually verified, so the script also prints where to look.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf as fitz  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.fonts.registry import FontRegistry  # noqa: E402
from backend.fonts.resolver import FontResolver  # noqa: E402

CASES = [
    ("hi", "नमस्ते दुनिया — हिन्दी में मुद्रित पाठ 2024", "Devanagari"),
    ("ar", "مرحبا بالعالم — نص مطبوع بالعربية 2024", "Arabic (RTL)"),
    ("ja", "こんにちは世界 — 日本語の組版 2024", "Japanese"),
    ("zh", "你好世界 — 中文排版 2024", "Chinese"),
    ("de", "Größe, Änderungsschlüssel — Übergrößenbereich", "Latin/German"),
]


def main() -> int:
    resolver = FontResolver()
    reg = FontRegistry()
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 60
    failures: list[str] = []
    notes: list[str] = []

    page.insert_text((50, 32), "LayoutLoom P1 font gate", fontsize=13,
                     fontname="helv")

    for code, text, label in CASES:
        rf = resolver.resolve("Helvetica", code)
        alias = reg.ensure(page, rf)
        page.insert_text((50, y), f"{label}  [{rf.path.name}]", fontsize=7,
                         fontname="helv", color=(0.45, 0.45, 0.45))
        # writer 1: insert_textbox
        rc = page.insert_textbox(fitz.Rect(50, y + 6, 545, y + 34), text,
                                 fontname=alias, fontsize=15)
        # writer 2: insert_htmlbox (HarfBuzz shaping, bidi for RTL)
        direction = "rtl" if code == "ar" else "ltr"
        html = (f"<div dir='{direction}' style=\"font-family:{reg.fallback_stack(rf)};"
                f"font-size:15px;\">{text}</div>")
        page.insert_htmlbox(fitz.Rect(50, y + 34, 545, y + 66), html,
                            css=reg.css(), archive=reg.archive())
        if rc < 0:
            if code == "ar":
                # Expected: unshaped RTL text is wider than its shaped form and
                # insert_textbox does no bidi. This is precisely why the engine
                # routes RTL and other complex scripts through insert_htmlbox.
                notes.append(f"{label}: insert_textbox rejected raw RTL "
                             "(engine uses insert_htmlbox for this script)")
            else:
                failures.append(f"{label}: insert_textbox did not fit")
        # glyph coverage check against the actual embedded face
        font = fitz.Font(fontfile=str(rf.path))
        missing = [c for c in text if not c.isspace() and font.has_glyph(ord(c)) == 0]
        if missing:
            failures.append(f"{label}: {len(missing)} missing glyphs {''.join(missing)!r}")
        y += 76

    out_pdf = Path(settings.data_dir) / "font_smoke.pdf"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    from backend.utils.io import save_pdf, save_pixmap
    save_pdf(doc, out_pdf)
    pix = doc[0].get_pixmap(dpi=140)
    out_png = out_pdf.with_suffix(".png")
    save_pixmap(pix, out_png)
    doc.close()

    print(f"[p1] wrote {out_pdf}\n[p1] wrote {out_png}")
    for n in notes:
        print(f"[p1] note {n}")
    for f in failures:
        print(f"[p1] FAIL {f}")
    print("[p1] " + ("PASS - inspect the PNG to confirm shaping" if not failures
                     else f"{len(failures)} failure(s)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
