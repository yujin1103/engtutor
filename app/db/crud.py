"""세션·턴·교정 읽고 쓰기. SQLAlchemy 의존은 이 파일 밖으로 새지 않게 한다."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import selectinload

from ..content import lexicon
from ..content.schemas import WordEntry, WordTip
from ..tutor.schemas import Correction, TurnResponse
from .models import TRACK_GENERAL, CorrectionRow, SessionRow, TurnRow, WordRow


def create_session(db: DbSession, *, session_id: str, scenario_id: str, level: str) -> SessionRow:
    row = SessionRow(id=session_id, scenario_id=scenario_id, level=level)
    db.add(row)
    db.flush()
    return row


def get_session(db: DbSession, session_id: str) -> SessionRow | None:
    stmt = (
        select(SessionRow)
        .where(SessionRow.id == session_id)
        .options(selectinload(SessionRow.turns).selectinload(TurnRow.corrections))
    )
    return db.execute(stmt).scalar_one_or_none()


def set_session_level(db: DbSession, session_id: str, level: str) -> None:
    """세션의 레벨을 바꾼다.

    레벨은 대화 중에도 바뀔 수 있다(app/main.py 의 `_resolve` 참고). 마지막에
    실제로 쓰인 값을 적어 둬야 리포트가 "이 세션은 몇 레벨이었나"를 맞게 적는다.
    """
    row = get_session(db, session_id)
    if row is not None:
        row.level = level


def list_sessions(db: DbSession, *, limit: int = 50) -> list[SessionRow]:
    stmt = select(SessionRow).order_by(SessionRow.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def _next_index(db: DbSession, session_id: str) -> int:
    stmt = select(func.coalesce(func.max(TurnRow.turn_index), -1)).where(
        TurnRow.session_id == session_id
    )
    return int(db.execute(stmt).scalar_one()) + 1


def record_turn(
    db: DbSession,
    *,
    session_id: str,
    user_text: str,
    turn: TurnResponse,
    input_mode: str = "text",
    transcript: str | None = None,
    transcript_words: list[dict] | None = None,
) -> None:
    """사용자 발화와 그에 대한 assistant 응답(+교정)을 한 번에 기록한다.

    음성 입력이면 `user_text` 는 학습자가 **확정한** 문장이고 `transcript` 는
    STT 가 들은 것이다. 둘이 다를 수 있고, 그 차이가 STT 를 믿어도 되는지에 대한
    답이 된다(app/tutor/transcript.py). 타자 입력이면 transcript 는 None 이다.
    """
    index = _next_index(db, session_id)

    db.add(
        TurnRow(
            session_id=session_id,
            turn_index=index,
            role="user",
            content=user_text,
            input_mode=input_mode,
            transcript=transcript,
            transcript_words=transcript_words,
        )
    )
    assistant = TurnRow(
        session_id=session_id,
        turn_index=index + 1,
        role="assistant",
        content=turn.reply,
        hint_ko=turn.hint_ko,
    )
    db.add(assistant)
    db.flush()

    for correction in turn.corrections:
        db.add(
            CorrectionRow(
                turn_id=assistant.id,
                original=correction.original,
                kind=correction.kind,
                better=correction.better,
                note=correction.note,
            )
        )


def messages_of(row: SessionRow) -> list[dict[str, str]]:
    """LLM 에 넘길 대화 히스토리 형태로 변환."""
    return [{"role": t.role, "content": t.content} for t in row.turns]


def corrections_of(db: DbSession, session_id: str) -> list[Correction]:
    """세션 전체의 교정을 발생 순서대로."""
    stmt = (
        select(CorrectionRow)
        .join(TurnRow, CorrectionRow.turn_id == TurnRow.id)
        .where(TurnRow.session_id == session_id)
        .order_by(TurnRow.turn_index, CorrectionRow.id)
    )
    rows = db.execute(stmt).scalars()
    return [
        Correction(original=r.original, kind=r.kind, better=r.better, note=r.note)
        for r in rows
    ]


def end_session(db: DbSession, session_id: str) -> None:
    row = db.get(SessionRow, session_id)
    if row is not None and row.ended_at is None:
        row.ended_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------- 단어 콘텐츠

_TOKEN = re.compile(r"[a-z][a-z']*")


def tokenize(text: str) -> set[str]:
    """교정 문장에서 단어를 뽑는다. 매칭용이라 단순 소문자 토큰이면 충분하다."""
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 2}


def existing_words(db: DbSession) -> set[str]:
    """이미 생성된 단어. 배치를 다시 돌려도 중복 생성하지 않기 위해."""
    return set(db.execute(select(WordRow.word)).scalars())


def upsert_word(
    db: DbSession,
    entry: WordEntry,
    *,
    topic: str | None = None,
    track: str = TRACK_GENERAL,
    rank: int | None = None,
) -> WordRow:
    """배치 생성 결과 저장. 항상 reviewed=False 로 들어간다.

    이미 있는 단어를 다시 생성하면 내용만 갱신하되, 사람이 이미 승인한 항목은
    건드리지 않는다(검수 결과를 배치가 덮어쓰면 안 된다).

    `topic` 은 **줄 때만** 쓴다. 없다고 지우면, 장면 없는 목록으로 한 번만 다시
    돌려도 묶음이 통째로 날아간다.

    `track` 은 **새로 만들 때만** 쓴다. 이미 있는 행의 트랙은 어떤 배치도 바꾸지
    않는다 — TSL 1,250개 중 149개(`airport`·`refund`·`lobby` …)가 이미 장면 팩으로
    생활 회화 트랙에 들어와 있는데, 토익 목록에 있다는 이유로 옮겨 버리면 카페·공항
    팩에서 그 낱말이 사라진다. 먼저 들어간 트랙이 그 낱말의 트랙이다.

    `rank` 는 **행을 새로 만들 때만** 적는다. 이미 있는 행의 순위는 `assign_ranks`
    가 맡는다 — 저쪽만 "먼저 실은 목록이 이긴다"는 규칙을 알고 있고, 배치는 생성
    전에 이미 그 함수를 돌린다. 여기서도 적으면 두 번째 목록이 첫 번째 목록의
    순위를 덮어 `client`(TOEIC 3위)가 1264위가 된다. 실제로 그렇게 됐었다.
    """
    row = db.execute(select(WordRow).where(WordRow.word == entry.word)).scalar_one_or_none()
    if row is None:
        row = WordRow(word=entry.word, reviewed=False, track=track, rank=rank)
        db.add(row)
    elif row.reviewed:
        if topic and not row.topic:
            row.topic = topic  # 묶음은 내용이 아니라 분류라 승인된 항목에도 붙인다
            db.flush()
        return row

    if topic:
        row.topic = topic
    row.level = entry.level
    row.meaning_ko = entry.meaning_ko
    row.pattern = entry.pattern
    row.example = entry.example
    row.usage_note = entry.usage_note
    row.confused_with = entry.confused_with
    db.flush()
    return row


def words_missing_pattern(
    db: DbSession, *, track: str | None = None, include_reviewed: bool = False
) -> list[str]:
    """문형이 비어 있는 단어. pattern 이 생기기 전에 만들어진 항목을 찾는다.

    2,801개를 통째로 다시 돌릴 이유가 없다 — 빠진 것만 채우면 된다.
    승인된 항목은 기본으로 제외한다(배치가 검수 결과를 덮어쓰지 않는다는 규칙 그대로).
    """
    stmt = select(WordRow.word).where(or_(WordRow.pattern.is_(None), WordRow.pattern == ""))
    if track:
        stmt = stmt.where(WordRow.track == track)
    if not include_reviewed:
        stmt = stmt.where(WordRow.reviewed.is_(False))
    return list(db.execute(stmt.order_by(WordRow.rank.is_(None), WordRow.rank)).scalars())


def words_missing_example_ko(
    db: DbSession,
    *,
    topic: str | None = None,
    track: str | None = None,
    include_reviewed: bool = False,
) -> list[str]:
    """예문 해석이 비어 있는 단어. words_missing_pattern 과 같은 모양이다.

    **장면(topic)이 붙은 것을 앞에 세운다.** 단어 연습장이 장면으로 문제를 고르기
    때문이다 — 카페 팩을 풀 수 있으려면 카페 60개의 해석이 먼저 있어야 하고,
    빈도 상위 60개의 해석은 그 화면에 한 문장도 안 나온다. 장면 안에서는 빈도 순,
    장면 없는 일반 어휘는 그 뒤에 역시 빈도 순으로 붙는다.

    예문이 없는 항목은 옮길 것이 없으므로 빠진다. 승인된 항목도 기본으로 빠진다
    (배치가 검수 결과를 덮어쓰지 않는다는 규칙 그대로).
    """
    stmt = select(WordRow.word).where(
        or_(WordRow.example_ko.is_(None), WordRow.example_ko == ""),
        WordRow.example != "",
    )
    if not include_reviewed:
        stmt = stmt.where(WordRow.reviewed.is_(False))
    if topic:
        stmt = stmt.where(WordRow.topic == topic)
    if track:
        # 트랙 하나만 채우고 싶을 때가 있다 — 토익 2,260개의 해석을 붙이는 동안
        # 생활 회화 쪽 순서에 끼어들 이유가 없다.
        stmt = stmt.where(WordRow.track == track)
    stmt = stmt.order_by(
        WordRow.topic.is_(None), WordRow.topic, WordRow.rank.is_(None), WordRow.rank, WordRow.word
    )
    return list(db.execute(stmt).scalars())


def word_examples(db: DbSession, words: list[str]) -> list[tuple[str, str, str]]:
    """(표제어, 낱말 뜻, 예문). 해석 백필이 모델에게 줄 세 칸만 뜬다.

    행 객체를 그대로 넘기지 않는 이유는 생성기가 동시 호출이라서다 — 세션에 매인
    ORM 객체가 스레드로 넘어가면 언제 무엇이 로드되는지가 불분명해진다.
    입력 순서를 그대로 지킨다(대상 순서가 곧 우선순위다).
    """
    wanted = [w.strip().lower() for w in words]
    rows = {
        r.word: r
        for r in db.execute(select(WordRow).where(WordRow.word.in_(wanted))).scalars()
    }
    return [
        (rows[w].word, rows[w].meaning_ko, rows[w].example)
        for w in wanted
        if w in rows and rows[w].example
    ]


def set_example_ko(
    db: DbSession, word: str, gloss: str, *, include_reviewed: bool = False
) -> bool:
    """예문 해석 한 칸만 쓴다. 나머지 칸은 건드리지 않는다.

    upsert_word 를 쓰지 않는 이유가 이것이다 — 저쪽은 항목 전체를 갈아 끼우므로
    해석을 붙이려다 예문·설명까지 새로 쓰인다. 승인된 항목은 기본으로 거른다.
    """
    row = db.execute(select(WordRow).where(WordRow.word == word.strip().lower())).scalar_one_or_none()
    if row is None or (row.reviewed and not include_reviewed):
        return False
    row.example_ko = gloss
    db.flush()
    return True


def list_words(
    db: DbSession,
    *,
    reviewed: bool | None = None,
    query: str | None = None,
    track: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WordRow]:
    """`track` 을 주지 않으면 트랙으로 가르지 않는다.

    검수 UI 와 소급 수정(정규화·레벨 조정)이 이 함수로 표를 통째로 훑는데, 그
    자리에서는 트랙이 관심사가 아니다 — 어느 트랙이든 표기 규칙은 같다. 학습자에게
    문제로 내보내는 조회(`cloze_candidates`)만 기본이 생활 회화 트랙이다.
    """
    stmt = select(WordRow)
    if reviewed is not None:
        stmt = stmt.where(WordRow.reviewed.is_(reviewed))
    if track:
        stmt = stmt.where(WordRow.track == track)
    if query:
        like = f"%{query.strip().lower()}%"
        stmt = stmt.where(or_(WordRow.word.like(like), WordRow.meaning_ko.like(like)))
    stmt = stmt.order_by(WordRow.reviewed, WordRow.word).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


def rank_map(words: list[str], *, offset: int = 0) -> dict[str, int]:
    """목록 순서를 표제어 -> 순위로. 같은 낱말이 두 번 나오면 앞의 자리를 쓴다."""
    ranks: dict[str, int] = {}
    for i, word in enumerate(words, start=1):
        ranks.setdefault(word.strip().lower(), i + offset)
    return ranks


def assign_ranks(
    db: DbSession,
    words: list[str],
    *,
    track: str = TRACK_GENERAL,
    offset: int = 0,
    keep_existing: bool = False,
) -> int:
    """단어 목록의 순서를 빈도 순위로 기록한다. 1이 가장 자주 쓰이는 단어.

    NGSL 은 파일에 등장하는 순서가 곧 빈도 순서인데, 그 정보가 저장 시점에
    사라지고 있었다. 검수를 중간에 멈춰도 **가장 많이 쓰는 단어부터** 승인돼
    있어야 리포트에 실제로 도움이 된다.

    **그 트랙의 행에만 적는다.** 순위는 트랙 안에서만 뜻이 있다 — TSL 로 이 함수를
    트랙 없이 돌리면 이미 장면 팩에 들어와 있는 `vacation` 이 TSL 2위를 얻어
    NGSL 2위(`and`)와 나란히 서고, 생활 회화 연습장의 출제 순서가 조용히 뒤집힌다.

    `offset` 은 한 트랙에 목록을 **이어 붙일 때** 쓴다. TSL 1,250 뒤에 BSL 을
    1251 부터 잇는 식이다. 두 목록의 1위는 서로 다른 코퍼스에서 나온 값이라 그냥
    합치면 순위가 겹쳐 정렬이 뒤엉킨다(content/data/bsl.csv 의 `# rank-offset:`).

    `keep_existing` 은 이어 붙이는 목록이 **먼저 온 목록의 순위를 덮지 않게** 한다.
    두 목록은 겹친다 — BSL 1,744개 중 525개가 이미 TSL 에 있는 낱말이었고, 이 빗장
    없이 BSL 을 돌렸더니 TOEIC 3위인 `client` 가 1264위로, `goods`(TSL 15위)가
    1252위로 밀렸다. 토익 화면의 첫 장이 통째로 다른 낱말들로 채워졌다는 뜻이다.
    한 트랙에서 **한 낱말의 순위는 그것을 먼저 실은 목록이 정한다.**
    """
    ranks = rank_map(words, offset=offset)

    changed = 0
    for row in db.execute(select(WordRow).where(WordRow.track == track)).scalars():
        if keep_existing and row.rank is not None:
            continue
        rank = ranks.get(row.word)
        if rank is not None and row.rank != rank:
            row.rank = rank
            changed += 1
    return changed


def assign_topics(db: DbSession, topics: dict[str, str]) -> int:
    """표제어에 장면 묶음을 붙인다. LLM 을 부르지 않는다.

    목록 파일에 `# topic:` 을 나중에 적었을 때, 이미 생성된 항목에 소급 적용하려고
    쓴다. 묶음은 내용이 아니라 분류라 승인된 항목에도 붙인다 — 검수 결과를
    덮어쓰는 게 아니다.
    """
    wanted = {w.strip().lower(): t for w, t in topics.items() if t}
    if not wanted:
        return 0
    changed = 0
    for row in db.execute(select(WordRow).where(WordRow.word.in_(wanted))).scalars():
        topic = wanted.get(row.word)
        if topic and row.topic != topic:
            row.topic = topic
            changed += 1
    return changed


def count_words(db: DbSession, *, reviewed: bool | None = None, track: str | None = None) -> int:
    stmt = select(func.count(WordRow.id))
    if reviewed is not None:
        stmt = stmt.where(WordRow.reviewed.is_(reviewed))
    if track:
        stmt = stmt.where(WordRow.track == track)
    return int(db.execute(stmt).scalar_one())


def save_word_edits(db: DbSession, word_id: int, **fields: object) -> WordRow | None:
    row = db.get(WordRow, word_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    db.flush()
    return row


def word_tips_for(
    db: DbSession, corrections: list[Correction], *, limit: int = 5
) -> list[WordTip]:
    """교정에 등장한 단어 중 검수된 항목만 리포트에 붙인다.

    LLM 을 부르지 않는다 — 대화 경로에서 단어 콘텐츠를 생성하지 않는다는 원칙 때문이다.
    """
    if not corrections:
        return []

    tokens: set[str] = set()
    for c in corrections:
        tokens |= tokenize(c.original)
        tokens |= tokenize(c.better)
    if not tokens:
        return []

    # 표제어는 원형으로 저장돼 있는데 교정 문장에는 굴절형이 나온다 — `working`,
    # `years`, `came`. 글자 그대로만 맞추면 그 팁이 통째로 빠진다. 실제 교정 70건에서
    # 그대로 맞은 토큰이 350개일 때 원형으로만 맞는 토큰이 66개 더 있었다.
    # 사전이 없는 환경에서는 lemmas 가 자기 자신만 돌려주므로 예전 동작 그대로다.
    wanted = set(tokens)
    for token in tokens:
        wanted |= {base for base in lexicon.lemmas(token) if len(base) >= 2}

    rows = list(
        db.execute(
            select(WordRow).where(WordRow.word.in_(wanted), WordRow.reviewed.is_(True))
        ).scalars()
    )
    # 학습자가 실제로 쓴 그 단어를 먼저 보여준다. 원형으로만 이어진 것(am -> be)은
    # 뒤로 미룬다 — 자리가 다섯뿐이라 순서가 곧 무엇을 버리느냐다.
    rows.sort(key=lambda r: (r.word not in tokens, r.level, r.word))
    return [
        WordTip(
            word=r.word,
            pattern=r.pattern,
            meaning_ko=r.meaning_ko,
            example=r.example,
            usage_note=r.usage_note,
            confused_with=list(r.confused_with or []),
        )
        for r in rows[:limit]
    ]


def topics(db: DbSession, *, track: str | None = TRACK_GENERAL) -> list[tuple[str, int, int]]:
    """(장면 묶음, 전체, 검수 완료). 묶음 없는 일반 어휘는 빠진다.

    기본이 생활 회화 트랙인 이유는 이 목록이 그 화면의 것이기 때문이다. 지금은
    토익 트랙에 장면이 하나도 붙어 있지 않아 결과가 같지만, 나중에 누가 붙이면
    카페·호텔 목록에 회의·송장 묶음이 섞여 나온다. `track=None` 이면 전부 센다.
    """
    stmt = (
        select(WordRow.topic, func.count(WordRow.id), func.sum(case((WordRow.reviewed, 1), else_=0)))
        .where(WordRow.topic.is_not(None))
        .group_by(WordRow.topic)
        .order_by(WordRow.topic)
    )
    if track:
        stmt = stmt.where(WordRow.track == track)
    return [(str(t), int(n), int(r or 0)) for t, n, r in db.execute(stmt)]


def cloze_candidates(
    db: DbSession,
    *,
    level: str | None = None,
    reviewed_only: bool = False,
    topic: str | None = None,
    track: str = TRACK_GENERAL,
    limit: int = 60,
) -> list[WordRow]:
    """빈칸 문제로 쓸 후보를 **빈도 순**으로 준다.

    빈도 순인 이유는 검수 UI 와 같다 — 학습자가 열 문제만 풀고 그만두더라도
    그 열 개가 가장 자주 쓰는 단어여야 한다.

    여기서는 SQL 로 걸러낼 수 있는 것만 거른다. 안전 판정(선별기 통과 여부)은
    파이썬이 해야 해서 tutor.cloze.is_safe_to_serve 가 맡는다. 그래서 호출부가
    필요한 개수보다 넉넉히 받아 걸러 쓰도록 limit 기본값을 크게 잡았다.

    **트랙 기본값이 생활 회화**인 것이 이 함수에서 제일 중요한 한 줄이다. 토익
    어휘 2,260개가 같은 표에 있으므로 여기서 트랙을 안 좁히면 카페 주문을 연습하는
    왕초보에게 `reimbursement` 가 빈칸으로 나간다. 호출부가 매번 기억해야 하는
    규칙으로 두지 않고 기본값으로 박아 둔다 — 잊어도 왕초보 화면은 그대로다.
    """
    stmt = select(WordRow).where(WordRow.example != "", WordRow.track == track)
    if level:
        stmt = stmt.where(WordRow.level == level)
    if reviewed_only:
        stmt = stmt.where(WordRow.reviewed.is_(True))
    if topic:
        # 장면을 고르면 그 장면 말만 낸다. 카페 연습 직전에 카페 단어를 푸는 게
        # 빈도 상위 열 개를 푸는 것보다 그 대화에 실제로 도움이 된다.
        stmt = stmt.where(WordRow.topic == topic)
    # rank 가 없는 단어(NGSL 목록 밖)는 뒤로 보낸다.
    stmt = stmt.order_by(WordRow.rank.is_(None), WordRow.rank, WordRow.word).limit(limit)
    return list(db.execute(stmt).scalars())


def cloze_alternatives(
    db: DbSession,
    *,
    word: str,
    topic: str | None,
    rank: int | None,
    level: str | None,
    track: str = TRACK_GENERAL,
    limit: int = 240,
) -> list[WordRow]:
    """같은 자리에 올 법한 다른 낱말의 **후보**를 준다. 여기서 고르지는 않는다.

    품사 대조는 WordNet 이 하는 일이라 SQL 로 못 한다. 그래서 이 함수는 넉넉히
    퍼 올리고, 품사로 거르는 것은 `tutor.practice` 가 맡는다. `cloze_candidates` 가
    안전 판정을 파이썬에 맡기는 것과 같은 분업이다.

    무엇을 후보로 보는가 — 두 가지뿐이고, 둘 다 **아는 사실**이다.

    1. **같은 장면(topic)**. 카페 어휘 60개는 실제로 같은 상황에서 쓰는 말이라
       "같은 장면에서 쓰는 명사예요"라고 말할 수 있다.
    2. 장면이 없으면 **빈도가 가까운 것**. 3,245개 중 장면이 붙은 것은 444개뿐이라
       대부분이 여기로 온다. 빈도가 가깝다는 것은 난이도가 비슷하다는 뜻이지
       그 자리에 들어간다는 뜻이 아니다 — 화면 문구도 딱 그만큼만 말한다.

    두 경우 다 **그 자리에 넣어도 뜻이 통하는지는 모른다.** WordNet 은 품사를 알지
    문맥을 모른다. 그래서 이 함수 이름을 alternatives 가 아니라 후보로 읽어야 한다.

    셋째 조건은 **같은 트랙**이다. 빈도가 가깝다는 말 자체가 트랙 안에서만 성립한다 —
    NGSL 300위와 TSL 300위는 다른 코퍼스의 300위라 나란히 놓을 근거가 없다.
    화면 문구가 "비슷하게 자주 쓰는 명사예요" 이므로 이 조건이 곧 그 문구의 참·거짓이다.
    """
    stmt = select(WordRow).where(
        WordRow.word != word.strip().lower(), WordRow.example != "", WordRow.track == track
    )
    if topic:
        stmt = stmt.where(WordRow.topic == topic).order_by(
            WordRow.rank.is_(None), WordRow.rank, WordRow.word
        )
    elif rank is not None:
        # 빈도가 가까운 순. NGSL 목록 밖(rank NULL)은 빈도를 모르니 후보로 쓰지 않는다.
        stmt = stmt.where(WordRow.rank.is_not(None)).order_by(
            func.abs(WordRow.rank - rank), WordRow.word
        )
    else:
        # 빈도도 장면도 모르면 같은 레벨의 자주 쓰는 말로 대신한다. 그마저 없으면 빈손.
        if not level:
            return []
        stmt = stmt.where(WordRow.level == level).order_by(
            WordRow.rank.is_(None), WordRow.rank, WordRow.word
        )
    return list(db.execute(stmt.limit(limit)).scalars())
