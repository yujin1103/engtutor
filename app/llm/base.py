"""LLM 백엔드 공통 계약.

스키마를 인자로 받는 이유:
1단계 턴 응답, 2단계 학습 리포트, 3단계 단어 배치 생성이 전부 같은 메서드를
재사용할 수 있어야 하기 때문이다. 턴 전용 메서드로 만들면 단계마다 다시 만들게 된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


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

    @abstractmethod
    def ping(self) -> bool:
        """백엔드가 응답 가능한 상태인지. /healthz 에서 사용."""

    @abstractmethod
    def describe(self) -> str:
        """헬스체크/로그용으로 사람이 읽는 한 줄."""
