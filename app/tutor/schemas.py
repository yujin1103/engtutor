"""턴 응답 스키마.

pydantic 모델이 단일 출처다. 두 백엔드에 넘길 JSON 스키마는 여기서 파생시킨다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .korean import normalize, reject_hangul, require_english, require_korean
from .levels import say_limits


CorrectionKind = Literal["mistake", "polish"]


class Correction(BaseModel):
    """직전 사용자 발화 하나에 대한 교정.

    kind 로 두 등급을 구분한다. 왕초보에게 "틀렸다"는 신호를 남발하면 위축되므로,
    통하긴 하는 표현은 polish 로 내려 별도로 보여준다.
    리포트의 '반복된 실수' 패턴은 mistake 만 근거로 삼는다.
    """

    model_config = ConfigDict(extra="forbid")

    original: str = Field(description="The learner's original wording, quoted as-is.")
    kind: CorrectionKind = Field(
        description=(
            "'mistake' = actually wrong or confusing to a listener. "
            "'polish' = correct English, but a native speaker would say it differently here."
        )
    )
    better: str = Field(description="A more natural English way to say it.")
    note: str = Field(description="Short friendly explanation in natural Korean (해요체).")

    _fix_note = field_validator("note")(lambda v: require_korean(normalize(v), "note"))


class TurnResponse(BaseModel):
    """LLM 1회 호출로 받아야 하는 턴 응답 전체."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(
        description=(
            "Your in-character English reply. Never mention grammar, mistakes, "
            "or that you are a tutor here."
        )
    )
    # reply 바로 뒤에 둔다. 필드 순서가 곧 생성 순서라, reply 를 흘려보내는
    # 스트리밍은 그대로 두면서 해석이 가장 먼저 확정된다.
    reply_ko: str = Field(
        description=(
            "Korean translation of `reply`. Plain everyday Korean (해요체), the way a "
            "Korean speaker would actually say it — not a word-by-word gloss. "
            "Translate only what you said; add nothing, explain nothing."
        )
    )
    corrections: list[Correction] = Field(
        default_factory=list,
        description="Corrections for the learner's last message. Empty list if it was fine.",
    )
    # say_en / say_more 를 hint_ko 앞에 둔다.
    # 필드 선언 순서가 그대로 Ollama format(GBNF) 생성 순서가 되므로,
    # 영어 '바닥'을 먼저 확정한 뒤 한국어 안내가 그것을 감싸게 된다. 추가 토큰 0.
    say_en: str = Field(
        description=(
            "One complete English line the learner can say right now, exactly as written. "
            "The shortest thing that works — a single word is fine. "
            "Never a template, a blank to fill, a choice list, or an instruction."
        )
    )
    say_more: str = Field(
        description=(
            "One step up from say_en: the same move said a little longer, "
            "or the other option if your reply offered a choice. Never empty."
        )
    )
    hint_ko: str = Field(
        description=(
            "One or two short Korean sentences: what just happened, "
            "and what say_en means. Never a template with blanks."
        )
    )

    # reply 에는 언어 검증이 없었다. 규칙 2 가 "reply 는 항상 영어"라고 못 박고 보안
    # 슈트도 그걸 검사하는데, 정작 스키마는 통과시키고 있었다. reply_ko 가 생기면서
    # 경계가 더 흐려져(한국어를 쓸 자리가 생겼다) 실제로 5번 중 1번 새어 나왔다.
    # 여기서 거부하면 재시도 경로로 넘어가고, 재시도까지 실패하면 오류가 나간다 —
    # 학습자에게 한국어 reply 를 보여주는 것보다 낫다(fail-closed).
    _chk_reply = field_validator("reply")(lambda v: reject_hangul(v, "reply"))
    @field_validator("say_en", "say_more")
    @classmethod
    def _chk_say_fields(cls, value: str, info: ValidationInfo) -> str:
        """따라 말할 영어 필드. 상한은 **레벨에 따라 다르다.**

        레벨은 `model_validate(..., context={"level": ...})` 로 들어온다. 문맥이
        없으면 가장 느슨한 값이 쓰인다 — 문맥이 없다는 이유로 정상 출력을 거부해
        재시도를 만들면 안 되기 때문이다. levels.say_limits 참고.
        """
        level = (info.context or {}).get("level")
        max_words, max_chars = say_limits(level, info.field_name)
        return require_english(
            value, info.field_name, max_words=max_words, max_chars=max_chars
        )
    _fix_hint = field_validator("hint_ko")(lambda v: require_korean(normalize(v), "hint_ko"))
    # 해석 칸에 영어가 그대로 돌아오면 학습자에게는 아무 도움이 안 된다.
    _fix_reply_ko = field_validator("reply_ko")(lambda v: require_korean(normalize(v), "reply_ko"))


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """$ref 를 인라인 전개하고 모든 object 에 additionalProperties=false 를 강제한다.

    - Ollama 의 `format` 파라미터는 JSON 스키마를 문법으로 변환하는데,
      중첩 모델이 만드는 $ref/$defs 를 항상 안정적으로 처리하지는 않는다.
    - Anthropic structured outputs 는 모든 object 에 additionalProperties=false 를 요구한다.

    pydantic 을 단일 출처로 유지한 채 스키마만 평탄화하는 것이 목적이다.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return _resolve(defs[name], defs)
        out = {k: _resolve(v, defs) for k, v in node.items() if k != "$defs"}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
            # 기본값이 있는 필드(corrections)는 pydantic 이 required 에서 빼버린다.
            # 그러면 모델이 그 키를 통째로 생략해도 스키마상 허용된다.
            # 생성은 엄격하게(모든 키를 강제), 파싱은 관대하게(pydantic 기본값 유지)가 목적.
            if "properties" in out:
                out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    return node


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """백엔드에 그대로 넘길 수 있는 평탄화된 JSON 스키마.

    모든 object 에 additionalProperties=false 를 붙이고 모든 속성을 required 로 만든다.
    Ollama 의 format 변환과 Anthropic structured outputs 가 둘 다 이 형태를 요구한다.
    """
    schema = model.model_json_schema()
    return _resolve(schema, schema.get("$defs", {}))


def turn_response_schema() -> dict[str, Any]:
    return json_schema_for(TurnResponse)
