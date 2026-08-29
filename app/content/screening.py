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

from ..tutor.korean import has_hangul, reject_foreign_script
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
# 한국어 두 문장이 사실상 같은 말인지 볼 때 걷어내는 것. 띄어쓰기와 문장부호가
# 달라도 같은 문장은 같다고 봐야 한다.
_TRIVIA_KO = re.compile(r"[\s.,!?~…·'\"]+")

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

    # 하이픈·아포스트로피가 든 표제어는 토큰 분해로는 영원히 못 찾는다 — `dine-in` 이
    # dine 과 in 으로, `o'clock` 이 o 와 clock 으로 쪼개진다. 실제로 예문에 그대로
    # 들어 있는데 "표제어를 안 쓴다"고 거부돼 배치에서 14개가 떨어졌다.
    # 붙여 쓰거나 띄어 쓴 형태도 같은 말로 본다 (`take-out`/`take out`/`takeout`).
    if "-" in w or "'" in w:
        parts = [re.escape(p) for p in re.split(r"[-']", w) if p]
        if parts and re.search(r"\b" + r"[\s'-]?".join(parts) + r"\b", lowered):
            return True

    # 축약형을 푼 토큰을 더한다. 빼지는 않는다 — can't 는 원본에서만 can 이 보이고,
    # I'm 은 푼 쪽에서만 am 이 보인다. 둘 다 필요하다.
    tokens = set(_WORD_TOKEN.findall(lowered))
    tokens |= set(_WORD_TOKEN.findall(_CONTRACTION.sub(lambda m: _CONTRACTIONS[m.group()], lowered)))
    if w in tokens or any(t in _IRREGULAR.get(w, ()) for t in tokens):
        return True
    stems = _stems(w)
    if any(t.startswith(s) and len(t) - len(s) <= 3 for t in tokens for s in stems):
        return True
    # 손으로 적은 불규칙 표는 언제나 모자란다 — `freeze` 가 빠져 있어서
    # "The water froze in the fridge." 가 표제어를 안 쓴 예문으로 **거부됐다**.
    # 앞부분 대조로는 froze/freeze 처럼 어간이 바뀌는 것을 잡을 수 없다.
    # 사전이 아는 것은 사전에게 묻는다. 사전이 없으면 여기까지가 답이다.
    return any(lexicon.same_lemma(t, w) for t in tokens)


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

    # 표제어가 실재하는 말인가. NGSL 2,801개 전수 조사에서 `restaurate`·`habor`·
    # `oranje` 13건이 나왔다(docs/hallucinations.md). 그때는 던져 쓰는 스크립트로
    # 잡았는데, 그러면 다음 배치에서 또 들어온다. 사전이 없으면(None) 판정하지 않는다.
    if lexicon.known(word) is False:
        out.append(
            Finding(
                "headword_not_in_dictionary",
                "high",
                f"'{word}' — 사전에 없는 말이에요. 지어낸 단어이거나 철자가 틀렸을 수 있어요"
                " (실재하는데 사전이 모르는 말이면 scripts/verify_words.py 로 확인해 등록하세요)",
            )
        )

    in_example = mentions(row.example, word)
    in_note = mentions(row.usage_note, word)
    if not in_example and not in_note:
        # arrange -> arrive 처럼 통째로 다른 단어를 설명한 경우가 여기 걸린다.
        out.append(Finding("headword_absent", "high", "예문·설명 어디에도 표제어가 없어요"))
    elif not in_example:
        out.append(Finding("example_missing_headword", "medium", "예문에 표제어가 안 보여요"))

    if not has_hangul(row.meaning_ko):
        out.append(Finding("meaning_not_korean", "high", "뜻이 한국어가 아니에요"))
    else:
        # 한글이 **하나라도** 있으면 위 검사는 통과다. 그래서 한글과 한자가 섞인 뜻이
        # 그대로 나갔다 — `bagel 백일(백面包)`, `sigh 叹气하다`, `spicy 매운, 辛い`.
        # 출제 가능 2,950개 중 14개였고, 다섯은 기본 장면 팩 안에 있었다.
        #
        # 검사기는 이미 있었다(`reject_foreign_script`). 후보 목록에만 걸려 있었을
        # 뿐이다. 여기 걸어 두면 두 가지가 같이 된다: 검수 큐가 이것들을 맨 앞으로
        # 올리고, 미검수 항목은 `cloze.is_safe_to_serve` 를 통과하지 못한다.
        # (승인된 항목까지 막는 것은 이 함수의 일이 아니라 출제 문의 일이다 —
        #  사람이 승인해도 왕초보가 `叹气` 를 읽게 되지는 않으므로 그쪽에도 건다.)
        try:
            reject_foreign_script(row.meaning_ko, "meaning_ko")
        except ValueError:
            out.append(
                Finding(
                    "meaning_foreign_script",
                    "high",
                    f"뜻에 학습자가 못 읽는 글자가 섞였어요: {row.meaning_ko[:40]!r}",
                )
            )

    repeated = repeated_meaning(row.meaning_ko)
    if repeated:
        out.append(
            Finding(
                "meaning_repeats",
                "medium",
                f"뜻에 '{repeated}' 가 두 번 들어 있어요 — 한 자리가 비어 나온 거예요",
            )
        )

    if not has_hangul(row.usage_note):
        out.append(Finding("usage_not_korean", "high", "설명이 한국어가 아니에요"))
    else:
        # 뜻 칸과 같은 검사를 설명 칸에도 건다. 안 걸어 둔 동안 31행이 새어 나갔고,
        # 그건 뜻 칸(21행)보다 많다 — 설명이 제일 긴 한국어 칸이라 미끄러질 자리가
        # 그만큼 많기 때문이다. 실제로 나온 것: `ward` 의 '病房', `earn` 의
        # 'работать', `seatbelt` 의 '타ク시'.
        #
        # 이 새는 것이 환각이 아니라 **언어 전환**이라는 데 주의할 것. 섞여 나온
        # 값들은 뜻이 맞다 — 捩伤(삐다)·形容词(형용사)·油腻的(기름진). 만드는 모델이
        # 중국 모델(qwen3)이라 한국어 토큰을 확신 못 할 때 뜻이 같은 중국어 토큰으로
        # 미끄러지는 것이고, 그래서 프롬프트로 더 세게 말해서 막을 성질이 아니다.
        # 같은 배치에서 example_ko 만 0건인 것이 근거다 — 그 칸에만 검사가 걸려 있다.
        try:
            reject_foreign_script(row.usage_note, "usage_note")
        except ValueError:
            out.append(
                Finding(
                    "usage_foreign_script",
                    "high",
                    f"설명에 학습자가 못 읽는 글자가 섞였어요: {row.usage_note[:40]!r}",
                )
            )
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
        # 문형도 화면에 그대로 나가는 칸이다. `itchy` 의 문형이 '形容词, + body part',
        # `recently` 가 'recently + 동사过去形' 였다. 여기 검사가 없어서 이 넷은
        # 검수 큐에도 안 올라오고 출제 문도 통과했다.
        try:
            reject_foreign_script(pattern, "pattern")
        except ValueError:
            out.append(
                Finding(
                    "pattern_foreign_script",
                    "high",
                    f"문형에 학습자가 못 읽는 글자가 섞였어요: {pattern[:40]!r}",
                )
            )
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

    if _korean_only_note(row.usage_note):
        out.append(
            Finding(
                "note_compares_korean_only",
                "low",
                "설명이 한국어 낱말끼리만 견주고 영어를 말하지 않아요 — 사람이 봐 주세요",
            )
        )

    echo = echoed_example_fragment(row.example, row.usage_note)
    if echo:
        out.append(
            Finding(
                "note_echoes_example",
                "low",
                f"설명이 예문을 그대로 옮겨 적었어요 — '{echo}' — 사람이 봐 주세요",
            )
        )

    return out


# "한국어 X 는 …" 으로 시작하면서 영어가 거의 없는 설명. **판정이 아니라 물음이다.**
#
# 왜 이 모양을 잡는가: 생성 프롬프트가 "한국어와 다른 점을 알려 주라" 고 시켰더니
# 모델이 **한국어 낱말 둘을 견주고 끝내는** 글을 대량으로 썼다. `banquet` 의
# "한국어 '연회' 는 주로 공식적인 자리에서 쓰지만, '대접' 은 더 일반적인 의미예요",
# `batch` 의 "한국어 '묶음' 이나 '셋' 과 혼동하지만". 영어를 배우러 온 사람에게
# 한국어 낱말 강의를 하는 셈이라 정보가 0이다.
#
# 정확하지는 않다 — 표본 12개에서 일곱이 실제 결함이었다(58%). 그래서 막지 않고
# 검수 큐에만 올린다(`cloze._ASKS_A_HUMAN`). 480개가 걸린다.
#
# **'한국어' 라는 낱말 하나에만 걸어 두면 안 된다.** 같은 결함을 '우리말' 로 시작해도
# 되기 때문이다. 실제로 이 결함을 고치던 중 교정본 여섯이 '한국어' 를 '우리말' 로
# 바꿨고(그 자체는 UI 문구로 더 나은 말이다), 그 여섯은 영어가 둘 이상이라 어차피
# 안 걸렸지만 — 누군가 나중에 그 설명을 줄이면 검사만 조용히 비껴간다.
_KOREAN_ONLY = re.compile(r"^(?:한국어|우리말|국어)\s")


def _korean_only_note(note: str) -> bool:
    """설명이 한국어끼리만 견주는가. 영어 낱말이 한 개 이하면 그렇게 본다."""
    if not _KOREAN_ONLY.match(note or ""):
        return False
    return len(re.findall(r"[A-Za-z]{2,}", note)) <= 1


# 설명이 예문을 그대로 옮겨 적었는가. **판정이 아니라 물음이다.**
#
# 왜 이 모양을 잡는가: 설명은 예문을 읽은 직후에 쓴다 — 모델도 그렇고 사람도
# 그렇다. 그러면 예문 문장이 설명 안으로 통째로 옮겨 붙는다. `master` 의 설명이
# "I want to master English." 로 예문과 글자까지 같았고, `taste` 는
# "This soup tastes good.", `deny` 는 "He denied stealing the money." 였다.
# **화면에서는 같은 문장이 두 줄 연속으로 뜬다** — 설명 칸이 한 줄 비는 셈이다.
#
# 눈으로는 안 잡힌다. 읽는 사람이 예문을 방금 봤기 때문에 설명에서 또 봐도 새
# 문장처럼 읽힌다. 실제로 40개씩 다섯 묶음을 한 항목씩 읽고도 못 봤고, 이 규칙을
# 돌려서야 그 200개 중 26개가 드러났다.
#
# 정확하지는 않다 — **겹침이 곧 설명인 자리가 있다.** `cup` 의 "양을 셀 때는
# a cup of coffee 처럼 of 를 넣어요" 는 예문과 겹치지만 그 겹침이 가르치려는
# 짝이고, `ring` 은 예문을 다시 적어 ring the doorbell 과 견준다. 표본 12개에서
# 절반쯤이 실제 결함이었다. 그래서 막지 않고 검수 큐에만 올린다
# (`cloze._ASKS_A_HUMAN`). DB 전체에서 315개가 걸린다.
#
# 잣대를 둘로 나눈 이유: 네 낱말만 보고 걸면 `to the west of`·`go to a concert`
# 처럼 짝을 가르치는 자리가 무더기로 딸려 온다(453개). 다섯 낱말 이상 이어지면
# 그것만으로 걸고, 넷이면 그 넷이 **예문의 7할 이상**일 때만 건다 — 짧은 예문은
# 네 낱말이 곧 문장 전체이기 때문이다.
_MIN_ECHO_WORDS = 4
_LONG_ECHO_WORDS = 5
_ECHO_COVERAGE = 0.7
_ECHO_STRIP = re.compile(r"[^a-z0-9 ]+")


def _echo_words(text: str) -> list[str]:
    """겹침을 재려고 깎은 낱말들. 대소문자와 문장부호는 같은 문장인지와 무관하다."""
    return _ECHO_STRIP.sub(" ", (text or "").lower()).split()


def echoed_example_fragment(example: str, usage_note: str) -> str | None:
    """설명이 예문에서 통째로 옮겨 온 조각. 없으면 None.

    가장 긴 것 하나만 돌려준다. 고치는 사람에게 필요한 것은 "어디가 겹쳤나"
    한 군데이고, 겹친 조각을 다 세어 봐야 고칠 자리가 늘지는 않는다.
    """
    words = _echo_words(example)
    if len(words) < _MIN_ECHO_WORDS:
        return None
    # 양끝에 빈칸을 대고 찾는다. 그냥 `in` 으로 보면 조각이 **낱말 가운데를 가로질러**
    # 맞는다 — `ear` 의 예문 'I have an ear infection.' 이 설명의 'I have an earache.'
    # 안에서 'i have an ear' 로 걸렸다. 겹친 것은 낱말 넷이 아니라 셋이다.
    note = f" {' '.join(_echo_words(usage_note))} "
    for size in range(len(words), _MIN_ECHO_WORDS - 1, -1):
        for start in range(len(words) - size + 1):
            fragment = " ".join(words[start : start + size])
            if f" {fragment} " not in note:
                continue
            if size >= _LONG_ECHO_WORDS or size / len(words) >= _ECHO_COVERAGE:
                return fragment
            # 가장 긴 겹침이 잣대에 못 미치면 더 짧은 겹침도 못 미친다.
            return None
    return None


_MEANING_SPLIT = re.compile(r"[,;·/]")


def repeated_meaning(meaning: str) -> str | None:
    """뜻 칸에 같은 말이 두 번 들어 있으면 그 말을 돌려준다.

    뜻은 쉼표로 여러 갈래를 적는 칸인데(`빌리다, 대여하다`), 두 자리에 같은 말이
    들어가면 갈래를 하나 적은 것과 다르지 않다. 그러면서 학습자에게는 "두 가지 뜻이
    있다"고 말하는 셈이라 더 나쁘다.

    실측으로 출제되는 5,083개 중 36개가 그랬다. `very` 가 '매우, 매우', `say` 가
    '말하다, 말하다 (말의 내용을 강조할 때)', `it` 이 '그것, 그것' 이었다. 빈도 1위
    `be` 부터 걸리므로 학습자가 앱을 켜자마자 만난다.

    괄호 안은 떼고 본다 — '창백한 (색상), 창백한 (얼굴)' 은 괄호로 갈래를 나눈 것처럼
    보이지만 앞말이 같아서 목록으로는 같은 말이 두 번이다. 실제로 `pale` 이 그랬다.
    """
    bare = _PATTERN_OPTIONAL.sub(" ", meaning or "")
    seen: set[str] = set()
    for piece in _MEANING_SPLIT.split(bare):
        token = piece.strip()
        if not token:
            continue
        if token in seen:
            return token
        seen.add(token)
    return None


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

    for row, other in colliding_glosses(rows):
        findings[row.word].append(
            Finding(
                "gloss_collision",
                "high",
                f"해석이 '{other}' 와 같은 말이에요 — 학습자가 어느 낱말을 쓸지 고를 근거가 없어요",
            )
        )

    return findings


# 해석에서 낱말을 뜯는 규칙. **숫자와 로마자를 반드시 포함한다.**
# 한글만 뜯었더니 '제 생일은 4월에 있어요' 와 '제 생일은 8월에 있어요' 가 똑같아졌다
# (둘 다 '월에' 로 남는다). 정작 두 문제를 갈라 주는 것이 숫자인 april/august ·
# november/december · eleventh/twelfth 가 통째로 오탐이 됐다.
_TOKEN = re.compile(r"[가-힣0-9A-Za-z]+")


def _content(text: str) -> frozenset[str]:
    """해석에서 뜯은 낱말들. 두 지시문이 사실상 같은 말인지 볼 때 쓴다."""
    return frozenset(_TOKEN.findall(_TRIVIA_KO.sub(" ", text or "")))


def _tells_apart(a: str, b: str) -> bool:
    """두 해석에 **서로를 가르는 낱말**이 있는가.

    처음에는 글자 조각의 겹침(자카드)으로 쟀는데 못 썼다. '남자 종업원이 메뉴를
    가져다줬어요' 와 '여자 종업원이 …' 는 87% 가 겹치지만 학습자는 한눈에 구별한다 —
    **다른 글자 두 개에 구별이 전부 실려 있기** 때문이다. 겹침을 재면 잘 고친 쌍일수록
    높게 나온다.

    그래서 양을 재지 않고 **있고 없음**을 본다. 한쪽에만 있는 낱말이 하나라도 있으면
    학습자에게 고를 근거가 있는 것이고, 하나도 없으면 두 문제의 지시문이 같은 말이다.
    """
    x, y = _content(a), _content(b)
    if not x or not y:
        return True
    return x != y


def colliding_glosses(rows: list[WordLike]) -> list[tuple[WordLike, str]]:
    """**빈칸의 답이 하나로 정해지지 않는** 쌍을 찾는다. (행, 상대 표제어, 겹침).

    왜 이 검사가 필요한가
    ---------------------
    연습장에서 해석은 장식이 아니라 **과제 지시문**이다. 학습자는 빈칸 옆의 한국어를
    읽고 답을 떠올린다. 그런데 뜻이 비슷한 두 낱말의 해석이 같으면 같은 지시문에
    답이 둘이 되고, 학습자는 옳게 생각하고도 오답 처리된다.

    실제로 이랬다 — `postpone` 의 해석이 '회의를 내일까지 미루어야 해요', `reschedule`
    의 해석이 '미팅을 내일로 미룰 수 있을까요?' 였다. 둘 다 '미루다' 라 갈리지 않는다
    (reschedule 은 **일정을 다시 잡는 것**이라고 써야 갈린다). 더 심한 것은 글자까지
    똑같은 경우로, `waiter`/`waitress`·`until`/`till`·`test`/`exam` 을 포함해 56개였다.

    왜 아무 쌍이나 보지 않고 `confused_with` 를 쓰나
    ------------------------------------------------
    5,497개를 전부 견주면 1,500만 쌍이고, 그중 해석이 비슷한 것은 대개 무관한 우연이다
    ('회의가 있어요' 는 여러 낱말의 예문에 나온다). 반면 이 자료에는 **어느 낱말끼리
    헷갈리는지 이미 적혀 있다**(`confused_with`). 학습자가 실제로 헷갈리는 자리만
    보므로 오탐이 적고, 무엇보다 그 자리가 바로 답이 갈리지 않는 자리다.

    같은 트랙 안에서만 본다. 학습자는 한 번에 한 트랙만 푼다.

    한계 — 글자가 안 겹치는 충돌은 못 잡는다
    ---------------------------------------
    정작 위의 `postpone` / `reschedule` 이 이 검사에 안 걸린다. 두 해석의 글자
    조각 겹침이 **0.04** 라서다. 학습자에게는 똑같이 '미루다' 하나로 읽히는데
    기계에게는 다른 문장이다.

    어간으로 보는 신호를 얹어 봤지만 못 썼다. 두 낱말이 같은 장면을 쓰면 해석이
    '회의'·'내일' 을 함께 갖게 되는데, 그건 표제어가 아니라 문장이 겹치는 것이라
    고친 뒤에도 계속 걸렸다. 한국어 뜻이 같은지는 결정론적으로 못 판정한다 —
    오늘 하루가 그 이야기였다. 그래서 이 검사는 **글자가 겹치는 부류만** 맡고,
    뜻이 겹치는 부류는 사람이 읽어서 잡는다(`content/data/gloss_fixes.yaml`).
    """
    by_word = {r.word.strip().lower(): r for r in rows}
    seen: set[tuple[str, str]] = set()
    out: list[tuple[WordLike, str]] = []
    for row in rows:
        gloss = (getattr(row, "example_ko", None) or "").strip()
        if not gloss:
            continue
        for name in getattr(row, "confused_with", None) or []:
            other = by_word.get(str(name).strip().lower())
            if other is None or other is row:
                continue
            if getattr(other, "track", None) != getattr(row, "track", None):
                continue
            theirs = (getattr(other, "example_ko", None) or "").strip()
            if not theirs:
                continue
            key = tuple(sorted((row.word, other.word)))
            if key in seen:
                continue
            if _tells_apart(gloss, theirs):
                continue
            seen.add(key)
            # 양쪽 다 지적한다. 어느 쪽을 고칠지는 사람이 정할 일이고,
            # 한쪽만 큐에 올리면 나머지 한쪽은 영영 안 보인다.
            out.append((row, other.word))
            out.append((other, row.word))
    return out


def risk_score(findings: list[Finding]) -> int:
    """정렬용 점수. 클수록 먼저 봐야 한다."""
    return sum(_WEIGHT[f.severity] for f in findings)


def worst_severity(findings: list[Finding]) -> Severity | None:
    for level in ("high", "medium", "low"):
        if any(f.severity == level for f in findings):
            return level  # type: ignore[return-value]
    return None
