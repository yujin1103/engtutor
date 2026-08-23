"""턴 응답 스키마.

pydantic 모델이 단일 출처다. 두 백엔드에 넘길 JSON 스키마는 여기서 파생시킨다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .korean import normalize, require_korean


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
    corrections: list[Correction] = Field(
        default_factory=list,
        description="Corrections for the learner's last message. Empty list if it was fine.",
    )
    hint_ko: str = Field(
        description="One short Korean sentence hinting what the learner could say next."
    )

    _fix_hint = field_validator("hint_ko")(lambda v: require_korean(normalize(v), "hint_ko"))


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
