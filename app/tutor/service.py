"""턴 처리 오케스트레이션: 프롬프트 조립 -> LLM 호출 -> 검증 -> 1회 재시도."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any, Literal, TypedDict

from pydantic import ValidationError

from ..llm.base import LLMClient, LLMError, Message
from .korean import has_hangul
from .loader import Scenario, load_prompt
from .levels import prompt_for as level_prompt_for
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
    "reply (string), reply_ko (string, Korean translation of reply), "
    "corrections (array of objects with original/kind/better/note "
    "where kind is 'mistake' or 'polish'), say_en (string, short English the learner "
    "can say as is), say_more (string, English, a little longer), "
    "hint_ko (string, Korean). No markdown fence, no commentary."
)


# 두 글자 이상의 영어 낱말. 한 개짜리(a, I)는 한국어 문장에도 섞여 나온다.
_ENGLISH_WORD = re.compile(r"[A-Za-z]{2,}")


def is_not_practice(text: str) -> bool:
    """학습자가 영어로 연습한 턴이 아닌가 — 한국어만 썼거나 인젝션을 던진 경우."""
    return has_hangul(text) and len(_ENGLISH_WORD.findall(text)) < 2


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
            # 길이·어휘 규칙을 레벨마다 갈아 끼운다. 예전에는 모든 레벨에서
            # 8단어로 고정돼 있어 B1 학습자에게 대화가 통째로 짧게 느껴졌다.
            level_guide=level_prompt_for(level),
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
        messages = self._messages(scenario, history, user_text)

        try:
            return self._ground(scenario, user_text, self._call(system, messages, temperature=0.7))
        except (LLMError, ValidationError) as first:
            logger.warning("턴 응답 1차 실패, 재시도합니다: %s", first)

        try:
            return self._ground(
                scenario, user_text, self._call(system, self._repair(messages), temperature=0.2)
            )
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
        messages = self._messages(scenario, history, user_text)

        streamed = False
        try:
            for event in self._stream_call(system, messages, temperature=0.7):
                streamed = streamed or event["type"] == "delta"
                if event["type"] == "turn":
                    event["turn"] = self._ground(scenario, user_text, event["turn"])
                yield event
            return
        except (LLMError, ValidationError) as first:
            logger.warning("스트리밍 1차 실패, 재시도합니다: %s", first)

        if streamed:
            # 이미 흘려보낸 글자는 폐기한다. 재시도 결과가 다를 수 있으므로
            # 화면에 남겨 두면 저장된 내용과 어긋난다.
            yield TurnEvent(type="reset", text="", turn=None)

        try:
            turn = self._ground(
                scenario, user_text, self._call(system, self._repair(messages), temperature=0.2)
            )
        except (LLMError, ValidationError) as second:
            raise LLMError(f"두 번 시도했지만 유효한 응답을 받지 못했습니다: {second}") from second
        yield TurnEvent(type="turn", text="", turn=turn)

    # ------------------------------------------------------------------ 내부
    def _ground(
        self, scenario: Scenario, user_text: str, turn: TurnResponse
    ) -> TurnResponse:
        """'말할 것'을 이 시나리오에 붙들어 맨다.

        학습자가 한국어만 쓴 턴에서는 모델이 쓸 재료가 없어 **프롬프트 예시를 통째로
        베낀다.** 실제로 택시·호텔·역 시나리오에서 학습자에게 `I have a headache.` 를
        말하라고 했다 — 약국 예시에 있는 문장이다. "예시의 말을 베끼지 말라"를
        모든 필드로 확장해 다시 써 봤지만 네 시나리오 전부에서 그대로 나왔다.
        프롬프트로 못 고치는 종류라 코드에서 막는다(korean.py 와 같은 이유).

        이 턴에 가장 쓸모 있는 문장은 시나리오가 이미 들고 있다 — 첫 발화에 대한 답이다.
        학습자는 지금 막혀서 한국어를 쓴 것이므로, 장면 안의 문장을 돌려주는 게 맞다.
        """
        if not is_not_practice(user_text):
            return turn
        return turn.model_copy(
            update={
                "say_en": scenario.opening_say_en,
                "say_more": scenario.opening_say_more,
            }
        )

    def _messages(
        self, scenario: Scenario, history: list[Message], user_text: str
    ) -> list[Message]:
        """모델에게 보낼 대화. **첫 발화를 앞에 붙인다.**

        시나리오의 `opening_line` 은 UI 가 화면에 그릴 뿐 서버에는 없었다. 그래서
        모델은 자기가 방금 한 말을 모른 채 첫 턴을 받았다. 프롬프트에 "이미 한 말을
        읽고 답하라"고 적어 놨지만 읽을 것이 없었던 셈이다.

        `asking_repeat` 에서 그대로 드러났다 — "The train leaves from platform nine"
        이라고 말해 놓고 학습자가 `pardon me?` 라고 하자
        "Let me repeat that slowly" 라고만 하고 **다시 말하지 못했다.**
        무엇을 다시 말해야 하는지 몰랐기 때문이다.

        저장하지 않고 매번 붙인다. 첫 발화는 시나리오의 속성이라 DB 에 복제하면
        YAML 을 고쳐도 옛 세션이 옛 문장을 들고 있게 된다.
        """
        opening: list[Message] = [{"role": "assistant", "content": scenario.opening_line}]
        recent = history[-MAX_HISTORY_MESSAGES:]
        return [*opening, *recent, {"role": "user", "content": user_text}]

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
