"""사전 조회. 특히 **원형 찾기** — 빈칸 채점이 여기에 걸려 있다.

`lemmas` 가 원형을 놓치면 "형태만 틀렸다"가 "다른 단어다"로 채점된다. 반대로
가짜 원형을 붙이면 없는 말을 "단어는 맞아요"라고 칭찬한다. 둘 다 학습자에게
거짓말이라, 놓치는 쪽과 지어내는 쪽을 같이 시험한다.
"""

from __future__ import annotations

import pytest

from app.content import lexicon

needs_lexicon = pytest.mark.skipif(not lexicon.available(), reason="WordNet 코퍼스가 없습니다")


# ---------------------------------------------------------------- 놓치던 자리
@needs_lexicon
@pytest.mark.parametrize(
    "surface,base",
    [
        # 복수형이 그 자체로 WordNet 표제어라 morphy 가 거기서 멈추던 것들.
        # minutes = 회의록, days = 시절, instructions = 사용 설명서.
        ("years", "year"),
        ("minutes", "minute"),
        ("days", "day"),
        ("instructions", "instruction"),
        ("hours", "hour"),
        ("ways", "way"),
        ("things", "thing"),
        # 규칙 굴절과 불규칙 둘 다 여전히 잡혀야 한다.
        ("borrowing", "borrow"),
        ("teeth", "tooth"),
        ("went", "go"),
        ("best", "good"),
        ("later", "late"),
    ],
)
def test_an_inflected_form_finds_its_base(surface: str, base: str) -> None:
    assert base in lexicon.lemmas(surface), f"{surface} 의 원형에 {base} 가 없습니다"


# ------------------------------------------------------------ 지어내던 자리
@needs_lexicon
@pytest.mark.parametrize(
    "surface,not_base",
    [
        # 접미사를 떼는 규칙이 만들어 내는 껍데기들. 사람이 실제로 말할 수 있는
        # 짧은 말이라(as/us) 그냥 두면 오답을 "형태만 달라요"로 칭찬한다.
        ("as", "a"),
        ("us", "u"),
        ("boss", "bos"),
        ("pass", "pas"),
        # 겹자음 규칙이 갈라 주는 자리. rat 의 과거는 ratted 지 rated 가 아니다.
        ("rated", "rat"),
        ("scared", "scar"),
        ("shining", "shin"),
        # serf 의 복수는 serfs 다.
        ("serves", "serf"),
    ],
)
def test_a_lookalike_does_not_become_a_base(surface: str, not_base: str) -> None:
    assert not_base not in lexicon.lemmas(surface), (
        f"{surface} 에 가짜 원형 {not_base} 가 붙었습니다"
    )


@needs_lexicon
def test_the_same_word_in_two_forms_is_the_same_word() -> None:
    assert lexicon.same_lemma("years", "year")
    assert lexicon.same_lemma("year", "years")
    assert not lexicon.same_lemma("year", "hour")


# ------------------------------------------------------------------ 형태 규칙
@pytest.mark.parametrize(
    "base,form",
    [
        ("stop", "stopped"),   # 자음-모음-자음은 겹친다
        ("run", "running"),
        ("play", "played"),    # y 앞이 모음이면 겹치지도 i 로 바뀌지도 않는다
        ("study", "studied"),
        ("watch", "watches"),
        ("late", "later"),
        ("late", "latest"),
        ("year", "years"),
    ],
)
def test_regular_forms_are_generated(base: str, form: str) -> None:
    """사전 없이도 도는 순수 규칙이다. WordNet 이 없는 환경에서도 시험한다."""
    assert form in lexicon._regular_forms(base)


@pytest.mark.parametrize(
    "base,form",
    [
        ("rat", "rated"),      # ratted 라야 한다
        ("scar", "scared"),
        ("serf", "serves"),
        ("bos", "boss"),       # s 로 끝나면 -es 다
    ],
)
def test_forms_outside_the_rules_are_not_generated(base: str, form: str) -> None:
    assert form not in lexicon._regular_forms(base)


def test_a_missing_dictionary_still_answers() -> None:
    """사전이 없는 환경에서도 자기 자신은 돌려줘야 한다 — 앱이 멈추면 안 된다."""
    assert "borrow" in lexicon.lemmas("borrow")


# ------------------------------------------------------ WordNet 이 모르는 실재어
def test_an_everyday_loanword_is_not_called_a_non_word() -> None:
    """WordNet 은 2006년 판이라 `americano` 를 모른다.

    이 앱의 첫 시나리오가 카페 주문인데, 그걸 '없는 단어'로 판정하면 빈칸 채점이
    학습자에게 "그런 단어가 없어요"라고 말한다. 확인해서 적어 둔 사전을 함께 본다.
    """
    if "americano" not in lexicon.extra_lexicon():
        pytest.skip("추가 사전에 아직 없습니다 (scripts/verify_words.py)")
    assert lexicon.known("americano") is True
    assert lexicon.parts_of_speech("americano") == frozenset({lexicon.POS_NOUN})


def test_every_extra_entry_carries_its_source() -> None:
    """출처 없는 항목은 지어낸 사전이다. 그러면 이 파일을 믿을 근거가 사라진다."""
    for word, entry in lexicon.extra_lexicon().items():
        assert entry.get("source", "").startswith("https://"), word
        assert entry.get("gloss") or entry.get("glosses"), word


def test_wordnet_wins_when_both_dictionaries_know_a_word() -> None:
    """두 사전이 겹칠 때 어느 쪽을 믿을지 고민할 일을 만들지 않는다."""
    if not lexicon.available():
        pytest.skip("WordNet 코퍼스가 없습니다")
    assert lexicon.POS_VERB in (lexicon.parts_of_speech("borrow") or frozenset())
