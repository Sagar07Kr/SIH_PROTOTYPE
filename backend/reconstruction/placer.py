"""The core erase-and-replace loop (§4.3).

Two passes per page, in this order, because they cannot be interleaved:

  1. ERASE  -- every translated block's text layer is redacted at once, with
               images and line art explicitly protected (I3).
  2. PLACE  -- each block is written back into its own rectangle, walking the
               fit ladder and stopping at the first rung that measurably fits.

Placing before erasing would delete the text we just wrote, and erasing block
by block would make each redaction re-render the page.

Vertical registration is done from baselines, not from box tops. The original
span bbox is a glyph bbox; `insert_textbox` measures from the font's ascender.
Matching those two directly is what keeps an identity round-trip within a
fraction of a point instead of drifting a line down the page.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf as fitz

from backend.config import settings
from backend.fonts.registry import FontRegistry
from backend.fonts.resolver import FontResolver, ResolvedFont
from backend.parsers.model import ElementType, TextBlock
from backend.reconstruction import shaping
from backend.reconstruction.fit_ladder import Attempt, describe, fit_ladder
from backend.utils.geometry import (Box, height, horizontal_free_space,
                                    overlap_fraction, vertical_free_space, width)
from backend.utils.langs import lang as lang_of


@dataclass
class PageContext:
    page: fitz.Page
    occupied: list[Box]                      # everything already on the page
    #: original boxes of blocks still to be placed. Without these, a block that
    #: grows into rung 4 or 5 can claim space that a later block is about to
    #: use, and the I5 sweep reports an overlap nobody could have avoided.
    reserved: dict[str, Box] = field(default_factory=dict)
    placed: list[Box] = field(default_factory=list)
    columns: list[tuple[float, float]] = field(default_factory=list)
    text_area: Box | None = None
    margin_right: float = 36.0

    @property
    def bottom(self) -> float:
        return self.page.rect.y1 - 18.0

    @property
    def right(self) -> float:
        return self.page.rect.x1 - 12.0


@dataclass
class Placement:
    block_id: str
    rung: int
    rect: Box
    size: float
    original_size: float
    leading: float
    font: ResolvedFont | None
    writer: str
    leftover: float
    concession: str
    issues: list[dict] = field(default_factory=list)
    skipped: bool = False

    def to_dict(self) -> dict:
        return {"block_id": self.block_id, "rung": self.rung,
                "rect": [round(v, 2) for v in self.rect],
                "size": round(self.size, 2),
                "original_size": round(self.original_size, 2),
                "writer": self.writer, "concession": self.concession,
                "font": self.font.to_dict() if self.font else None,
                "issues": self.issues}


class Placer:
    def __init__(self, resolver: FontResolver | None = None,
                 registry: FontRegistry | None = None):
        self.resolver = resolver or FontResolver()
        self.registry = registry or FontRegistry()
        self._scratch_doc: fitz.Document | None = None
        self._scratch_page: fitz.Page | None = None
        self._scratch_key: tuple[float, float] | None = None
        self._scratch_uses = 0

    # ---------------------------------------------------------------- erase
    def erase(self, page: fitz.Page, blocks: list[TextBlock]) -> int:
        """Redact the text layer of `blocks` only. Images and vectors are
        explicitly excluded, which is invariant I3."""
        n = 0
        for b in blocks:
            rect = fitz.Rect(*b.bbox)
            if rect.is_empty or rect.is_infinite:
                continue
            page.add_redact_annot(rect)
            n += 1
        if n:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                  text=fitz.PDF_REDACT_TEXT_REMOVE)
        return n

    # ---------------------------------------------------------------- place
    def place(self, ctx: PageContext, block: TextBlock, text: str,
              target_lang: str, *, size_floor: float | None = None,
              allow_grow: bool = True, script_change: bool = True) -> Placement:
        page = ctx.page
        style = block.style
        meta = lang_of(target_lang)
        rf = self.resolver.resolve(style.font, target_lang,
                                   flags=_flags(style))
        base_size = style.size * rf.size_factor
        writer = shaping.writer_for(target_lang)
        if block.type == ElementType.CODE:
            writer = shaping.WRITER_TEXTBOX

        # The script's extra leading is a *change* allowance: when the source
        # is already in the target script (an identity round-trip, or Hindi to
        # Hindi), adding it would push every block down a rung for nothing.
        leading = max(0.95, style.leading)
        if script_change:
            leading *= shaping.line_height_factor(target_lang)
        align = shaping.flip_align(style.align, meta.rtl)
        if block.type == ElementType.TABLE_CELL and block.numeric:
            align = 2                              # numeric cells stay right
        rect = self._baseline_rect(block, rf, base_size, leading)
        rect = self._trim(ctx, block, rect, base_size, rf)

        grow_down = vertical_free_space(rect, self._others(ctx, block), ctx.bottom)
        margin = max(0.0, ctx.right - (ctx.text_area[2] if ctx.text_area else rect[2]))
        grow_right = horizontal_free_space(rect, self._others(ctx, block), ctx.right)
        can_grow = allow_grow and block.table_id is None and \
            (block.column_index in (0, -1) or len(ctx.columns) <= 1)

        # Glyph coverage: a character the substituted face cannot draw comes
        # out as an empty box. Where a fallback chain can rescue it we switch
        # to the html writer, which honours one; where it cannot (CJK faces are
        # far too large to re-embed per html call) the block is placed anyway
        # and the characters are reported rather than quietly boxed.
        missing = _missing_glyphs(text, rf)
        coverage_issues: list[dict] = []
        if missing:
            if meta.script not in ("jp", "sc") and \
                    not _missing_glyphs(text, rf, fallback=True):
                writer = shaping.WRITER_HTMLBOX
            else:
                coverage_issues.append({
                    "code": "MISSING_GLYPHS", "severity": "WARNING",
                    "message": "the substituted face cannot render "
                               + " ".join(sorted(missing))
                               + "; those characters will appear as empty boxes",
                    "characters": sorted(missing)})

        attempts = fit_ladder(rect, base_size, leading, writer,
                             size_floor_factor=size_floor,
                             grow_down=grow_down,
                             grow_right=grow_right if can_grow else 0.0,
                             margin_width=margin,
                             allow_grow=can_grow,
                             allow_tracking=shaping.supports_tracking(target_lang))
        last_excess = 0.0
        for att in attempts:
            if att.rung >= 4 and self._collides(ctx, block, att.rect):
                continue                            # grown box hit something
            leftover = self._measure(ctx, block, text, att, rf, align,
                                     target_lang)
            if leftover >= 0:
                self._write(ctx, block, text, att, rf, align, target_lang)
                ctx.placed.append(att.rect)
                concession = describe(att.rung, original_size=style.size,
                                      size=att.size, grow=att.grow_pt)
                issues = list(coverage_issues)
                if rf.substituted and block.type == ElementType.HEADING:
                    issues.append({"code": "FONT_SUBSTITUTION_IN_HEADING",
                                   "severity": "WARNING",
                                   "message": f"{rf.original_name} -> {rf.family}"})
                if att.rung >= 4:
                    issues.append({"code": "BOX_GROWN", "severity": "WARNING",
                                   "message": concession})
                elif att.rung >= 1:
                    issues.append({"code": "FIT_ADJUSTED", "severity": "INFO",
                                   "message": concession})
                return Placement(block.id, att.rung, att.rect, att.size,
                                 style.size, att.leading, rf, att.writer,
                                 leftover, concession, issues)
            last_excess = max(last_excess, -leftover)

        # rung 6: never clip -- place what fits at the floor and flag it (I4)
        floor_att = attempts[-1]
        self._write(ctx, block, text, floor_att, rf, align, target_lang)
        ctx.placed.append(floor_att.rect)
        concession = describe(6, excess=last_excess)
        return Placement(block.id, 6, floor_att.rect, floor_att.size, style.size,
                         floor_att.leading, rf, floor_att.writer, -last_excess,
                         concession,
                         coverage_issues
                         + [{"code": "OVERFLOW", "severity": "ERROR",
                             "message": concession,
                             "excess_pt": round(last_excess, 2)}])

    # ------------------------------------------------------------- internals
    def _others(self, ctx: PageContext, block: TextBlock) -> list[Box]:
        pending = [b for key, b in ctx.reserved.items() if key != block.id]
        return [b for b in ctx.occupied + pending
                if overlap_fraction(b, block.bbox) < 0.9] + ctx.placed

    def _collides(self, ctx: PageContext, block: TextBlock, rect: Box) -> bool:
        tol = settings.overlap_tolerance
        for other in self._others(ctx, block):
            if overlap_fraction(rect, other) > tol:
                return True
        return False

    def _trim(self, ctx: PageContext, block: TextBlock, rect: Box, size: float,
              rf: ResolvedFont) -> Box:
        """Pull the box back off its neighbours before placing (I5).

        The baseline-derived rect is a little taller and wider than the ink it
        replaces, so on a dense page -- or on OCR output, where the recovered
        boxes are loose -- it can clip into the block below. Trimming the
        bottom and right edges keeps the first line intact (the top edge is
        load-bearing for baseline registration) and removes almost every
        overlap before the sweep has to report one.
        """
        x0, y0, x1, y1 = rect
        font = _font(rf)
        min_height = size * (font.ascender - font.descender)
        tol = settings.overlap_tolerance
        for other in self._others(ctx, block):
            if overlap_fraction((x0, y0, x1, y1), other) <= tol:
                continue
            # neighbour below: shorten. Neighbour to the right: narrow.
            if other[1] >= y0 + min_height - 0.5 and other[1] < y1:
                y1 = max(y0 + min_height, other[1] - 0.5)
            elif other[0] >= x0 + 4 and other[0] < x1:
                x1 = max(x0 + 8, other[0] - 0.5)
        return (x0, y0, x1, y1)

    def _baseline_rect(self, block: TextBlock, rf: ResolvedFont, size: float,
                       leading: float) -> Box:
        """Rect whose first line lands on the original first baseline."""
        font = _font(rf)
        asc, desc = font.ascender, font.descender
        x0, y0, x1, y1 = block.bbox
        first_baseline = block.lines[0].spans[0].origin[1] if block.lines else y0 + size
        last_baseline = block.lines[-1].spans[0].origin[1] if block.lines else y1
        top = first_baseline - asc * size
        # +1.2pt of slack. Without it a block whose original leading equals the
        # writer's line advance has exactly zero spare height, and floating
        # point noise alone pushes it onto rung 1 for no visible reason.
        bottom = max(y1, last_baseline - desc * size) + 1.2
        if bottom - top < size * (asc - desc):
            bottom = top + size * (asc - desc)
        return (x0, top, x1, bottom)

    def _measure(self, ctx: PageContext, block: TextBlock, text: str,
                 att: Attempt, rf: ResolvedFont, align: int,
                 target_lang: str) -> float:
        """Ask the writer whether the text fits, without touching the page.

        The measurement runs the *same* writer on a scratch page of identical
        geometry, so the answer is the writer's own layout result rather than a
        character-count estimate. Writing invisibly onto the real page would
        also answer the question, but it would leave an invisible text layer
        behind and corrupt every later text-coverage measurement.
        """
        scratch = self._scratch(ctx.page)
        return self._emit(scratch, block, text, att, rf, align, target_lang)

    def _write(self, ctx: PageContext, block: TextBlock, text: str,
               att: Attempt, rf: ResolvedFont, align: int,
               target_lang: str) -> float:
        return self._emit(ctx.page, block, text, att, rf, align, target_lang)

    def _emit(self, page: fitz.Page, block: TextBlock, text: str, att: Attempt,
              rf: ResolvedFont, align: int, target_lang: str) -> float:
        rect = fitz.Rect(*att.rect)
        meta = lang_of(target_lang)
        body = shaping.prepare_text(text, target_lang, att.writer)
        if meta.script in ("jp", "sc") and att.writer == shaping.WRITER_TEXTBOX:
            body = shaping.cjk_wrap(body, _font(rf), att.size,
                                    rect.width - 1.0)
        if att.writer == shaping.WRITER_HTMLBOX:
            return self._emit_html(page, rect, body, att, rf, align, meta, block)
        alias = self.registry.ensure(page, rf)
        return float(page.insert_textbox(
            rect, body, fontname=alias, fontsize=att.size,
            lineheight=att.leading, align=align, color=block.style.color,
            rotate=block.style.rotation))

    def _emit_html(self, page, rect, body, att, rf, align, meta, block) -> float:
        self.registry.note_used(rf)
        css = self.registry.css()
        stack = self.registry.fallback_stack(rf)
        color = "#%02x%02x%02x" % tuple(int(round(c * 255))
                                        for c in block.style.color)
        align_css = {0: "left", 1: "center", 2: "right", 3: "justify"}[align]
        style = (f"font-family:{stack};font-size:{att.size:.2f}px;"
                 f"line-height:{att.leading:.3f};text-align:{align_css};"
                 f"color:{color};margin:0;padding:0;"
                 f"letter-spacing:{att.tracking:.3f}em;"
                 f"font-weight:{rf.weight};"
                 f"{'font-style:italic;' if rf.italic else ''}")
        direction = "rtl" if meta.rtl else "ltr"
        html = (f"<div dir='{direction}' style=\"{style}\">"
                f"{_escape(body)}</div>")
        try:
            spare, _scale = page.insert_htmlbox(
                rect, html, css=css, archive=self.registry.archive(),
                rotate=block.style.rotation, scale_low=1)
        except Exception:
            return -1.0
        return float(spare)

    # -- scratch page used for measurement only
    def _scratch(self, page: fitz.Page) -> fitz.Page:
        key = (round(page.rect.width, 1), round(page.rect.height, 1))
        if self._scratch_key != key or self._scratch_uses > 400 \
                or self._scratch_page is None:
            self._scratch_doc = fitz.open()
            self._scratch_page = self._scratch_doc.new_page(
                width=key[0], height=key[1])
            self._scratch_key = key
            self._scratch_uses = 0
        self._scratch_uses += 1
        return self._scratch_page

    def close(self) -> None:
        if self._scratch_doc is not None:
            self._scratch_doc.close()
            self._scratch_doc = None
            self._scratch_page = None


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"))


_FONT_CACHE: dict[str, fitz.Font] = {}


def _font(rf: ResolvedFont) -> fitz.Font:
    key = str(rf.path)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = fitz.Font(fontfile=key)
        _FONT_CACHE[key] = f
    return f


def _missing_glyphs(text: str, rf: ResolvedFont, fallback: bool = False
                    ) -> set[str]:
    """Characters the face cannot draw. `fallback=True` tests the Latin face
    that the html writer's font stack falls back to."""
    path = rf.path
    if fallback:
        suffix = "Bold" if rf.weight >= 700 else "Regular"
        cand = path.parent / f"NotoSans-{suffix}.ttf"
        if not cand.exists():
            return {c for c in set(text) if not c.isspace()}
        path = cand
    font = _font_at(str(path))
    out = set()
    for ch in set(text):
        if ch.isspace() or ch in ("\n", "\u200b"):
            continue
        if font.has_glyph(ord(ch)) == 0:
            out.add(ch)
    if fallback:
        primary = _font_at(str(rf.path))
        out = {c for c in out if primary.has_glyph(ord(c)) == 0}
    return out


def _font_at(path: str) -> fitz.Font:
    f = _FONT_CACHE.get(path)
    if f is None:
        f = fitz.Font(fontfile=path)
        _FONT_CACHE[path] = f
    return f


def _flags(style) -> int:
    flags = 0
    if style.italic:
        flags |= 2
    if style.mono:
        flags |= 8
    if style.bold:
        flags |= 16
    return flags
