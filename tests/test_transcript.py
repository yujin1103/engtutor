"""음성 전사와 확정본을 따로 저장하는 것.

핵심은 **차이를 버리지 않는 것**이다. 한 칸에만 저장하면 "STT 를 믿어도 되는가"
라는 질문에 영원히 답할 수 없다. 여기 시험은 그 차이가 실제로 남는지, 그리고
가장 위험한 실패(틀린 영어를 매끄럽게 고쳐 적는 것)가 잡히는지를 본다.
"""

from __future__ import annotations

import pytest

from tests.conftest import temporary_database

from app.tutor.transcript import (
    LOW_CONFIDENCE,
    TranscriptWord,
    confident_edits,
    edits,
    parse_words,
    tokens,
    uncertain_words,
)

# faster-whisper 가 주는 모양. 앞 공백까지 그대로 온다.
HEARD = "I want an iced americano"
CONFIRMED = "I want ice americano"
WORDS = [
    {"word": " I", "probability": 0.99},
    {"word": " want", "probability": 0.98},
    {"word": " an", "probability": 0.91},
    {"word": " iced", "probability": 0.88},
    {"word": " americano", "probability": 0.42},
]


# --- 전사 파싱: 깨지지 않는 것이 우선 -----------------------------------------


def test_words_are_parsed_with_their_probability():
    words = parse_words(WORDS)
    assert [w.word for w in words] == ["I", "want", "an", "iced", "americano"]
    assert words[-1].probability == pytest.approx(0.42)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        ["plain", "strings"],
        [{"text": "iced", "confidence": 0.8}],
        [{"word": "iced", "probability": "0.8"}],
        [{"word": "iced"}],
        [{"nonsense": 1}, {"word": ""}, "ok"],
        [None, 3, {"word": "fine", "probability": None}],
    ],
)
def test_odd_shapes_never_raise(raw):
    """전사 하나 때문에 턴이 통째로 죽으면 안 된다. 못 알아보면 조용히 건너뛴다."""
    parse_words(raw)


def test_an_unknown_probability_is_not_low_confidence():
    """모르는 것과 낮은 것은 다르다. 섞으면 확률 없는 엔진에서 전부 흐려진다."""
    assert not TranscriptWord("iced").uncertain
    assert TranscriptWord("iced", LOW_CONFIDENCE - 0.01).uncertain
    assert not TranscriptWord("iced", LOW_CONFIDENCE).uncertain


def test_uncertain_words_are_the_ones_to_show():
    assert [w.word for w in uncertain_words(parse_words(WORDS))] == ["americano"]


# --- 차이 --------------------------------------------------------------------


def test_identical_text_has_no_edits():
    assert edits(HEARD, HEARD) == []


def test_case_and_punctuation_are_not_edits():
    assert edits("I want ice americano", "I want ice americano.") == []
    assert edits("I want ice", "I WANT ICE") == []


def test_the_learner_restoring_their_own_error_is_captured():
    changes = edits(HEARD, CONFIRMED)
    assert ("an", "ice") in [(c.heard, c.confirmed) for c in changes if c.kind == "replaced"]
    assert "iced" in [c.heard for c in changes if c.kind == "removed"]


def test_a_pure_insertion_is_reported():
    changes = edits("I want coffee", "I want a coffee")
    assert [c.kind for c in changes] == ["inserted"]
    assert changes[0].confirmed == "a"


def test_a_lopsided_replacement_does_not_invent_pairs():
    """짝이 안 맞는 자리를 억지로 짝지으면 차이가 왜곡된다."""
    for change in edits("I go store", "I am going to the store"):
        if change.kind == "replaced":
            assert change.heard and change.confirmed


# --- 가장 위험한 실패: 확신했는데 고쳐진 자리 ---------------------------------


def test_a_confident_word_the_learner_changed_is_the_smoothing_signal():
    """Whisper 가 자신 있게 다른 말을 적었고 학습자가 되돌린 자리.

    확률이 낮았던 곳을 고친 건 그냥 잘못 들은 것이라 덜 위험하다.
    """
    flagged = confident_edits(parse_words(WORDS), edits(HEARD, CONFIRMED))
    assert {c.heard for c in flagged} == {"an", "iced"}


def test_editing_a_word_the_stt_doubted_is_not_flagged():
    words = parse_words([{"word": "I", "probability": 0.99},
                         {"word": "want", "probability": 0.99},
                         {"word": "tea", "probability": 0.30}])
    flagged = confident_edits(words, edits("I want tea", "I want coffee"))
    assert flagged == []


def test_transcripts_without_probabilities_are_never_called_confident():
    """모르는 것을 '확신했다'로 치면 숫자가 통째로 거짓이 된다."""
    words = parse_words(["I", "want", "an", "iced", "americano"])
    assert confident_edits(words, edits(HEARD, CONFIRMED)) == []


def test_tokens_ignore_punctuation():
    assert tokens("I don't want, thanks!") == ["i", "don't", "want", "thanks"]


# --- 저장 ---------------------------------------------------------------------


def _turn():
    from app.tutor.schemas import TurnResponse

    return TurnResponse(
        reply="Sure!",
        reply_ko="그럼요!",
        corrections=[],
        say_en="Yes, please.",
        say_more="Yes, that sounds good.",
        hint_ko="이렇게 말해 보세요.",
    )


def _rows(database, session_id):
    from sqlalchemy import select

    from app.db.models import TurnRow

    with database.db_session() as db:
        return db.execute(
            select(TurnRow).where(TurnRow.session_id == session_id, TurnRow.role == "user")
        ).scalar_one()


def test_a_voice_turn_keeps_both_strings(tmp_path, monkeypatch):
    """확정본과 전사가 **둘 다** 남아야 한다. 하나만 남기면 차이가 사라진다."""
    with temporary_database(tmp_path / "t.db", monkeypatch) as database:
        from app.session_store import SqliteSessionStore

        store = SqliteSessionStore()
        session = store.create(scenario_id="cafe_order", level="A1")
        store.record_turn(
            session.id,
            user_text=CONFIRMED,
            turn=_turn(),
            input_mode="voice",
            transcript=HEARD,
            transcript_words=WORDS,
        )
        row = _rows(database, session.id)
        assert row.content == CONFIRMED
        assert row.transcript == HEARD
        assert row.input_mode == "voice"
        assert len(parse_words(row.transcript_words)) == 5


def test_a_typed_turn_stores_no_transcript(tmp_path, monkeypatch):
    """타자 입력에서 전사 칸이 채워지면 나중에 통계가 거짓이 된다."""
    with temporary_database(tmp_path / "t.db", monkeypatch) as database:
        from app.session_store import SqliteSessionStore

        store = SqliteSessionStore()
        session = store.create(scenario_id="cafe_order", level="A1")
        store.record_turn(session.id, user_text="I want tea", turn=_turn())
        row = _rows(database, session.id)
        assert row.input_mode == "text"
        assert row.transcript is None and row.transcript_words is None


def test_chat_request_defaults_to_typing():
    """기존 호출부가 새 필드를 몰라도 지금과 똑같이 동작해야 한다."""
    from app.main import ChatRequest

    req = ChatRequest(scenario_id="cafe_order", message="hi")
    assert req.input_mode == "text"
    assert req.transcript is None and req.transcript_words is None


def test_chat_request_accepts_a_voice_turn():
    from app.main import ChatRequest

    req = ChatRequest(
        scenario_id="cafe_order",
        message=CONFIRMED,
        input_mode="voice",
        transcript=HEARD,
        transcript_words=WORDS,
    )
    assert req.transcript == HEARD and len(req.transcript_words) == 5
