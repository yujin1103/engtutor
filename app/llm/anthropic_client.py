"""Anthropic 백엔드.

claude-haiku-4-5 는 structured outputs(output_config.format)를 지원하므로
tool-use 우회 없이 Ollama 와 똑같은 JSON 스키마를 그대로 강제할 수 있다.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic, APIError

from .base import LLMClient, LLMError, Message


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, timeout: float = 120.0) -> None:
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY 가 비어 있습니다. .env 를 확인하세요 "
                "(LLM_BACKEND=anthropic 일 때만 필요합니다)."
            )
        self._client = Anthropic(api_key=api_key, timeout=timeout)
        self._model = model

    def describe(self) -> str:
        return f"anthropic({self._model})"

    def ping(self) -> bool:
        try:
            self._client.models.retrieve(self._model)
            return True
        except APIError:
            return False

    def chat_json(
        self,
        *,
        system: str,
        messages: list[Message],
        schema: dict[str, Any],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except APIError as exc:
            raise LLMError(f"Anthropic 호출 실패: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError("Anthropic 이 요청을 거절했습니다 (stop_reason=refusal).")
        if response.stop_reason == "max_tokens":
            raise LLMError("max_tokens 에 걸려 JSON 이 잘렸습니다. max_tokens 를 올리세요.")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("Anthropic 응답에 text 블록이 없습니다.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON 파싱 실패. 원문 앞부분: {text[:300]!r}") from exc
