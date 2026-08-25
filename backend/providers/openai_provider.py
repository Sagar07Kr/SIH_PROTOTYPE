"""OpenAI-compatible provider.

Works against api.openai.com or any endpoint that speaks the same chat
completions shape (set AI_BASE_URL). Responses are parsed as JSON and validated
with Pydantic; anything else raises ProviderResponseError, is retried once with
the schema restated, and then becomes a per-segment failure. A provider hiccup
must never crash a job (§5.4).
"""
from __future__ import annotations

import asyncio
import json
import random

import httpx
from pydantic import BaseModel, ValidationError

from backend.config import settings
from backend.providers.base import (DetectRequest, DetectResponse, ReviewRequest,
                                    ReviewResponse, SegmentOut,
                                    TranslateRequest, TranslateResponse)
from backend.utils.errors import (ProviderError, ProviderRateLimited,
                                  ProviderResponseError, ProviderTimeout)
from backend.utils.langs import lang as lang_of

SYSTEM = (
    "You are a professional document translator. You translate one unit of a "
    "document at a time and return only that unit.\n"
    "Absolute rules:\n"
    "1. Placeholders of the form U+27E6 P<number> U+27E7 must appear in your "
    "output exactly once each, unchanged. They stand for numbers, URLs, code "
    "and proper nouns.\n"
    "2. Do not add, remove, summarise or explain anything. No preamble.\n"
    "3. Preserve the register and formality of the source.\n"
    "4. Keep the unit's role: a heading stays a heading, a table cell stays "
    "terse.\n"
    "Return JSON: {\"segments\":[{\"id\":\"...\",\"text\":\"...\","
    "\"confidence\":0.0-1.0}]}"
)


class _Wire(BaseModel):
    segments: list[SegmentOut]


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        self.api_key = api_key or settings.ai_api_key
        self.base_url = (base_url or settings.ai_base_url).rstrip("/")
        self.model = model or settings.ai_model
        if not self.api_key:
            raise ProviderError(
                "AI_PROVIDER=openai but no AI_API_KEY is set. Use the mock "
                "provider for the offline demo.", retryable=False)

    # ------------------------------------------------------------------
    async def _post(self, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        delay = 1.0
        last: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=settings.ai_timeout_s) as c:
                    r = await c.post(url, headers=headers, json=payload)
                if r.status_code == 429:
                    raise ProviderRateLimited("The provider is rate limiting "
                                              "this job.")
                if r.status_code >= 500:
                    raise ProviderError(f"Provider returned {r.status_code}.")
                if r.status_code >= 400:
                    raise ProviderError(
                        f"Provider rejected the request ({r.status_code}).",
                        {"body": r.text[:400]}, retryable=False)
                return r.json()
            except httpx.TimeoutException as exc:
                last = ProviderTimeout("The provider did not respond in time.",
                                       {"timeout_s": settings.ai_timeout_s})
            except (ProviderRateLimited, ProviderError) as exc:
                last = exc
                if not getattr(exc, "retryable", True):
                    raise
            await asyncio.sleep(delay + random.random() * 0.3)
            delay *= 2
        raise last or ProviderError("Provider call failed.")

    async def detect_language(self, req: DetectRequest) -> DetectResponse:
        payload = {
            "model": self.model, "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content":
                 "Identify the dominant language of the text. Return JSON "
                 "{\"lang\":\"ISO 639-1\",\"confidence\":0.0-1.0}."},
                {"role": "user", "content": req.text[:4000]}]}
        data = await self._post(payload)
        try:
            body = json.loads(data["choices"][0]["message"]["content"])
            return DetectResponse(lang=str(body["lang"])[:5],
                                  confidence=float(body.get("confidence", 0.5)))
        except Exception as exc:
            raise ProviderResponseError("Language detection returned an "
                                        "unexpected shape.", {"error": str(exc)})

    async def translate(self, req: TranslateRequest) -> TranslateResponse:
        target = lang_of(req.target_lang)
        user = {
            "source_lang": req.source_lang, "target_lang": target.name,
            "style": req.style, "domain": req.domain,
            "segments": [s.model_dump() for s in req.segments]}
        payload = {"model": self.model, "temperature": 0.2,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user",
                                 "content": json.dumps(user, ensure_ascii=False)}]}
        data = await self._post(payload)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            wire = _Wire.model_validate_json(content)
        except (ValidationError, ValueError):
            # one retry with the schema restated, per §5.4
            payload["messages"].append({"role": "assistant", "content": content})
            payload["messages"].append(
                {"role": "user", "content":
                 "That was not valid JSON in the required shape. Return only "
                 "{\"segments\":[{\"id\":...,\"text\":...,\"confidence\":...}]}"})
            data = await self._post(payload)
            content = data["choices"][0]["message"]["content"]
            try:
                wire = _Wire.model_validate_json(content)
            except (ValidationError, ValueError) as exc:
                raise ProviderResponseError(
                    "The provider did not return the required JSON shape.",
                    {"error": str(exc)[:300]})
        usage = data.get("usage", {}) or {}
        return TranslateResponse(segments=wire.segments,
                                 input_tokens=int(usage.get("prompt_tokens", 0)),
                                 output_tokens=int(usage.get("completion_tokens", 0)))

    async def review(self, req: ReviewRequest) -> ReviewResponse:
        payload = {"model": self.model, "temperature": 0,
                   "response_format": {"type": "json_object"},
                   "messages": [
                       {"role": "system", "content":
                        "Compare a translation with its source. Return JSON "
                        "{\"ok\":bool,\"score\":0-1,\"comments\":[string]}."},
                       {"role": "user", "content": json.dumps(
                           {"source": req.source, "target": req.target,
                            "target_lang": req.target_lang}, ensure_ascii=False)}]}
        data = await self._post(payload)
        try:
            body = json.loads(data["choices"][0]["message"]["content"])
            return ReviewResponse(ok=bool(body.get("ok", True)),
                                  score=float(body.get("score", 1.0)),
                                  comments=list(body.get("comments", []))[:10])
        except Exception as exc:
            raise ProviderResponseError("Review returned an unexpected shape.",
                                        {"error": str(exc)})
