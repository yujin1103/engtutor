"""턴 처리 오케스트레이션: 프롬프트 조립 -> LLM 호출 -> 검증 -> 1회 재시도."""

from __future__ import annotations

import logging

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
    "where kind is 'mistake' or 'polish'), hint_ko (string, Korean). "
    "No markdown fence, no commentary."
)


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
        messages: list[Message] = [
            *history[-MAX_HISTORY_MESSAGES:],
            {"role": "user", "content": user_text},
        ]

        try:
            return self._call(system, messages, temperature=0.7)
        except (LLMError, ValidationError) as first:
            logger.warning("턴 응답 1차 실패, 재시도합니다: %s", first)

        repair: list[Message] = [*messages, {"role": "user", "content": _REPAIR_NOTE}]
        try:
            return self._call(system, repair, temperature=0.2)
        except (LLMError, ValidationError) as second:
            raise LLMError(f"두 번 시도했지만 유효한 응답을 받지 못했습니다: {second}") from second

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
