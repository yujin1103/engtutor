"""교정이 진짜 교정인지 결정론적으로 검사한다. LLM 을 부르지 않는다.

왜 필요한가
-----------
교정 채널은 이 앱에서 가장 신뢰가 필요한 자리다. 대화가 어색한 건 참을 수 있지만
**맞게 말한 학습자에게 틀렸다고 하면** 그 자리에서 그만둔다.

실측한 결함이 하나 있다. `How much is it?`(완벽히 맞는 문장)을 넣으면 15회 중
15회 교정이 나왔고, 그중 11회는 `Can I get a coffee, please?` 였다 — 가격을
물었는데 주문하라고 바꾼다. **교정이 아니라 의도 교체다.**

이건 규칙으로 잡힌다. 두 문장의 토큰 겹침을 보면 '고친 것'과 '다른 문장'이 갈린다.

  'how much is it' vs 'how much does it cost'      겹침 0.75  교정
  'how much is it' vs 'can i get a coffee please'  겹침 0.00  교체

여기 있는 검사는 전부 규칙이다. 확률적 판단은 하나도 없다.

무엇을 검사하지 않는가
----------------------
`better` 가 문법적으로 맞는지는 여기서 판정하지 않는다. 그건 파서가 필요한 일이고,
파서를 붙이면 그 파서의 오탐이 새 문제가 된다. 대신 **교정이 새로 집어넣은 단어가
실재하는지**만 본다 — 없는 단어를 지어내는 건 실제로 일어났고(docs/hallucinations.md)
사전 조회로 확실히 잡힌다.

학습자가 이미 쓴 단어는 검사하지 않는다. WordNet 에 `americano`·`app`·`barista`
같은 앱 핵심 어휘가 없어서, 되받은 단어까지 검사하면 카페 시나리오가 통째로
오탐이 된다. 모델이 **새로 넣은** 단어만 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..content import lexicon
from .schemas import Correction, TurnResponse

# 곱슬따옴표(U+2019)를 빼먹으면 doesn’t 가 doesn + t 로 쪼개지고, doesn 이
# 사전에 없어서 정상 교정이 환각으로 잡힌다. 실제로 오탐이 나왔다.
_TOKEN = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")

# 원문이 이보다 짧으면 겹침 비율이 요동친다 — 한 단어짜리 답("Large")에서
# 0.5 와 1.0 사이에 중간이 없다. 짧은 발화는 겹침 검사를 건너뛴다.
MIN_TOKENS_FOR_OVERLAP = 3

# 원문 토큰의 이 비율보다 적게 남으면 고친 게 아니라 다른 문장을 쓴 것이다.
# 0.5 로 잡으면 정당한 교정이 걸린다 —
#   'I want ice americano' -> 'Can I get an iced americano, please?' 는 0.5 다.
MIN_OVERLAP = 0.34


# 공손 표지. 왕초보 회화에서 이걸 **빼는** 방향의 교정은 개선이 아니다.
# 실측: 'Can I get a hot latte, please?' -> 'Can I get a hot latte?' 가 나왔다.
# 모델이 교정 칸을 비워 두지 못하고 아무 차이나 만들어 채운 흔적이다.
POLITENESS: frozenset[str] = frozenset(
    {"please", "thanks", "thank", "could", "would", "may", "sorry", "excuse"}
)


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def overlap(original: str, better: str) -> float:
    """원문 토큰 중 교정본에 남은 비율. 원문이 비면 1.0(검사 불가는 통과)."""
    src = set(tokens(original))
    if not src:
        return 1.0
    return len(src & set(tokens(better))) / len(src)


def check_correction(correction: Correction, learner_said: str) -> list[Issue]:
    """교정 하나를 학습자의 실제 발화와 대조한다."""
    out: list[Issue] = []
    original, better = correction.original.strip(), correction.better.strip()

    said = set(tokens(learner_said))
    original_tokens = tokens(original)

    # 1. 학습자가 하지 않은 말을 교정하는 경우. `original` 은 발화를 그대로
    #    인용해야 하는 필드다. 여기가 어긋나면 교정 전체가 허구 위에 서 있다.
    if original_tokens and said:
        kept = sum(1 for t in original_tokens if t in said) / len(original_tokens)
        if kept < 0.5:
            out.append(
                Issue("original_not_said", f"학습자가 하지 않은 말이에요: {original!r}")
            )

    # 2. 고친 게 없는 교정. 학습자에게 아무것도 알려주지 못한다.
    if tokens(original) == tokens(better):
        out.append(Issue("better_same_as_original", f"원문과 같아요: {better!r}"))

    # 3. 의도 교체. 고친 게 아니라 다른 문장을 쓴 것이다.
    elif len(original_tokens) >= MIN_TOKENS_FOR_OVERLAP:
        ratio = overlap(original, better)
        if ratio < MIN_OVERLAP:
            out.append(
                Issue(
                    "better_replaces_intent",
                    f"원문이 {ratio:.0%}만 남았어요 — {original!r} -> {better!r}",
                )
            )

    # 4. 공손 표지를 떼는 교정. 다른 공손 표지로 바꾸는 건(please -> could) 정상이라
    #    교정본에 공손 표지가 하나도 안 남았을 때만 지적한다.
    before = POLITENESS & set(original_tokens)
    after = POLITENESS & set(tokens(better))
    if before and not after:
        out.append(
            Issue("better_drops_politeness", f"공손 표현이 사라졌어요: {'·'.join(sorted(before))}")
        )

    # 5. 교정이 새로 집어넣은 단어가 실재하는가. 사전이 없으면 건너뛴다.
    introduced = [t for t in tokens(better) if t not in said]
    for token in introduced:
        if lexicon.known(token) is False:
            out.append(
                Issue("better_invents_word", f"사전에 없는 단어를 넣었어요: {token!r}")
            )

    return out


def check_turn(turn: TurnResponse, learner_said: str) -> list[tuple[int, Issue]]:
    """턴 하나의 모든 교정을 검사한다. (교정 index, Issue) 목록."""
    out: list[tuple[int, Issue]] = []
    for i, correction in enumerate(turn.corrections):
        for issue in check_correction(correction, learner_said):
            out.append((i, issue))
    return out


def sound_corrections(turn: TurnResponse, learner_said: str) -> list[Correction]:
    """검사를 통과한 교정만 남긴다.

    떨어뜨리는 쪽이 항상 안전하다 — 교정을 하나 덜 보여주는 비용은 배울 기회를
    한 번 놓치는 것이고, 잘못된 교정을 보여주는 비용은 틀린 것을 가르치는 것이다.
    """
    bad = {i for i, _ in check_turn(turn, learner_said)}
    return [c for i, c in enumerate(turn.corrections) if i not in bad]
