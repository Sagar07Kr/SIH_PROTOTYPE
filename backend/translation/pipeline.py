"""Translation pipeline: protect, batch, translate, verify, repair.

The pipeline owns everything between "here are the segments" and "here are the
target strings", including the parts that are easy to skip and expensive to
omit: placeholder verification with a repair round, per-segment fallback to the
source text, and term locking.

Failure is per segment. A provider that mangles one paragraph costs that
paragraph, not the job -- and the paragraph is reported as untranslated rather
than silently emitted in the source language (§12).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from backend.config import settings
from backend.providers.base import (AIProvider, SegmentIn, TranslateRequest)
from backend.translation.protect import Masked, mask, repair_instruction, restore, verify
from backend.translation.segmenter import Segment
from backend.translation.termmemory import TermMemory
from backend.utils.errors import AppError, ProviderError

BATCH_CHARS = 4000
BATCH_SEGMENTS = 12


@dataclass
class TranslatedSegment:
    id: str
    source: str
    target: str
    confidence: float
    status: str = "OK"                # OK | PROTECTION_FAILURE | PROVIDER_FAILURE
    placeholders: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "target": self.target,
                "confidence": round(self.confidence, 4), "status": self.status,
                "placeholders": self.placeholders, "issues": self.issues}


@dataclass
class PipelineResult:
    segments: list[TranslatedSegment]
    input_tokens: int = 0
    output_tokens: int = 0
    ms: int = 0
    term_memory: dict = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, TranslatedSegment]:
        return {s.id: s for s in self.segments}

    def texts(self) -> dict[str, str]:
        """Blocks to re-place. Verbatim blocks are deliberately excluded.

        Code, formulas and list markers keep their *original glyphs* by not
        being redacted at all, which is a stronger guarantee than erasing and
        redrawing them in a substituted face -- and it avoids carrying source
        punctuation (a Devanagari danda, say) into a face that cannot draw it.
        """
        return {s.id: s.target for s in self.segments
                if s.target.strip() and s.status != "VERBATIM"}


class TranslationPipeline:
    def __init__(self, provider: AIProvider, target_lang: str, *,
                 source_lang: str = "en", style: str = "neutral",
                 domain: str = "general",
                 user_glossary: dict[str, str] | None = None):
        self.provider = provider
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.style = style
        self.domain = domain
        self.memory = TermMemory(target_lang, user_glossary=dict(user_glossary or {}))

    async def run(self, segments: list[Segment],
                  on_progress=None) -> PipelineResult:
        t0 = time.perf_counter()
        translatable = [s for s in segments if s.translatable and s.text.strip()]
        verbatim = [s for s in segments if not (s.translatable and s.text.strip())]
        self.memory.scan([s.text for s in translatable])
        await self._lock_terms(translatable)

        results: list[TranslatedSegment] = [
            TranslatedSegment(s.id, s.text, s.text, 1.0, "VERBATIM")
            for s in verbatim]
        in_tok = out_tok = 0
        done = 0
        for batch in _batches(translatable):
            masked: dict[str, Masked] = {}
            wire: list[SegmentIn] = []
            for seg in batch:
                m = mask(seg.text, self.memory.glossary_for(seg.text))
                masked[seg.id] = m
                wire.append(SegmentIn(
                    id=seg.id, text=m.text, element_type=seg.element_type,
                    context_before=seg.context_before[-400:],
                    context_after=seg.context_after[:400],
                    glossary=self.memory.glossary_for(seg.text)))
            try:
                resp = await self.provider.translate(TranslateRequest(
                    segments=wire, source_lang=self.source_lang,
                    target_lang=self.target_lang, style=self.style,
                    domain=self.domain))
                in_tok += resp.input_tokens
                out_tok += resp.output_tokens
                got = {s.id: s for s in resp.segments}
            except AppError as exc:
                got = {}
                for seg in batch:
                    results.append(TranslatedSegment(
                        seg.id, seg.text, seg.text, 0.0, "PROVIDER_FAILURE",
                        issues=[{"code": exc.code, "severity": "ERROR",
                                 "message": exc.message}]))
            for seg in batch:
                if seg.id not in got:
                    continue
                results.append(await self._finalise(seg, masked[seg.id],
                                                    got[seg.id]))
            done += len(batch)
            if on_progress:
                on_progress(done, len(translatable))
        return PipelineResult(results, in_tok, out_tok,
                              int((time.perf_counter() - t0) * 1000),
                              self.memory.to_dict())

    # ------------------------------------------------------------------
    async def _finalise(self, seg: Segment, m: Masked, out) -> TranslatedSegment:
        text = out.text or ""
        missing = verify(m, text)
        issues: list[dict] = []
        if missing:
            # one repair round with an explicit instruction (§5.1)
            try:
                repaired = await self.provider.translate(TranslateRequest(
                    segments=[SegmentIn(id=seg.id, text=m.text,
                                        element_type=seg.element_type,
                                        context_before=repair_instruction(missing))],
                    source_lang=self.source_lang, target_lang=self.target_lang,
                    style=self.style, domain=self.domain))
                cand = repaired.segments[0].text if repaired.segments else ""
                if cand and not verify(m, cand):
                    text = cand
                    issues.append({"code": "PLACEHOLDER_REPAIRED",
                                   "severity": "INFO",
                                   "message": f"{len(missing)} placeholder(s) "
                                              "restored on retry"})
                    missing = []
            except AppError as exc:
                issues.append({"code": exc.code, "severity": "WARNING",
                               "message": exc.message})
        if missing:
            issues.append({
                "code": "PROTECTION_FAILURE", "severity": "ERROR",
                "message": f"{len(missing)} protected span(s) did not survive "
                           "translation; the source text was kept for this "
                           "segment", "tokens": missing})
            return TranslatedSegment(seg.id, seg.text, seg.text, 0.0,
                                     "PROTECTION_FAILURE", m.to_dict(), issues)
        final, dropped = restore(text, m)
        if dropped:
            issues.append({"code": "PLACEHOLDER_DROPPED", "severity": "WARNING",
                           "message": f"{len(dropped)} placeholder(s) missing "
                                      "after restore", "tokens": dropped})
        conf = float(out.confidence or 0.0)
        if seg.ocr_confidence is not None and \
                seg.ocr_confidence < settings.ocr_min_confidence:
            issues.append({
                "code": "LOW_OCR_CONFIDENCE", "severity": "WARNING",
                "message": f"OCR confidence {seg.ocr_confidence:.0f}% on the "
                           "source of this block"})
            return TranslatedSegment(seg.id, seg.text, final, conf,
                                     "LOW_OCR_CONFIDENCE", m.to_dict(), issues)
        return TranslatedSegment(seg.id, seg.text, final, conf, "OK",
                                 m.to_dict(), issues)

    async def _lock_terms(self, segments: list[Segment]) -> None:
        """Translate each recurring term once, alone, and lock the result."""
        terms = list(self.memory.candidates)[:40]
        if not terms:
            return
        wire = [SegmentIn(id=f"term::{i}", text=t, element_type="term")
                for i, t in enumerate(terms)]
        try:
            resp = await self.provider.translate(TranslateRequest(
                segments=wire, source_lang=self.source_lang,
                target_lang=self.target_lang, style=self.style,
                domain=self.domain))
        except AppError:
            return
        for out in resp.segments:
            try:
                idx = int(out.id.split("::")[1])
            except (IndexError, ValueError):
                continue
            if 0 <= idx < len(terms) and out.text.strip():
                self.memory.lock(terms[idx], out.text.strip())


def _batches(segments: list[Segment]) -> list[list[Segment]]:
    out: list[list[Segment]] = []
    cur: list[Segment] = []
    size = 0
    for s in segments:
        if cur and (size + len(s.text) > BATCH_CHARS or len(cur) >= BATCH_SEGMENTS):
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s.text)
    if cur:
        out.append(cur)
    return out
