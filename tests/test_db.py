"""2단계 스모크 테스트: SQLite 저장·복원, 리포트 조립."""

from __future__ import annotations

import pytest

from app.report.schemas import ReportInsight
from app.report.service import ReportService
from app.session_store import InMemorySessionStore
from app.tutor.loader import load_scenarios
from app.tutor.schemas import Correction, TurnResponse, json_schema_for


@pytest.fixture()
def sqlite_store(tmp_path, monkeypatch):
    """임시 DB 파일로 SqliteSessionStore 를 만든다."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    from app import config

    config.get_settings.cache_clear()

    import importlib

    from app.db import database

    importlib.reload(database)
    database.init_db()

    from app.session_store import SqliteSessionStore

    return SqliteSessionStore()


def _turn(reply: str, corrections: list[Correction] | None = None) -> TurnResponse:
    return TurnResponse(reply=reply, corrections=corrections or [], hint_ko="다음엔 이렇게요.")


def test_sqlite_round_trip(sqlite_store):
    session = sqlite_store.create(scenario_id="cafe_order", level="A1")

    sqlite_store.record_turn(
        session.id,
        user_text="I want ice americano",
        turn=_turn(
            "Sure! What size?",
            [Correction(kind="mistake", original="I want ice americano", better="Can I get an iced americano?", note="더 자연스러워요.")],
        ),
    )
    sqlite_store.record_turn(session.id, user_text="Large please", turn=_turn("For here or to go?"))

    restored = sqlite_store.get(session.id)
    assert restored is not None
    assert restored.level == "A1"
    # user/assistant 가 번갈아 4개
    assert [m["role"] for m in restored.messages] == ["user", "assistant", "user", "assistant"]
    assert restored.messages[0]["content"] == "I want ice americano"
    assert restored.messages[3]["content"] == "For here or to go?"


def test_corrections_persist_in_order(sqlite_store):
    session = sqlite_store.create(scenario_id="cafe_order", level="A1")
    sqlite_store.record_turn(
        session.id,
        user_text="I go yesterday",
        turn=_turn("Nice!", [Correction(kind="mistake", original="I go yesterday", better="I went yesterday.", note="지난 일이에요.")]),
    )
    sqlite_store.record_turn(
        session.id,
        user_text="He don't know",
        turn=_turn("Okay.", [Correction(kind="mistake", original="He don't know", better="He doesn't know.", note="he 는 doesn't 예요.")]),
    )

    corrections = sqlite_store.corrections(session.id)
    assert [c.original for c in corrections] == ["I go yesterday", "He don't know"]


def test_correction_kind_persists_and_splits_counts(sqlite_store):
    """mistake / polish 가 DB 를 왕복하고 리포트 카운트가 나뉘는지."""
    session = sqlite_store.create(scenario_id="cafe_order", level="A1")
    sqlite_store.record_turn(
        session.id,
        user_text="I go yesterday",
        turn=_turn(
            "Nice!",
            [
                Correction(kind="mistake", original="I go yesterday", better="I went yesterday.", note="지난 일이에요."),
                Correction(kind="polish", original="Large", better="Large, please.", note="please 를 붙이면 부드러워요."),
            ],
        ),
    )

    restored = sqlite_store.corrections(session.id)
    assert [c.kind for c in restored] == ["mistake", "polish"]

    class FakeClient:
        name = "fake"

        def describe(self) -> str:
            return "fake"

        def ping(self) -> bool:
            return True

        def chat_json(self, **_):
            return {"summary_ko": "좋아요.", "patterns_ko": [], "learned": []}

    report = ReportService(FakeClient()).build(
        session_id=session.id,
        scenario=load_scenarios()["cafe_order"],
        level="A1",
        messages=[{"role": "user", "content": "I go yesterday"}],
        corrections=restored,
    )
    # polish 는 '틀린 것'으로 세지 않는다
    assert report.mistake_count == 1
    assert report.polish_count == 1
    assert len(report.mistakes) == 2


def test_transcript_separates_mistake_from_polish():
    """리포트 프롬프트가 polish 를 실수로 오해하지 않도록 분리해 넘기는지."""
    from app.report.service import _transcript

    text = _transcript(
        [{"role": "user", "content": "I go"}],
        [
            Correction(kind="mistake", original="I go", better="I went", note="지난 일이에요."),
            Correction(kind="polish", original="Large", better="Large, please.", note="부드러워요."),
        ],
    )
    mistakes_at = text.index("REAL MISTAKES")
    polish_at = text.index("POLISH")
    assert text.index('"I go"') > mistakes_at
    assert text.index('"Large"') > polish_at


def test_end_marks_session_ended(sqlite_store):
    session = sqlite_store.create(scenario_id="cafe_order", level="A1")
    assert sqlite_store.get(session.id).ended is False
    sqlite_store.end(session.id)
    assert sqlite_store.get(session.id).ended is True


def test_get_unknown_session_returns_none(sqlite_store):
    assert sqlite_store.get("nope") is None


def test_report_insight_schema_is_flat():
    schema = json_schema_for(ReportInsight)
    import json

    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert set(schema["required"]) == {"summary_ko", "patterns_ko", "learned"}


def test_report_assembles_without_calling_llm_for_mistakes():
    """틀린 문장 모음은 DB 값 그대로여야 한다 (LLM 이 지어내지 않는다)."""

    class FakeClient:
        name = "fake"

        def describe(self) -> str:
            return "fake"

        def ping(self) -> bool:
            return True

        def chat_json(self, **_):
            return {
                "summary_ko": "잘 하셨어요.",
                "patterns_ko": ["과거형을 자주 빠뜨려요."],
                "learned": [{"english": "I went there.", "note_ko": "지난 일을 말할 때 써요."}],
            }

    corrections = [Correction(kind="mistake", original="I go", better="I went", note="지난 일이에요.")]
    report = ReportService(FakeClient()).build(
        session_id="s1",
        scenario=load_scenarios()["cafe_order"],
        level="A1",
        messages=[{"role": "user", "content": "I go"}, {"role": "assistant", "content": "Nice!"}],
        corrections=corrections,
    )

    assert report.mistake_count == 1
    assert report.mistakes[0].original == "I go"
    assert report.turn_count == 1
    assert report.insight.patterns_ko == ["과거형을 자주 빠뜨려요."]


def test_in_memory_store_matches_protocol():
    store = InMemorySessionStore()
    session = store.create(scenario_id="cafe_order", level="A2")
    store.record_turn(session.id, user_text="hi", turn=_turn("Hello!"))
    assert len(store.get(session.id).messages) == 2
    store.end(session.id)
    assert store.get(session.id).ended is True
