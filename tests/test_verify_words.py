"""단어 후보를 사전으로 걸러내는 검사기.

핵심은 **통과시키는 쪽이 아니라 떨어뜨리는 쪽**이다. Wiktionary 에는 폐어와
오철자까지 다 있어서, 존재만 보면 우리가 이미 지운 환각(`restaurate`)이 되돌아온다.
그래서 꼬리표를 읽는 부분을 고정해 둔다. 네트워크는 타지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_words as vw  # noqa: E402


def _section(*lines: str) -> str:
    return "\n".join(lines)


# ------------------------------------------------------------------ 꼬리표 판정
def test_a_word_whose_only_senses_are_obsolete_is_rejected():
    """`restaurate` 는 실제로 Wiktionary 표제어다 — 폐어라는 꼬리표가 붙어 있을 뿐이다."""
    parsed = vw.parse_english(
        _section("===Verb===", "{{en-verb}}", "# {{lb|en|obsolete|or|nonstandard}} To [[restore]].")
    )
    assert parsed["senses"] == 1
    assert parsed["modern"] == 0
    assert "obsolete" in parsed["labels"]


def test_a_word_with_one_living_sense_survives_its_old_ones():
    """`tumbler` 는 낡은 뜻이 여럿이지만 '유리컵'이 살아 있어서 통과해야 한다."""
    parsed = vw.parse_english(
        _section(
            "===Noun===",
            "# {{lb|en|archaic}} One who [[tumble|tumbles]].",
            "# A [[glass]] with no handle or stem.",
            "# {{lb|en|obsolete}} A [[dog]] used to catch rabbits.",
        )
    )
    assert parsed["senses"] == 3
    assert parsed["modern"] == 1
    assert parsed["glosses"][0].startswith("A glass")


def test_domain_and_region_labels_are_not_staleness():
    """`chemistry`·`US` 는 낡았다는 표시가 아니다. 여기서 막으면 멀쩡한 말이 사라진다."""
    parsed = vw.parse_english(_section("===Noun===", "# {{lb|en|US|informal}} A [[cookie]]."))
    assert parsed["modern"] == 1


def test_a_spelling_pointer_is_not_a_sense_of_its_own():
    """'X 의 오철자' 뿐인 표제어는 가르칠 단어가 아니라 다른 단어로 가는 표지판이다."""
    parsed = vw.parse_english(_section("===Noun===", "# {{misspelling of|en|harbor}}"))
    assert parsed["senses"] == 1
    assert parsed["modern"] == 0


def test_quotations_and_examples_are_not_counted_as_senses():
    """`#*` 와 `#:` 는 인용과 예문이다. 뜻으로 세면 숫자가 통째로 어긋난다."""
    parsed = vw.parse_english(
        _section("===Noun===", "# A [[glass]].", "#: I drank from the glass.", "#* 1899, Some Book")
    )
    assert parsed["senses"] == 1


def test_only_english_headings_count_as_parts_of_speech():
    parsed = vw.parse_english(
        _section("===Etymology 1===", "===Pronunciation===", "===Noun===", "# A [[drink]].")
    )
    assert parsed["pos_raw"] == ["Noun"]


# ------------------------------------------------------------------ 표기 정리
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A [[glass]] with no handle.", "A glass with no handle."),
        ("A [[cup|small cup]] of tea.", "A small cup of tea."),
        ("{{lb|en|US}} A [[cookie]].", "A cookie."),
        ("''Really'' hot coffee.", "Really hot coffee."),
    ],
)
def test_wikitext_markup_is_stripped(raw: str, expected: str):
    assert vw.strip_wikitext(raw) == expected


def test_rest_api_style_junk_is_stripped():
    """REST 응답에 위키 스타일 블록이 통째로 섞여 나올 때가 있다."""
    dirty = "<b>Assault</b>. .mw-parser-output .defdate{font-size:smaller}"
    assert vw.clean_definition(dirty) == "Assault."


# ------------------------------------------------------------------ 표제어 모양
@pytest.mark.parametrize("word", ["latte", "check-in", "o'clock", "sugar-free"])
def test_a_single_word_headword_is_accepted(word: str):
    assert vw._HEADWORD.match(word)


@pytest.mark.parametrize("word", ["ice cream", "Latte", "3rd", ""])
def test_phrases_and_odd_shapes_are_not_headwords(word: str):
    assert not vw._HEADWORD.match(word)


def test_the_user_agent_can_actually_be_sent():
    """헤더는 ASCII 여야 한다. 한글을 넣었다가 요청이 아예 안 나갔고, 재시도가 그걸
    삼켜서 멀쩡한 낱말 11개가 '사전에 없음'으로 떨어졌다."""
    vw.HEADERS["User-Agent"].encode("ascii")
