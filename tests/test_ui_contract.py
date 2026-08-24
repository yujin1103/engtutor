"""UI ↔ API 응답 계약.

Streamlit UI 는 API 응답을 dict 로 받아 키로 꺼내 쓴다. 스키마에서 필드 이름이
바뀌거나 빠지면 브라우저를 열기 전까지 아무도 모른다 — 테스트가 없으면 런타임에
KeyError 로 터진다. 여기서 '화면이 읽는 키'를 고정한다.

렌더 결과(모양)를 검증하지는 못한다. 그건 눈으로 봐야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.content.schemas import WordTip
from app.main import ChatResponse, ScenarioOut
from app.report.schemas import LearnedExpression, ReportInsight, SessionReport
from app.tutor.loader import get_scenarios
from app.tutor.schemas import Correction, TurnResponse

UI = Path(__file__).resolve().parent.parent / "ui" / "chat_app.py"
REVIEW_UI = Path(__file__).resolve().parent.parent / "content" / "review_app.py"


def _sample_report() -> SessionReport:
    return SessionReport(
        session_id="s1",
        scenario_title="카페에서 음료 주문하기",
        level="A1",
        turn_count=3,
        mistake_count=1,
        polish_count=1,
        mistakes=[
            Correction(
                original="I want ice americano",
                kind="mistake",
                better="Can I get an iced americano, please?",
                note="ice 가 아니라 iced 예요.",
            ),
            Correction(
                original="Large",
                kind="polish",
                better="Large, please.",
                note="please 를 붙이면 부드러워요.",
            ),
        ],
        insight=ReportInsight(
            summary_ko="잘 하셨어요.",
            patterns_ko=["과거형을 자주 놓쳐요."],
            learned=[LearnedExpression(english="Can I get ~?", note_ko="주문할 때 써요.")],
        ),
        word_tips=[
            WordTip(
                word="get",
                meaning_ko="받다, 얻다",
                example="I get a gift.",
                usage_note="주는 쪽은 give 예요.",
                confused_with=["give"],
            )
        ],
    )


def _keys_read_from(source: Path, variable: str) -> set[str]:
    """`변수["키"]` 와 `변수.get("키"` 패턴에서 읽는 키를 뽑는다."""
    text = source.read_text(encoding="utf-8")
    pattern = rf'{variable}(?:\.get\(|\[)["\']([a-z_]+)["\']'
    return set(re.findall(pattern, text))


# ---------------------------------------------------------------- 채팅 UI
def test_ui_reads_only_keys_the_turn_response_has():
    """render_turn(turn) 이 읽는 키가 TurnResponse 에 전부 있어야 한다."""
    available = set(TurnResponse.model_fields)
    read = _keys_read_from(UI, "turn")
    assert read, "UI 에서 turn 키 접근을 찾지 못했습니다 — 정규식을 확인하세요"
    assert read <= available, f"UI 가 없는 키를 읽습니다: {read - available}"


def test_ui_reads_only_keys_the_report_has():
    available = set(SessionReport.model_fields)
    read = _keys_read_from(UI, "report")
    assert read, "UI 에서 report 키 접근을 찾지 못했습니다"
    assert read <= available, f"UI 가 없는 키를 읽습니다: {read - available}"


def test_ui_reads_only_keys_the_scenario_has():
    available = set(ScenarioOut.model_fields)
    read = _keys_read_from(UI, "scenario")
    assert read, "UI 에서 scenario 키 접근을 찾지 못했습니다"
    assert read <= available, f"UI 가 없는 키를 읽습니다: {read - available}"


@pytest.mark.parametrize("key", ["session_id", "turn"])
def test_chat_response_carries_what_ui_expects(key):
    assert key in ChatResponse.model_fields


def test_report_json_has_every_field_the_report_view_touches():
    """직렬화된 실제 응답으로 확인 — 모델 필드명과 JSON 키가 어긋날 수 있다."""
    payload = _sample_report().model_dump()
    for key in ("turn_count", "mistake_count", "polish_count", "level", "insight", "mistakes", "word_tips"):
        assert key in payload, f"리포트 응답에 {key} 가 없습니다"

    for key in ("summary_ko", "patterns_ko", "learned"):
        assert key in payload["insight"]

    assert set(payload["mistakes"][0]) >= {"original", "kind", "better", "note"}
    assert set(payload["word_tips"][0]) >= {"word", "meaning_ko", "example", "usage_note", "confused_with"}
    assert set(payload["insight"]["learned"][0]) >= {"english", "note_ko"}


def test_mistake_polish_split_matches_ui_logic():
    """UI 는 kind 로 두 묶음을 가른다. 그 분기가 실제 데이터에서 동작하는지."""
    payload = _sample_report().model_dump()
    items = payload["mistakes"]
    real = [m for m in items if m.get("kind", "mistake") == "mistake"]
    polish = [m for m in items if m.get("kind") == "polish"]
    assert len(real) == payload["mistake_count"]
    assert len(polish) == payload["polish_count"]


def test_scenario_payload_has_opening_fields():
    """UI 는 첫 발화와 첫 힌트로 대화를 시작한다."""
    payload = ScenarioOut.of(get_scenarios()["cafe_order"]).model_dump()
    assert payload["opening_line"]
    assert payload["opening_hint_ko"]


# ---------------------------------------------------------------- 검수 UI
def test_review_ui_uses_existing_crud_functions():
    """검수 UI 가 부르는 crud 함수가 실제로 있는지."""
    from app.db import crud

    text = REVIEW_UI.read_text(encoding="utf-8")
    for name in re.findall(r"crud\.([a-z_]+)\(", text):
        assert hasattr(crud, name), f"crud.{name} 가 없습니다 (검수 UI 가 호출함)"


def test_review_ui_edits_only_real_columns():
    """save_word_edits 에 넘기는 필드가 WordRow 컬럼과 맞는지."""
    from app.db.models import WordRow

    columns = {c.name for c in WordRow.__table__.columns}
    text = REVIEW_UI.read_text(encoding="utf-8")
    block = text[text.index("save_word_edits") : text.index("st.success")]
    for field in re.findall(r"^\s{20,}([a-z_]+)=", block, re.MULTILINE):
        assert field in columns, f"WordRow 에 {field} 컬럼이 없습니다"
