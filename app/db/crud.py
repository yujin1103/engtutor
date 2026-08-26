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
from .models import CorrectionRow, SessionRow, TurnRow, WordRow


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


def upsert_word(db: DbSession, entry: WordEntry, *, topic: str | None = None) -> WordRow:
    """배치 생성 결과 저장. 항상 reviewed=False 로 들어간다.

    이미 있는 단어를 다시 생성하면 내용만 갱신하되, 사람이 이미 승인한 항목은
    건드리지 않는다(검수 결과를 배치가 덮어쓰면 안 된다).

    `topic` 은 **줄 때만** 쓴다. 없다고 지우면, 장면 없는 목록으로 한 번만 다시
    돌려도 묶음이 통째로 날아간다.
    """
    row = db.execute(select(WordRow).where(WordRow.word == entry.word)).scalar_one_or_none()
    if row is None:
        row = WordRow(word=entry.word, reviewed=False)
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


def words_missing_pattern(db: DbSession, *, include_reviewed: bool = False) -> list[str]:
    """문형이 비어 있는 단어. pattern 이 생기기 전에 만들어진 항목을 찾는다.

    2,801개를 통째로 다시 돌릴 이유가 없다 — 빠진 것만 채우면 된다.
    승인된 항목은 기본으로 제외한다(배치가 검수 결과를 덮어쓰지 않는다는 규칙 그대로).
    """
    stmt = select(WordRow.word).where(or_(WordRow.pattern.is_(None), WordRow.pattern == ""))
    if not include_reviewed:
        stmt = stmt.where(WordRow.reviewed.is_(False))
    return list(db.execute(stmt.order_by(WordRow.rank.is_(None), WordRow.rank)).scalars())


def list_words(
    db: DbSession,
    *,
    reviewed: bool | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WordRow]:
    stmt = select(WordRow)
    if reviewed is not None:
        stmt = stmt.where(WordRow.reviewed.is_(reviewed))
    if query:
        like = f"%{query.strip().lower()}%"
        stmt = stmt.where(or_(WordRow.word.like(like), WordRow.meaning_ko.like(like)))
    stmt = stmt.order_by(WordRow.reviewed, WordRow.word).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


def assign_ranks(db: DbSession, words: list[str]) -> int:
    """단어 목록의 순서를 빈도 순위로 기록한다. 1이 가장 자주 쓰이는 단어.

    NGSL 은 파일에 등장하는 순서가 곧 빈도 순서인데, 그 정보가 저장 시점에
    사라지고 있었다. 검수를 중간에 멈춰도 **가장 많이 쓰는 단어부터** 승인돼
    있어야 리포트에 실제로 도움이 된다.
    """
    ranks = {}
    for i, word in enumerate(words, start=1):
        ranks.setdefault(word.strip().lower(), i)

    changed = 0
    for row in db.execute(select(WordRow)).scalars():
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


def count_words(db: DbSession, *, reviewed: bool | None = None) -> int:
    stmt = select(func.count(WordRow.id))
    if reviewed is not None:
        stmt = stmt.where(WordRow.reviewed.is_(reviewed))
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


def topics(db: DbSession) -> list[tuple[str, int, int]]:
    """(장면 묶음, 전체, 검수 완료). 묶음 없는 일반 어휘는 빠진다."""
    stmt = (
        select(WordRow.topic, func.count(WordRow.id), func.sum(case((WordRow.reviewed, 1), else_=0)))
        .where(WordRow.topic.is_not(None))
        .group_by(WordRow.topic)
        .order_by(WordRow.topic)
    )
    return [(str(t), int(n), int(r or 0)) for t, n, r in db.execute(stmt)]


def cloze_candidates(
    db: DbSession,
    *,
    level: str | None = None,
    reviewed_only: bool = False,
    topic: str | None = None,
    limit: int = 60,
) -> list[WordRow]:
    """빈칸 문제로 쓸 후보를 **빈도 순**으로 준다.

    빈도 순인 이유는 검수 UI 와 같다 — 학습자가 열 문제만 풀고 그만두더라도
    그 열 개가 가장 자주 쓰는 단어여야 한다.

    여기서는 SQL 로 걸러낼 수 있는 것만 거른다. 안전 판정(선별기 통과 여부)은
    파이썬이 해야 해서 tutor.cloze.is_safe_to_serve 가 맡는다. 그래서 호출부가
    필요한 개수보다 넉넉히 받아 걸러 쓰도록 limit 기본값을 크게 잡았다.
    """
    stmt = select(WordRow).where(WordRow.example != "")
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
