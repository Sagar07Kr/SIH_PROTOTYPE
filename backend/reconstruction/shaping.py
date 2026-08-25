"""Bidi, shaping and line breaking (§4.4).

The absence of this module is the usual reason a project like this ships broken
output for half its advertised languages: the text is translated correctly,
placed correctly, and drawn as disconnected Arabic letters in visual order, or
as a CJK line that never wraps because there are no spaces to wrap on.

Writer routing is decided here as well, and the reasoning is worth recording
because the obvious arrangement does not survive contact with real fonts:

* Everything is written with `insert_textbox`, which is fast, predictable, and
  embeds each face once per document.
* Arabic is pre-shaped here (arabic_reshaper + the Unicode bidi algorithm)
  because `insert_textbox` does neither. Alignment is flipped to the right.
* CJK is pre-wrapped here: a UAX #14 approximation finds the break
  opportunities, and the text arrives at the writer with explicit newlines,
  because there are no spaces for the writer to break on.
* `insert_htmlbox` is used only for the tracking rung of the fit ladder, where
  CSS letter-spacing is the only way to get -2% em. It is deliberately not used
  for CJK: MuPDF re-embeds the referenced face on every layout pass, and the
  Noto CJK faces are 17-25MB, which exhausts memory within a page.
"""
from __future__ import annotations

import regex as re2

from backend.utils.langs import lang as lang_of

ZWSP = "​"
WRITER_TEXTBOX = "textbox"
WRITER_HTMLBOX = "htmlbox"

# Characters that may not start a line (closing punctuation, small kana, marks)
CJK_NO_START = "）］｝〉》」』】〕、。，．！？；：ー…‥・々ゝゞヽヾぁぃぅぇぉっゃゅょゎ" \
               "ァィゥェォッャュョヮ"
# Characters that may not end a line (opening punctuation)
CJK_NO_END = "（［｛〈《「『【〔＄￥＃"
CJK_RANGES = (
    (0x3040, 0x30FF),   # kana
    (0x3400, 0x4DBF),   # CJK ext A
    (0x4E00, 0x9FFF),   # CJK unified
    (0xF900, 0xFAFF),   # compatibility ideographs
    (0xFF00, 0xFF60),   # fullwidth forms
)


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in CJK_RANGES)


def writer_for(target_lang: str) -> str:
    return WRITER_TEXTBOX


def supports_tracking(target_lang: str) -> bool:
    """Whether the tracking rung is available for this script."""
    return lang_of(target_lang).script not in ("jp", "sc")


def cjk_break_text(text: str) -> str:
    """Insert U+200B at UAX #14-ish break opportunities.

    Rules implemented (§4.4): break between two CJK characters, except before
    closing punctuation and except after opening punctuation; never break
    inside a Latin word embedded in CJK text.
    """
    out: list[str] = []
    for i, ch in enumerate(text):
        out.append(ch)
        if i + 1 >= len(text):
            break
        nxt = text[i + 1]
        if not (is_cjk(ch) and is_cjk(nxt)):
            continue                      # at least one side is not CJK
        if nxt in CJK_NO_START or ch in CJK_NO_END:
            continue
        out.append(ZWSP)
    return "".join(out)


def shape_rtl(text: str) -> str:
    """Presentation-form Arabic in visual order, for the textbox writer only."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def grapheme_clusters(text: str) -> list[str]:
    """Extended grapheme clusters -- never split a Devanagari conjunct."""
    return re2.findall(r"\X", text)


def safe_truncate(text: str, clusters: int) -> str:
    """Truncate on a cluster boundary (used only for UI previews)."""
    gc = grapheme_clusters(text)
    return "".join(gc[:clusters])


def prepare_text(text: str, target_lang: str, writer: str) -> str:
    """Final string handed to the writer, before any wrapping."""
    meta = lang_of(target_lang)
    if meta.script in ("jp", "sc"):
        return cjk_break_text(text) if writer == WRITER_HTMLBOX else text
    if meta.rtl and writer == WRITER_TEXTBOX:
        return shape_rtl(text)
    return text


def cjk_wrap(text: str, font, size: float, width: float) -> str:
    """Hard-wrap CJK text to `width` points, breaking only where UAX #14 allows.

    `insert_textbox` breaks on spaces, and CJK has none, so the wrapping is
    done here against the real advance widths of the substituted face. Latin
    words embedded in CJK text are measured whole and never split.
    """
    if width <= 1 or not text:
        return text
    marked = cjk_break_text(text)
    chunks: list[str] = []
    buf = ""
    for ch in marked:
        if ch == ZWSP:
            if buf:
                chunks.append(buf)
                buf = ""
            continue
        if ch == " ":
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(" ")
            continue
        buf += ch
        if is_cjk(ch):
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)

    lines: list[str] = []
    cur = ""
    for chunk in chunks:
        candidate = cur + chunk
        if cur and font.text_length(candidate, fontsize=size) > width:
            lines.append(cur.rstrip())
            cur = chunk.lstrip() if chunk != " " else ""
        else:
            cur = candidate
    if cur.strip():
        lines.append(cur.rstrip())
    return "\n".join(lines)


def flip_align(align: int, rtl: bool) -> int:
    """Source LEFT becomes target RIGHT for an RTL target; JUSTIFY stays."""
    if not rtl:
        return align
    return {0: 2, 2: 0}.get(align, align)


def line_height_factor(target_lang: str) -> float:
    """Extra leading some scripts need; Devanagari ascenders exceed Latin."""
    return 1.0 + lang_of(target_lang).line_height_bonus
