"""Provider factory. Two implementations, no more (§11)."""
from __future__ import annotations

from backend.config import settings
from backend.providers.base import AIProvider


def get_provider(name: str | None = None) -> AIProvider:
    chosen = (name or settings.ai_provider or "mock").lower()
    if chosen == "mock":
        from backend.providers.mock import MockProvider
        return MockProvider()
    if chosen in ("openai", "openai-compatible"):
        from backend.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    from backend.utils.errors import BadRequest
    raise BadRequest(f"Unknown AI provider '{chosen}'. Use 'mock' or 'openai'.")


def available_providers() -> list[str]:
    out = ["mock"]
    if settings.ai_api_key:
        out.append("openai")
    return out
