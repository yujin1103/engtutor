"""교정 검증.

떨어뜨리는 쪽이 항상 안전하다 — 교정을 하나 덜 보여주면 배울 기회를 한 번 놓치지만,
잘못된 교정을 보여주면 틀린 것을 가르친다. 그래서 검사는 느슨한 쪽이 아니라
민감한 쪽으로 맞춘다. 단, 정당한 교정을 떨어뜨리면 앱의 존재 이유가 사라지므로
"정당한 교정은 통과한다"는 시험이 더 중요하다.
"""

from __future__ import annotations

import pytest

from app.tutor.schemas import Correction, TurnResponse
from app.tutor.verify import (
    check_correction,
    check_turn,
    overlap,
    sound_corrections,
)


def _c(original, better, note="이렇게 말해요.", kind="mistake"):
    return Correction(original=original, kind=kind, better=better, note=note)


def _codes(correction, said):
    return {i.code for i in check_correction(correction, said)}


# --- 정당한 교정은 반드시 통과해야 한다 ---------------------------------------


@pytest.mark.parametrize(
    "said, original, better",
    [
        ("I want ice americano", "I want ice americano", "Can I get an iced americano, please?"),
        ("I am live in Seoul", "I am live in Seoul", "I live in Seoul."),
        ("How long it takes?", "How long it takes?", "How long does it take?"),
        ("He don't know", "He don't know", "He doesn't know."),
        ("My hobby is listen to music", "listen to music", "listening to music"),
    ],
)
def test_a_real_correction_passes(said, original, better):
    assert _codes(_c(original, better), said) == set()


def test_a_one_word_answer_is_not_judged_on_overlap():
    """'Large' -> 'Large, please.' 는 겹침 비율이 요동치는 구간이라 검사하지 않는다."""
    assert "better_replaces_intent" not in _codes(_c("Large", "Large, please."), "Large")


# --- 의도 교체: 실측된 결함 ---------------------------------------------------


def test_replacing_the_learners_intent_is_caught():
    """실측: 'How much is it?' 이 15회 중 11회 주문 문장으로 바뀌었다."""
    said = "How much is it?"
    assert "better_replaces_intent" in _codes(_c(said, "Can I get a coffee, please?"), said)


def test_a_rephrase_that_keeps_the_intent_is_not_caught():
    """같은 발화라도 'How much does it cost?' 는 의미를 지킨다 — 여기서 걸리면 안 된다."""
    said = "How much is it?"
    assert "better_replaces_intent" not in _codes(_c(said, "How much does it cost?"), said)


# --- 하지 않은 말 교정 --------------------------------------------------------


def test_correcting_something_the_learner_never_said_is_caught():
    issues = _codes(_c("I goed to the park", "I went to the park."), "Can I get a latte?")
    assert "original_not_said" in issues


def test_quoting_only_part_of_the_utterance_is_fine():
    """교정은 발화 전체가 아니라 문제가 된 조각만 인용하는 게 정상이다."""
    said = "My hobby is listen to music every day"
    assert "original_not_said" not in _codes(_c("listen to music", "listening to music"), said)


# --- 고친 게 없는 교정 --------------------------------------------------------


def test_a_correction_identical_to_the_original_is_caught():
    assert "better_same_as_original" in _codes(_c("I like coffee", "I like coffee."), "I like coffee")


# --- 없는 단어를 새로 넣는 경우 -----------------------------------------------


def test_inventing_a_word_in_the_correction_is_caught():
    said = "I go to restaurant"
    assert "better_invents_word" in _codes(_c(said, "You can restaurate there."), said)


def test_a_word_the_learner_already_used_is_not_judged():
    """WordNet 에 americano 가 없다. 학습자가 쓴 단어까지 검사하면 카페가 통째로 오탐이다."""
    said = "I want ice americano"
    assert "better_invents_word" not in _codes(
        _c(said, "Can I get an iced americano, please?"), said
    )


# --- 턴 단위 ------------------------------------------------------------------


def _turn(corrections):
    return TurnResponse(
        reply="Sure!",
        reply_ko="그럼요!",
        corrections=corrections,
        say_en="Yes, please.",
        say_more="Yes, that sounds good.",
        hint_ko="이렇게 말해 보세요.",
    )


def test_only_the_bad_correction_is_dropped():
    said = "How much is it? I want ice americano"
    good = _c("I want ice americano", "Can I get an iced americano, please?")
    bad = _c("How much is it?", "Can I get a coffee, please?")
    kept = sound_corrections(_turn([good, bad]), said)
    assert [c.better for c in kept] == [good.better]


def test_a_clean_turn_keeps_everything():
    said = "I am live in Seoul"
    good = _c(said, "I live in Seoul.")
    assert sound_corrections(_turn([good]), said) == [good]
    assert check_turn(_turn([good]), said) == []


def test_overlap_is_a_ratio_of_the_original():
    assert overlap("how much is it", "how much does it cost") == pytest.approx(0.75)
    assert overlap("how much is it", "can i get a coffee") == 0.0


# --- 공손 표지를 떼는 교정 ----------------------------------------------------


def test_dropping_please_is_caught():
    """실측: 'Can I get a hot latte, please?' -> 'Can I get a hot latte?' 가 나왔다."""
    said = "Can I get a hot latte, please?"
    assert "better_drops_politeness" in _codes(_c(said, "Can I get a hot latte?"), said)


def test_swapping_one_politeness_marker_for_another_is_fine():
    said = "Please give me water"
    assert "better_drops_politeness" not in _codes(_c(said, "Could I get some water?"), said)


def test_a_sentence_without_politeness_is_not_judged():
    """'I am live in Seoul' -> 'I live in Seoul.' 은 순수 삭제지만 정당한 교정이다."""
    said = "I am live in Seoul"
    assert "better_drops_politeness" not in _codes(_c(said, "I live in Seoul."), said)


def test_a_curly_apostrophe_does_not_look_like_an_invented_word():
    """모델은 doesn’t 를 곱슬따옴표로 쓴다. 이걸 doesn + t 로 쪼개면 정상 교정이 환각으로 잡힌다."""
    said = "He don't know the way."
    assert _codes(_c(said, "He doesn’t know the way."), said) == set()
