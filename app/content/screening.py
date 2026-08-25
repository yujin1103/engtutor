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
from . import lexicon

Severity = Literal["high", "medium", "low"]

# 점수는 정렬용 가중치다. high 하나가 low 여러 개보다 항상 앞서야 한다.
_WEIGHT: dict[Severity, int] = {"high": 100, "medium": 10, "low": 1}

VALID_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

# A1~A2 예문은 짧아야 한다. 넘으면 학습자가 통째로 못 읽는다.
MAX_EXAMPLE_WORDS = 12
MIN_USAGE_NOTE_CHARS = 15
MAX_USAGE_NOTE_CHARS = 300
MAX_CONFUSED_WITH = 4
# 문형은 형태 표기다. 이보다 길면 설명이 섞여 들어온 것이다(칸 자체는 120자까지 받는다).
MAX_PATTERN_DISPLAY_CHARS = 60

_WORD_TOKEN = re.compile(r"[a-z]+")

# 문형에서 괄호 안은 선택 사항이다 — `borrow + 목적어 (+ from + 사람)` 의 from 이
# 예문에 없다고 지적하면 안 된다.
_PATTERN_OPTIONAL = re.compile(r"\([^)]*\)")

# 형태 자리를 표시하는 기호. 실제 단어가 아니라서 예문에서 찾으면 안 된다.
_PLACEHOLDER = {
    "v", "n", "adj", "adv", "o", "s", "c", "sb", "sth", "one",
    "someone", "something", "somebody", "somewhere", "someplace",
    "anyone", "anything", "anybody", "everyone", "everything", "everybody",
    "ing", "ed", "pp", "a", "an", "the",
    # 문법 용어. 형태를 설명하는 말이지 예문에서 찾을 단어가 아니다 —
    # `to an extent + that-clause` 의 clause 를 예문에서 찾으면 영원히 못 찾는다.
    "clause", "infinitive", "gerund", "phrase", "noun", "verb",
    "adjective", "adverb", "object", "subject", "form",
}

# 축약형을 풀어 쓴 형태. `be against` 의 be 는 예문에서 `I'm` 으로 나타난다.
# 원본 토큰은 그대로 두고 여기서 나온 토큰을 **더한다** — can't -> ca 처럼
# 원본이 있어야만 찾을 수 있는 경우가 있어서 치환이 아니라 합집합이어야 한다.
_CONTRACTIONS = {
    "n't": " not", "'m": " am", "'re": " are", "'s": " is",
    "'ve": " have", "'ll": " will", "'d": " would",
}
_CONTRACTION = re.compile("|".join(re.escape(k) for k in _CONTRACTIONS))

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
    # 문형. pattern 컬럼이 생기기 전에 저장된 항목은 None 이다.
    pattern: str | None
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
    lowered = text.lower()
    # 축약형을 푼 토큰을 더한다. 빼지는 않는다 — can't 는 원본에서만 can 이 보이고,
    # I'm 은 푼 쪽에서만 am 이 보인다. 둘 다 필요하다.
    tokens = set(_WORD_TOKEN.findall(lowered))
    tokens |= set(_WORD_TOKEN.findall(_CONTRACTION.sub(lambda m: _CONTRACTIONS[m.group()], lowered)))
    if w in tokens or any(t in _IRREGULAR.get(w, ()) for t in tokens):
        return True
    stems = _stems(w)
    return any(t.startswith(s) and len(t) - len(s) <= 3 for t in tokens for s in stems)


def pattern_forms(pattern: str, word: str) -> list[tuple[str, ...]]:
    """문형을 **대안 형태들**로 쪼개고, 각 형태가 예문에 요구하는 영어 조각을 돌려준다.

    한 형태 안의 조각은 전부 있어야 하고, 형태끼리는 하나만 만족하면 된다.
    `feel + 형용사 / feel like + 명사` 는 둘 중 아무 쪽으로 써도 맞는 예문이다.

    괄호 안(선택 사항), 표제어 자신, 자리 표시어(V, -ing, somewhere)는 뺀다.
    남는 건 대개 전치사·불변화사인데, 그게 정확히 왕초보가 빠뜨리는 것이다 —
    `listen to` 의 to, `look forward to` 의 forward.

    빈 튜플은 '요구하는 게 없는 형태'라 항상 만족한다. 처음에는 슬래시를 한 조각
    안의 대안으로만 처리했는데, 실제 데이터에서 `hope + that + 문장 / hope + to + 동사`
    같은 **형태 나열**이 훨씬 흔했고 그걸 전부 요구하다 오탐 10건을 냈다.
    """
    core = _PATTERN_OPTIONAL.sub(" ", pattern)
    forms: list[tuple[str, ...]] = []
    for segment in re.split(r"[,/|]", core):
        tokens = _WORD_TOKEN.findall(segment.lower())
        if not tokens:
            # 영어가 하나도 없는 조각은 형태가 아니라 자리 표시어의 일부다 —
            # `area + of + 장소/주제` 의 '주제'. 이걸 형태로 세면 '요구하는 게 없는
            # 형태'가 하나 생겨서 검사가 통째로 무력해진다(실제로 area 를 놓쳤다).
            continue
        required = []
        for token in tokens:
            if token in _PLACEHOLDER or len(token) < 2:
                continue
            if mentions(token, word):  # 표제어(굴절형 포함)는 따로 검사한다
                continue
            required.append(token)
        forms.append(tuple(dict.fromkeys(required)))
    return forms


# ---------------------------------------------------------------------------
# 품사 단정 검사
#
# 생성된 설명이 가장 자주 저지르는 거짓이 품사 단정이다. NGSL 2,801개 중
# "…로만 쓰인다" 형태의 주장이 123건 있었고, 표본을 읽어 보니 상당수가 틀렸다 —
# "'name'은 명사로만 쓰이고" (name 은 동사다), "'abroad'는 명사로만" (부사다).
#
# 이건 없는 단어보다 잡기 어렵다. 단어는 전부 실재하고 **주장만 거짓**이라서
# 존재 검사로는 하나도 안 걸린다. 사전의 품사 태그와 대조해야 드러난다.
#
# 주어가 한국어인 주장은 판정하지 않는다 — "한국어 '이점'은 명사로만 쓰이지만"
# 은 한국어에 대한 말이라 영어 사전으로 반증할 수 없다. 실제로 20건이 그랬다.
_QUOTE = r"['\"‘’“”]?"
_POS_ONLY_CLAIM = re.compile(
    _QUOTE + r"([A-Za-z][A-Za-z\-]{1,24})" + _QUOTE + r"\s*(?:은|는)\s*(명사|동사|형용사|부사)로만"
)

# "'X'는 동사로는 쓰지 않아요" 처럼 특정 품사를 부정하는 주장.
_POS_DENIAL_CLAIM = re.compile(
    _QUOTE + r"([A-Za-z][A-Za-z\-]{1,24})" + _QUOTE
    + r"\s*(?:은|는)[^.!?\n]{0,40}?(명사|동사|형용사|부사)로는[^.!?\n]{0,20}?(?:않|안 )"
)

# 가산성 주장. WordNet 에는 가산성 정보가 없어서 **판정할 수 없다** — 사람에게 넘긴다.
# advice 를 불가산이라 한 건 맞지만, adviser 를 불가산이라 한 항목도 실제로 있었다.
_COUNTABILITY_CLAIM = re.compile(r"불가산|가산명사|셀 수 (?:없|있)")


def _pos_claim_findings(row: WordLike) -> list[Finding]:
    """설명의 품사 단정을 사전과 대조한다. 사전이 없으면 아무것도 하지 않는다."""
    out: list[Finding] = []
    note = row.usage_note

    # 품사 대조만 사전이 필요하다. 가산성 호출은 사전 없이도 해야 한다 —
    # 사전이 없다고 "사람이 봐 주세요"까지 사라지면 검사가 조용히 약해진다.
    for match in _POS_ONLY_CLAIM.finditer(note) if lexicon.available() else ():
        target, claimed_ko = match.group(1), match.group(2)
        actual = lexicon.parts_of_speech(target)
        if actual is None:
            continue  # 사전에 없는 단어는 "모른다"지 "틀렸다"가 아니다
        claimed = lexicon.KO_POS[claimed_ko]
        actual_ko = "·".join(lexicon.POS_KO[p] for p in lexicon.ALL_POS if p in actual)
        if claimed not in actual:
            out.append(
                Finding(
                    "pos_claim_wrong",
                    "medium",
                    f"'{target}' — {claimed_ko}라고 했는데 사전에는 {actual_ko} 뜻만 있어요",
                )
            )
        elif actual - {claimed}:
            # 교육적 단순화일 수 있어서 심각도를 낮춘다. 다만 'name 은 명사로만'
            # 처럼 학습자가 그대로 외우면 틀리는 것도 여기 들어온다.
            others = "·".join(lexicon.POS_KO[p] for p in lexicon.ALL_POS if p in actual - {claimed})
            out.append(
                Finding(
                    "pos_claim_overreach",
                    "low",
                    f"'{target}' — {claimed_ko}로만이라고 했는데 {others} 뜻도 있어요",
                )
            )

    for match in _POS_DENIAL_CLAIM.finditer(note) if lexicon.available() else ():
        target, denied_ko = match.group(1), match.group(2)
        actual = lexicon.parts_of_speech(target)
        if actual is None:
            continue
        if lexicon.KO_POS[denied_ko] in actual:
            out.append(
                Finding(
                    "pos_claim_wrong",
                    "medium",
                    f"'{target}' — {denied_ko}로 안 쓴다고 했는데 사전에 {denied_ko} 뜻이 있어요",
                )
            )

    if _COUNTABILITY_CLAIM.search(note):
        # 판정이 아니라 호출이다. 사전에 가산성 정보가 없으니 사람이 봐야 한다.
        out.append(
            Finding(
                "countability_claim_unchecked",
                "low",
                "가산성을 단정했어요 — 사전으로 확인이 안 되니 사람이 봐 주세요",
            )
        )

    return out


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

    # 문형 검사. 없는 건 여기서 지적하지 않는다 — pattern 이전에 생성된 항목이
    # 전부 걸려 큐가 무의미해진다. 그건 사람이 한 줄씩 고칠 일이 아니라 배치가
    # 채울 일이라서, screen_words.py 가 개수만 따로 알려 준다.
    pattern = (getattr(row, "pattern", None) or "").strip()
    if pattern:
        if len(pattern) > MAX_PATTERN_DISPLAY_CHARS:
            out.append(
                Finding("pattern_too_long", "low", f"문형이 {len(pattern)}자예요 — 설명이 섞였을 수 있어요")
            )
        if not _WORD_TOKEN.search(pattern.lower()):
            # 형태를 적는 칸에 영어가 하나도 없으면 뜻풀이를 옮겨 적은 것이다.
            out.append(Finding("pattern_without_english", "low", "문형에 영어가 없어요"))
        forms = pattern_forms(pattern, word)
        unmet = [
            tuple(t for t in form if not mentions(row.example, t)) for form in forms
        ]
        # 형태 중 하나라도 예문이 보여주면 통과다. 전부 못 보여줄 때만 지적하고,
        # 가장 가까운(빠진 게 적은) 형태를 알려 준다 — 사람이 고칠 지점이 거기다.
        if forms and all(unmet):
            closest = min(unmet, key=len)
            out.append(
                Finding(
                    "example_ignores_pattern",
                    "medium",
                    f"예문이 문형을 안 보여줘요 — {'·'.join(closest)} 가 예문에 없어요",
                )
            )

    out.extend(_pos_claim_findings(row))

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
