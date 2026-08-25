"""Provider interface (§5.4).

Provider code touches nothing outside this package. Everything crossing the
boundary is a Pydantic model, so a malformed response is a typed error at the
edge rather than an exception in the middle of the reconstruction engine.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class DetectRequest(BaseModel):
    text: str
    candidates: list[str] = Field(default_factory=list)


class DetectResponse(BaseModel):
    lang: str
    confidence: float = 0.0
    alternatives: dict[str, float] = Field(default_factory=dict)


class SegmentIn(BaseModel):
    id: str
    text: str
    element_type: str = "paragraph"
    context_before: str = ""
    context_after: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)


class TranslateRequest(BaseModel):
    segments: list[SegmentIn]
    source_lang: str
    target_lang: str
    style: str = "neutral"
    domain: str = "general"


class SegmentOut(BaseModel):
    id: str
    text: str
    confidence: float = 0.0
    note: str = ""


class TranslateResponse(BaseModel):
    segments: list[SegmentOut]
    input_tokens: int = 0
    output_tokens: int = 0


class ReviewRequest(BaseModel):
    source: str
    target: str
    target_lang: str


class ReviewResponse(BaseModel):
    ok: bool = True
    score: float = 1.0
    comments: list[str] = Field(default_factory=list)


@runtime_checkable
class AIProvider(Protocol):
    name: str

    async def detect_language(self, req: DetectRequest) -> DetectResponse: ...

    async def translate(self, req: TranslateRequest) -> TranslateResponse: ...

    async def review(self, req: ReviewRequest) -> ReviewResponse: ...
