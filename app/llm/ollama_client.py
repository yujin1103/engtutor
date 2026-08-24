"""Ollama 백엔드. /api/chat 에 JSON 스키마를 format 으로 강제한다."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx

from .base import LLMClient, LLMError, Message, StreamChunk
from .partial_json import extract_string

# qwen3 계열은 하이브리드 추론 모델이라 <think> 블록을 뱉는다.
# think=false 로 막는 게 1순위, 그래도 새어 나오면 아래 정규식으로 걷어낸다.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_SPAN = re.compile(r"\{.*\}", re.DOTALL)


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        num_ctx: int = 4096,
        keep_alive: str = "30m",
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self._keep_alive = keep_alive
        self._timeout = timeout
        # think 파라미터를 지원하지 않는 모델이면 400 이 온다. 한 번 겪으면 이후로 끈다.
        self._send_think_flag = True

    def describe(self) -> str:
        return f"ollama({self._model}) @ {self._base_url}"

    def ping(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(f"{self._base_url}/api/tags")
            return res.status_code == 200
        except httpx.HTTPError:
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
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "format": schema,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self._num_ctx,
                "num_predict": max_tokens,
            },
        }
        if self._send_think_flag:
            payload["think"] = False

        data = self._post(payload)
        content = (data.get("message") or {}).get("content", "")
        return _parse_json(content)

    def chat_json_stream(
        self,
        *,
        system: str,
        messages: list[Message],
        schema: dict[str, Any],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream_field: str = "reply",
    ) -> Iterator[StreamChunk]:
        """토큰이 나오는 대로 `stream_field` 를 긁어 흘려보낸다.

        스키마 필드 순서상 `reply` 가 가장 먼저 완성되므로, 전체가 8초 걸려도
        첫 글자는 1초 안에 화면에 뜬다. format 은 그대로 걸어 두기 때문에
        최종 결과물의 구조 보장은 비스트리밍과 동일하다.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "format": schema,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self._num_ctx,
                "num_predict": max_tokens,
            },
        }
        if self._send_think_flag:
            payload["think"] = False

        parts: list[str] = []
        sent = 0
        field_done = False
        for piece in self._stream_post(payload):
            parts.append(piece)
            if field_done:
                continue
            value, field_done = extract_string("".join(parts), stream_field)
            if len(value) > sent:
                yield StreamChunk(delta=value[sent:], done=False, data=None)
                sent = len(value)

        yield StreamChunk(delta="", done=True, data=_parse_json("".join(parts)))

    def _stream_post(self, payload: dict[str, Any]) -> Iterator[str]:
        """NDJSON 스트림에서 본문 조각만 뽑아 준다."""
        url = f"{self._base_url}/api/chat"
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    with client.stream("POST", url, json=payload) as res:
                        # think 미지원 모델이면 400. 플래그를 빼고 한 번만 재시도한다.
                        if res.status_code == 400 and "think" in payload and attempt == 0:
                            res.read()
                            self._send_think_flag = False
                            payload.pop("think")
                            continue
                        if res.status_code >= 400:
                            res.read()
                            raise LLMError(
                                f"Ollama 응답 오류 {res.status_code}: {res.text[:300]}"
                            )
                        for line in res.iter_lines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue  # 잘린 줄은 다음 줄에서 온전히 온다
                            if event.get("error"):
                                raise LLMError(f"Ollama 스트림 오류: {event['error']}")
                            chunk = (event.get("message") or {}).get("content", "")
                            if chunk:
                                yield chunk
                            if event.get("done"):
                                return
                return
            except httpx.HTTPError as exc:
                raise LLMError(
                    f"Ollama 스트리밍에 실패했습니다 ({self._base_url}). 원인: {exc}"
                ) from exc

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/api/chat"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                res = client.post(url, json=payload)
                # think 미지원 모델이면 400. 플래그를 빼고 한 번만 재시도한다.
                if res.status_code == 400 and "think" in payload:
                    self._send_think_flag = False
                    payload.pop("think")
                    res = client.post(url, json=payload)
                res.raise_for_status()
                return res.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise LLMError(f"Ollama 응답 오류 {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Ollama 에 연결하지 못했습니다 ({self._base_url}). "
                f"컨테이너가 떠 있는지, 모델을 pull 했는지 확인하세요. 원인: {exc}"
            ) from exc


def _parse_json(content: str) -> dict[str, Any]:
    cleaned = _THINK_BLOCK.sub("", content).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 앞뒤에 설명이 붙어 나온 경우를 위한 최후 폴백
    match = _JSON_SPAN.search(cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise LLMError(f"JSON 파싱 실패. 원문 앞부분: {cleaned[:300]!r}")
