"""생성 중인 JSON 에서 reply 를 긁어내는 로직.

스트리밍의 토대라 여기가 틀리면 화면에 깨진 글자가 흐른다.
"""

from __future__ import annotations

import json

import pytest

from app.llm.partial_json import extract_string


def test_returns_nothing_before_the_field_appears():
    assert extract_string('{"rep', "reply") == ("", False)
    assert extract_string('{"corrections": []', "reply") == ("", False)


def test_grows_as_the_buffer_grows():
    assert extract_string('{"reply": "Sure', "reply") == ("Sure", False)
    assert extract_string('{"reply": "Sure! What', "reply") == ("Sure! What", False)


def test_marks_complete_at_the_closing_quote():
    assert extract_string('{"reply": "Sure!", "say_en"', "reply") == ("Sure!", True)


def test_handles_whitespace_variants():
    for buf in ('{"reply":"Hi"', '{"reply" : "Hi"', '{"reply"\n  :\n  "Hi"'):
        assert extract_string(buf, "reply") == ("Hi", True)


def test_unescapes_while_streaming():
    assert extract_string('{"reply": "He said \\"hi\\"', "reply") == ('He said "hi"', False)
    assert extract_string('{"reply": "a\\nb"', "reply") == ("a\nb", True)
    assert extract_string('{"reply": "back\\\\slash"', "reply") == ("back\\slash", True)


def test_stops_before_a_half_written_escape():
    """이스케이프가 잘린 지점에서 멈춘다 — 다음 청크가 오면 이어진다."""
    assert extract_string('{"reply": "hi\\', "reply") == ("hi", False)
    assert extract_string('{"reply": "hi\\u00', "reply") == ("hi", False)
    assert extract_string('{"reply": "hi\\u00e9', "reply") == ("hié", False)


def test_korean_survives():
    assert extract_string('{"hint_ko": "사이즈를 물어봤어요', "hint_ko") == ("사이즈를 물어봤어요", False)


def test_picks_the_named_field_not_the_first_one():
    buf = '{"reply": "Hi", "say_en": "Large."'
    assert extract_string(buf, "say_en") == ("Large.", True)


def test_matches_the_real_json_for_every_prefix():
    """실제 응답을 한 글자씩 늘려가며, 완성 시점의 값이 json.loads 와 같은지."""
    payload = {
        "reply": 'Sure! "Large" it is.\nAnything else?',
        "corrections": [],
        "say_en": "No, thanks.",
        "say_more": "No, thank you.",
        "hint_ko": "더 필요한 게 있는지 물어봤어요.",
    }
    full = json.dumps(payload, ensure_ascii=False)

    for n in range(len(full) + 1):
        value, done = extract_string(full[:n], "reply")
        assert payload["reply"].startswith(value), f"{n}자에서 어긋남: {value!r}"
        if done:
            assert value == payload["reply"]

    assert extract_string(full, "reply") == (payload["reply"], True)
    assert extract_string(full, "hint_ko") == (payload["hint_ko"], True)


@pytest.mark.parametrize("field", ["reply", "reply_ko", "say_en", "say_more", "hint_ko"])
def test_every_string_field_is_extractable(field):
    from app.tutor.schemas import TurnResponse

    turn = TurnResponse(
        reply="Sure!",
        corrections=[],
        say_en="Large.",
        reply_ko="네, 알겠어요.",
        say_more="A large one, please.",
        hint_ko="사이즈를 물어봤어요.",
    )
    full = turn.model_dump_json()
    value, done = extract_string(full, field)
    assert done and value == getattr(turn, field)
