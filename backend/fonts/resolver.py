"""FontResolver -- maps an original PDF font onto a vendored Noto face that can
actually render the target script, and corrects the point size so the
substitute carries the same optical weight on the page (§4.2).

Substitution is a disclosed compromise: every resolution that changes the
typeface is recorded so the API can surface it (ValidationResult
.font_substitutions) instead of silently redrawing the document in a new voice.

Future implementations: a `SystemFontResolver` that prefers faces embedded in
the source PDF when they already cover the target script.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from backend.config import settings
from backend.utils.langs import script_of

# x-height / em for the PDF standard-14 faces, which have no embedded metrics.
STANDARD_X_HEIGHT = {
    "helvetica": 0.523, "arial": 0.519, "times": 0.448, "timesnewroman": 0.448,
    "courier": 0.426, "couriernew": 0.426, "symbol": 0.450, "zapfdingbats": 0.500,
}
DEFAULT_X_HEIGHT = 0.50

SERIF_HINTS = ("serif", "times", "georgia", "garamond", "roman", "book",
               "minion", "cambria", "palatino", "century", "ming", "song",
               "sungti", "mincho", "batang", "kai")
SANS_HINTS = ("sans", "helvetica", "arial", "verdana", "tahoma", "calibri",
              "futura", "gothic", "grotesk", "inter", "roboto", "segoe", "hei")
MONO_HINTS = ("mono", "courier", "consolas", "menlo", "inconsolata", "code")

WEIGHT_WORDS = {
    "thin": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "regular": 400, "normal": 400, "book": 400, "roman": 400, "medium": 500,
    "semibold": 600, "demibold": 600, "bold": 700, "extrabold": 800,
    "ultrabold": 800, "black": 900, "heavy": 900,
}
ITALIC_WORDS = ("italic", "oblique", "it")

# PyMuPDF span flag bits
FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_SERIFED = 4
FLAG_MONO = 8
FLAG_BOLD = 16


@dataclass(frozen=True)
class FontDescriptor:
    """What we could learn about the font in the source document."""
    raw_name: str
    family: str
    weight: int
    italic: bool
    serif: bool
    mono: bool
    x_height: float

    @property
    def bold(self) -> bool:
        return self.weight >= 550


@dataclass
class ResolvedFont:
    alias: str                 # stable name used to register with PyMuPDF
    path: Path
    family: str
    weight: int
    italic: bool
    serif: bool
    x_height: float
    size_factor: float         # multiply the original point size by this
    substituted: bool
    original_name: str
    reason: str = ""
    css_family: str = field(default="")

    def to_dict(self) -> dict:
        return {
            "alias": self.alias, "family": self.family, "weight": self.weight,
            "italic": self.italic, "serif": self.serif,
            "size_factor": round(self.size_factor, 4),
            "substituted": self.substituted, "original": self.original_name,
            "reason": self.reason, "file": self.path.name,
        }


def parse_font_name(name: str) -> tuple[str, int, bool, bool, bool]:
    """Split a PostScript name into (family, weight, italic, serif, mono).

    Handles the usual PDF spellings: `ABCDEF+NotoSans-BoldItalic`,
    `Helvetica-Oblique`, `TimesNewRomanPS-BoldMT`, `Arial,Bold`.
    """
    raw = name or ""
    n = re.sub(r"^[A-Z]{6}\+", "", raw)              # strip subset tag
    n = n.replace(",", "-").replace("_", "-")
    n = re.sub(r"(MT|PS|PSMT|Std|Pro)$", "", n)
    parts = [p for p in re.split(r"[-\s]+", n) if p]
    family_bits, weight, italic = [], None, False
    for p in parts:
        low = re.sub(r"[^a-z]", "", p.lower())
        matched = False
        for word, w in WEIGHT_WORDS.items():
            if low == word or low.endswith(word) and len(low) - len(word) <= 3:
                weight = max(weight or 0, w)
                matched = True
                low = low[: len(low) - len(word)]
                break
        if any(low == it or low.endswith(it) for it in ITALIC_WORDS if it != "it") \
                or low in ("it", "ita"):
            italic = True
            matched = True
        if not matched:
            family_bits.append(p)
    # a name like "NotoSansBold" (no separator) still needs weight detection
    lowered = n.lower()
    if weight is None:
        for word, w in WEIGHT_WORDS.items():
            if word in lowered and word not in ("book", "roman", "normal", "regular"):
                weight = w
                break
    if "italic" in lowered or "oblique" in lowered:
        italic = True
    family = "".join(family_bits) or n or "Unknown"
    # Foundry suffixes ride along on the family segment too, not just at the end
    # of the whole name: "TimesNewRomanPS-BoldMT" -> family "TimesNewRoman".
    family = re.sub(r"(?:PSMT|PS|MT|Std|Pro)+$", "", family) or family
    mono = any(h in lowered for h in MONO_HINTS)
    serif = any(h in lowered for h in SERIF_HINTS) and not any(
        h in lowered for h in SANS_HINTS)
    return family, weight or 400, italic, serif, mono


@lru_cache(maxsize=256)
def measure_x_height(path: str) -> float:
    """x-height / units_per_em for a font file, from OS/2 or the 'x' outline."""
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(path, fontNumber=0, lazy=True)
        upm = f["head"].unitsPerEm or 1000
        os2 = f.get("OS/2")
        if os2 is not None and getattr(os2, "sxHeight", 0):
            return float(os2.sxHeight) / upm
        cmap = f.getBestCmap()
        gname = cmap.get(ord("x"))
        if gname and "glyf" in f:
            g = f["glyf"][gname]
            if g.numberOfContours:
                return float(g.yMax) / upm
        if gname and "CFF " in f:
            cff = f["CFF "].cff
            cs = cff[cff.fontNames[0]].CharStrings
            if gname in cs:
                bounds = cs[gname].calcBounds(cs)
                if bounds:
                    return float(bounds[3]) / upm
    except Exception:
        pass
    return DEFAULT_X_HEIGHT


def descriptor_from_span(font_name: str, flags: int = 0,
                         embedded_path: str | None = None) -> FontDescriptor:
    family, weight, italic, serif, mono = parse_font_name(font_name)
    if flags:
        italic = italic or bool(flags & FLAG_ITALIC)
        if flags & FLAG_BOLD:
            weight = max(weight, 700)
        serif = serif or bool(flags & FLAG_SERIFED)
        mono = mono or bool(flags & FLAG_MONO)
    key = re.sub(r"[^a-z]", "", family.lower())
    if embedded_path:
        xh = measure_x_height(embedded_path)
    else:
        xh = STANDARD_X_HEIGHT.get(key, DEFAULT_X_HEIGHT)
    return FontDescriptor(font_name or "Unknown", family, weight, italic,
                          serif, mono, xh)


class FontResolver:
    """Noto implementation. One implementation only, per §11."""

    #: script -> (sans family stem, serif family stem)
    FAMILIES = {
        "latin": ("NotoSans", "NotoSerif"),
        "devanagari": ("NotoSansDevanagari", "NotoSerifDevanagari"),
        "arabic": ("NotoNaskhArabic", "NotoNaskhArabic"),
        "jp": ("NotoSansJP", "NotoSerifJP"),
        "sc": ("NotoSansSC", "NotoSerifSC"),
    }
    #: scripts whose vendored faces have no italic cut
    NO_ITALIC = {"devanagari", "arabic", "jp", "sc"}

    def __init__(self, fonts_dir: Path | None = None):
        self.dir = Path(fonts_dir or settings.fonts_dir)
        self._cache: dict[tuple, ResolvedFont] = {}

    # -- file lookup ----------------------------------------------------
    def _file(self, stem: str, bold: bool, italic: bool) -> Path | None:
        for style in self._style_order(bold, italic):
            p = self.dir / f"{stem}-{style}.ttf"
            if p.exists():
                return p
            p = p.with_suffix(".otf")
            if p.exists():
                return p
        return None

    @staticmethod
    def _style_order(bold: bool, italic: bool) -> list[str]:
        if bold and italic:
            return ["BoldItalic", "Bold", "Italic", "Regular"]
        if bold:
            return ["Bold", "Regular"]
        if italic:
            return ["Italic", "Regular"]
        return ["Regular"]

    def available(self) -> list[str]:
        return sorted(p.name for p in self.dir.glob("*.tt[fc]")) + \
            sorted(p.name for p in self.dir.glob("*.otf"))

    def missing_scripts(self, langs: list[str]) -> list[str]:
        out = []
        for code in langs:
            sc = script_of(code)
            sans, _ = self.FAMILIES[sc]
            if not self._file(sans, False, False):
                out.append(sc)
        return out

    # -- the actual policy ---------------------------------------------
    def resolve(self, original_font: str, target_lang: str, *, flags: int = 0,
                embedded_path: str | None = None) -> ResolvedFont:
        key = (original_font, target_lang, flags, embedded_path)
        hit = self._cache.get(key)
        if hit:
            return hit
        desc = descriptor_from_span(original_font, flags, embedded_path)
        script = script_of(target_lang)
        sans_stem, serif_stem = self.FAMILIES[script]
        stem = serif_stem if desc.serif else sans_stem
        italic = desc.italic and script not in self.NO_ITALIC
        path = self._file(stem, desc.bold, italic)
        reason_bits = []
        if path is None:
            # fall back within the same script before crossing scripts
            other = sans_stem if desc.serif else serif_stem
            path = self._file(other, desc.bold, italic)
            if path is not None:
                stem = other
                reason_bits.append(f"{'serif' if desc.serif else 'sans'} cut unavailable")
        if path is None:
            path = self._file(self.FAMILIES["latin"][0], desc.bold, False)
            reason_bits.append("no face for script; Latin fallback (expect tofu)")
        if path is None:
            raise FileNotFoundError(
                f"No vendored font for script '{script}'. Run scripts/fetch_fonts.py."
            )
        weight = 700 if desc.bold else 400
        sub_xh = measure_x_height(str(path))
        factor = desc.x_height / sub_xh if sub_xh else 1.0
        factor = max(0.85, min(1.15, factor))
        if desc.italic and italic is False:
            reason_bits.append("italic dropped (no italic cut for script)")
        if desc.mono:
            reason_bits.append("monospaced source mapped to proportional face")
        family = path.stem.split("-")[0]
        substituted = _normalise(desc.family) != _normalise(family)
        rf = ResolvedFont(
            alias=f"ll-{path.stem.lower()}", path=path, family=family,
            weight=weight, italic=italic, serif=desc.serif,
            x_height=sub_xh, size_factor=factor, substituted=substituted,
            original_name=desc.raw_name,
            reason="; ".join(reason_bits) or (
                "script coverage" if substituted else "kept"),
            css_family=path.stem,
        )
        self._cache[key] = rf
        return rf


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


_default: FontResolver | None = None


def default_resolver() -> FontResolver:
    global _default
    if _default is None:
        _default = FontResolver()
    return _default
