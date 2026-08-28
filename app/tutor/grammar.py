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
import secrets
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
    return {w: VerbForms.model_validate(f) for w, f in (doc.get("verbs") or {}).items()}


@lru_cache(maxsize=1)
def rules() -> dict[str, Rule]:
    """규칙 파일 전부. `verb_forms.yaml` 은 규칙이 아니라 재료라 뺀다.

    `loader.load_scenarios` 와 같은 것들을 본다. 파일 이름과 안의 이름이 어긋나면
    사람이 파일을 못 찾고, 이름이 겹치면 **한쪽이 말없이 사라진다** — 둘 다 시작할
    때 시끄럽게 멈추는 편이 낫다. 오류에 파일 이름을 붙이는 것도 같은 이유다.

    틀이 부르는 동사가 형태 표에 없는 것도 여기서 잡는다. 그냥 두면 `make_item` 이
    None 을 돌려주고 그 짝이 조용히 사라진다 — 오타 하나로 열 문제가 없어져도
    아무도 모른다.
    """
    out: dict[str, Rule] = {}
    forms = verb_forms()
    for path in sorted(RULES_DIR.glob("*.yaml")):
        if path == FORMS_FILE:
            continue
        rule = Rule.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        if rule.rule != path.stem:
            raise ValueError(f"{path.name}: rule({rule.rule})이 파일명과 다릅니다.")
        if rule.rule in out:
            raise ValueError(f"규칙 이름이 중복됩니다: {rule.rule}")
        unknown = sorted({v for f in rule.frames for v in f.verbs if v not in forms})
        if unknown:
            raise ValueError(
                f"{path.name}: 형태 표({FORMS_FILE.name})에 없는 동사입니다: {', '.join(unknown)}"
            )
        out[rule.rule] = rule
    if not out:
        raise ValueError(f"규칙 파일을 찾지 못했습니다: {RULES_DIR}")
    return out


# 보기 순서를 섞는 데 쓰는 **응답 밖의 값**. 프로세스마다 새로 뽑는다.
#
# 왜 필요한가: 이게 없으면 보기 순서가 정답에 대한 검증 가능한 커밋먼트가 된다.
# 씨앗 재료(규칙 이름·틀 문장·정답 동사)가 전부 응답 안에 있거나 응답에서 복원되고
# 후보는 넷뿐이라, 넷을 다 해시해 관측한 순서와 맞는 것을 고르면 정답이 나온다.
# 되읽기 어려운 해시를 써도 소용이 없다 — 역상을 구하는 게 아니라 넷을 쳐 보는 것이다.
#
# 그래서 씨앗에 응답 밖의 값을 섞는다. 프로세스가 다시 뜨면 순서가 바뀌는데,
# 그건 학습자에게 보이지 않는 상태라 상관없다 — 한 번 푸는 동안은 고정이다.
_ORDER_SALT = secrets.token_bytes(16)


def _order(seed: str, count: int) -> list[int]:
    """보기 순서를 **한 프로세스 안에서 문제마다 고정된** 자리바꿈으로 정한다.

    화면에서 섞으면 새로고침할 때마다 답 번호가 달라져서, 서버가 채점한 결과와
    학습자가 본 화면이 어긋난다. 그렇다고 매번 무작위로 섞으면 같은 문제를 다시
    풀 때 답이 옮겨 다녀 학습자가 "아까는 ②였는데" 하고 헷갈린다.

    `random` 을 쓰지 않는 이유는 씨앗을 심는 전역 상태가 이 함수 밖의 코드에까지
    영향을 주기 때문이다.
    """
    digest = hashlib.sha256(_ORDER_SALT + seed.encode("utf-8")).digest()
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

    # id 를 세 번 고쳤다. 앞의 둘이 왜 틀렸는지가 지금 모양의 이유다.
    #
    # ① `to_infinitive:Please remember...:send` — 읽으면 답이 보인다.
    # ② 그 문자열의 sha256 — 되읽을 수는 없지만 씨앗 재료가 전부 응답에서 복원되고
    #    후보는 넷뿐이라, 넷을 해시해 맞는 것을 고르면 된다. **되읽기 어려운 것과
    #    맞혀 보기 어려운 것은 다른 성질이다.**
    # ③ 자리 번호(`to_infinitive#0007`) — 맞혀 볼 재료는 없앴는데, id 가 내용이
    #    아니라 **자리**를 가리켜서 틀 하나를 지우면 그 뒤가 전부 한 칸씩 민다.
    #    조용히 민다 — 404 도 안 난다. 실제로 146→141→134 로 바뀌는 동안 같은
    #    번호가 다른 문장을 가리켰고, 오답 노트나 '틀린 것 다시 풀기' 를 붙이는
    #    순간 저장된 id 가 전부 엉뚱한 문제로 간다.
    #
    # 지금은 **정답을 뺀 내용**으로 짓는다. 씨앗은 틀 문장과 보기 넷을 정렬한 것
    # 이라 응답만 보고도 똑같이 계산할 수 있는데, 그래도 아무것도 안 새어 나온다 —
    # 정렬해 놓으면 넷 중 어느 것이 답인지가 씨앗에 안 들어간다. 내용이 같으면
    # 자리가 바뀌어도 id 가 그대로다.
    words = "|".join(sorted(c.word for c in picked))
    item_id = hashlib.sha256(f"{rule.rule}:{frame.text}:{words}".encode()).hexdigest()[:12]
    order = _order(f"{rule.rule}:{frame.text}:{verb}", CHOICE_COUNT)
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

    **한 바퀴씩 도는 것으로는 부족하다.** 틀마다 동사 수가 달라서(4개짜리부터
    11개짜리까지) 짧은 틀이 먼저 바닥나고, 끝에 가면 남은 긴 틀 둘이 번갈아 나온다
    — 실제로 147번부터 문장이 딱 두 개로 갈마들었다. 막으려던 바로 그 현상이
    목록 뒤쪽에서 되살아난 것이다.

    그래서 자리마다 **남은 것이 가장 많은 틀**을 집는다. 그러면 긴 틀이 목록 전체에
    고르게 흩어지고, 어느 자리에서 잘라 봐도 같은 문장이 붙어 나오지 않는다.
    """
    rows = [
        [item for verb in frame.verbs if (item := make_item(rule, frame, verb)) is not None]
        for frame in rule.frames
    ]
    out: list[GrammarItem] = []
    remaining = [list(r) for r in rows]
    last = -1
    while any(remaining):
        # 남은 것이 많은 틀부터. 같으면 원래 차례대로, 그리고 **직전에 낸 틀은
        # 뒤로 미룬다** — 남은 것이 하나뿐일 때 같은 문장이 이어 붙는 것을 막는다.
        pick = max(
            (i for i, r in enumerate(remaining) if r),
            key=lambda i: (len(remaining[i]), i != last, -i),
        )
        out.append(remaining[pick].pop(0))
        last = pick
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
    """채점 결과. 왜 그런지까지 말한다 — 맞히는 것보다 아는 것이 목적이다.

    **보기 중에서 고르지 않았으면 `answer` 가 비어 있다.** 아래 `grade` 참고.
    """

    ok: bool
    answer: str
    chosen: str
    message_ko: str
    why_ko: list[str]


def grade(item: GrammarItem, chosen: str, rule: Rule) -> Verdict:
    """보기 하나를 채점한다.

    **보기에 없는 값에는 정답을 알려 주지 않는다.** 처음에는 무엇을 보내든 답과
    해설을 돌려줬는데, 그러면 아무 글자나 한 번 보내는 것만으로 문제마다 답을
    받아 갈 수 있다 — 응답에서 정답을 빼고 id 에서 지우고 보기 순서까지 소금으로
    가린 것이 이 한 줄에서 무너진다.

    막는 이유가 보안만은 아니다. 보기 중에서 고르지 않았으면 **답한 것이 아니고**,
    답하지 않은 사람에게 답을 펴면 연습 한 번이 통째로 사라진다. 빈칸 연습장이
    오타(`not_a_word`)에 설명을 안 펴고 '답 보기' 를 따로 두는 것과 같은 판단이다.
    """
    picked = chosen.strip()
    kinds = {c.word: c.kind for c in item.choices}
    if picked not in kinds:
        return Verdict(
            ok=False,
            answer="",
            chosen=picked,
            message_ko="보기 중에서 골라 주세요.",
            why_ko=[],
        )
    ok = picked == item.answer

    why = [
        f"{c.word} — {FORM_KO.get(c.kind, c.kind)}"
        + ("  ← 정답" if c.word == item.answer else "")
        for c in item.choices
    ]
    if ok:
        message = "맞았어요. " + rule.explain_ko
    else:
        name = FORM_KO.get(kinds[picked], kinds[picked])
        message = f"'{picked}' — {name}{_copula(name)}. " + rule.explain_ko
    return Verdict(ok=ok, answer=item.answer, chosen=picked, message_ko=message, why_ko=why)
