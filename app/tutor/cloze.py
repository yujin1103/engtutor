"""빈칸 채우기. LLM 을 부르지 않는다 — 이미 있는 항목에서 **깎아낸다**.

왜 LLM 을 안 쓰는가
-------------------
CLAUDE.md 의 규칙이기도 하지만, 그 전에 근거가 있다. 실시간으로 문제를 만들게 하면
없는 단어를 정답으로 내는 일이 실제로 일어난다 — NGSL 2,801개 전수 조사에서
`restaurate`·`habor`·`oranje` 가 나왔다(docs/hallucinations.md). 문제는 정답이
확실해야 하고, 확실한 정답은 **이미 사람 손을 거친 데이터**에만 있다.

그래서 여기서 하는 일은 생성이 아니라 삭제다. 검수를 통과한 예문에서 표제어를
지우면 그 자리가 빈칸이고, 지운 것이 정답이다. 새로 만들어지는 정보가 없으니
새로 틀릴 것도 없다.

왜 '형태가 틀림' 을 따로 두는가
------------------------------
이 프로젝트의 전제가 "왕초보는 뜻이 아니라 형태에서 틀린다"이다. `listen` 을
써야 할 자리에 `listening` 을 넣은 답과 `watch` 를 넣은 답을 같은 오답으로
묶으면, 정작 가르쳐야 할 것을 못 가르친다. 원형이 같으면 다른 판정을 준다.

음성 입력을 염두에 둔 설계
--------------------------
정답 후보가 **하나로 정해져 있다.** 자유 발화 전사와 달리 여기서는 무엇이 나와야
하는지 미리 알고 있어서, 전사가 흔들려도 판정이 무너지지 않는다. 사전에 없는 말이
들어오면 오답이 아니라 `not_a_word` 로 돌려준다 — 학습자 잘못인지 마이크 잘못인지
구분해서 다시 말할 기회를 주기 위해서다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..content import lexicon
from ..content.screening import mentions, screen

BLANK = "____"

# 예문에서 낱말을 집는다. 축약형과 하이픈 합성어는 한 낱말로 본다.
_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")


@dataclass(frozen=True)
class ClozeItem:
    """빈칸 하나. `answer` 는 예문에 실제로 있던 표면형이라 굴절형일 수 있다."""

    word: str
    level: str
    meaning_ko: str
    sentence: str
    answer: str
    pattern: str | None
    rank: int | None
    reviewed: bool


Verdict = Literal["correct", "wrong_form", "wrong_word", "not_a_word", "empty"]


@dataclass(frozen=True)
class ClozeResult:
    verdict: Verdict
    said: str
    answer: str
    message_ko: str

    @property
    def ok(self) -> bool:
        return self.verdict == "correct"


def normalize(text: str) -> str:
    """비교용으로 다듬는다. 대소문자·문장부호·군더더기 공백을 없앤다."""
    return " ".join(_WORD.findall(text.lower()))


def make_item(row) -> ClozeItem | None:
    """항목 하나에서 빈칸 문제를 깎아낸다. 만들 수 없으면 None.

    예문에 표제어가 없으면 만들지 않는다. 선별기가 이미 그런 항목을 지적하고
    있으므로(`example_missing_headword`) 여기서 억지로 만들 이유가 없다.
    """
    example = (getattr(row, "example", "") or "").strip()
    word = (getattr(row, "word", "") or "").strip()
    if not example or not word:
        return None

    for match in _WORD.finditer(example):
        if not mentions(match.group(), word):
            continue
        surface = match.group()
        sentence = example[: match.start()] + BLANK + example[match.end() :]
        return ClozeItem(
            word=word,
            level=getattr(row, "level", "A1"),
            meaning_ko=getattr(row, "meaning_ko", ""),
            sentence=sentence,
            answer=surface,
            pattern=getattr(row, "pattern", None),
            rank=getattr(row, "rank", None),
            reviewed=bool(getattr(row, "reviewed", False)),
        )
    return None


def is_safe_to_serve(row) -> bool:
    """학습자에게 내보내도 되는 항목인가.

    사람이 승인했으면(`reviewed`) 무조건 통과다. 아직 검수 큐에 있는 항목은
    **선별기 지적이 하나도 없을 때만** 내보낸다.

    이건 검수를 대신하는 게 아니다. 빈칸 문제가 쓰는 것은 `example` 과 `word`
    뿐이고, 확인된 환각 13건은 전부 `usage_note` 와 `confused_with` 에 있었다.
    그래도 예문이 어색할 여지는 남으므로, 승인된 항목이 쌓이면 이 문은 좁혀야 한다.
    """
    if bool(getattr(row, "reviewed", False)):
        return True
    return not screen(row)


def grade(item: ClozeItem, said: str) -> ClozeResult:
    """학습자가 말하거나 적은 답을 채점한다. 사전이 없어도 동작한다."""
    heard = normalize(said)
    answer = normalize(item.answer)

    if not heard:
        return ClozeResult("empty", said, item.answer, "아직 아무 말도 못 들었어요.")

    if heard == answer:
        return ClozeResult("correct", said, item.answer, "맞아요!")

    # 원형이 같으면 단어는 맞고 형태가 틀린 것이다. 이 앱이 가장 가르치고 싶은 자리다.
    if lexicon.same_lemma(heard, answer):
        return ClozeResult(
            "wrong_form",
            said,
            item.answer,
            f"단어는 맞아요. 형태만 달라요 — 여기서는 '{item.answer}' 예요.",
        )

    # 사전에 없는 말은 오답이라기보다 잘못 들었을 가능성이 크다. 음성 입력에서 특히.
    if lexicon.known(heard) is False:
        return ClozeResult(
            "not_a_word",
            said,
            item.answer,
            f"'{said.strip()}' 로 들었는데 그런 단어가 없어요. 다시 말해 볼까요?",
        )

    return ClozeResult(
        "wrong_word", said, item.answer, f"다른 단어예요. 여기서는 '{item.answer}' 예요."
    )


def is_speakable(item: ClozeItem) -> bool:
    """음성으로 답하기에 적당한 빈칸인가.

    NGSL 빈도 최상위는 전부 기능어다 — `be`, `and`, `to`, `a`, `in`. 이것들은
    짧고 강세가 없어서 전사가 가장 많이 흔들리는 부류이고, 소리 내어 연습할
    가치도 낮다. "and 를 말해 보세요"는 연습이 아니다.

    처음에는 "WordNet 에 품사가 잡히면 내용어"로 판정했는데 듣지 않았다. WordNet 에
    `he`(헬륨), `a`(비타민 A), `in`(인치) 같은 **동형 내용어**가 들어 있어서
    기능어가 그대로 통과한다. 그래서 사전 조회가 아니라 기능어 목록으로 뺀다.

    타자로 답할 때는 이 문을 열어 둔다 — 기능어 빈칸도 읽기 연습으로는 쓸모가 있다.
    """
    word = item.word.strip().lower()
    if word in lexicon.FUNCTION_WORDS or len(word) < 3:
        return False
    # 정답이 굴절형이면 그쪽도 기능어일 수 있다 (be -> am, do -> does).
    return normalize(item.answer) not in lexicon.FUNCTION_WORDS
