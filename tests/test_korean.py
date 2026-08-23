"""한국어 표기 정규화. 학습자에게 보이는 텍스트라 모델 출력에 맡기지 않는다."""

from __future__ import annotations

import pytest

from app.report.schemas import LearnedExpression, ReportInsight
from app.tutor.korean import normalize
from app.tutor.schemas import Correction, TurnResponse


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("걸리는 시간을 묻을 때 사용해요.", "걸리는 시간을 물을 때 사용해요."),
        ("길을 묻을 때 써요.", "길을 물을 때 써요."),
        ("가격을 묻어보세요.", "가격을 물어보세요."),
        ("사이즈를 묻으면 돼요.", "사이즈를 물으면 돼요."),
        ("이름을 묻으세요.", "이름을 물으세요."),
    ],
)
def test_dieut_irregular_is_fixed(given, expected):
    assert normalize(given) == expected


@pytest.mark.parametrize(
    "text",
    [
        "묻는 게 자연스러워요.",  # '묻는'은 규칙 활용 — 건드리면 안 된다
        "이렇게 물어보세요.",  # 이미 올바른 표기
        "",
    ],
)
def test_correct_forms_are_left_alone(text):
    assert normalize(text) == text


def test_correction_note_is_normalized_on_validation():
    """서비스 코드가 아니라 스키마 검증 단계에서 자동 적용돼야 한다."""
    c = Correction(
        original="How long it takes?",
        kind="mistake",
        better="How long does it take?",
        note="걸리는 시간을 묻을 때는 does 를 붙여요.",
    )
    assert "물을 때" in c.note
    assert "묻을 때" not in c.note


def test_turn_hint_is_normalized():
    turn = TurnResponse.model_validate(
        {"reply": "Sure!", "corrections": [], "hint_ko": "사이즈를 묻어보세요."}
    )
    assert turn.hint_ko == "사이즈를 물어보세요."


def test_report_fields_are_normalized():
    insight = ReportInsight(
        summary_ko="길을 묻을 때 표현을 배웠어요.",
        patterns_ko=["시간을 묻을 때 does 를 빠뜨려요."],
        learned=[LearnedExpression(english="How long does it take?", note_ko="시간을 묻을 때 써요.")],
    )
    assert "물을 때" in insight.summary_ko
    assert "물을 때" in insight.patterns_ko[0]
    assert "물을 때" in insight.learned[0].note_ko
