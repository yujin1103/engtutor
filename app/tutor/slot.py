"""빈칸 **그 자리**를 읽는다. 그리고 학습자가 낸 구·절에서 머리 낱말을 뽑는다.

LLM 도 통계 태거도 쓰지 않는다
------------------------------
자리의 품사를 알아내는 흔한 방법은 품사 태거(nltk 의 averaged perceptron 같은)를
문장에 돌리는 것이다. 쓰지 않았다. 태거는 확률로 답하고, 틀려도 왜 틀렸는지
설명할 수 없다. 이 앱은 학습자에게 "여기엔 명사가 들어가요"라고 **가르치므로**,
근거를 댈 수 없는 주장을 화면에 올리면 그건 품질 문제가 아니라 결함이다
(docs/hallucinations.md 의 전제와 같다).

그래서 여기 규칙은 전부 **닫힌 부류**만 본다 — 관사·소유격·조동사처럼 목록이
유한하고 변하지 않는 낱말들. 규칙이 안 걸리면 판정하지 않고 None 을 준다.
`lexicon.parts_of_speech` 가 None 으로 "모른다"를 말하는 것과 같은 규약이다.

실측 (검수 통과 예문 2,950개, 2026-08-26)
------------------------------------------
규칙이 걸린 것 844개(28.6%). 그중 낱말 품사가 둘 이상이라 **실제로 좁혀진 것이
405개**다 — 서빙 가능한 항목의 절반 가까이(1,414/2,950)가 다품사 낱말이라
이 좁히기가 없으면 그 절반에 대고 "명사예요"라고 말할 수 없다.

규칙과 사전이 어긋난 것은 844개 중 4개(0.5%)였다.

    [modal]      text    사전={명사}   자리={동사}   I will ____ you later.
    [modal]      barely  사전={부사}   자리={동사}   I can ____ see the road.
    [modal_subj] pickup  사전={명사}   자리={동사}   Can I ____ a coffee to go?
    [det]        specifically 사전={부사} 자리={명사} I want to talk about this ____.

앞의 셋은 WordNet 이 동사 `text`·`pick up` 을 모르거나 조동사 뒤에 부사가 낀
경우이고, 마지막은 예문 자체가 어색하다. 넷 다 **어긋나면 규칙을 버린다**로
처리한다(`narrow`). 좁히지 못할 뿐 거짓을 말하지는 않는다.
"""

from __future__ import annotations

import re
from typing import Literal

from ..content.lexicon import ALL_POS

BLANK = "____"

# 낱말과 문장부호를 따로 집는다. 부호를 버리면 "that, ____" 의 쉼표가 사라져서
# `that` 이 빈칸 바로 앞에 있는 것처럼 보인다 — 실제로 오탐이 하나 있었다.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’\-]*|[^\sA-Za-z]")

# 뒤에 명사구의 머리가 오는 한정사들. 전부 닫힌 부류다.
_DETERMINERS = frozenset(
    """
    a an the this that these those
    my your his her its our their
    some any another each every no
    """.split()
)

# 뒤에 동사 원형이 오는 조동사들.
_MODALS = frozenset("can could will would shall should must may might".split())

# 조동사 의문문의 주어. "Can I ____" 처럼 조동사와 빈칸 사이에 하나 낀다.
_SUBJECTS = frozenset("i you he she it we they".split())

# 명사구가 거기서 끝났다고 볼 수 있는 낱말들. "a ____ of", "a ____ with my tea".
# 이게 없으면 "a ____ pen"(형용사 자리)까지 명사로 단정한다.
_CLOSERS = frozenset("of in on at with for from to that which who and or but".split())

Source = Literal["slot", "word"]


def _split(sentence: str) -> tuple[list[str], list[str]] | None:
    """빈칸 앞뒤 토큰. 빈칸이 없으면 None."""
    if BLANK not in sentence:
        return None
    head, _, tail = sentence.partition(BLANK)
    return _TOKEN.findall(head.lower()), _TOKEN.findall(tail.lower())


def slot_pos(sentence: str) -> frozenset[str] | None:
    """이 빈칸이 받는 품사. 규칙이 안 걸리면 None("모른다").

    규칙은 넷뿐이고 전부 빈칸 **왼쪽**을 본다. 오른쪽은 명사구가 끝났는지
    확인하는 데만 쓴다.
    """
    parts = _split(sentence)
    if parts is None:
        return None
    left, right = parts
    prev = left[-1] if left else None
    prev2 = left[-2] if len(left) >= 2 else None
    nxt = right[0] if right else None
    # 뒤가 없거나 부호로 끝나면 명사구가 빈칸에서 끝난 것이다.
    closed = nxt is None or not nxt[0].isalpha() or nxt in _CLOSERS

    if prev in _DETERMINERS and closed:
        return frozenset("n")
    if prev in _MODALS:
        return frozenset("v")
    if prev in _SUBJECTS and prev2 in _MODALS:
        return frozenset("v")
    if prev == "please" and len(left) == 1:
        return frozenset("v")
    return None


def narrow(sentence: str, word_pos: frozenset[str] | None) -> tuple[frozenset[str], Source] | None:
    """자리 규칙으로 낱말 품사를 좁힌다. 좁히지 못하면 낱말 품사를 그대로.

    돌려주는 두 번째 값이 **무엇을 근거로 말해도 되는지**를 정한다.
    `"slot"` 이면 "여기엔 명사가 들어가요"라고 자리를 말할 수 있고,
    `"word"` 면 "이 낱말은 명사예요"까지만 말할 수 있다.

    규칙과 사전이 어긋나면(교집합이 빔) 규칙을 버린다. 규칙이 옳고 사전이
    모르는 경우(`text` 의 동사 용법)와 규칙이 틀린 경우(조동사 뒤 부사)를
    여기서 구별할 방법이 없어서, **덜 주장하는 쪽**으로 넘어진다.
    """
    if not word_pos:
        return None
    found = slot_pos(sentence)
    if found and (found & word_pos):
        return frozenset(found & word_pos), "slot"
    return frozenset(word_pos), "word"


def order_pos(pos: frozenset[str] | set[str]) -> tuple[str, ...]:
    """품사를 늘 같은 순서로 준다(명사·동사·형용사·부사). 화면과 시험이 흔들리지 않게."""
    return tuple(p for p in ALL_POS if p in pos)


# ---------------------------------------------------------------- 구·절의 머리
#
# 학습자가 한 낱말만 낸다는 보장이 없다. 사용자가 명시적으로 원한 것이기도 하다 —
# "펜 좀 빌려도 될까요?" 를 알면 `pen`·`a pen`·`your pen` 이 다 답이다. 그래서
# 구·절을 오답으로 처리하지 않고 **머리 낱말 하나로 줄여서** 같은 사다리에 태운다.

# 머리 앞에 붙는 것들. 여기까지가 "a pen -> pen" 을 만든다.
_LEADING = _DETERMINERS | frozenset("to of at in on for with from".split())

# 구를 여기서 끊는다. "a cup of coffee" 의 머리는 coffee 가 아니라 cup 이다.
_LINKERS = _CLOSERS | frozenset("than as whom whose".split())

# 동사에 붙는 불변화사. "pick up" 의 머리는 up 이 아니라 pick 이다.
_PARTICLES = frozenset("up out off down over on in away back through around".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")


def head_word(said: str) -> tuple[str, bool]:
    """학습자의 답에서 판정할 낱말 하나를 고른다. (머리 낱말, 구였는가).

    영어 명사구가 **머리-끝**(a red pen -> pen)이라는 성질에만 기댄다.
    그래서 다음은 틀린다 — 주석에 남겨 두고 화면에서는 "…에서 '…' 을 봤어요"로
    무엇을 판정했는지 반드시 드러낸다.

    - 절: `what I want` 의 머리를 `want`(동사)로 본다. 실제로는 명사절이다.
    - 목록에 없는 불변화사: `look after` 는 `after` 가 `_LINKERS` 에 없어 걸러지지만
      `figure out` 같은 것은 `_PARTICLES` 에 있어야 맞는다. 목록 밖은 뒤 낱말을 집는다.
    - 고유명사 여러 낱말: `New York` 의 머리를 `york` 로 본다.

    왕초보 연습장에서 실제로 들어오는 구는 거의 명사구(`a pen`, `some water`,
    `my friend`)와 짧은 부정사(`to go`)라, 이 한계를 감수하고 규칙을 단순하게 둔다.
    """
    tokens = _WORD.findall(said.lower())
    if not tokens:
        return "", False
    if len(tokens) == 1:
        return tokens[0], False

    core = list(tokens)
    while len(core) > 1 and core[0] in _LEADING:
        core = core[1:]
    for i, token in enumerate(core):
        if i > 0 and token in _LINKERS:
            core = core[:i]
            break
    head = core[-1]
    if len(core) >= 2 and head in _PARTICLES:
        head = core[-2]
    return head, True
