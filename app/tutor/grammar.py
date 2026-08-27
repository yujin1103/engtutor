"""토익 Part 5 형 4지선다 문법 문제. **LLM 을 부르지 않는다.**

무엇이 다른가 — 빈칸 연습장과 나란히 놓고 보면
------------------------------------------------
연습장(`cloze.py`)은 **뜻**을 묻는다. 예문에서 표제어를 지우고 한국어 해석을 단서로
주면서 "이 자리에 들어갈 낱말이 무엇이냐" 를 묻는다. 답은 자유 입력이고, 채점은
철자·형태·품사를 훑는 일곱 단 사다리다.

이쪽은 **형태**를 묻는다. 보기 넷이 전부 같은 낱말의 다른 모양이라 뜻은 이미 알려
준 것과 같고, 가르는 것은 오직 "이 자리에 어느 모양이 오는가" 하나다. 그래서
`cloze` 의 어느 함수도 그대로 쓸 수 없다 — 지우는 자리가 표제어가 아니고,
채점이 인덱스 비교이며, **`pos_hint` 는 여기서는 정답 그 자체라 내보내면 안 된다.**

왜 문장을 데이터로 두는가
-------------------------
`to` 가 to부정사를 이끄는지 전치사인지를 **결정론적으로 판정할 수단이 없다.**
토익 예문 2,252개에서 to 를 캐면 373건이 나오는데 그중 상당수가 `to work`,
`to our company`, `to tomorrow` 처럼 전치사다. 부정사만 확실히 골라내면 109건이
남고 그중 84건이 `need to` 한 틀이라, 문제가 죄다 "We need to ______" 로 나온다.

그래서 문장 틀을 손으로 쓴다(`grammar_rules/to_infinitive.yaml`). 틀을 사람이
쓰면 그 `to` 가 부정사라는 것이 **작성 시점에 보증**되고, 판정할 필요 자체가 없어진다.

왜 낱말의 모양도 데이터로 두는가
--------------------------------
만들려고 해 봤고, 안전하게는 안 된다. WordNet 은 표면 철자를 검사하지 못한다 —
`morphy('builded')` 도 `lexicon.known('attachs')` 도, `known('sended')` 마저 참이다.
접미사를 떼어 원형에 닿기만 하면 통과시키기 때문이다. 색인 원본은 반대로 너무 좁아
`announcing`·`canceling` 같은 멀쩡한 형태가 표제어에 없다.

게다가 -ing 의 자음 겹치기는 **강세**에 달려 있어 규칙으로 정할 수 없다
(prefer→preferring 인데 offer→offering, open→opening 이지 openning 이 아니다).

그래서 `grammar_rules/verb_forms.yaml` 에 미리 만들어 사람이 보고 넣었다.
이 프로젝트가 낱말 콘텐츠에 쓰는 원칙과 같다 — 생성은 미리, 검수는 사람, 서빙은 조회.
지어낸 철자가 학습자 앞에 놓일 길이 아예 없다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from .slot import BLANK

RULES_DIR = Path(__file__).parent / "grammar_rules"
FORMS_FILE = RULES_DIR / "verb_forms.yaml"

# 문제 하나에 나가는 보기 수. 토익 Part 5 가 넷이다.
CHOICE_COUNT = 4

# 낱말의 모양마다 학습자에게 보여줄 이름. 오답 해설이 이 이름으로 말한다.
FORM_KO: dict[str, str] = {
    "base": "동사원형",
    "ing": "-ing 형(동명사)",
    "noun": "명사",
    "adj": "형용사",
    "past": "과거형",
    "s": "3인칭 단수형",
}

# 오답을 고르는 순서. 앞의 것부터 있는 대로 집는다.
#
# 왜 순서가 있는가: 넷 중 셋만 오답이라 다 쓸 수 없다. 품사가 다른 것(명사·형용사)이
# 굴절형(과거형·3인칭)보다 문제를 낫게 만든다 — 굴절형만 모아 두면 "-ing 냐 -ed 냐"
# 를 묻는 문제가 되어, 정작 가르치려는 "to 뒤에는 동사원형" 이 흐려진다.
DISTRACTOR_ORDER = ("ing", "noun", "adj", "past", "s")


class VerbForms(BaseModel):
    """동사 하나가 가지는 다른 모양들. YAML 한 줄 = 이것 하나."""

    model_config = ConfigDict(extra="forbid")

    ing: str
    noun: str | None = None
    adj: str | None = None
    past: str | None = None
    s: str | None = None

    def named(self) -> list[tuple[str, str]]:
        """(모양 이름, 낱말) 을 `DISTRACTOR_ORDER` 순으로. 없는 것은 건너뛴다."""
        out: list[tuple[str, str]] = []
        for kind in DISTRACTOR_ORDER:
            value = getattr(self, kind, None)
            if value:
                out.append((kind, value))
        return out


class Frame(BaseModel):
    """문장 틀 하나. `___` 자리에 동사가 들어간다.

    `verbs` 는 이 문장에 넣어 말이 되는 동사만 적은 것이고, **그 목록이 곧 사람의
    검수다.** 아무 동사나 넣으면 영어로 말이 안 되는 문장이 학습자에게 나간다.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    ko: str
    verbs: list[str]


class Rule(BaseModel):
    """문법 규칙 하나와 그것을 묻는 문장 틀들. YAML 한 파일 = 규칙 하나."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    title: str
    explain_ko: str
    frames: list[Frame]


@dataclass(frozen=True)
class Choice:
    """보기 하나. `kind` 는 왜 이것이 답이 아닌지 말할 때 쓴다."""

    word: str
    kind: str


@dataclass(frozen=True)
class GrammarItem:
    """문제 하나. **`answer` 는 서버 안에서만 산다** — 응답에 넣지 않는다."""

    id: str
    rule: str
    sentence: str
    sentence_ko: str
    choices: tuple[Choice, ...]
    answer: str
    verb: str


@lru_cache(maxsize=1)
def verb_forms() -> dict[str, VerbForms]:
    """검수된 동사 모양 표. 파일이 곧 진실이라 한 번만 읽는다."""
    doc = yaml.safe_load(FORMS_FILE.read_text(encoding="utf-8")) or {}
    return {w: VerbForms(**f) for w, f in (doc.get("verbs") or {}).items()}


@lru_cache(maxsize=1)
def rules() -> dict[str, Rule]:
    """규칙 파일 전부. `verb_forms.yaml` 은 규칙이 아니라 재료라 뺀다."""
    out: dict[str, Rule] = {}
    for path in sorted(RULES_DIR.glob("*.yaml")):
        if path == FORMS_FILE:
            continue
        rule = Rule(**(yaml.safe_load(path.read_text(encoding="utf-8")) or {}))
        out[rule.rule] = rule
    return out


def _order(item_id: str, count: int) -> list[int]:
    """보기 순서를 **문제마다 고정된** 자리바꿈으로 정한다.

    화면에서 섞으면 새로고침할 때마다 답 번호가 달라져서, 서버가 채점한 결과와
    학습자가 본 화면이 어긋난다. 그렇다고 매번 무작위로 섞으면 같은 문제를 다시
    풀 때 답이 옮겨 다녀 학습자가 "아까는 ②였는데" 하고 헷갈린다.

    그래서 문제 id 를 씨앗으로 쓴다. 같은 문제는 언제 봐도 같은 순서고, 다른
    문제끼리는 답의 자리가 흩어진다. `random` 을 쓰지 않는 이유는 씨앗을 심는
    전역 상태가 이 함수 밖의 코드에까지 영향을 주기 때문이다.
    """
    digest = hashlib.sha256(item_id.encode("utf-8")).digest()
    slots = list(range(count))
    out: list[int] = []
    for i in range(count):
        # 남은 것 중 하나를 고른다. 바이트가 모자랄 일은 없다(count 는 넷이다).
        out.append(slots.pop(digest[i] % len(slots)))
    return out


def make_item(rule: Rule, frame: Frame, verb: str) -> GrammarItem | None:
    """틀 하나와 동사 하나로 문제를 만든다. 보기를 넷 못 채우면 None.

    None 을 돌려주는 것은 결함이 아니라 정상이다 — 어떤 동사는 검수된 모양이
    셋뿐이라 오답 셋을 채울 수 있고, 그보다 적으면 문제가 성립하지 않는다.
    """
    forms = verb_forms().get(verb)
    if forms is None:
        return None

    seen = {verb}
    picked: list[Choice] = [Choice(verb, "base")]
    for kind, word in forms.named():
        if word in seen:
            # 같은 글자가 두 번 나오면 답이 둘인 문제가 된다. `change` 의 명사형이
            # 동사와 같은 글자인 것처럼, 실제로 겹치는 낱말이 있다.
            continue
        seen.add(word)
        picked.append(Choice(word, kind))
        if len(picked) == CHOICE_COUNT:
            break
    if len(picked) < CHOICE_COUNT:
        return None

    # **id 에 정답을 넣지 않는다.** `to_infinitive:Please remember...:send` 처럼
    # 읽을 수 있는 id 를 쓰면 정답을 응답에서 뺀 것이 아무 소용이 없다 — 화면이
    # id 만 보고 답을 안다. 그래서 같은 재료로 매번 같은 값이 나오되 되읽을 수는
    # 없는 짧은 지문을 쓴다.
    seed = f"{rule.rule}:{frame.text}:{verb}"
    item_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    order = _order(seed, CHOICE_COUNT)
    return GrammarItem(
        id=item_id,
        rule=rule.rule,
        sentence=frame.text.replace("___", BLANK),
        sentence_ko=frame.ko,
        choices=tuple(picked[i] for i in order),
        answer=verb,
        verb=verb,
    )


def items_of(rule: Rule) -> list[GrammarItem]:
    """이 규칙으로 낼 수 있는 문제 전부. **틀을 돌아가며** 낸다.

    틀 하나를 끝내고 다음 틀로 가면 학습자가 처음 열 문제에서 같은 문장만 열 번
    본다("Please remember to ____ the invoice." 가 열 줄). 문제는 다르지만 화면은
    같아 보이고, 그러면 문장을 안 읽고 보기만 보게 된다 — 이 문제 유형에서 그건
    연습을 통째로 무의미하게 만든다.

    무작위로 섞지는 않는다. 같은 호출이 매번 다른 것을 돌려주면 시험을 쓸 수 없고,
    `offset` 으로 다음 쪽을 받는 화면이 문제를 건너뛰거나 두 번 받는다.
    """
    rows = [
        [item for verb in frame.verbs if (item := make_item(rule, frame, verb)) is not None]
        for frame in rule.frames
    ]
    out: list[GrammarItem] = []
    for i in range(max((len(r) for r in rows), default=0)):
        out.extend(r[i] for r in rows if i < len(r))
    return out


def _copula(word: str) -> str:
    """받침을 보고 '이에요' 와 '예요' 를 가른다. '과거형이에요' / '명사예요'.

    영어 낱말에는 쓰지 않는다 — 한글은 받침을 글자에서 읽을 수 있지만 영어는
    소리로 정해져서(`sending` 은 '은', `sender` 는 '는') 글자만으로는 틀린다.
    그래서 문구 쪽에서 영어 뒤에 조사가 붙지 않게 줄표로 끊는다.
    """
    last = word.strip()[-1:]
    if not last or not ("가" <= last <= "힣"):
        return "예요"
    has_final = (ord(last) - 0xAC00) % 28 != 0
    return "이에요" if has_final else "예요"


@dataclass(frozen=True)
class Verdict:
    """채점 결과. 왜 그런지까지 말한다 — 맞히는 것보다 아는 것이 목적이다."""

    ok: bool
    answer: str
    chosen: str
    message_ko: str
    why_ko: list[str]


def grade(item: GrammarItem, chosen: str, rule: Rule) -> Verdict:
    """보기 하나를 채점한다. 고른 것이 보기에 없으면 오답으로 본다."""
    picked = chosen.strip()
    kinds = {c.word: c.kind for c in item.choices}
    ok = picked == item.answer

    why = [
        f"{c.word} — {FORM_KO.get(c.kind, c.kind)}"
        + ("  ← 정답" if c.word == item.answer else "")
        for c in item.choices
    ]
    if ok:
        message = "맞았어요. " + rule.explain_ko
    elif picked in kinds:
        name = FORM_KO.get(kinds[picked], kinds[picked])
        message = f"'{picked}' — {name}{_copula(name)}. " + rule.explain_ko
    else:
        message = "보기 중에서 고르세요. " + rule.explain_ko
    return Verdict(ok=ok, answer=item.answer, chosen=picked, message_ko=message, why_ko=why)
