"""STT 측정의 판정 로직.

녹음은 사람이 해야 하는 일이라 다시 하기 어렵다. 그러니 **녹음하기 전에** 판정이
맞는지 확인해 둔다 — 판정기가 틀리면 녹음이 통째로 헛수고가 된다.

핵심 질문은 하나다: 전사가 학습자의 오류를 살려 뒀는가, 매끄럽게 고쳐 버렸는가.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eval_stt import contains_phrase, load_probes, wer  # noqa: E402

PROBES = Path(__file__).resolve().parent.parent / "scripts" / "data" / "correction_probes.yaml"


# --- 오류가 살아남았는지 --------------------------------------------------------


@pytest.mark.parametrize(
    "transcript, phrase",
    [
        # 그대로 전사된 경우 — 오류가 살아남았다.
        ("I want ice americano", "ice americano"),
        ("I want ice americano.", "ice americano"),
        ("i WANT ICE americano", "ice americano"),
        ("Um, I want ice americano please", "ice americano"),
        ("He don't know the way.", "he don't"),
    ],
)
def test_a_faithful_transcript_keeps_the_error(transcript, phrase):
    assert contains_phrase(transcript, phrase)


@pytest.mark.parametrize(
    "transcript, phrase",
    [
        # Whisper 가 매끄럽게 고쳐 버린 경우 — 교정할 것이 사라졌다.
        ("I want an iced americano", "ice americano"),
        ("I want iced americano", "ice americano"),
        ("He doesn't know the way.", "he don't"),
        ("I live in Seoul", "am live"),
        ("My hobby is listening to music", "is listen to"),
        ("I'm interested in movies.", "interesting in"),
    ],
)
def test_a_smoothed_transcript_loses_the_error(transcript, phrase):
    assert not contains_phrase(transcript, phrase)


def test_word_order_repair_is_caught_even_though_every_word_survives():
    """어순 오류가 이 측정의 함정이다. 낱말은 다 있는데 오류만 사라진다."""
    assert contains_phrase("How I can go to subway station?", "how i can")
    assert not contains_phrase("How can I go to the subway station?", "how i can")
    # 낱말 집합으로만 보면 둘을 구분하지 못한다는 것을 명시해 둔다.
    assert set("how can i go".split()) == set("how i can go".split())


@pytest.mark.parametrize(
    "transcript, phrase",
    [
        ("How long does it take?", "long it takes"),
        ("How much does this coffee cost?", "much cost this"),
    ],
)
def test_the_other_order_errors_are_caught_too(transcript, phrase):
    assert not contains_phrase(transcript, phrase)


def test_an_empty_or_failed_transcript_is_not_a_survival():
    for transcript in ("", "   ", "..."):
        assert not contains_phrase(transcript, "ice americano")


def test_a_transcript_shorter_than_the_phrase_does_not_crash():
    assert not contains_phrase("ice", "ice americano")


# --- WER ----------------------------------------------------------------------


def test_wer_is_zero_for_an_exact_match():
    assert wer("I want ice americano", "I want ice americano.") == 0.0


def test_wer_counts_words_against_the_reference():
    # 4단어 중 1단어가 바뀌었다.
    assert wer("I want ice americano", "I want iced americano") == pytest.approx(0.25)


def test_wer_handles_an_empty_hypothesis():
    assert wer("I want ice americano", "") == 1.0


# --- 문제 목록 ----------------------------------------------------------------


def test_every_mistake_probe_can_be_judged():
    """error_phrase 가 없으면 그 문장은 측정에서 조용히 빠진다."""
    import yaml

    rows = yaml.safe_load(PROBES.read_text(encoding="utf-8"))
    mistakes = [r for r in rows if r["expect"] == "mistake"]
    assert mistakes
    assert all(r.get("error_phrase") for r in mistakes), "error_phrase 가 빠진 문장이 있다"
    assert len(load_probes(PROBES)) == len(mistakes)


def test_every_error_phrase_actually_occurs_in_its_sentence():
    """조각이 문장에 없으면 어떤 전사도 통과하지 못해 생존율이 항상 0이 된다."""
    for probe in load_probes(PROBES):
        assert contains_phrase(probe["say"], probe["error_phrase"]), probe["say"]


def test_clean_sentences_are_not_measured():
    """맞는 문장은 오류가 없으니 생존을 물을 수 없다."""
    assert all(p["expect"] == "mistake" for p in load_probes(PROBES))
