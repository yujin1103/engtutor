"""답한 뒤에 보여 줄 **설명 카드**를 조립한다. LLM 을 부르지 않는다.

이 파일이 따로 있는 이유
------------------------
`cloze.py` 는 판정만 한다 — 사전이 없어도, DB 가 없어도 돈다. 설명 카드는 반대로
`words` 행 전체와 다른 낱말들까지 봐야 해서 DB 세션이 필요하다. 둘을 한 파일에
두면 판정 시험을 돌리는 데 DB 가 필요해진다.

무엇을 말해도 되는가
--------------------
여기 나가는 문장은 전부 세 군데에서만 온다.

1. `words` 행 — meaning_ko, example, example_ko, pattern, usage_note, confused_with
2. WordNet 품사 — `lexicon.parts_of_speech`
3. 위 둘을 조합한 **셈** — 같은 장면·같은 품사 낱말 고르기

**WordNet 은 품사를 알지 뜻이 통하는지는 모른다.** `banana` 가 명사인 건 알아도
`Can I borrow your ____?` 에 어울리는지는 모른다. 그래서 후보 목록의 이름표가
"이 자리에 올 수 있어요"가 아니라 **"같은 장면에서 쓰는 명사예요"** 다. 앞의 것은
거짓이고 뒤의 것은 참이다. 이 앱은 학습자를 가르치므로 콘텐츠의 거짓은 품질
문제가 아니라 결함이다(docs/hallucinations.md).

검수 안 된 설명을 어떻게 다루는가
---------------------------------
`usage_note` 와 `confused_with` 는 3,245개 중 4개만 승인됐고, 확인된 환각 13건이
**전부 그 두 칸에** 있었다. 그런데 값어치도 그 두 칸에 몰려 있다 — "빌려주는 쪽은
lend 예요" 같은 말이 이 앱을 챗봇 래퍼가 아니게 만든다.

숨기면 안전하지만 4개짜리 앱이 되고, 그냥 보여 주면 학습자가 거짓을 외운다.
그래서 **자리를 갈랐다**: 승인된 항목의 설명만 `usage_note`/`confused_with` 로
나가고, 승인 전 설명은 `unverified` 라는 다른 상자에 들어간다. 화면이 실수로
같은 자리에 그릴 수 없게 **구조로** 막은 것이다 — 플래그 하나로 두면 언젠가
누군가 그 플래그를 안 본다. 상자에는 "아직 사람이 확인하지 않았어요"라는 문구가
같이 들어 있어서, 화면이 그걸 빼먹어도 텍스트 자체가 스스로를 밝힌다.

기본 사실(뜻·예문·해석·문형·품사)은 갈라 두지 않았다. 환각 13건이 하나도 여기
있지 않았고, 빈칸 문제가 이미 `example` 과 `word` 를 그대로 쓰고 있어서
설명 카드만 더 엄격하게 굴 이유가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..content import lexicon
from ..content.screening import screen
from .cloze import ClozeItem, PosHint, pos_hint, pos_of, readable_ko
from .slot import narrow, order_pos

# 장면 이름의 한국어. `content.generator.TOPIC_SCENES` 와 같은 목록인데 저쪽은
# 모델에게 줄 영어 한 줄이고 이쪽은 학습자가 읽을 이름이다. 배치 생성 모듈을
# API 경로에서 임포트하지 않으려고 따로 둔다.
TOPIC_KO: dict[str, str] = {
    "cafe": "카페",
    "fastfood": "패스트푸드",
    "food": "식당",
    "grocery": "장보기",
    "transport": "대중교통",
    "airport": "공항",
    "hotel": "호텔",
    "shopping": "쇼핑",
    "money": "계산·환불",
    "health": "병원·약국",
    "daily": "날씨·일정",
    "home": "집",
    "talk": "잡담",
    "number": "숫자",
    "weekday": "요일",
    "month": "날짜",
    "ordinal": "순서",
}


def topic_ko(topic: str | None) -> str | None:
    """장면 이름을 한국어로. 모르는 이름은 그대로 — 새 팩이 들어와도 화면이 안 깨진다."""
    if not topic:
        return None
    return TOPIC_KO.get(topic, topic)


@dataclass(frozen=True)
class Alternative:
    """같은 자리 후보 하나. `reviewed` 가 False 면 뜻도 아직 사람이 안 본 것이다."""

    word: str
    meaning_ko: str
    pos_ko: tuple[str, ...]
    reviewed: bool


@dataclass(frozen=True)
class Alternatives:
    """후보 묶음. `label_ko` 가 **무엇을 근거로 모았는지**를 그대로 말한다.

    근거를 이름표에서 떼면 "이 자리에 올 수 있어요"가 되고, 그건 우리가 모르는
    사실이다. 그래서 목록과 이름표를 한 객체로 묶어 둔다.
    """

    basis: str  # "topic" | "rank" | "level"
    label_ko: str
    words: tuple[Alternative, ...] = ()


@dataclass(frozen=True)
class Unverified:
    """아직 사람이 확인하지 않은 설명. 승인된 항목이면 이 상자는 만들어지지 않는다."""

    usage_note: str | None
    confused_with: tuple[str, ...]
    note_ko: str = "아직 사람이 확인하지 않은 설명이에요. 참고만 하세요."

    @property
    def empty(self) -> bool:
        return not self.usage_note and not self.confused_with


@dataclass(frozen=True)
class Explanation:
    """빈칸 하나에 대해 우리가 정직하게 말할 수 있는 전부."""

    word: str
    answer: str
    meaning_ko: str
    example: str
    example_ko: str | None
    pattern: str | None
    topic: str | None
    topic_ko: str | None
    pos: tuple[str, ...]
    pos_ko: tuple[str, ...]
    pos_text_ko: str | None
    reviewed: bool
    # 승인된 항목에서만 채워진다. 나머지는 `unverified` 로 간다.
    usage_note: str | None = None
    confused_with: tuple[str, ...] = ()
    unverified: Unverified | None = None
    alternatives: Alternatives | None = None
    hint: PosHint | None = None


def _pos_sentence(pos: tuple[str, ...]) -> str | None:
    """"이 낱말은 명사로도 동사로도 써요." — 사용자가 원한 학습 지점 그대로다.

    낱말 이름을 문장에 넣지 않는다. `'water' 는` 은 맞지만 `'towel' 는` 은 틀리고,
    영어 철자만 보고 끝소리에 받침이 있는지 결정론적으로 알 수 없다. 낱말은
    카드의 `word` 칸에 이미 있으니 문장에서는 "이 낱말"로 가리킨다.
    """
    if not pos:
        return None
    labels = [lexicon.POS_KO[p] for p in pos]
    if len(labels) == 1:
        return f"이 낱말은 {labels[0]}예요."
    return "이 낱말은 " + "로도 ".join(labels) + "로도 써요."


def alternatives_for(
    db, item: ClozeItem, *, track: str | None = None, limit: int = 6
) -> Alternatives | None:
    """같은 품사의 다른 낱말들. 낼 수 없으면 None — **지어내지 않는다.**

    후보는 전부 `words` 테이블에서 온다. 목록에 없는 낱말이 여기 나올 방법이 없다.
    그 위에 두 가지를 더 건다.

    - **품사가 겹칠 것.** 자리 규칙이 걸리면 좁혀진 품사로, 아니면 정답 낱말의
      품사 전부로 본다(`slot.narrow`).
    - **선별기 지적이 없을 것.** 후보의 `meaning_ko` 를 화면에 함께 띄우므로,
      빈칸으로 내보낼 수 없는 행의 뜻을 설명 카드로 우회해서 보여 주면 안 된다.
    - **뜻이 읽히는 한국어일 것.** `bagel` 의 뜻이 `백일(백面包)` 로 저장돼 있다.
      여섯 개를 **골라서** 내미는 자리라 읽을 수 없는 것을 끼워 넣을 이유가 없다.
      이제 같은 검사가 출제 문(`cloze.is_answerable`)에도 걸려 있다.

    이름표는 **구성원이 실제로 공유하는 품사**로 짓는다
    --------------------------------------------------
    예전에는 이름표를 정답 낱말의 품사 **전부**로 지어 놓고, 구성원은 "교집합만 비지
    않으면" 통과시켰다. 그래서 이름표가 구성원 대부분에 대해 거짓이었다 — 후보가 붙는
    2,813개 중 927개(33%)가 최소 한 구성원을 과장했다.

        as     → "비슷하게 자주 쓰는 명사·부사예요."
                 구성원: country(명사) · away(형용사·부사) · build(명사·동사)
        bitter → "같은 장면(카페)에서 쓰는 명사·동사·형용사·부사예요."
                 구성원: americano(명사)

    그래서 순서를 뒤집었다. 먼저 품사 하나(`shared`)를 **고르고**, 그 품사를 가진
    후보만 모은다. 어느 것을 고르느냐는 후보가 가장 많이 걸리는 쪽 — 전수에서
    목록이 짧아진 문제는 2,813개 중 1개였다(버킷을 자르기 전 후보 전체에서 고르므로
    대개 여섯 개가 그대로 남는다). 이름표에서 품사를 통째로 빼는 길도 있었지만,
    "같은 자리에 올 만한 낱말"이라는 이 목록의 값어치가 바로 품사라서 그쪽은
    목록 자체를 밍밍하게 만든다.

    마지막에 이름표를 **자른 뒤의 구성원**으로 다시 좁힌다. 여섯 개가 모두 명사이자
    동사면 "명사·동사예요"라고 더 말해도 되고, 그 말은 여전히 구성원 전부에 대해 참이다.

    순서는 **정답 낱말로 섞는다.** SQL 이 주는 순서를 그대로 쓰면 장면 어휘는
    rank 가 대부분 NULL 이라 알파벳순이 되고, 카페 문제를 몇 개를 풀든 늘
    americano·bagel·barista 만 본다. 같은 문제는 늘 같은 목록을 주되(시험이
    흔들리지 않게) 문제마다는 달라야 해서, 난수 씨앗을 낱말로 고정했다.
    """
    import random

    from ..db import crud

    target = narrow(item.sentence, pos_of(item.word))
    if target is None:
        return None  # 정답 낱말의 품사를 모른다. 같은 품사를 고를 기준이 없다.
    wanted, _ = target

    rows = crud.cloze_alternatives(
        db,
        word=item.word,
        topic=item.topic,
        rank=item.rank,
        level=item.level,
        # 트랙을 넘기지 않으면 토익 문제 밑에 카페 낱말이 붙는다. 빈도가 가깝다는
        # 근거 자체가 트랙 안에서만 성립한다(crud.cloze_alternatives 참고).
        **({"track": track} if track else {}),
    )
    picked: list[tuple[Alternative, frozenset[str]]] = []
    for row in rows:
        found = pos_of(row.word)
        if not found or not (found & wanted):
            continue
        if not row.reviewed and screen(row):
            continue
        meaning = (row.meaning_ko or "").strip()
        if not readable_ko(meaning):
            continue
        picked.append(
            (
                Alternative(
                    word=row.word,
                    meaning_ko=meaning,
                    pos_ko=tuple(lexicon.POS_KO[p] for p in order_pos(found)),
                    reviewed=bool(row.reviewed),
                ),
                frozenset(found & wanted),
            )
        )
    if not picked:
        return None

    # 이름표를 지을 품사를 먼저 고른다. 후보가 가장 많이 걸리는 쪽이고, 같으면
    # 늘 같은 순서(명사·동사·형용사·부사)에서 앞선 것 — 목록이 흔들리면 안 된다.
    ordered = order_pos(wanted)
    counts = {p: sum(1 for _, pos in picked if p in pos) for p in ordered}
    shared = max(ordered, key=lambda p: (counts[p], -ordered.index(p)))

    members = [pair for pair in picked if shared in pair[1]]
    random.Random(item.word).shuffle(members)
    members = members[:limit]

    # 자른 뒤의 구성원만 보고 이름표를 다시 좁힌다. `shared` 는 정의상 전원이 가지고
    # 있으니 이 교집합은 절대 비지 않는다.
    common = frozenset.intersection(*(pos for _, pos in members))
    labels = "·".join(lexicon.POS_KO[p] for p in order_pos(common))

    if item.topic:
        basis = "topic"
        label = f"같은 장면({topic_ko(item.topic)})에서 쓰는 {labels}예요."
    elif item.rank is not None:
        basis = "rank"
        label = f"비슷하게 자주 쓰는 {labels}예요."
    else:
        basis = "level"
        label = f"같은 단계({item.level})의 {labels}예요."
    return Alternatives(basis=basis, label_ko=label, words=tuple(alt for alt, _ in members))


def explain(db, row, item: ClozeItem, *, limit: int = 6) -> Explanation:
    """설명 카드 하나. `db` 가 None 이면 후보 목록만 빠지고 나머지는 그대로 나온다.

    설명(`usage_note`)에도 못 읽는 글자가 섞인 것이 있다 — 출제 가능 2,950개 중
    22개다(`remember` 의 설명에 키릴 문자, `pull` 에 `拽`, `chewy` 에 가나).
    뜻과 달리 **항목을 빼지 않고 그 칸만 뗀다.** 설명은 문제 본문에 나가지 않고
    답을 본 뒤에만 보이는 곁가지라, 이것 때문에 항목을 빼면 `she`·`need`·`sure`
    같은 흔한 낱말이 연습장에서 통째로 사라진다. 학습자가 못 읽는 글자를 보는 일은
    똑같이 막으면서 값은 안 치르는 쪽을 골랐다.
    """
    reviewed = bool(getattr(row, "reviewed", False))
    note = (getattr(row, "usage_note", "") or "").strip() or None
    if note and not readable_ko(note):
        note = None
    confused = tuple(str(c) for c in (getattr(row, "confused_with", None) or []) if str(c).strip())
    found = pos_of(item.word)
    pos = order_pos(found) if found else ()

    unverified = None if reviewed else Unverified(usage_note=note, confused_with=confused)
    if unverified is not None and unverified.empty:
        unverified = None

    return Explanation(
        word=item.word,
        answer=item.answer,
        meaning_ko=item.meaning_ko,
        example=item.example,
        example_ko=item.example_ko,
        pattern=item.pattern,
        topic=item.topic,
        topic_ko=topic_ko(item.topic),
        pos=pos,
        pos_ko=tuple(lexicon.POS_KO[p] for p in pos),
        pos_text_ko=_pos_sentence(pos),
        reviewed=reviewed,
        usage_note=note if reviewed else None,
        confused_with=confused if reviewed else (),
        unverified=unverified,
        alternatives=(
            None
            if db is None
            else alternatives_for(db, item, track=getattr(row, "track", None), limit=limit)
        ),
        hint=pos_hint(item),
    )
