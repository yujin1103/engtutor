"""영어 어휘 사실을 조회한다. LLM 을 부르지 않는다.

왜 사전을 따로 두는가
---------------------
생성된 콘텐츠가 실재하지 않는 단어를 가르치거나("'restaurate'가 있어요"),
품사를 잘못 단정하는("'name'은 명사로만 쓰여요") 일이 실제로 있었다.
NGSL 2,801개 전수 조사에서 없는 단어 13건이 나왔다 — docs/hallucinations.md.

이걸 LLM 으로 검사하면 검사기도 환각한다. 그래서 사전 조회로만 판정한다.

무엇을 쓰는가
-------------
WordNet(CLAUDE.md §3.5 가 교차 확인용으로 지정한 공개 자원). 표제어의 품사
집합을 준다 — `name` 이 명사이자 동사라는 사실이 여기 있다.

한계를 알고 써야 한다
---------------------
WordNet 은 **내용어 사전**이다. 기능어(although, whereas), 약어(app, pdf),
고유명사, 최신 어휘가 없다. 그래서 "없으면 가짜"가 아니라 **"없으면 모른다"** 로
다룬다. `parts_of_speech` 가 None 을 돌려주면 판정하지 않는다.

가산성(countable/uncountable)은 WordNet 에 아예 없다. 'advice 는 불가산' 같은
주장은 여기서 판정할 수 없고, Wiktionary 를 붙여야 가능하다.

없어도 앱은 돌아가야 한다
-------------------------
nltk 나 코퍼스가 없는 환경에서는 `available()` 이 False 가 되고 모든 조회가
None 을 준다. 선별기는 해당 검사를 조용히 건너뛴다 — 사전이 없다고 검수 UI 가
못 뜨면 안 된다.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# WordNet 품사 태그. a(형용사)와 s(부속 형용사)는 학습자에게 같은 것이라 합친다.
POS_NOUN = "n"
POS_VERB = "v"
POS_ADJ = "a"
POS_ADV = "r"
ALL_POS = (POS_NOUN, POS_VERB, POS_ADJ, POS_ADV)

# 학습자에게 보이는 이름. 지적 메시지에 그대로 나간다.
POS_KO: dict[str, str] = {
    POS_NOUN: "명사",
    POS_VERB: "동사",
    POS_ADJ: "형용사",
    POS_ADV: "부사",
}
KO_POS: dict[str, str] = {v: k for k, v in POS_KO.items()}

_wordnet = None
_load_failed = False


def _corpus():
    """WordNet 리더를 한 번만 연다. 없으면 None 을 돌려주고 다시 시도하지 않는다."""
    global _wordnet, _load_failed
    if _wordnet is not None or _load_failed:
        return _wordnet
    try:
        from nltk.corpus import wordnet

        wordnet.synsets("test")  # 코퍼스가 실제로 내려받아져 있는지 여기서 드러난다
        _wordnet = wordnet
    except Exception as exc:  # ImportError, LookupError 둘 다
        _load_failed = True
        logger.info("WordNet 을 못 열어 어휘 검사를 건너뜁니다: %s", exc)
    return _wordnet


def available() -> bool:
    """사전을 쓸 수 있는가. 검사를 건너뛸지 판단하는 데 쓴다."""
    return _corpus() is not None


@lru_cache(maxsize=8192)
def parts_of_speech(word: str) -> frozenset[str] | None:
    """이 단어가 가질 수 있는 품사들. 사전에 없거나 사전이 없으면 None.

    None 과 빈 집합은 다르다. None 은 "모른다", 빈 집합은 있을 수 없다(그래서
    안 돌려준다). 이 구분이 무너지면 사전에 없는 단어를 전부 결함으로 만든다.
    """
    wn = _corpus()
    if wn is None:
        return None
    w = word.strip().lower()
    if not w:
        return None
    found = {pos for pos in ALL_POS if wn.synsets(w, pos=pos)}
    return frozenset(found) if found else None


# WordNet 이 담지 않는 닫힌 부류. 실재하는데 사전에 없어서, 존재 검사에 그대로
# 쓰면 the·of·although 가 전부 "없는 단어"로 잡힌다. 실제로 오탐 40건이 이것이었다.
#
# 품사 조회에는 쓰지 않는다 — parts_of_speech 는 WordNet 만 본다. 존재와 품사는
# 다른 질문이고, 여기 있는 단어의 품사를 지어내면 품사 검사가 통째로 거짓이 된다.
FUNCTION_WORDS: frozenset[str] = frozenset(
    """
    a an the this that these those
    i you he she it we they me him her us them my your his its our their
    mine yours hers ours theirs myself yourself himself herself itself
    ourselves yourselves themselves
    am is are was were be been being do does did done doing have has had having
    will would shall should can could may might must ought need dare
    and or but nor so yet for because if unless although though while whereas whilst
    when where how why what which who whom whose whether than then
    to of in on at by with from into onto upon about over under above below
    between among through during before after since until against across behind
    beside beyond within without toward towards versus via per
    not no there here too very just only also even still already ever never
    some any each every all both few many much more most less least other another
    such one none
    something anything nothing everything someone anyone everyone nobody
    somebody anybody everybody somewhere anywhere everywhere nowhere
    somehow anyhow anyway somewhat elsewhere else
    please thanks thank hello hi hey bye ok okay yes yeah yep no nope
    let lets gonna wanna gotta cannot
    s t re ve ll d m
    """.split()
)

# 축약형의 뒷조각. don't -> don + t 처럼 쪼개져 들어온다.
_CONTRACTION = re.compile(r"^[a-z]+['’](s|t|re|ve|ll|d|m)$")


def known(word: str) -> bool | None:
    """이 단어가 실재하는가. 모르면 None.

    굴절형은 원형으로 되돌려 보고, 기능어와 축약형은 사전을 보지 않고 통과시킨다.
    None 과 False 는 다르다 — None 은 사전 자체가 없다는 뜻이다.
    """
    w = word.strip().lower().strip("-'’")
    if not w:
        return None
    if w in FUNCTION_WORDS or _CONTRACTION.match(word.strip().lower()):
        return True
    wn = _corpus()
    if wn is None:
        return None
    if wn.synsets(w):
        return True
    if any(wn.morphy(w, pos) for pos in ALL_POS):
        return True
    # 하이픈 합성어는 조각이 모두 실재하면 실재로 본다 (grown-up, e-mail).
    if "-" in w:
        parts = [p for p in w.split("-") if p]
        return bool(parts) and all(known(p) for p in parts)
    return False
