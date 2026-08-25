"""Registration glue between ResolvedFont and the two PyMuPDF text writers.

`insert_textbox` needs a font registered on the page under an alias;
`insert_htmlbox` needs an Archive plus @font-face CSS. Both are cached per
document so a 200-page job embeds each face once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from backend.config import settings
from backend.fonts.resolver import ResolvedFont


@dataclass
class FontRegistry:
    fonts_dir: Path = field(default_factory=lambda: Path(settings.fonts_dir))
    _page_registered: dict[tuple[int, str], bool] = field(default_factory=dict)
    _used: dict[str, ResolvedFont] = field(default_factory=dict)
    _archive: fitz.Archive | None = None
    _css_cache: str | None = None
    _css_key: tuple | None = None

    # -- textbox path ---------------------------------------------------
    def ensure(self, page: fitz.Page, rf: ResolvedFont) -> str:
        """Register the face on this page and return its alias.

        `insert_font` is called every time rather than memoised: PyMuPDF
        already de-duplicates by xref inside the document, and a memo keyed on
        the document object silently breaks when the same alias is used on a
        second document (the measurement scratch page), which fails later with
        an unhelpful "need font file or buffer".
        """
        self._used[rf.alias] = rf
        page.insert_font(fontname=rf.alias, fontfile=str(rf.path))
        self._page_registered[(id(page.parent), rf.alias)] = True
        return rf.alias

    # -- htmlbox path ---------------------------------------------------
    def note_used(self, rf: ResolvedFont) -> None:
        """Record a face so `css()` emits an @font-face rule for it."""
        self._used[rf.alias] = rf

    def archive(self) -> fitz.Archive:
        """One Archive over the fonts directory, built once.

        Re-creating it per call re-scans the directory, and the CJK faces in
        there are 17-25MB each; that turned every htmlbox measurement into a
        disk crawl.
        """
        if self._archive is None:
            self._archive = fitz.Archive(str(self.fonts_dir))
        return self._archive

    def css(self) -> str:
        """@font-face rules for the faces in play -- and only those.

        Emitting a rule for every vendored face makes MuPDF parse ~150MB of
        CJK outlines on each layout pass. The Devanagari and Arabic faces
        already carry Latin subsets, so one extra Latin face is all any
        fallback chain needs.
        """
        key = tuple(sorted(self._used))
        if key == self._css_key and self._css_cache is not None:
            return self._css_cache
        rules: list[str] = []
        seen: set[str] = set()
        for rf in self._used.values():
            for name in self._chain_files(rf):
                if name in seen:
                    continue
                seen.add(name)
                stem = name.rsplit(".", 1)[0]
                bold = stem.endswith("Bold") or stem.endswith("BoldItalic")
                ital = stem.endswith("Italic")
                rules.append(_face_rule(stem, name, 700 if bold else 400, ital))
        self._css_cache = "\n".join(rules)
        self._css_key = key
        return self._css_cache

    def _chain_files(self, rf: ResolvedFont) -> list[str]:
        suffix = "Bold" if rf.weight >= 700 else "Regular"
        names = [rf.path.name]
        latin = f"NotoSans-{suffix}.ttf"
        if (self.fonts_dir / latin).exists() and latin not in names:
            names.append(latin)
        return names

    def fallback_stack(self, rf: ResolvedFont) -> str:
        """CSS font-family list: the chosen face, then one Latin face for
        embedded Latin runs (numbers, URLs, product names)."""
        names = [n.rsplit(".", 1)[0] for n in self._chain_files(rf)]
        return ", ".join(f"'{n}'" for n in names) + ", sans-serif"

    @property
    def used(self) -> dict[str, ResolvedFont]:
        return dict(self._used)


def _face_rule(family: str, filename: str, weight: int, italic: bool) -> str:
    return (f"@font-face {{ font-family: '{family}'; "
            f"src: url('{filename}'); font-weight: {weight}; "
            f"font-style: {'italic' if italic else 'normal'}; }}")
