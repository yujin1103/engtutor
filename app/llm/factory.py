"""LLM_BACKEND 환경변수 하나로 백엔드를 고른다. 호출부는 어느 쪽인지 몰라도 된다."""

from __future__ import annotations

from functools import lru_cache

from ..config import Settings, get_settings
from .anthropic_client import AnthropicClient
from .base import LLMClient
from .ollama_client import OllamaClient


def build_client(settings: Settings | None = None) -> LLMClient:
    s = settings or get_settings()
    if s.llm_backend == "anthropic":
        return AnthropicClient(
            api_key=s.anthropic_api_key,
            model=s.anthropic_model,
            timeout=s.request_timeout,
        )
    return OllamaClient(
        base_url=s.ollama_base_url,
        model=s.ollama_model,
        num_ctx=s.ollama_num_ctx,
        keep_alive=s.ollama_keep_alive,
        timeout=s.request_timeout,
    )


@lru_cache
def get_client() -> LLMClient:
    return build_client()
