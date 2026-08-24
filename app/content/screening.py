"""생성된 단어 항목을 결정론적으로 선별한다. LLM 을 부르지 않는다.

왜 필요한가
-----------
NGSL 2,801개를 사람이 한 항목씩 보면 15초씩만 잡아도 12시간이다. 그렇다고
자동 승인하면 "검수는 사람"이라는 원칙이 무너진다 — 검수의 존재 이유가
LLM 이 만든 걸 LLM 이 통과시키지 않게 하는 것이기 때문이다.

그래서 여기서는 **승인하지 않는다.** 의심스러운 순서를 매길 뿐이다.
사람이 나쁜 것부터 보고, 멀쩡한 다수는 빠르게 넘긴다.

여기 있는 검사는 전부 규칙이다. 확률적 판단은 하나도 없다 — 배치에서
arrange 가 arrive 로 바뀐 사고를 잡은 것도 규칙(표제어 대조)이었다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Protocol

from ..tutor.korean import has_hangul

Severity = Literal["high", "medium", "low"]

# 점수는 정렬용 가중치다. high 하나가 low 여러 개보다 항상 앞서야 한다.
_WEIGHT: dict[Severity, int] = {"high": 100, "medium": 10, "low": 1}

VALID_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

# A1~A2 예문은 짧아야 한다. 넘으면 학습자가 통째로 못 읽는다.
MAX_EXAMPLE_WORDS = 12
MIN_USAGE_NOTE_CHARS = 15
MAX_USAGE_NOTE_CHARS = 300
MAX_CONFUSED_WITH = 4

_WORD_TOKEN = re.compile(r"[a-z]+")

# 영어 단어(구 포함). 아포스트로피와 하이픈은 허용하고, 괄호·기호는 막는다.
_PLAIN_WORD = re.compile(r"[a-z]+(?:['\-][a-z]+)*(?: [a-z]+(?:['\-][a-z]+)*){0,2}")

# 불규칙 변화. 이게 없으면 go/went, buy/bought 가 전부 오탐으로 뜬다.
# 전부 담을 필요는 없다 — 오탐이 줄어드는 만큼만 담으면 된다.
_IRREGULAR: dict[str, tuple[str, ...]] = {
    "be": ("am", "is", "are", "was", "were", "been", "being"),
    "become": ("became", "becoming"),
    "begin": ("began", "begun"),
    "bring": ("brought",),
    "build": ("built",),
    "buy": ("bought",),
    "catch": ("caught",),
    "choose": ("chose", "chosen"),
    "come": ("came",),
    "do": ("does", "did", "done"),
    "drink": ("drank", "drunk"),
    "drive": ("drove", "driven"),
    "eat": ("ate", "eaten"),
    "fall": ("fell", "fallen"),
    "feel": ("felt",),
    "find": ("found",),
    "fly": ("flew", "flown"),
    "forget": ("forgot", "forgotten"),
    "get": ("got", "gotten"),
    "give": ("gave", "given"),
    "go": ("went", "gone", "goes"),
    "grow": ("grew", "grown"),
    "have": ("has", "had", "having"),
    "hear": ("heard",),
    "hold": ("held",),
    "keep": ("kept",),
    "know": ("knew", "known"),
    "leave": ("left",),
    "lend": ("lent",),
    "lose": ("lost",),
    "make": ("made",),
    "mean": ("meant",),
    "meet": ("met",),
    "pay": ("paid",),
    "put": ("puts",),
    "read": ("reads",),
    "run": ("ran",),
    "say": ("said", "says"),
    "see": ("saw", "seen"),
    "sell": ("sold",),
    "send": ("sent",),
    "sit": ("sat",),
    "sleep": ("slept",),
    "speak": ("spoke", "spoken"),
    "spend": ("spent",),
    "stand": ("stood",),
    "take": ("took", "taken"),
    "teach": ("taught",),
    "tell": ("told",),
    "think": ("thought",),
    "understand": ("understood",),
    "wear": ("wore", "worn"),
    "win": ("won",),
    "write": ("wrote", "written"),
    "arise": ("arose", "arisen"),
    "blow": ("blew", "blown"),
    "break": ("broke", "broken"),
    "draw": ("drew", "drawn"),
    "feed": ("fed",),
    "fight": ("fought",),
    "hang": ("hung",),
    "hide": ("hid", "hidden"),
    "hit": ("hits",),
    "hurt": ("hurts",),
    "lead": ("led",),
    "lie": ("lay", "lain", "lying"),
    "overcome": ("overcame",),
    "ride": ("rode", "ridden"),
    "ring": ("rang", "rung"),
    "rise": ("rose", "risen"),
    "seek": ("sought",),
    "shake": ("shook", "shaken"),
    "shoot": ("shot",),
    "show": ("showed", "shown"),
    "shut": ("shuts",),
    "sing": ("sang", "sung"),
    "sink": ("sank", "sunk"),
    "steal": ("stole", "stolen"),
    "stick": ("stuck",),
    "strike": ("struck",),
    "swim": ("swam", "swum"),
    "tear": ("tore", "torn"),
    "throw": ("threw", "thrown"),
    "wake": ("woke", "woken"),
    # 명사 불규칙 복수
    "child": ("children",),
    "man": ("men",),
    "woman": ("women",),
    "person": ("people",),
    "foot": ("feet",),
    "tooth": ("teeth",),
    "life": ("lives",),
}


class WordLike(Protocol):
    """WordRow 와 WordEntry 를 둘 다 받기 위한 최소 계약."""

    word: str
    level: str
    meaning_ko: str
    example: str
    usage_note: str
    confused_with: list[str]


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str


def _stems(word: str) -> set[str]:
    """표제어가 문장 안에서 취할 법한 앞부분들."""
    w = word.strip().lower()
    out = {w}
    if len(w) > 3:
        if w.endswith("e"):
            out.add(w[:-1])          # arrange -> arrang(ing)
        if w.endswith("y"):
            out.add(w[:-1])          # study -> stud(ied)
        if len(w) > 4:
            out.add(w[:-1])          # 자음 중복(run -> runn) 등 느슨한 여지
    return out


def mentions(text: str, word: str) -> bool:
    """`text` 가 표제어를 (굴절형 포함) 담고 있는가.

    완벽할 필요는 없다. 이건 거부 판정이 아니라 **사람에게 보여줄 순서**를
    정하는 신호다. 놓치면 검수가 늦어질 뿐 잘못된 항목이 통과하지는 않는다.
    """
    w = word.strip().lower()
    tokens = set(_WORD_TOKEN.findall(text.lower()))
    if w in tokens or any(t in _IRREGULAR.get(w, ()) for t in tokens):
        return True
    stems = _stems(w)
    return any(t.startswith(s) and len(t) - len(s) <= 3 for t in tokens for s in stems)


def screen(row: WordLike) -> list[Finding]:
    """한 항목만 보고 판단할 수 있는 검사."""
    out: list[Finding] = []
    word = row.word.strip().lower()

    in_example = mentions(row.example, word)
    in_note = mentions(row.usage_note, word)
    if not in_example and not in_note:
        # arrange -> arrive 처럼 통째로 다른 단어를 설명한 경우가 여기 걸린다.
        out.append(Finding("headword_absent", "high", "예문·설명 어디에도 표제어가 없어요"))
    elif not in_example:
        out.append(Finding("example_missing_headword", "medium", "예문에 표제어가 안 보여요"))

    if not has_hangul(row.meaning_ko):
        out.append(Finding("meaning_not_korean", "high", "뜻이 한국어가 아니에요"))
    if not has_hangul(row.usage_note):
        out.append(Finding("usage_not_korean", "high", "설명이 한국어가 아니에요"))
    if has_hangul(row.example):
        out.append(Finding("example_has_hangul", "high", "예문에 한글이 섞였어요"))

    confused = [c.strip().lower() for c in (row.confused_with or [])]
    if word in confused:
        out.append(Finding("self_reference", "high", "자기 자신을 헷갈리는 단어로 넣었어요"))
    if len(confused) > MAX_CONFUSED_WITH:
        out.append(Finding("confused_with_too_many", "low", f"헷갈리는 단어가 {len(confused)}개예요"))
    # he'll, can't, driver's license 는 정당한 영어다. 걸러야 하는 건
    # "chip (as in 'a piece')" 처럼 단어 대신 해설이 들어간 경우와 '+' 같은 기호다.
    bad = [c for c in confused if c and not _PLAIN_WORD.fullmatch(c)]
    if bad:
        out.append(
            Finding("confused_with_malformed", "low", f"단어가 아닌 값이 있어요: {bad[0]!r}")
        )

    if row.level not in VALID_LEVELS:
        out.append(Finding("bad_level", "medium", f"레벨 값이 이상해요: {row.level!r}"))

    example_words = len(row.example.split())
    if example_words > MAX_EXAMPLE_WORDS:
        out.append(Finding("example_too_long", "low", f"예문이 {example_words}단어예요"))
    if len(row.usage_note) < MIN_USAGE_NOTE_CHARS:
        out.append(Finding("usage_note_too_short", "low", "설명이 너무 짧아요"))
    if len(row.usage_note) > MAX_USAGE_NOTE_CHARS:
        out.append(Finding("usage_note_too_long", "low", f"설명이 {len(row.usage_note)}자예요"))

    return out


def screen_all(rows: list[WordLike]) -> dict[str, list[Finding]]:
    """항목 간 비교가 필요한 검사까지 포함한다. 표제어 -> 발견 목록.

    복제 검사가 여기 있는 이유: few-shot 예시나 앞 항목을 그대로 베끼는 실패는
    한 항목만 봐서는 절대 안 보인다. borrow 의 설명이 lend 에 그대로 실려도
    각각은 완벽해 보인다.
    """
    findings = {row.word: screen(row) for row in rows}

    # 설명이 겹치는 건 베낀 것이다 — 단어마다 경고할 지점이 다르므로 겹칠 이유가 없다.
    # 예문이 겹치는 건 대부분 정당하다: "I am a student." 는 be·i·student 세 표제어
    # 모두에 맞는 예문이다. 한 번 볼 가치는 있어도 결함은 아니라서 심각도를 낮춘다.
    for field, code, label, severity in (
        ("usage_note", "duplicate_usage_note", "설명", "high"),
        ("example", "duplicate_example", "예문", "low"),
    ):
        counts = Counter(getattr(r, field).strip() for r in rows)
        for row in rows:
            value = getattr(row, field).strip()
            if value and counts[value] > 1:
                findings[row.word].append(
                    Finding(code, severity, f"{label}이 다른 {counts[value] - 1}개 항목과 똑같아요")
                )

    return findings


def risk_score(findings: list[Finding]) -> int:
    """정렬용 점수. 클수록 먼저 봐야 한다."""
    return sum(_WEIGHT[f.severity] for f in findings)


def worst_severity(findings: list[Finding]) -> Severity | None:
    for level in ("high", "medium", "low"):
        if any(f.severity == level for f in findings):
            return level  # type: ignore[return-value]
    return None
