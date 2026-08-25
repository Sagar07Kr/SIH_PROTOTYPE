"""Term consistency (§5.3).

A term of two or more words that recurs at least three times in a document is
translated once, then locked: every later segment gets it as a glossary entry.
This is what stops "Artificial Intelligence" becoming three different Hindi
phrases across thirty-eight pages.

Locking is enforced by substitution, not by instruction. A locked term is
masked as a protected span whose restored value is the target term, so the
model cannot quietly ignore it -- see translation/protect.py.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

WORD_RE = re.compile(r"[^\W\d_](?:[\w'’-]*)", re.UNICODE)
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is",
    "are", "was", "were", "be", "by", "as", "at", "that", "this", "it", "from",
    "der", "die", "das", "und", "oder", "von", "zu", "im", "in", "für", "auf",
    "mit", "ist", "sind", "den", "dem", "des", "eine", "einer", "einen",
}
MIN_WORDS = 2
MAX_WORDS = 4
MIN_OCCURRENCES = 3


@dataclass
class TermMemory:
    target_lang: str
    locked: dict[str, str] = field(default_factory=dict)     # source -> target
    candidates: dict[str, int] = field(default_factory=dict)  # source -> count
    user_glossary: dict[str, str] = field(default_factory=dict)

    def scan(self, texts: list[str]) -> dict[str, int]:
        """Find repeated multi-word phrases across the whole document."""
        counts: Counter = Counter()
        for text in texts:
            words = WORD_RE.findall(text)
            for n in range(MIN_WORDS, MAX_WORDS + 1):
                for i in range(len(words) - n + 1):
                    gram = words[i:i + n]
                    if gram[0].lower() in STOP or gram[-1].lower() in STOP:
                        continue
                    if sum(1 for w in gram if w[0].isupper()) == 0 and n > 2:
                        continue                # long lowercase n-grams: noise
                    counts[" ".join(gram)] += 1
        self.candidates = {t: c for t, c in counts.items()
                           if c >= MIN_OCCURRENCES}
        # keep the longest phrase when one contains another
        keys = sorted(self.candidates, key=len, reverse=True)
        kept: dict[str, int] = {}
        for k in keys:
            if not any(k != other and k in other for other in kept):
                kept[k] = self.candidates[k]
        self.candidates = kept
        return self.candidates

    def glossary_for(self, text: str) -> dict[str, str]:
        """Locked entries relevant to this segment. User entries always win."""
        out = {t: v for t, v in self.locked.items()
               if t.lower() in text.lower()}
        for t, v in self.user_glossary.items():
            if t.lower() in text.lower():
                out[t] = v
        return out

    def pending_terms(self, text: str) -> list[str]:
        """Candidate terms in this segment that are not locked yet."""
        low = text.lower()
        return [t for t in self.candidates
                if t.lower() in low and t not in self.locked
                and t not in self.user_glossary]

    def lock(self, source_term: str, target_term: str) -> None:
        if source_term and target_term:
            self.locked[source_term] = target_term

    def to_dict(self) -> dict:
        return {"target_lang": self.target_lang, "locked": self.locked,
                "candidates": self.candidates,
                "user_glossary": self.user_glossary}
