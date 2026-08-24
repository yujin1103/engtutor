"""LLM 백엔드 공통 계약.

스키마를 인자로 받는 이유:
1단계 턴 응답, 2단계 학습 리포트, 3단계 단어 배치 생성이 전부 같은 메서드를
재사용할 수 있어야 하기 때문이다. 턴 전용 메서드로 만들면 단계마다 다시 만들게 된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, Literal, TypedDict


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class StreamChunk(TypedDict):
    """스트리밍 도중 흘러나오는 조각.

    delta: 지정한 필드에서 **새로** 생성된 부분만 (누적본이 아니다)
    done : 마지막 조각인지
    data : done 일 때만 채워지는 완성된 JSON 전체
    """

    delta: str
    done: bool
    data: dict[str, Any] | None


class LLMError(RuntimeError):
    """백엔드 종류와 무관한 LLM 호출 실패."""


class LLMClient(ABC):
    name: str

    @abstractmethod
    def chat_json(
        self,
        *,
        system: str,
        messages: list[Message],
        schema: dict[str, Any],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """JSON 스키마를 강제해 dict 를 돌려준다. 검증은 호출부(pydantic)가 한다."""

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
        """`stream_field` 가 생성되는 대로 흘려보내고, 마지막에 완성본을 준다.

        추상 메서드가 아닌 이유: 스트리밍을 지원하지 않는 백엔드도 그대로 동작해야
        호출부가 백엔드를 신경 쓰지 않는다는 원칙이 유지된다. 기본 구현은
        완성될 때까지 기다렸다가 한 번에 흘린다 — 느릴 뿐, 결과는 같다.
        """
        data = self.chat_json(
            system=system,
            messages=messages,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        value = data.get(stream_field)
        if isinstance(value, str) and value:
            yield StreamChunk(delta=value, done=False, data=None)
        yield StreamChunk(delta="", done=True, data=data)

    @abstractmethod
    def ping(self) -> bool:
        """백엔드가 응답 가능한 상태인지. /healthz 에서 사용."""

    @abstractmethod
    def describe(self) -> str:
        """헬스체크/로그용으로 사람이 읽는 한 줄."""
