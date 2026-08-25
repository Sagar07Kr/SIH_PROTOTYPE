"""Language metadata for the eight supported targets.

`expansion` is the empirically observed mean character-count ratio relative to
English source text; the MockProvider uses it (§5.5) and the fit ladder uses it
only for logging -- never for deciding fit, which is always measured.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lang:
    code: str
    name: str
    script: str          # latin | devanagari | arabic | jp | sc
    rtl: bool
    expansion: float
    sigma: float
    numerals: str = "latin"      # target numeral system
    line_height_bonus: float = 0.0   # extra leading needed by the script
    word_spaced: bool = True     # False => needs UAX#14-style breaking


LANGS: dict[str, Lang] = {
    "en": Lang("en", "English", "latin", False, 1.00, 0.10),
    "de": Lang("de", "German", "latin", False, 1.32, 0.15),
    "fr": Lang("fr", "French", "latin", False, 1.22, 0.12),
    "es": Lang("es", "Spanish", "latin", False, 1.20, 0.12),
    "hi": Lang("hi", "Hindi", "devanagari", False, 1.18, 0.12,
               line_height_bonus=0.08),
    "ar": Lang("ar", "Arabic", "arabic", True, 1.05, 0.14,
               line_height_bonus=0.05),
    "ja": Lang("ja", "Japanese", "jp", False, 0.68, 0.10, word_spaced=False),
    "zh": Lang("zh", "Chinese (Simplified)", "sc", False, 0.62, 0.10,
               word_spaced=False),
}

SUPPORTED = tuple(LANGS)


def lang(code: str) -> Lang:
    c = (code or "en").lower().split("-")[0]
    return LANGS.get(c, LANGS["en"])


def is_rtl(code: str) -> bool:
    return lang(code).rtl


def script_of(code: str) -> str:
    return lang(code).script
