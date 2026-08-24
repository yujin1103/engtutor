"""턴 처리 오케스트레이션: 프롬프트 조립 -> LLM 호출 -> 검증 -> 1회 재시도."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, Literal, TypedDict

from pydantic import ValidationError

from ..llm.base import LLMClient, LLMError, Message
from .loader import Scenario, load_prompt
from .schemas import TurnResponse, turn_response_schema
from .strictness import DEFAULT_STRICTNESS, Strictness, prompt_for

logger = logging.getLogger(__name__)

# 컨텍스트가 무한히 늘어나지 않도록 최근 N개 메시지만 보낸다.
MAX_HISTORY_MESSAGES = 12

# 스키마 위반 시 재요청에 덧붙이는 수리 지시.
# 같은 요청을 그대로 반복하면 대개 똑같이 실패하므로,
# 온도를 낮추고 무엇이 틀렸는지 알려주는 것이 핵심이다.
_REPAIR_NOTE = (
    "SYSTEM NOTE: your previous output did not match the required JSON schema. "
    "Reply again with ONLY a JSON object containing exactly these keys: "
    "reply (string), corrections (array of objects with original/kind/better/note "
    "where kind is 'mistake' or 'polish'), say_en (string, short English the learner "
    "can say as is), say_more (string, English, a little longer), "
    "hint_ko (string, Korean). No markdown fence, no commentary."
)


class TurnEvent(TypedDict):
    """스트리밍 중 호출부로 나가는 사건.

    delta: reply 에 새로 붙은 글자
    reset: 1차 시도가 검증에 실패해 지금까지 보여준 글자를 버려야 함
    turn : 검증까지 끝난 최종 응답
    """

    type: Literal["delta", "reset", "turn"]
    text: str
    turn: Any  # TurnResponse | None — TypedDict 안에서 순환 참조를 피한다


class TutorService:
    def __init__(self, client: LLMClient) -> None:
        self._client = client
        # 요청마다 새로 만든다 -> 프롬프트 파일을 고치면 재시작 없이 반영된다(튜닝용).
        self._system_template = load_prompt("tutor_system.md")
        self._guardrails = load_prompt("guardrails.md")
        self._schema = turn_response_schema()

    def build_system(
        self,
        scenario: Scenario,
        level: str,
        strictness: Strictness = DEFAULT_STRICTNESS,
    ) -> str:
        guardrails = self._guardrails.format(ai_role=scenario.ai_role)
        return self._system_template.format(
            level=level,
            ai_role=scenario.ai_role,
            situation=scenario.situation,
            goal=scenario.goal,
            strictness=prompt_for(strictness),
            guardrails=guardrails,
        )

    def respond(
        self,
        *,
        scenario: Scenario,
        level: str,
        history: list[Message],
        user_text: str,
        strictness: Strictness = DEFAULT_STRICTNESS,
    ) -> TurnResponse:
        system = self.build_system(scenario, level, strictness)
        messages = self._messages(history, user_text)

        try:
            return self._call(system, messages, temperature=0.7)
        except (LLMError, ValidationError) as first:
            logger.warning("턴 응답 1차 실패, 재시도합니다: %s", first)

        try:
            return self._call(system, self._repair(messages), temperature=0.2)
        except (LLMError, ValidationError) as second:
            raise LLMError(f"두 번 시도했지만 유효한 응답을 받지 못했습니다: {second}") from second

    def respond_stream(
        self,
        *,
        scenario: Scenario,
        level: str,
        history: list[Message],
        user_text: str,
        strictness: Strictness = DEFAULT_STRICTNESS,
    ) -> Iterator[TurnEvent]:
        """respond 와 같은 결과를 주되, reply 를 생성되는 대로 먼저 흘려보낸다.

        마지막 turn 사건은 respond 와 동일하게 pydantic 검증을 통과한 것만 나간다.
        즉 스트리밍은 **보여주는 시점**만 앞당길 뿐, 보장 수준을 낮추지 않는다.
        """
        system = self.build_system(scenario, level, strictness)
        messages = self._messages(history, user_text)

        streamed = False
        try:
            for event in self._stream_call(system, messages, temperature=0.7):
                streamed = streamed or event["type"] == "delta"
                yield event
            return
        except (LLMError, ValidationError) as first:
            logger.warning("스트리밍 1차 실패, 재시도합니다: %s", first)

        if streamed:
            # 이미 흘려보낸 글자는 폐기한다. 재시도 결과가 다를 수 있으므로
            # 화면에 남겨 두면 저장된 내용과 어긋난다.
            yield TurnEvent(type="reset", text="", turn=None)

        try:
            turn = self._call(system, self._repair(messages), temperature=0.2)
        except (LLMError, ValidationError) as second:
            raise LLMError(f"두 번 시도했지만 유효한 응답을 받지 못했습니다: {second}") from second
        yield TurnEvent(type="turn", text="", turn=turn)

    # ------------------------------------------------------------------ 내부
    def _messages(self, history: list[Message], user_text: str) -> list[Message]:
        return [*history[-MAX_HISTORY_MESSAGES:], {"role": "user", "content": user_text}]

    def _repair(self, messages: list[Message]) -> list[Message]:
        return [*messages, {"role": "user", "content": _REPAIR_NOTE}]

    def _stream_call(
        self, system: str, messages: list[Message], *, temperature: float
    ) -> Iterator[TurnEvent]:
        for chunk in self._client.chat_json_stream(
            system=system,
            messages=messages,
            schema=self._schema,
            temperature=temperature,
            stream_field="reply",
        ):
            if chunk["done"]:
                turn = TurnResponse.model_validate(chunk["data"])
                yield TurnEvent(type="turn", text="", turn=turn)
                return
            if chunk["delta"]:
                yield TurnEvent(type="delta", text=chunk["delta"], turn=None)

    def _call(
        self, system: str, messages: list[Message], *, temperature: float
    ) -> TurnResponse:
        raw = self._client.chat_json(
            system=system,
            messages=messages,
            schema=self._schema,
            temperature=temperature,
        )
        return TurnResponse.model_validate(raw)
