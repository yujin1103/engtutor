"""1단계 스모크 테스트: 스키마 평탄화, 시나리오 로딩, 프롬프트 조립."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.tutor.loader import load_scenarios
from app.tutor.schemas import TurnResponse, turn_response_schema
from app.tutor.service import TutorService


def _walk(node, seen=None):
    """스키마 트리의 모든 dict 노드를 순회한다."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_schema_has_no_refs():
    """$ref/$defs 가 남아 있으면 Ollama 의 format 변환이 불안정해진다."""
    schema = turn_response_schema()
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text


def test_every_object_forbids_extra_properties():
    """Anthropic structured outputs 는 모든 object 에 이 필드를 요구한다."""
    for node in _walk(turn_response_schema()):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False


def test_schema_requires_all_fields():
    schema = turn_response_schema()
    assert set(schema["required"]) == {"reply", "corrections", "say_en", "say_more", "hint_ko"}


def test_repair_note_lists_every_required_field():
    """재시도 지시문이 스키마와 어긋나면 재시도 경로가 구조적으로 실패한다.

    실제로 kind 를 추가하면서 이 문자열을 안 고쳐, 1차 실패한 턴에게
    '필수 필드를 빼라'고 가르치고 있었다. 두 곳을 강제로 묶어 둔다.
    """
    from app.tutor.service import _REPAIR_NOTE

    schema = turn_response_schema()
    required = list(schema["required"])
    required += schema["properties"]["corrections"]["items"]["required"]

    missing = [f for f in required if f not in _REPAIR_NOTE]
    assert not missing, f"_REPAIR_NOTE 에 빠진 필수 필드: {missing}"


def test_repair_note_mentions_kind_values():
    """kind 는 enum 이라 값까지 알려주지 않으면 모델이 임의 값을 넣는다."""
    from app.tutor.service import _REPAIR_NOTE

    for value in ("mistake", "polish"):
        assert value in _REPAIR_NOTE


def test_turn_response_accepts_empty_corrections():
    turn = TurnResponse.model_validate(
        {"reply": "Sure! What size?", "corrections": [], "say_en": "Yes.", "say_more": "Yes, please.", "hint_ko": "사이즈를 말해보세요."}
    )
    assert turn.corrections == []


def test_correction_kind_is_required_and_constrained():
    """kind 가 빠지거나 임의 값이면 검증에서 걸려야 한다."""
    from app.tutor.schemas import Correction

    ok = Correction(original="I go", kind="polish", better="I went", note="지난 일이에요.")
    assert ok.kind == "polish"

    with pytest.raises(ValidationError):
        Correction(original="I go", better="I went", note="설명이에요.")  # kind 없음
    with pytest.raises(ValidationError):
        Correction(original="I go", kind="nitpick", better="I went", note="설명이에요.")


def test_correction_schema_enumerates_kind():
    """두 백엔드에 넘기는 스키마에 kind enum 이 실려야 모델이 아무 값이나 못 쓴다."""
    schema = turn_response_schema()
    kind = schema["properties"]["corrections"]["items"]["properties"]["kind"]
    assert set(kind.get("enum", [])) == {"mistake", "polish"}


def test_turn_response_rejects_missing_reply():
    with pytest.raises(ValidationError):
        TurnResponse.model_validate({"corrections": [], "say_en": "Yes.", "say_more": "Yes, please.", "hint_ko": "..."})


def test_scenarios_load():
    scenarios = load_scenarios()
    assert {"cafe_order", "self_intro", "directions"} <= set(scenarios)


@pytest.mark.parametrize("scenario_id", ["cafe_order", "self_intro", "directions"])
def test_system_prompt_renders(scenario_id):
    """플레이스홀더가 하나라도 빠지면 KeyError 로 여기서 잡힌다."""
    scenario = load_scenarios()[scenario_id]
    service = TutorService.__new__(TutorService)  # LLM 클라이언트 없이 조립만 검증
    from app.tutor.loader import load_prompt

    service._system_template = load_prompt("tutor_system.md")
    service._guardrails = load_prompt("guardrails.md")

    system = service.build_system(scenario, "A1")
    assert scenario.ai_role in system
    assert "{level}" not in system
    assert "{guardrails}" not in system
