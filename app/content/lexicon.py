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

WordNet 이 모르는 일상어(`americano`, `latte`, `wifi`)는 Wiktionary 로 확인해
`data/lexicon_extra.yaml` 에 출처와 함께 담아 두고 여기서 함께 본다. 만들어 넣는 게
아니라 **조회해서 적어 둔 것**이다 — scripts/verify_words.py 가 만든다.

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
import threading
from functools import lru_cache
from pathlib import Path

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

# 순우리말 품사 이름도 같은 태그로 받는다. **읽어 주기만 하고 내보내지는 않는다** —
# 이 자료의 표기는 한자어로 통일되어 있고(명사 1441 : 이름씨 0) 지적 메시지도 POS_KO
# 를 쓴다. 그런데도 여기 두는 이유는 검사기가 눈멀지 않게 하기 위해서다:
# 품사 단정 검사가 `(명사|동사|형용사|부사)` 만 알던 동안 "'name'은 이름씨로만 쓰여요"
# 는 통째로 지나갔다. 표기를 통일한 뒤에도 누군가 순우리말로 다시 쓰면 같은 구멍이
# 다시 열리므로, 자료가 아니라 **검사기 쪽에서** 막아 둔다.
KO_POS.update(
    {
        "이름씨": POS_NOUN,
        "움직씨": POS_VERB,
        "그림씨": POS_ADJ,
        "어찌씨": POS_ADV,
    }
)

# 품사 단정 검사의 정규식이 쓰는 대안 목록. 한자어와 순우리말을 함께 잡는다.
POS_KO_ALTERNATION = "|".join(sorted(KO_POS, key=len, reverse=True))

_wordnet = None
_load_failed = False

# nltk 의 WordNet 리더는 **스레드 안전하지 않다.** 코퍼스가 zip 안에 있고 파일
# 핸들 하나를 공유해서, 두 스레드가 동시에 조회하면 `assert self.fp is None` 로
# 죽는다(단어 확인 스크립트를 4개 스레드로 돌리다 실제로 겪었다).
#
# 부르는 쪽은 이미 여럿이 병렬이다 — 배치 생성기(스레드 4개), FastAPI 요청 스레드풀.
# 그래서 잠금은 여기서 건다. 조회 결과는 lru_cache 로 남으므로 두 번째부터는
# 잠금 앞까지 오지도 않는다.
_lock = threading.Lock()


def _corpus():
    """WordNet 리더를 한 번만 연다. 없으면 None 을 돌려주고 다시 시도하지 않는다."""
    global _wordnet, _load_failed
    if _wordnet is not None or _load_failed:
        return _wordnet
    with _lock:
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


# ---------------------------------------------------------------------------
# WordNet 이 모르는 실재어
#
# WordNet 2006년 판이라 그 뒤에 자리 잡은 일상어를 모른다 — `americano`, `latte`,
# `smoothie`, `wifi`, `charger`. 이 앱의 첫 시나리오가 카페 주문인데 `americano` 를
# "없는 단어"로 판정하면, 빈칸 채점이 학습자에게 "그런 단어가 없어요"라고 말한다.
#
# 그렇다고 코드에 단어를 적어 넣으면 그게 곧 지어낸 사전이 된다. 그래서 **확인한
# 것만** 파일에 담고, 항목마다 Wiktionary 출처를 함께 남긴다
# (scripts/verify_words.py 가 조회해서 만든다. 사람이 손으로 늘리지 않는다).
_EXTRA_PATH = Path(__file__).parent / "data" / "lexicon_extra.yaml"
_extra: dict[str, dict] | None = None


def extra_lexicon() -> dict[str, dict]:
    """WordNet 밖에서 확인된 표제어들. 파일이 없으면 빈 사전."""
    global _extra
    if _extra is None:
        try:
            import yaml

            loaded = yaml.safe_load(_EXTRA_PATH.read_text(encoding="utf-8")) or {}
            _extra = {str(k).strip().lower(): dict(v or {}) for k, v in loaded.items()}
        except FileNotFoundError:
            _extra = {}
        except Exception as exc:  # 깨진 파일 하나로 앱이 안 뜨면 안 된다
            logger.warning("추가 사전을 못 읽었습니다: %s", exc)
            _extra = {}
    return _extra


# ---------------------------------------------------------------------------
# 바깥에서 만든 등급표
#
# 이 앱의 level(A1/A2/B1)은 LLM 이 붙인다. 프롬프트에 "부풀리지 말라"고 적어 놓고도
# 2,801개 중 1,899개(68%)가 B1 으로 나왔다. 모델이 붙인 등급을 모델로 검사하면
# 같은 편향이 두 번 들어가므로, 밖에서 만든 표와 대조한다.
#
# CEFR-J Vocabulary Profile 1.5 (Tono Laboratory, TUFS). content/prepare_cefrj.py 가
# 표제어와 레벨만 옮겨 온다 — 뜻·예문은 가져오지 않는다.
_LEVELS_PATH = Path(__file__).parent / "data" / "cefrj_levels.csv"
_levels: dict[str, str] | None = None

LEVEL_ORDER = ("A1", "A2", "B1", "B2", "C2")


def reference_level(word: str) -> str | None:
    """바깥 등급표가 보는 이 단어의 레벨. 표에 없으면 None("모른다")."""
    global _levels
    if _levels is None:
        table: dict[str, str] = {}
        try:
            for line in _LEVELS_PATH.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or line.startswith("word,"):
                    continue
                head, _, level = line.partition(",")
                level = level.strip().upper()
                if head and level in LEVEL_ORDER:
                    table[head.strip().lower()] = level
        except FileNotFoundError:
            logger.info("등급표가 없어 레벨 대조를 건너뜁니다: %s", _LEVELS_PATH)
        except Exception as exc:
            logger.warning("등급표를 못 읽었습니다: %s", exc)
        _levels = table
    w = word.strip().lower()
    if w in _levels:
        return _levels[w]
    # 굴절형은 원형으로 한 번 더 본다 — 표는 원형만 담는다.
    for base in lemmas(w):
        if base in _levels:
            return _levels[base]
    return None


def level_distance(ours: str, theirs: str) -> int | None:
    """두 등급이 몇 칸 떨어져 있는가. 모르는 등급이 섞이면 None."""
    try:
        return LEVEL_ORDER.index(ours.upper()) - LEVEL_ORDER.index(theirs.upper())
    except ValueError:
        return None


@lru_cache(maxsize=8192)
def parts_of_speech(word: str) -> frozenset[str] | None:
    """이 단어가 가질 수 있는 품사들. 사전에 없거나 사전이 없으면 None.

    None 과 빈 집합은 다르다. None 은 "모른다", 빈 집합은 있을 수 없다(그래서
    안 돌려준다). 이 구분이 무너지면 사전에 없는 단어를 전부 결함으로 만든다.

    WordNet 이 먼저다. WordNet 이 모르는 말만 추가 사전을 본다 — 두 사전이 겹칠 때
    어느 쪽을 믿을지 고민할 일을 아예 만들지 않는다.
    """
    w = word.strip().lower()
    if not w:
        return None
    wn = _corpus()
    if wn is not None:
        with _lock:
            found = {pos for pos in ALL_POS if wn.synsets(w, pos=pos)}
        if found:
            return frozenset(found)
    tags = {t for t in (extra_lexicon().get(w, {}).get("pos") or []) if t in ALL_POS}
    return frozenset(tags) if tags else None


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


@lru_cache(maxsize=8192)
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
    if w in extra_lexicon():
        return True  # WordNet 밖에서 확인된 말 (americano, latte, wifi ...)
    wn = _corpus()
    if wn is None:
        return None
    with _lock:
        if wn.synsets(w):
            return True
        if any(wn.morphy(w, pos) for pos in ALL_POS):
            return True
    # 하이픈 합성어는 조각이 모두 실재하면 실재로 본다 (grown-up, e-mail).
    if "-" in w:
        parts = [p for p in w.split("-") if p]
        return bool(parts) and all(known(p) for p in parts)
    return False


_VOWELS = "aeiou"


def _doubles_final_consonant(base: str) -> bool:
    """-ed·-ing 를 붙일 때 끝 자음을 겹치는 형태인가. run -> running, stop -> stopped.

    자음-모음-자음으로 끝나면 겹친다. w·x·y 는 겹치지 않는다(play -> playing).
    강세까지 보지는 않으므로 visit -> visitted 를 만들어 낸다 — 그래도 되는 이유는
    이 함수가 **형태를 만들어 내는 쪽이 아니라 되돌려 확인하는 쪽**에만 쓰이기
    때문이다. 없는 형태를 만들면 후보가 하나 덜 붙을 뿐이고, 있는 형태를 만들면
    가짜 원형이 붙는다. 그래서 넉넉한 쪽이 아니라 좁은 쪽으로 틀린다.
    """
    if len(base) < 3:
        return False
    c1, v, c2 = base[-3], base[-2], base[-1]
    return c1 not in _VOWELS and v in _VOWELS and c2 not in _VOWELS and c2 not in "wxy"


def _regular_forms(base: str) -> set[str]:
    """원형에서 규칙적으로 만들어지는 굴절형들. 규칙 밖의 것은 만들지 않는다."""
    if len(base) < 3:
        return set()
    forms: set[str] = set()
    hard_y = base.endswith("y") and base[-2] not in _VOWELS

    # 복수·3인칭 -s
    if base.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(base + "es")
    elif hard_y:
        forms.add(base[:-1] + "ies")
    elif base.endswith("o"):
        forms.update({base + "s", base + "es"})
    else:
        forms.add(base + "s")

    # 과거 -ed, 진행 -ing, 비교급 -er/-est
    if base.endswith("e"):
        forms.update({base + "d", base[:-1] + "ing", base + "r", base + "st"})
    elif hard_y:
        stem = base[:-1]
        forms.update({stem + "ied", base + "ing", stem + "ier", stem + "iest"})
    elif _doubles_final_consonant(base):
        twin = base + base[-1]
        forms.update({twin + "ed", twin + "ing", twin + "er", twin + "est"})
    else:
        forms.update({base + "ed", base + "ing", base + "er", base + "est"})
    return forms


def _extra_bases(word: str, pos: str, wn) -> set[str]:
    """morphy 가 놓친 원형들. WordNet 의 비공개 경로를 쓰므로 실패해도 넘어간다.

    왜 필요한가
    -----------
    `wn.morphy` 는 **후보를 하나만** 돌려주고, 표면형 자체가 표제어이면 거기서
    멈춘다. `years`·`minutes`·`instructions`·`days` 는 WordNet 에 독립 표제어라
    (`minutes` = 회의록) 원형 `year`·`minute` 를 영영 못 준다. 빈칸 채우기에서
    이건 "형태만 틀렸다"를 "다른 단어다"로 바꾼다 — 이 앱이 가르치겠다는 바로 그
    자리다. 예문 3,641개 토큰 중 42개가 여기 걸렸다.

    왜 그대로 다 받지 않는가
    ------------------------
    `wn._morphy` 는 접미사를 떼는 규칙을 전부 적용해 후보를 만든다. 그래서 진짜
    원형(years -> year) 옆에 가짜(as -> a, rated -> rat, serves -> serf, uses -> us)가
    같이 나온다. 42개 중 11개가 가짜였다.

    그래서 출처를 갈라 다르게 다룬다.
    - **예외 목록**(사전이 손으로 적어 둔 불규칙: teeth -> tooth, best -> good)은 믿는다.
    - **규칙**으로 나온 것은 원형에서 굴절형을 **되만들어** 표면형이 나오는지 본다.
      rat 의 과거는 ratted 라서 rated 가 안 나오고, serf 의 복수는 serfs 라서
      serves 가 안 나온다. 이 검사로 가짜 11개 중 10개가 걸러진다
      (남는 건 species -> specie 하나인데, 실제로 관련된 단어다).
    """
    try:
        candidates = [str(b) for b in wn._morphy(word, pos)]
        exceptions = {str(b) for b in wn._exception_map[pos].get(word, ())}
    except Exception:  # nltk 내부 구조는 바뀔 수 있다. 바뀌면 예전 동작으로 돌아간다.
        return set()

    found: set[str] = set()
    for base in candidates:
        if base == word:
            continue
        if base in exceptions:
            found.add(base)
            continue
        # 기능어와 두 글자 이하는 규칙이 만들어 낸 껍데기다 (as -> a, us -> u).
        if base in FUNCTION_WORDS or len(base) < 3:
            continue
        if word in _regular_forms(base):
            found.add(base)
    return found


@lru_cache(maxsize=8192)
def lemmas(word: str) -> frozenset[str]:
    """이 단어가 가질 수 있는 원형들. 사전에 없으면 자기 자신만.

    품사마다 원형이 다르다 — `listening` 은 명사로는 그대로지만 동사로는 `listen`
    이다. 하나만 고르면(첫 품사) 틀린 쪽을 집는다. 그래서 전부 모아 집합으로 준다.

    빈칸 채우기에서 **'뜻은 맞는데 형태가 틀린' 답**을 가려내는 데 쓴다. 이 프로젝트의
    전제가 "왕초보는 뜻이 아니라 형태에서 틀린다"이므로, 그 둘을 같은 오답으로 묶으면
    정작 가르쳐야 할 것을 못 가르친다.
    """
    w = word.strip().lower().strip("-'’")
    if not w:
        return frozenset()
    found = {w}
    wn = _corpus()
    if wn is not None:
        with _lock:  # nltk 리더는 스레드 안전하지 않다 — _corpus 의 주석 참고
            for pos in ALL_POS:
                base = wn.morphy(w, pos)
                if base:
                    found.add(str(base))
                found |= _extra_bases(w, pos, wn)
    return frozenset(found)


def same_lemma(a: str, b: str) -> bool:
    """두 단어가 같은 원형을 공유하는가. `borrowed` 와 `borrow` 는 참."""
    return bool(lemmas(a) & lemmas(b))
