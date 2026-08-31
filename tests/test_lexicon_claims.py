"""품사 단정 검사.

없는 단어보다 잡기 어려운 결함을 다룬다 — 단어는 전부 실재하고 **주장만 거짓**인
경우다. "'name'은 명사로만 쓰여요" 는 문장이 멀쩡하고 단어도 멀쩡한데 거짓이다.
사전의 품사 태그와 대조해야만 드러난다.

사전(WordNet)이 없는 환경에서도 테스트가 통과해야 한다. 사전이 필요한 시험은
건너뛰고, 사전 없이도 돌아야 하는 것(가산성 호출)은 항상 검사한다.
"""

from __future__ import annotations

import pytest

from app.content import lexicon
from app.content.screening import screen

needs_lexicon = pytest.mark.skipif(
    not lexicon.available(), reason="WordNet 코퍼스가 없습니다"
)


def _row(word: str, usage_note: str, **over):
    base = {
        "word": word,
        "level": "A2",
        "meaning_ko": "뜻",
        "pattern": None,
        "example": f"I like the {word}.",
        "usage_note": usage_note,
        "confused_with": [],
    }
    base.update(over)
    return type("W", (), base)()


def _codes(row):
    return {f.code for f in screen(row)}


# --- 사전 자체 ---------------------------------------------------------------


@needs_lexicon
def test_a_word_with_two_parts_of_speech_reports_both():
    assert lexicon.parts_of_speech("name") == frozenset({"n", "v"})


@needs_lexicon
def test_an_invented_word_is_unknown_not_empty():
    """None 과 빈 집합을 구분해야 한다. 섞이면 사전에 없는 단어가 전부 결함이 된다."""
    assert lexicon.parts_of_speech("restaurate") is None
    assert lexicon.known("restaurate") is False


@needs_lexicon
def test_an_inflected_form_is_still_known():
    assert lexicon.known("borrowed") is True


# --- 품사 한정 주장 ("…로만") -------------------------------------------------


@needs_lexicon
def test_a_noun_only_claim_about_a_verb_is_flagged():
    row = _row("name", "'name'은 명사로만 쓰이고, 동사 'to name'과는 다릅니다.")
    assert "pos_claim_overreach" in _codes(row)


@needs_lexicon
def test_a_claim_for_a_part_of_speech_the_word_never_has_is_worse():
    """abroad 는 부사다. '명사로만' 은 단순화가 아니라 그냥 거짓이라 심각도가 높다."""
    row = _row("abroad", "'abroad'는 명사로만 쓰이고, 가는 곳은 따로 말해요.")
    findings = {f.code: f.severity for f in screen(row)}
    assert findings.get("pos_claim_wrong") == "medium"


@needs_lexicon
def test_a_true_noun_only_claim_is_not_flagged():
    row = _row("music", "'music'은 명사로만 써요. 음악을 듣는 건 listen to music 이에요.")
    assert not {"pos_claim_wrong", "pos_claim_overreach"} & _codes(row)


@needs_lexicon
def test_a_claim_about_a_korean_word_is_not_judged():
    """영어 사전으로 한국어에 대한 주장을 반증할 수 없다. 실제 데이터에 20건 있었다."""
    row = _row("advantage", "한국어 '이점'은 명사로만 쓰이지만 영어는 달라요.")
    assert not {"pos_claim_wrong", "pos_claim_overreach"} & _codes(row)


@needs_lexicon
def test_a_claim_about_a_word_the_dictionary_lacks_is_not_judged():
    """사전에 없는 건 '모른다'지 '틀렸다'가 아니다."""
    row = _row("whereas", "'whereas'는 부사로만 쓰여요.")
    assert not {"pos_claim_wrong", "pos_claim_overreach"} & _codes(row)


# --- 품사 부정 주장 ("…로는 쓰지 않는다") --------------------------------------


@needs_lexicon
def test_denying_a_part_of_speech_the_word_has_is_flagged():
    row = _row("name", "'name'은 동사로는 쓰지 않아요.")
    assert "pos_claim_wrong" in _codes(row)


@needs_lexicon
def test_a_true_denial_is_not_flagged():
    """fact 는 정말 명사뿐이라 '동사로 안 쓴다' 는 참이다."""
    row = _row("fact", "'fact'는 동사로는 쓰지 않아요.")
    assert "pos_claim_wrong" not in _codes(row)


# --- 표기를 바꿔 검사를 피할 수 없다 -------------------------------------------
#
# 정규식에 `(명사|동사|형용사|부사)` 를 박아 두었더니 순우리말로 쓴 단정이 통째로
# 지나갔다. 이 자료의 표기는 한자어로 통일했지만, 검사기가 표기 하나에 매여 있으면
# 누군가 다시 순우리말로 쓰는 순간 구멍이 다시 열린다. 그래서 자료가 아니라
# 검사기 쪽에서 막고, 그 사실을 여기서 못 박는다.


@needs_lexicon
def test_a_native_term_only_claim_is_flagged_too():
    """'이름씨로만' 도 '명사로만' 과 똑같이 잡혀야 한다."""
    row = _row("name", "'name'은 이름씨로만 쓰이고, 동사 'to name'과는 다릅니다.")
    assert "pos_claim_overreach" in _codes(row)


@needs_lexicon
def test_a_native_term_denial_is_flagged_too():
    row = _row("name", "'name'은 움직씨로는 쓰지 않아요.")
    assert "pos_claim_wrong" in _codes(row)


# --- 가산성: 판정하지 않고 사람을 부른다 ---------------------------------------


def test_a_countability_claim_is_handed_to_a_human():
    """WordNet 에 가산성 정보가 없다. 그래서 판정이 아니라 호출이다."""
    row = _row("advice", "advice는 불가산명사라서 'a advice' 처럼 쓰면 안 돼요.")
    assert "countability_claim_unchecked" in _codes(row)


def test_the_countability_call_does_not_need_the_dictionary():
    """사전이 없다고 이 검사까지 조용히 꺼지면 안 된다."""
    row = _row("adviser", "'adviser'는 불가산명사예요.")
    assert "countability_claim_unchecked" in _codes(row)


def test_a_note_without_any_claim_is_left_alone():
    row = _row("borrow", "빌려주는 쪽은 lend 예요. 방향이 반대예요.")
    assert not {
        "pos_claim_wrong",
        "pos_claim_overreach",
        "countability_claim_unchecked",
    } & _codes(row)
