"""백엔드 전환 (명세 8절 pytest 요구 항목).

호출부가 백엔드를 몰라야 한다는 게 핵심이라, LLM_BACKEND 하나로
구현체가 갈리고 두 백엔드가 같은 계약을 지키는지 고정한다.
"""

from __future__ import annotations

import inspect

import pytest

from app.config import Settings
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMClient, LLMError
from app.llm.factory import build_client
from app.llm.ollama_client import OllamaClient


def _settings(**over) -> Settings:
    base = {
        "llm_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "ollama_model": "qwen3:14b",
        "anthropic_api_key": "sk-ant-test",
        "anthropic_model": "claude-haiku-4-5",
    }
    return Settings(**{**base, **over})


def test_ollama_backend_is_selected():
    client = build_client(_settings(llm_backend="ollama"))
    assert isinstance(client, OllamaClient)
    assert client.name == "ollama"
    assert "qwen3:14b" in client.describe()


def test_anthropic_backend_is_selected():
    client = build_client(_settings(llm_backend="anthropic"))
    assert isinstance(client, AnthropicClient)
    assert client.name == "anthropic"
    assert "claude-haiku-4-5" in client.describe()


def test_switching_only_needs_the_env_var():
    """설정 한 줄만 바꾸면 구현체가 갈려야 한다."""
    a = build_client(_settings(llm_backend="ollama"))
    b = build_client(_settings(llm_backend="anthropic"))
    assert type(a) is not type(b)
    assert a.name != b.name


def test_anthropic_without_key_fails_loudly():
    """키가 없으면 조용히 넘어가지 말고 바로 알려줘야 한다."""
    with pytest.raises(LLMError) as exc:
        build_client(_settings(llm_backend="anthropic", anthropic_api_key=""))
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_unknown_backend_is_rejected_by_settings():
    with pytest.raises(Exception):
        _settings(llm_backend="openai")  # Literal 이라 검증에서 걸린다


@pytest.mark.parametrize("impl", [OllamaClient, AnthropicClient])
def test_both_implement_the_same_contract(impl):
    """추상 메서드 시그니처가 어긋나면 호출부가 백엔드를 알아야 해진다."""
    for name in ("chat_json", "ping", "describe"):
        assert callable(getattr(impl, name)), f"{impl.__name__}.{name} 누락"

    expected = inspect.signature(LLMClient.chat_json).parameters
    actual = inspect.signature(impl.chat_json).parameters
    assert list(expected) == list(actual), f"{impl.__name__}.chat_json 시그니처 불일치"


def test_client_is_abstract():
    with pytest.raises(TypeError):
        LLMClient()  # type: ignore[abstract]
