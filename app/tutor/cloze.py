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

문은 하나만 둔다
----------------
학습자에게 나갈 자격이 있는지 판정하는 자리는 `is_safe_to_serve` **하나**다. 검사기를
곁다리에만 걸어 두면 어떻게 되는지 실제로 봤다 — `practice.alternatives_for` 는
`bagel` 의 뜻(`백일(백面包)`)을 후보 목록에서 걸러 내면서, 같은 `bagel` 이 문제
본문으로 나가는 것은 못 막았다. 검사기가 없어서가 아니라 **문 앞에 안 걸려서**다.
그래서 여기서는 검사기를 새로 만들지 않고 이미 있는 것들(`screen`,
`reject_foreign_script`, `mask_answer`)을 그 한 문으로 모은다.

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
from .korean import has_hangul, reject_foreign_script
from .slot import BLANK, head_word, narrow, order_pos

__all__ = [
    "BLANK",
    "ClozeItem",
    "ClozeResult",
    "PosHint",
    "Verdict",
    "grade",
    "is_answerable",
    "is_safe_to_serve",
    "is_speakable",
    "make_item",
    "mask_answer",
    "normalize",
    "pos_hint",
    "pos_of",
    "readable_ko",
    "SpellHint",
    "spell_hints",
]

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
    # 예문 **그 문장**의 한국어 해석. 3,245개 중 792개만 채워져 있어서 대부분 None 이다.
    # 없으면 안 보여줄 뿐, 이것 때문에 출제가 실패하면 안 된다.
    example_ko: str | None = None
    # 빈칸을 뺀 원문. 답을 맞힌 뒤 설명 카드에서 문장을 통째로 다시 보여 준다.
    example: str = ""
    topic: str | None = None


# 판정. 기존 이름은 하나도 바꾸지 않았다 — 웹 UI 와 시험이 문자열을 그대로 쓴다.
#
# `right_pos` 와 `wrong_pos` 는 예전에 전부 `wrong_word` 로 뭉쳐 있던 것을 가른 것이다.
# 가른 이유는 `wrong_form` 을 따로 둔 이유와 같다. "명사 자리에 동사를 넣었다"와
# "명사 자리에 다른 명사를 넣었다"는 학습자가 다음에 고쳐야 할 것이 서로 다르다.
# 앞의 것이 이 연습장이 가장 가르치고 싶은 자리다.
Verdict = Literal[
    "correct",
    "wrong_form",
    "right_pos",
    "wrong_pos",
    "wrong_word",
    "not_a_word",
    "empty",
]


@dataclass(frozen=True)
class PosHint:
    """빈칸에 대해 **말해도 되는 것**. 말할 수 없으면 이 객체 자체를 만들지 않는다.

    `source` 가 근거를 밝힌다. `"slot"` 은 자리(관사·조동사)에서 좁힌 것이라
    "여기엔 명사가 들어가요"라고 자리를 말할 수 있고, `"word"` 는 정답 낱말이
    가질 수 있는 품사 전부라 "이 낱말은 명사로도 동사로도 써요"까지만 말할 수 있다.

    둘을 섞어 쓰면 아는 것보다 더 주장하게 된다. `text_ko` 를 여기서 만들어
    내보내는 이유가 그것이다 — 문구를 화면 쪽에서 조립하면 근거가 떨어져 나간다.
    """

    pos: tuple[str, ...]
    labels_ko: tuple[str, ...]
    text_ko: str
    source: Literal["slot", "word"]


@dataclass(frozen=True)
class ClozeResult:
    verdict: Verdict
    said: str
    answer: str
    message_ko: str
    # 실제로 판정한 낱말. 구·절로 답하면 그 안의 머리 낱말이라 `said` 와 다르다.
    # 무엇을 보고 판정했는지 학습자에게 그대로 보여 주기 위해 결과에 남긴다.
    head: str = ""
    # 학습자 답의 품사. 모르면 빈 튜플 — 모르는데 말하면 그게 거짓이 된다.
    said_pos: tuple[str, ...] = ()

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
            example_ko=_serviceable_gloss(getattr(row, "example_ko", None)),
            example=example,
            topic=(getattr(row, "topic", None) or None),
        )
    return None


def readable_ko(text: str | None) -> bool:
    """학습자가 읽을 수 있는 한국어인가. 한자·가나·키릴이 섞이면 아니다.

    `practice._readable` 에 있던 것을 여기로 옮겼다. 원래 이 검사는 후보 목록에만
    걸려 있었고 문제 본문이 나가는 자리에는 안 걸려 있었다. 판정 모듈에 두면
    문(`is_answerable`)과 설명 카드가 같은 함수를 쓴다.
    """
    if not text:
        return False
    try:
        reject_foreign_script(text, "한국어 칸")  # 메시지는 안 쓴다 — 여기서는 참/거짓만 필요하다
    except ValueError:
        return False
    return True


def _serviceable_gloss(text: str | None) -> str | None:
    """예문 해석. 학습자가 못 읽는 글자가 섞였으면 안 붙인다.

    해석은 있으면 좋고 없어도 문제가 성립하는 칸이라(3,245개 중 792개만 채워져 있다)
    항목을 버리지 않고 **그 칸만** 뗀다. 뜻(`meaning_ko`)을 다르게 처분하는 이유는
    `is_answerable` 에 적어 두었다.

    지금 전수 조사에서 여기 걸리는 항목은 0개다. 해석을 채운 배치가 이미 같은
    화이트리스트(`reject_foreign_script`)로 막고 있어서인데, 뜻을 쓴 배치도 한글
    유무만 검사받고 통과해서 14개를 흘렸다. 검사를 만드는 쪽에만 두면 언젠가 한 칸이
    빠진다 — 그래서 나가는 자리에도 같이 건다.
    """
    gloss = (text or "").strip() or None
    if gloss and not readable_ko(gloss):
        return None
    return gloss


# 뜻풀이의 괄호 주석. `개찰구 (지하철에 있는 출입문)` 의 괄호 안은 부연이지
# 그 낱말의 한국어 이름이 아니다.
_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]|（[^）]*）")


def mask_answer(text: str | None, word: str) -> str | None:
    """문제와 함께 나가는 텍스트에서 정답 낱말을 가린다.

    빈칸을 만들어 놓고 옆 칸으로 답을 흘리면 빈칸 문제가 아니다. 그런데 실제로
    흘리고 있었다 — 서빙 가능한 2,950개 중 **2,882개(97.7%)의 `pattern` 이 표제어를
    그대로 담고 있다**(`borrow + 목적어`, `a/the + cookie`). `meaning_ko` 에서 3개,
    `example_ko` 에서 2개가 더 나왔다.

    가리는 판정은 `make_item` 이 빈칸을 뚫을 때 쓰는 것과 **같은 함수**(`mentions`)로
    한다. 빈칸이 지운 것과 여기서 가리는 것이 어긋나면 안 되기 때문이다. 이 함수는
    느슨해서 관계없는 낱말까지 가릴 수 있는데, 그쪽으로 틀리는 게 맞다 — 더 가리면
    문제가 조금 덜 친절해지고, 덜 가리면 문제가 아니게 된다.

    설명 카드(`practice.explain`)는 답을 본 뒤라 가리지 않는다.
    """
    if not text:
        return text
    out = []
    last = 0
    for match in _WORD.finditer(text):
        if not mentions(match.group(), word):
            continue
        out.append(text[last : match.start()])
        out.append(BLANK)
        last = match.end()
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


def is_answerable(item: ClozeItem) -> bool:
    """이 빈칸이 **문제로 성립하는가.** 승인 여부와 상관없이 늘 본다.

    `screen` 과 갈라 둔 이유가 있다. 선별기는 "이 항목의 내용을 믿을 수 있는가"를
    묻고, 사람이 승인하면 그 물음은 사람의 판단으로 대체된다. 여기서 묻는 것은
    **화면에 남는 것이 과제가 되는가**이고, 그건 승인으로 대체되지 않는다. 사람이
    `sigh 叹气하다` 를 승인해도 왕초보가 그 글자를 읽게 되지는 않는다.

    검사 셋이 결국 한 가지를 묻는다 — **화면에 그 낱말의 한국어 이름이 남는가.**

    1. **뜻이 읽히는 글자인가.** 출제 가능 2,950개 중 14개의 뜻에 한자·가나·키릴이
       섞여 있었다 — `bagel 백일(백面包)`, `sigh 叹气하다`, `spicy 매운, 辛い`,
       `narrow  hẹavy`(뜻도 반대다). 다섯 개가 기본 장면 팩 안에 있어서 카페 60개를
       도는 학습자는 `bagel` 을 반드시 만난다.

       **뜻만 빼지 않고 항목을 통째로 뺀다.** 뜻은 이 문제의 유일한 단서라
       (`main._cloze_out`: "뜻을 안 주면 왕초보에게는 과제가 성립하지 않는다")
       뜻을 지운 문제는 곧 2번이 막는 그 문제가 된다. 항목을 빼는 값은
       2,950분의 14이고, 이 열넷은 어차피 검수에서 고쳐야 할 것들이다.

    2. **가린 뒤에도 그 이름이 남는가.** `mask_answer` 가 뜻에서 정답을 가리는데,
       뜻이 `turnstile (지하철, 버스 등에 있는 출입문)` 처럼 **영어 낱말을 이름
       자리에 그대로 적어 놓은** 경우 가리고 나면 `____ (지하철, 버스 등에 있는
       출입문)` 이 된다. 괄호 안은 부연이지 이름이 아니다. 학습자는 화면 어디에서도
       그 낱말의 한국어를 볼 수 없어서 답을 고를 근거가 없다.

       그래서 **괄호 밖에 한글이 남는가**로 본다. "가린 뒤에 한글이 남는가"로는
       못 잡는다 — `mask_answer` 는 영문자만 가리므로 한글은 애초에 줄지 않는다.
       전수에서 2개(`turnstile`·`coleslaw`)가 걸리고,
       `concentration 집중, 중점 (집중력: ____ level)` 처럼 이름이 괄호 밖에 살아
       있는 것은 그대로 나간다.

    3. **답이 문장에 그대로 남아 있지 않은가.** `make_item` 은 표제어의 **첫**
       등장만 지운다. `He is ____ tall as his father.` 는 두 번째 `as` 가 남아
       답을 그대로 알려 준다.

       여기서는 `mentions` 를 쓰지 않고 표면형 정확일치로 본다. 다른 칸을 가릴
       때는 넓게 잡는 쪽으로 틀리는 게 맞지만(더 가려도 문제는 남는다) 여기서
       걸리면 항목을 버리므로 반대쪽으로 틀려야 한다. 실제로 `mentions` 기준은
       5개를 빼는데 그중 넷은 `The ____ did a magic trick.`(정답 magician),
       `The baby is ____ on the swing.`(정답 swinging)처럼 **가까운 형태**를
       보여 줄 뿐 답을 알려 주지 않는다. 정확일치로는 1개다.
    """
    if not readable_ko(item.meaning_ko):
        return False
    masked = mask_answer(item.meaning_ko, item.word) or ""
    if not has_hangul(_PARENTHETICAL.sub(" ", masked)):
        return False
    rest = normalize(item.sentence.replace(BLANK, " ")).split()
    return normalize(item.answer) not in rest


def is_safe_to_serve(row) -> bool:
    """학습자에게 내보내도 되는 항목인가. **출제 경로의 유일한 문이다.**

    두 가지를 순서대로 묻는다.

    - **문제로 성립하는가**(`is_answerable`) — 승인 여부와 무관하게 늘 본다.
    - **내용을 믿을 수 있는가** — 사람이 승인했으면(`reviewed`) 사람의 판단이
      이기고, 검수 큐에 있으면 **선별기 지적이 하나도 없을 때만** 내보낸다.

    뒤의 것은 검수를 대신하는 게 아니다. 빈칸 문제가 쓰는 것은 `example` 과 `word`
    뿐이고, 확인된 환각 13건은 전부 `usage_note` 와 `confused_with` 에 있었다.
    그래도 예문이 어색할 여지는 남으므로, 승인된 항목이 쌓이면 이 문은 좁혀야 한다.

    빈칸을 여기서 다시 깎는다(`make_item`). 이미 만들어 둔 것을 호출부에게서
    받게 하면 검사한 값과 내보내는 값이 어긋날 수 있고, 무엇보다 **호출부가 이
    함수를 부르는 것을 잊을 수 있다** — 이번에 고친 결함들이 정확히 그렇게 생겼다.
    깎는 값은 순수 문자열 처리라 두 번 해도 싸다.
    """
    item = make_item(row)
    if item is None or not is_answerable(item):
        return False
    if bool(getattr(row, "reviewed", False)):
        return True
    return not screen(row)


def pos_of(word: str) -> frozenset[str] | None:
    """이 낱말이 가질 수 있는 품사. 말할 수 없으면 None.

    `lexicon.parts_of_speech` 를 그냥 부르지 않고 두 겹을 더 두른다.

    **기능어는 아예 묻지 않는다.** WordNet 에 `a`(비타민 A), `in`(인치),
    `he`(헬륨) 같은 동형 내용어가 들어 있어서, 학습자가 `the` 자리에 `a` 를 넣으면
    "명사예요"라는 답이 나온다. 사전이 맞고 화면이 거짓말을 하는 경우다.
    `is_speakable` 이 기능어를 사전 조회로 못 거른 것과 같은 함정이라, 같은 목록으로 막는다.

    **굴절형은 원형으로 되돌려 한 번 더 본다.** 표면형을 그대로 물으면 `said` 가
    형용사(aforesaid)로, `saw` 가 명사(톱)로 잡힌다. 원형들의 품사를 합집합으로
    모으는 이유는 `listening` 이 명사이자 동사인 것처럼 원형이 여럿일 수 있어서다.
    """
    w = word.strip().lower()
    if not w or w in lexicon.FUNCTION_WORDS:
        return None
    found = lexicon.parts_of_speech(w)
    if found:
        return found
    gathered: set[str] = set()
    for base in lexicon.lemmas(w):
        if base == w or base in lexicon.FUNCTION_WORDS:
            continue
        gathered |= lexicon.parts_of_speech(base) or set()
    return frozenset(gathered) if gathered else None


def _labels(pos: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(lexicon.POS_KO[p] for p in pos)


def _join(labels: tuple[str, ...]) -> str:
    """'명사' / '명사·동사'. 넷 다 '사' 로 끝나 뒤에 붙는 조사가 흔들리지 않는다."""
    return "·".join(labels)


def pos_hint(item: ClozeItem) -> PosHint | None:
    """문제와 함께 내보낼 품사 힌트. 말할 근거가 없으면 None.

    None 이 되는 경우가 실제로 있다 — 기능어 빈칸(`and`, `the`)과 WordNet 이
    모르는 낱말이다(서빙 가능한 2,950개 중 53개). 그때는 **힌트를 지어내지 않고
    빼는 것**이 맞다. 힌트 없는 빈칸도 빈칸으로 성립한다.

    정답 낱말 자체는 절대 나가지 않는다. 나가는 것은 품사 이름뿐이다.
    """
    word_pos = pos_of(item.word)
    narrowed = narrow(item.sentence, word_pos)
    if narrowed is None:
        return None
    pos, source = narrowed
    ordered = order_pos(pos)
    labels = _labels(ordered)
    if source == "slot":
        # 자리에서 좁혔으니 자리를 말해도 된다.
        text = f"여기엔 {_join(labels)}가 들어가요."
    elif len(labels) == 1:
        # 자리는 모르고 낱말만 안다. "여기엔 명사가 들어가요"는 아는 것보다 더 주장하는 말이다.
        text = f"이 낱말은 {labels[0]}예요."
    else:
        # 사용자가 원한 바로 그 학습 지점 — "이 단어가 동사도 되고 명사도 되는지".
        text = "이 낱말은 " + "로도 ".join(labels) + "로도 써요."
    return PosHint(pos=ordered, labels_ko=labels, text_ko=text, source=source)


@dataclass(frozen=True)
class SpellHint:
    """빈칸을 못 채우는 학습자에게 **한 걸음씩** 내주는 철자 단서.

    왜 필요한가 — 지금 단서로는 알파벳을 못 읽는 사람에게 과제가 성립하지 않는다.
    빈칸이 주는 것은 낱말 뜻·문장 해석·문형·품사뿐이고 넷 다 **한국어**다. 영어를
    아예 모르는 사람은 뜻을 다 알고도 첫 글자를 못 적는다. 그 사람에게 빈칸은
    문제가 아니라 벽이다.

    왜 한 번에 다 주지 않는가 — 다 주면 연습이 아니라 베껴 쓰기가 된다. 그래서
    단계로 나누고, 어느 단계까지 볼지는 학습자가 정한다.

    `shape` 는 아직 안 드러난 글자를 밑줄로 둔 모양이다(`s _ _ _`). 글자가 아닌
    것(아포스트로피·하이픈)은 철자가 아니라 짜임이라 처음부터 보여 준다.
    """

    step: int
    label_ko: str
    text_ko: str
    shape: str


# 글자 수를 세는 우리말. '네 글자' 처럼 고유어로 센다.
#
# 열여섯까지 있는 이유: 서빙 가능한 답 중 가장 긴 것이 열여섯 글자다. 열둘에서
# 끊어 뒀더니 `multi-language` 가 "13 글자예요" 로 나왔다 — 앞뒤가 다 우리말인데
# 숫자만 아라비아 숫자라 눈에 걸린다.
_COUNT_KO: tuple[str, ...] = (
    "", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열",
    "열한", "열두", "열세", "열네", "열다섯", "열여섯", "열일곱", "열여덟",
    "열아홉", "스무",
)


def _count_ko(n: int) -> str:
    return _COUNT_KO[n] if n < len(_COUNT_KO) else str(n)


# 알파벳을 우리말로 읽었을 때 **받침으로 끝나는** 것. 엘·엠·엔·알 넷뿐이다.
# 나머지는 에이·비·에프(프)·에이치(치)·에스(스)처럼 모음이나 받침 없는 글자로 끝난다.
_FINAL_CONSONANT = frozenset("lmnr")

# 그중 받침이 ㄹ 인 것. '으로' 는 ㄹ 뒤에 붙지 않는다 — '엘로'·'알로' 이지
# '엘으로' 가 아니다. 그래서 조사 둘이 서로 다른 집합을 본다.
_FINAL_RIEUL = frozenset("lr")


def _with_ro(letters: str) -> str:
    """'a 로' / 'n 으로' / 'l 로'. 여러 글자면 마지막 글자가 정한다.

    학습자가 영어를 아예 모른다는 전제로 쓰는 문구라 조사가 어긋나면 바로 눈에 띈다.
    """
    tail = letters[-1:].lower()
    ro = "으로" if tail in _FINAL_CONSONANT and tail not in _FINAL_RIEUL else "로"
    return f"'{letters}' {ro}"


def _with_yeyo(letters: str) -> str:
    """'a 예요' / 'n 이에요'. 받침이 ㄹ 이어도 이쪽은 '이에요' 다(엘이에요)."""
    tail = letters[-1:].lower()
    return f"'{letters}' {'이에요' if tail in _FINAL_CONSONANT else '예요'}"


def _upto(answer: str, revealed: int) -> str:
    """앞에서부터 `revealed` **글자**까지의 조각. 글자가 아닌 것은 안 센다.

    `answer[:revealed]` 로 자르면 안 된다. 그건 **문자**를 세는 것이라 하이픈·
    아포스트로피가 앞쪽에 있으면 어긋난다 — `t-shirt` 를 `answer[:3]` 하면
    `t-s` 가 나와 "앞 세 글자" 라고 말하면서 실제로는 두 글자만 준다.
    """
    seen = 0
    out: list[str] = []
    for ch in answer:
        if ch.isalpha():
            if seen >= revealed:
                break
            seen += 1
        out.append(ch)
    # 끝에 붙은 하이픈·아포스트로피는 떼어 낸다. `e-` 를 "앞 두 글자" 라고 부르면
    # 하이픈을 글자로 세는 셈이고, 이 모듈은 그것을 철자가 아니라 짜임으로 본다.
    return "".join(out).rstrip("-'’")


def _shape(answer: str, revealed: int) -> str:
    """앞에서부터 `revealed` 글자만 드러낸 모양. 나머지는 밑줄이다.

    `_upto` 와 **같은 규칙으로 센다.** 둘이 어긋나면 "앞 세 글자는 't-s' 예요"
    라고 말해 놓고 `t - s h _ _ _` 를 보여 주는 일이 생긴다(실제로 그랬다).
    """
    seen = 0
    out: list[str] = []
    for ch in answer:
        if not ch.isalpha():
            # 아포스트로피·하이픈은 철자가 아니라 짜임이다. 가리면 오히려 어렵다.
            out.append(ch)
            continue
        seen += 1
        out.append(ch if seen <= revealed else "_")
    return " ".join(out)


def spell_hints(item: ClozeItem) -> tuple[SpellHint, ...]:
    """이 빈칸에 줄 수 있는 철자 단서 전부. 짧은 답에는 적게 나온다.

    **정답을 통째로 드러내는 단계는 만들지 않는다.** 마지막 단계까지 봐도 최소
    한 글자는 밑줄로 남는다 — 그 한 글자를 학습자가 적어야 연습이 성립한다.
    그래서 두 글자짜리 답(`am`, `it`)은 글자 수 하나만 나온다. 거기서 첫 글자를
    주면 남는 것이 없다.

    답의 대소문자는 낮춘다. 문장 맨 앞이라 대문자인 것(`It`)을 그대로 보여 주면
    철자가 아니라 자리 때문에 생긴 모양을 철자로 가르치게 된다. 채점은 어차피
    대소문자를 가리지 않는다.
    """
    answer = item.answer.strip().lower()
    letters = sum(1 for ch in answer if ch.isalpha())
    if letters < 2:
        # 한 글자짜리 답(`a`, `I`)은 글자 수가 곧 정답이다.
        return ()

    out = [
        SpellHint(
            step=1,
            label_ko="글자 수",
            text_ko=f"{_count_ko(letters)} 글자예요.",
            shape=_shape(answer, 0),
        )
    ]
    if letters >= 3:
        out.append(
            SpellHint(
                step=2,
                label_ko="첫 글자",
                text_ko=f"{_with_ro(_upto(answer, 1))} 시작해요.",
                shape=_shape(answer, 1),
            )
        )
    if letters >= 5:
        half = letters // 2
        out.append(
            SpellHint(
                step=3,
                label_ko="앞 절반",
                text_ko=f"앞 {_count_ko(half)} 글자는 {_with_yeyo(_upto(answer, half))}.",
                shape=_shape(answer, half),
            )
        )
    return tuple(out)


def grade(item: ClozeItem, said: str) -> ClozeResult:
    """학습자가 말하거나 적은 답을 채점한다. 사전이 없어도 동작한다.

    사다리 순서가 곧 이 연습장의 교육 순서다. 위로 갈수록 "거의 맞았다"이고
    아래로 갈수록 "무엇을 몰라서 틀렸는지"가 달라진다.

        correct    → 그대로 맞음. 구·절이면 머리 낱말이 정답과 같음(`a pen`)
        wrong_form → 낱말은 맞고 형태가 틀림 (`borrowing`)
        right_pos  → 다른 낱말인데 품사가 겹침 (`cup` 자리에 `plate`)
        wrong_pos  → 다른 낱말이고 품사도 다름 (`cup` 자리에 `drink`)
        not_a_word → 사전에 없음
        wrong_word → 위 어디에도 못 넣음. 품사를 **모를 때**만 여기로 온다

    `wrong_word` 를 없애지 않고 남긴 이유가 여기 있다. 기능어를 답으로 냈거나
    WordNet 이 모르는 낱말이면 품사를 비교할 수 없는데, 그때 억지로
    `right_pos`/`wrong_pos` 중 하나를 고르면 없는 근거로 판정하는 것이 된다.
    """
    heard = normalize(said)
    answer = normalize(item.answer)

    if not heard:
        return ClozeResult("empty", said, item.answer, "아직 아무 말도 못 들었어요.")

    if heard == answer:
        return ClozeResult("correct", said, item.answer, "맞아요!", head=heard)

    # 구·절로 답하면 머리 낱말 하나로 줄여서 같은 사다리에 태운다. 사용자가 명시적으로
    # 원한 것이다 — '펜 좀 빌려도 될까요?' 를 알면 pen · a pen · your pen 이 다 답이다.
    head, phrased = head_word(said)

    # 무엇을 보고 판정했는지 늘 드러낸다. 머리 낱말 뽑기는 틀릴 수 있고(slot.head_word 주석),
    # 틀렸을 때 학습자가 알아챌 수 있어야 한다.
    #
    # 아래 문구들은 **영어 낱말 뒤에 받침을 가리는 조사를 붙이지 않는다.** 'pen' 뒤는
    # '은', 'pizza' 뒤는 '는' 인데 철자만 보고 끝소리에 받침이 있는지 알 수 없다
    # (cake·pen·pizza). 그래서 은/는·이/가 대신 줄표와 '도'·'에서' 로 잇는다.
    seen = f" ('{said.strip()}' 에서 '{head}' 하나만 보고 판정했어요.)" if phrased else ""

    if head == answer:
        return ClozeResult("correct", said, item.answer, f"맞아요!{seen}", head=head)

    # 원형이 같으면 단어는 맞고 형태가 틀린 것이다. 이 앱이 가장 가르치고 싶은 자리다.
    if lexicon.same_lemma(head, answer):
        return ClozeResult(
            "wrong_form",
            said,
            item.answer,
            f"단어는 맞아요. 형태만 달라요 — 여기서는 '{item.answer}' 예요.{seen}",
            head=head,
        )

    # 사전에 없는 말은 오답이라기보다 잘못 들었을 가능성이 크다. 음성 입력에서 특히.
    if lexicon.known(head) is False:
        return ClozeResult(
            "not_a_word",
            said,
            item.answer,
            f"'{head}' 로 들었는데 그런 단어가 없어요. 다시 말해 볼까요?{seen}",
            head=head,
        )

    said_pos = pos_of(head)
    target = narrow(item.sentence, pos_of(item.word))
    if said_pos and target:
        wanted, source = target
        said_ordered = order_pos(said_pos)
        said_label = _join(_labels(said_ordered))
        wanted_label = _join(_labels(order_pos(wanted)))
        if said_pos & wanted:
            # "품사는 맞아요" 까지만 말한다. 이 문장에서 **뜻이 통하는지**는 모른다 —
            # WordNet 은 banana 가 명사인 건 알아도 'Can I borrow your ____?' 에
            # 어울리는지는 모른다. 그 말을 하면 거짓이 된다.
            #
            # 그리고 **겹치는 품사만** 말한다. 'pen' 의 품사는 명사·동사지만 이 자리에서
            # 겹친 것은 하나뿐이다. 전부 늘어놓으면 어느 쪽 때문에 맞다는 건지 모른다.
            shared_label = _join(_labels(order_pos(said_pos & wanted)))
            message = (
                f"품사는 맞아요 — 여기엔 {wanted_label}가 들어가고 '{head}' 도 {shared_label}로 써요. "
                f"다만 여기서 쓰는 낱말은 '{item.answer}' 예요."
                if source == "slot"
                else f"'{head}' 도 {shared_label}로 써요 — 정답 낱말과 품사가 겹쳐요. "
                f"여기서는 '{item.answer}' 예요."
            )
            return ClozeResult(
                "right_pos", said, item.answer, message + seen, head=head, said_pos=said_ordered
            )
        message = (
            f"'{head}' — {said_label}예요. 여기엔 {wanted_label}가 들어가요. "
            f"여기서는 '{item.answer}' 예요."
            if source == "slot"
            else f"'{head}' — {said_label}예요. 정답 낱말은 {wanted_label}고요. "
            f"여기서는 '{item.answer}' 예요."
        )
        return ClozeResult(
            "wrong_pos", said, item.answer, message + seen, head=head, said_pos=said_ordered
        )

    # 품사를 비교할 수 없는 경우. 기능어를 냈거나 사전이 모르는 낱말이다.
    return ClozeResult(
        "wrong_word",
        said,
        item.answer,
        f"다른 단어예요. 여기서는 '{item.answer}' 예요.{seen}",
        head=head,
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
