"""MockProvider -- the offline demo and test engine (§5.5).

This mock exists to *stress* the layout engine, not to stand in politely for a
model. A mock that echoes the source at the same length means the fit ladder,
the overflow detector and the whole validation layer never execute, and the
prototype looks flawless until the first real API call.

So it:

* is deterministic -- seeded by blake2b(segment_id + target_lang), never
  Python's salted hash, so two runs produce identical bytes;
* writes in the *target script*, by transliterating source syllables into
  plausible target-script sequences, which makes Devanagari shaping, Arabic
  bidi and CJK line breaking all execute for real;
* applies empirically realistic length ratios with variance, so some blocks
  overflow and some underflow;
* preserves every ⟦Pn⟧ placeholder exactly;
* returns confidences from a seeded beta distribution centred at 0.93, with
  about 4% of segments below 0.80 so the review queue has something in it;
* simulates latency (40ms + 8ms per word) so the progress UI is honest.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import re

from backend.config import settings
from backend.providers.base import (DetectRequest, DetectResponse, SegmentOut,
                                    TranslateRequest, TranslateResponse)
from backend.translation.protect import TOKEN_RE
from backend.utils.langs import lang as lang_of

SYLLABLES = {
    "devanagari": ["क", "का", "कि", "की", "कु", "के", "को", "ख", "खा", "ग", "गा",
                   "गि", "घ", "च", "चा", "चि", "छ", "ज", "जा", "जि", "ट", "टा",
                   "ठ", "ड", "त", "ता", "ति", "ती", "थ", "द", "दा", "दि", "ध",
                   "न", "ना", "नि", "नी", "प", "पा", "पि", "प्र", "फ", "ब", "बा",
                   "भ", "म", "मा", "मि", "य", "या", "र", "रा", "रि", "ल", "ला",
                   "व", "वा", "वि", "श", "शा", "ष", "स", "सा", "सि", "स्त", "ह",
                   "हा", "क्ष", "त्र", "ज्ञ", "श्र", "ण", "ङ्ग"],
    "arabic": ["مر", "حب", "بال", "عا", "لم", "نص", "مط", "بو", "ية", "سا",
               "لة", "تق", "ري", "ر", "من", "ال", "مش", "روع", "خط", "ة",
               "تن", "فيذ", "مو", "عد", "جد", "ول", "بي", "ان", "قا", "نون",
               "شر", "كة", "إد", "ارة", "مع", "لو", "مات"],
    "jp": ["こ", "ん", "に", "ち", "は", "せ", "か", "い", "の", "文", "書", "翻",
           "訳", "処", "理", "系", "統", "情", "報", "管", "理", "報", "告", "書",
           "資", "料", "確", "認", "対", "応", "実", "施", "検", "討", "結", "果",
           "サ", "ー", "ビ", "ス", "デ", "ー", "タ"],
    "sc": ["你", "好", "世", "界", "文", "档", "翻", "译", "系", "统", "信", "息",
           "管", "理", "报", "告", "资", "料", "确", "认", "处", "理", "实", "施",
           "检", "查", "结", "果", "服", "务", "数", "据", "接", "口", "规", "范",
           "版", "本", "说", "明"],
}
LATIN_STEMS = {
    "de": ["Verarbeitung", "Schnittstelle", "Bestandsdaten", "Anforderung",
           "Zuständigkeit", "Übermittlung", "Genehmigungsverfahren",
           "Berichtszeitraum", "Rückmeldung", "Massenverarbeitung",
           "Nachvollziehbarkeit", "Betriebsprotokoll", "Verwaltungsvorschrift"],
    "fr": ["traitement", "interface", "données", "exigence", "compétence",
           "transmission", "procédure", "période", "réponse", "vérification",
           "responsabilité", "réglementation", "établissement"],
    "es": ["procesamiento", "interfaz", "datos", "requisito", "competencia",
           "transmisión", "procedimiento", "período", "respuesta",
           "verificación", "responsabilidad", "reglamentación"],
    "en": ["processing", "interface", "record data", "requirement", "authority",
           "transmission", "procedure", "reporting period", "response",
           "verification", "accountability", "regulation", "establishment"],
}


def _seed(segment_id: str, target_lang: str) -> int:
    h = hashlib.blake2b(f"{segment_id}|{target_lang}".encode(), digest_size=8)
    return int.from_bytes(h.digest(), "big")


class MockProvider:
    name = "mock"

    def __init__(self, latency_scale: float | None = None):
        self.latency_scale = (settings.mock_latency_scale
                              if latency_scale is None else latency_scale)

    async def detect_language(self, req: DetectRequest) -> DetectResponse:
        from backend.parsers.pdf_parser import detect_language_text
        lang, conf = detect_language_text(req.text)
        return DetectResponse(lang=lang, confidence=conf)

    async def translate(self, req: TranslateRequest) -> TranslateResponse:
        out: list[SegmentOut] = []
        total_words = 0
        for seg in req.segments:
            words = max(1, len(seg.text.split()))
            total_words += words
            text = self._translate_one(seg.id, seg.text, req.target_lang)
            conf = self._confidence(seg.id, req.target_lang)
            out.append(SegmentOut(id=seg.id, text=text, confidence=conf,
                                  note="mock"))
        if self.latency_scale:
            delay = (0.040 + 0.008 * total_words) * self.latency_scale
            await asyncio.sleep(min(delay, 8.0))
        chars = sum(len(s.text) for s in req.segments)
        return TranslateResponse(segments=out, input_tokens=chars // 4,
                                 output_tokens=sum(len(s.text) for s in out) // 4)

    async def review(self, req):
        from backend.providers.base import ReviewResponse
        return ReviewResponse(ok=True, score=0.95,
                              comments=["mock review: no findings"])

    # ------------------------------------------------------------------
    def _translate_one(self, segment_id: str, text: str, target_lang: str) -> str:
        rng = random.Random(_seed(segment_id, target_lang))
        meta = lang_of(target_lang)
        ratio = max(0.35, rng.gauss(meta.expansion, meta.sigma))
        parts = TOKEN_RE.split(text)
        # re.split with a group yields [text, num, text, num, ...]
        pieces: list[str] = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                pieces.append(f"⟦P{part}⟧")
                continue
            pieces.append(self._render(part, meta.script, target_lang, ratio, rng))
        joined = "".join(pieces)
        return re.sub(r"[ \t]{2,}", " ", joined).strip()

    def _render(self, chunk: str, script: str, target_lang: str, ratio: float,
                rng: random.Random) -> str:
        if not chunk.strip():
            return chunk
        target_len = max(1, int(round(len(chunk) * ratio)))
        lead = " " if chunk[:1].isspace() else ""
        trail = " " if chunk[-1:].isspace() else ""
        tail_punct = ""
        stripped = chunk.strip()
        if stripped and stripped[-1] in ".!?;:।؟。！":
            # Terminal punctuation is translated too. Copying the source's
            # danda into a German sentence would carry a Devanagari character
            # into a Latin face -- which renders as an empty box, and is exactly
            # the class of silent breakage this project is about.
            question = stripped[-1] in "?؟？"
            tail_punct = {
                "devanagari": "?" if question else "।",
                "arabic": "؟" if question else ".",
                "jp": "？" if question else "。",
                "sc": "？" if question else "。",
            }.get(script, "?" if question else ".")
            target_len = max(1, target_len - 1)
        if script in SYLLABLES:
            pool = SYLLABLES[script]
            body: list[str] = []
            length = 0
            word: list[str] = []
            while length < target_len:
                syl = pool[rng.randrange(len(pool))]
                word.append(syl)
                length += len(syl)
                if script in ("jp", "sc"):
                    continue                      # no spaces in CJK
                if rng.random() < 0.32:
                    body.append("".join(word))
                    word = []
                    length += 1
            if word:
                body.append("".join(word))
            text = ("" if script in ("jp", "sc") else " ").join(body)
        else:
            stems = LATIN_STEMS.get(target_lang, LATIN_STEMS["en"])
            words: list[str] = []
            length = 0
            while length < target_len:
                w = stems[rng.randrange(len(stems))]
                if rng.random() < 0.25:
                    w = w.lower() if rng.random() < 0.5 else w.capitalize()
                words.append(w)
                length += len(w) + 1
            text = " ".join(words)
            if stripped[:1].isupper():
                text = text[:1].upper() + text[1:]
        return f"{lead}{text}{tail_punct}{trail}"

    def _confidence(self, segment_id: str, target_lang: str) -> float:
        rng = random.Random(_seed(segment_id, target_lang) ^ 0xC0FFEE)
        # beta(18.6, 1.4) has mean ~0.93; the low tail lands ~4% below 0.80
        v = rng.betavariate(18.6, 1.4)
        return round(min(0.995, max(0.42, v)), 4)
