"""교정 강도 3단계 (유연/중간/엄격)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tutor.loader import get_scenarios, load_prompt
from app.tutor.service import TutorService
from app.tutor.strictness import (
    ORDER,
    CAPTIONS,
    DEFAULT_STRICTNESS,
    LABELS,
    prompt_for,
    show_polish,
)

UI = Path(__file__).resolve().parent.parent / "ui" / "chat_app.py"
LEVELS = ("gentle", "balanced", "strict")


def _service() -> TutorService:
    svc = TutorService.__new__(TutorService)
    svc._system_template = load_prompt("tutor_system.md")
    svc._guardrails = load_prompt("guardrails.md")
    return svc


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_has_a_prompt(level):
    assert prompt_for(level).strip()


def test_levels_produce_different_prompts():
    texts = {prompt_for(v) for v in LEVELS}
    assert len(texts) == 3, "세 단계가 실제로 다른 지시를 내려야 한다"


def test_gentle_drops_polish_instead_of_relabelling_it():
    """유연 모드의 핵심 — polish 를 만들지 않는다. 단 **이름만 바꿔 다는 것**까지 막아야 한다.

    처음 문구는 `kind 는 항상 "mistake" 입니다. "polish" 는 절대 만들지 마세요` 였다.
    모델은 이걸 '침묵하라'가 아니라 '이름을 바꿔 달라'로 읽었다 — 맞는 문장 54회 중
    7회에 mistake 가 붙었다(balanced 는 0회). 배출구를 막으면 남은 관으로 나온다.
    문구를 고치자 맞는 문장에 붙은 mistake 가 7 -> 3, 화면에 보이는 오탐은 0% 가 됐다.
    측정: scripts/eval_corrections.py
    """
    text = prompt_for("gentle")
    assert "polish" in text
    assert "이름만 바꿔 달지" in text, "polish 를 mistake 로 재라벨하는 걸 막는 문장이 있어야 한다"
    assert "항상" not in text, "'kind 는 항상 mistake' 류 문구가 돌아오면 재라벨이 되살아난다"
    assert not show_polish("gentle")


@pytest.mark.parametrize("level", ["balanced", "strict"])
def test_other_levels_allow_polish(level):
    assert show_polish(level)


def test_strict_names_the_fine_grained_categories():
    text = prompt_for("strict")
    for token in ("관사", "전치사", "시제", "복수형"):
        assert token in text


def test_unknown_level_falls_back_to_default():
    assert prompt_for("nonsense") == prompt_for(DEFAULT_STRICTNESS)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", LEVELS)
def test_build_system_injects_the_strictness_block(level):
    system = _service().build_system(get_scenarios()["cafe_order"], "A1", level)
    assert "{strictness}" not in system, "플레이스홀더가 남았다"
    assert prompt_for(level).splitlines()[0] in system


def test_build_system_defaults_without_strictness_argument():
    """기존 호출부가 인자 없이 불러도 깨지지 않아야 한다."""
    system = _service().build_system(get_scenarios()["cafe_order"], "A1")
    assert prompt_for(DEFAULT_STRICTNESS).splitlines()[0] in system


def test_strictness_does_not_break_other_placeholders():
    system = _service().build_system(get_scenarios()["directions"], "A2", "strict")
    assert "{level}" not in system and "{guardrails}" not in system
    assert "Persona guardrails" in system


# ---------------------------------------------------------------- UI 계약
@pytest.mark.parametrize("mapping,name", [(LABELS, "LABELS"), (CAPTIONS, "CAPTIONS")])
def test_server_defines_all_three(mapping, name):
    assert set(mapping) == set(LEVELS), f"{name} 에 빠진 단계가 있다"


def test_ui_does_not_hardcode_labels():
    """라벨을 UI 가 복제하면 서버와 갈라진다. /strictness 로 받아 써야 한다."""
    text = UI.read_text(encoding="utf-8")
    for label in LABELS.values():
        assert label not in text, f"UI 에 '{label}' 이 하드코딩돼 있다 — /strictness 를 쓰세요"
    assert "/strictness" in text, "UI 가 강도 목록을 서버에서 받아오지 않는다"


def test_strictness_endpoint_serves_every_level():
    from app.main import list_strictness

    rows = list_strictness()
    assert [r.key for r in rows] == list(ORDER)
    for r in rows:
        assert r.label == LABELS[r.key]
        assert r.caption == CAPTIONS[r.key]


def test_ui_sends_strictness_in_the_chat_payload():
    text = UI.read_text(encoding="utf-8")
    assert '"strictness"' in text, "UI 가 strictness 를 API 로 보내지 않는다"


def test_chat_request_accepts_strictness():
    from app.main import ChatRequest

    req = ChatRequest(scenario_id="cafe_order", message="hi", strictness="strict")
    assert req.strictness == "strict"
    assert ChatRequest(scenario_id="cafe_order", message="hi").strictness == DEFAULT_STRICTNESS


def test_chat_request_rejects_unknown_strictness():
    from pydantic import ValidationError

    from app.main import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(scenario_id="cafe_order", message="hi", strictness="brutal")
