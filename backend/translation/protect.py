"""Protected spans (§5.1).

Numbers, identifiers, URLs, citations, code and formulas are masked into
placeholder tokens before any model sees the text and restored afterwards. The
placeholder is `⟦P{n}⟧`: no natural language uses those brackets, so no model
translates, reorders or "improves" them.

Every mask is verified on restore. A segment whose placeholders came back
mangled is retried once with an explicit repair instruction and, failing that,
falls back to the source text with a PROTECTION_FAILURE issue -- shipping a
mangled URL or an altered number is never acceptable (I6).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OPEN, CLOSE = "⟦", "⟧"
TOKEN_RE = re.compile(rf"{OPEN}P(\d+){CLOSE}")

#: order matters: earlier patterns win over later ones on overlap
PROTECT_PATTERNS: list[tuple[str, str]] = [
    ("URL", r"\b(?:https?://|www\.)[^\s<>()\[\]{}\"']+"),
    ("EMAIL", r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
    ("IPV4", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("FILE_PATH", r"(?:[A-Za-z]:\\|/)[\w./\\-]{3,}"),
    ("LATEX_MATH", r"\$[^$]{1,120}\$|\\\([^)]{1,120}\\\)"),
    ("INLINE_CODE", r"`[^`]{1,120}`"),
    ("ISO_DATE", r"\b\d{4}-\d{2}-\d{2}\b"),
    ("LOCALE_DATE", r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
    ("CITATION", r"\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]"),
    ("CURRENCY", r"[€$£¥₹]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:EUR|USD|GBP|INR|JPY|CNY)\b"),
    ("PERCENTAGE", r"\b\d[\d.,]*\s?%"),
    ("NUMBER_WITH_UNIT",
     r"\b\d[\d.,]*\s?(?:mm|cm|m|km|kg|g|mg|ms|s|min|h|pt|px|dpi|MB|GB|KB|TB|"
     r"Hz|kHz|MHz|V|W|kW|°C|°F|bar|Nm)\b"),
    # no \b before the section sign: § is not a word character, so \b would
    # only match when a letter or digit precedes it. The reference marker stays
    # inside the span, but a *source-language* word never does -- protecting
    # "धारा 17(2)" would carry Devanagari into a Japanese page, where the
    # substituted face has no glyphs for it.
    ("SECTION_REF", r"(?:§+|Abs\.|Art\.)\s?\d+(?:\s?\(\d+\))?"),
    ("CLAUSE_REF", r"\b\d{1,3}\s?\(\d{1,3}\)"),
    ("ACRONYM_3PLUS_CAPS", r"\b[A-Z]{3,}(?:-\d+)?\b"),
    ("CODE_ID", r"\b[A-Z]{1,3}[-/]\d{2,}(?:[-/]\d+)*\b"),
    ("BARE_NUMBER", r"\b\d[\d.,]*\b"),
]
COMPILED = [(name, re.compile(rx)) for name, rx in PROTECT_PATTERNS]


@dataclass
class Masked:
    text: str
    tokens: dict[str, str] = field(default_factory=dict)   # token -> original
    kinds: dict[str, str] = field(default_factory=dict)    # token -> pattern name

    @property
    def count(self) -> int:
        return len(self.tokens)

    def to_dict(self) -> dict:
        return {"tokens": self.tokens, "kinds": self.kinds}


def mask(text: str, locked_terms: dict[str, str] | None = None,
         start_index: int = 0) -> Masked:
    """Replace protected spans with placeholders.

    `locked_terms` maps a source term to the target string it must become; such
    a term is promoted to a protected span whose restored value is the *target*
    term, which is how glossary locking and term consistency are enforced
    (§5.3) without trusting the model to obey an instruction.
    """
    spans: list[tuple[int, int, str, str]] = []          # start, end, kind, value
    if locked_terms:
        for term, target in sorted(locked_terms.items(), key=lambda kv: -len(kv[0])):
            if not term.strip():
                continue
            for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
                spans.append((m.start(), m.end(), "GLOSSARY_LOCKED_TERM", target))
    for kind, rx in COMPILED:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), kind, m.group(0)))

    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: list[tuple[int, int, str, str]] = []
    last_end = -1
    for s in spans:
        if s[0] < last_end:
            continue                                    # overlaps a stronger span
        chosen.append(s)
        last_end = s[1]

    out: list[str] = []
    tokens: dict[str, str] = {}
    kinds: dict[str, str] = {}
    cursor = 0
    for n, (a, b, kind, value) in enumerate(chosen, start=start_index):
        token = f"{OPEN}P{n}{CLOSE}"
        out.append(text[cursor:a])
        out.append(token)
        tokens[token] = value
        kinds[token] = kind
        cursor = b
    out.append(text[cursor:])
    return Masked("".join(out), tokens, kinds)


def restore(text: str, masked: Masked) -> tuple[str, list[str]]:
    """Put the protected values back. Returns (text, missing tokens)."""
    missing: list[str] = []
    out = text
    for token, value in masked.tokens.items():
        if out.count(token) == 1:
            out = out.replace(token, value)
        elif out.count(token) == 0:
            missing.append(token)
        else:
            # duplicated by the model: first wins, the rest are dropped
            first = out.index(token)
            out = (out[:first] + value
                   + out[first + len(token):].replace(token, ""))
    leftovers = TOKEN_RE.findall(out)
    if leftovers:
        out = TOKEN_RE.sub("", out)
    return re.sub(r"\s{2,}", " ", out).strip(), missing


def verify(masked: Masked, translated: str) -> list[str]:
    """Tokens that did not survive the round trip, exactly once each."""
    return [t for t in masked.tokens if translated.count(t) != 1]


def repair_instruction(missing: list[str]) -> str:
    return ("The following placeholders must appear exactly once each, "
            "unchanged: " + ", ".join(missing) +
            ". Return only the corrected translation.")
