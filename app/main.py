"""FastAPI 엔트리포인트."""

from __future__ import annotations

import io
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .db.database import init_db
from .db.models import TRACK_GENERAL
from .llm.base import LLMError
from .llm.factory import get_client
from .report.schemas import SessionReport
from .report.service import ReportService
from .session_store import SqliteSessionStore
from .stt import SttUnavailable, get_stt_service
from .tutor.categories import CATEGORIES
from .tutor import cloze as cloze_mod
from .tutor import practice
from .tutor.levels import DEFAULT_LEVEL
from .tutor.loader import Scenario, get_scenarios
from .tutor.schemas import TurnResponse
from .tutor.service import TutorService
from .tutor.strictness import (
    CAPTIONS as STRICTNESS_CAPTIONS,
)
from .tutor.strictness import (
    DEFAULT_STRICTNESS,
    ORDER as STRICTNESS_ORDER,
    Strictness,
    show_polish,
)
from .tutor.strictness import (
    LABELS as STRICTNESS_LABELS,
)
from .web import mount_spa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="engtutor", version="0.2.0", lifespan=lifespan)
store = SqliteSessionStore()


class ScenarioOut(BaseModel):
    id: str
    title: str
    category: str
    level: str
    situation: str
    goal: str
    opening_line: str
    opening_line_ko: str
    opening_say_en: str
    opening_say_more: str
    opening_hint_ko: str

    @classmethod
    def of(cls, scenario: Scenario) -> "ScenarioOut":
        return cls(**scenario.model_dump())


class ChatRequest(BaseModel):
    scenario_id: str
    # 음성 입력이면 학습자가 **확정한** 문장이다. 전사 그대로가 아니라 고친 뒤의 것.
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    level: Literal["A1", "A2", "B1"] | None = None
    strictness: Strictness = DEFAULT_STRICTNESS
    # --- 음성 입력에서만 채워진다. 타자면 전부 비어 있고 동작은 지금과 같다. ---
    input_mode: Literal["text", "voice"] = "text"
    # STT 가 들은 것. message 와 다를 수 있고, 그 차이가 STT 를 믿어도 되는지에
    # 대한 답이 된다(app/tutor/transcript.py).
    transcript: str | None = Field(default=None, max_length=2000)
    # 낱말별 확률. 자신 없는 단어를 화면에 표시하고, **확신했는데 학습자가 고친
    # 자리**를 세는 데 쓴다. 후자가 STT 가 틀린 영어를 매끄럽게 고친 흔적이다.
    transcript_words: list[dict] | None = None


class ChatResponse(BaseModel):
    session_id: str
    turn: TurnResponse


@app.get("/healthz")
def healthz() -> dict[str, object]:
    """백엔드가 실제로 닿는지까지 확인한다. '왜 안 되지'를 빨리 좁히기 위한 것."""
    client = get_client()
    return {
        "backend": client.name,
        "detail": client.describe(),
        "reachable": client.ping(),
        "scenarios": len(get_scenarios()),
        # 음성 인식은 없어도 앱이 도는 선택 기능이라 상태를 따로 보여 준다.
        # loaded 는 첫 요청 전에는 false 다 — 500MB 를 미리 올리지 않기 때문이다.
        "stt": get_stt_service().describe(),
    }


@app.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios() -> list[ScenarioOut]:
    return [ScenarioOut.of(s) for s in get_scenarios().values()]


class CategoryOut(BaseModel):
    id: str
    label: str
    emoji: str
    blurb: str
    count: int


@app.get("/categories", response_model=list[CategoryOut])
def list_categories() -> list[CategoryOut]:
    """시나리오 분류. 화면이 '분류 고르기 -> 그 안에서 고르기'로 들어가기 위한 것.

    개수를 함께 내려준다 — 비어 있는 분류를 눌러 놓고 아무것도 없는 화면을
    보는 일이 없어야 한다.
    """
    counted = Counter(s.category for s in get_scenarios().values())
    return [
        CategoryOut(
            id=c.id, label=c.label, emoji=c.emoji, blurb=c.blurb, count=counted.get(c.id, 0)
        )
        for c in CATEGORIES
        if counted.get(c.id, 0)
    ]


class StrictnessOut(BaseModel):
    key: Strictness
    label: str
    caption: str


@app.get("/strictness", response_model=list[StrictnessOut])
def list_strictness() -> list[StrictnessOut]:
    """교정 강도 선택지. 프런트엔드가 라벨을 복제하지 않게 서버가 내려준다.

    나중에 PWA 를 붙여도 문구가 한 곳에서만 관리된다.
    """
    return [
        StrictnessOut(key=k, label=STRICTNESS_LABELS[k], caption=STRICTNESS_CAPTIONS[k])
        for k in STRICTNESS_ORDER
    ]


def _resolve(req: ChatRequest):
    """시나리오와 세션을 확인/생성한다. 스트리밍이 시작되기 **전에** 끝나야
    4xx 를 정상적인 HTTP 상태코드로 돌려줄 수 있다."""
    scenario = get_scenarios().get(req.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"시나리오를 찾을 수 없습니다: {req.scenario_id}")

    if req.session_id:
        session = store.get(req.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다. 새로 시작하세요.")
        if session.ended:
            raise HTTPException(status_code=409, detail="이미 종료된 세션입니다. 새로 시작하세요.")
        # **레벨은 요청이 이긴다.** 세션에 굳은 값을 쓰면, 화면의 난이도 조절기가
        # 살아 있는 것처럼 생겼는데 실제로는 대화를 시작할 때 한 번만 먹는다.
        # 학습자가 "너무 어렵다" 고 낮춰도 아무 일이 안 일어나고, 낮추려면 하던
        # 대화를 버려야 한다. 그건 조절기가 아니다.
        #
        # 바꾼 값은 세션에도 적는다 — 리포트가 마지막에 실제로 쓰인 레벨을 적게 하려고.
        if req.level and req.level != session.level:
            store.set_level(session.id, req.level)
            session.level = req.level
    else:
        # 시나리오의 level 은 **요청에 레벨이 없을 때만** 쓰는 기본값이다. 목록에
        # 보이는 레벨 뱃지는 "이 상황은 이 정도에 맞춰 만들었다"는 권장이지 설정이 아니다.
        session = store.create(scenario_id=scenario.id, level=req.level or scenario.level)
    return scenario, session


def _finalize(session_id: str, req: ChatRequest, turn: TurnResponse) -> TurnResponse:
    """유연 모드 필터링 + 저장. 스트리밍/비스트리밍이 같은 경로를 쓴다."""
    # 유연 모드는 프롬프트로 polish 를 만들지 말라고 하지만, 모델이 규칙을 흘릴 수 있다.
    # 저장 전에 코드로 한 번 더 걷어낸다 — 프롬프트로 못 막는 건 코드로 막는다는 원칙.
    if not show_polish(req.strictness):
        turn = turn.model_copy(
            update={"corrections": [c for c in turn.corrections if c.kind != "polish"]}
        )
    store.record_turn(
        session_id,
        user_text=req.message,
        turn=turn,
        input_mode=req.input_mode,
        transcript=req.transcript,
        transcript_words=req.transcript_words,
    )
    return turn


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    scenario, session = _resolve(req)

    service = TutorService(get_client())
    try:
        turn = service.respond(
            scenario=scenario,
            level=session.level,
            history=session.messages,
            user_text=req.message,
            strictness=req.strictness,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(session_id=session.id, turn=_finalize(session.id, req, turn))


def _sse(payload: dict[str, object]) -> str:
    # json.dumps 가 개행을 이스케이프하므로 SSE 프레임이 깨지지 않는다.
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """`/chat` 과 결과는 같고, reply 만 생성되는 대로 먼저 흘려보낸다.

    전체 응답이 8초 걸려도 첫 글자는 1초 안에 도착한다. 교정과 힌트는
    검증이 끝난 뒤 마지막 turn 사건에 한 번에 실려 온다 — 반쯤 만들어진
    교정을 학습자에게 보여 주는 일은 없어야 하기 때문이다.
    """
    scenario, session = _resolve(req)
    service = TutorService(get_client())

    def events() -> Iterator[str]:
        yield _sse({"type": "session", "session_id": session.id})
        try:
            for event in service.respond_stream(
                scenario=scenario,
                level=session.level,
                history=session.messages,
                user_text=req.message,
                strictness=req.strictness,
            ):
                if event["type"] == "turn":
                    turn = _finalize(session.id, req, event["turn"])
                    yield _sse({"type": "turn", "turn": turn.model_dump()})
                elif event["type"] == "reset":
                    yield _sse({"type": "reset"})
                else:
                    yield _sse({"type": "delta", "text": event["text"]})
        except LLMError as exc:
            # 스트림이 이미 200 으로 시작돼 상태코드를 바꿀 수 없다. 사건으로 알린다.
            logger.exception("스트리밍 턴 실패")
            yield _sse({"type": "error", "detail": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 나중에 Caddy 뒤에 둘 때 프록시가 버퍼링해 스트리밍이 죽는 걸 막는다.
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------- 음성 입력
#
# 서버가 하는 일은 **오디오를 글자로 옮기는 한 단계뿐**이다. 화면은 그 글자를
# 통째로 고칠 수 있는 칸에 넣고 "말한 대로 나왔나요?" 만 묻는다. 확신도가 낮은
# 낱말을 흐리게 찍어 주는 방법은 재 봤더니 소음이었다(정확도 20%).
#
# 확인이 끝난 문장은 평소처럼 /chat 의 `message` 로 오고, 전사 원본은 같은 요청의
# `transcript` · `transcript_words` 로 온다. 둘의 차이가 이 STT 를 믿어도 되는지에
# 대한 답이 된다(app/tutor/transcript.py).


class SttWordOut(BaseModel):
    """낱말 하나와 STT 가 그것에 준 확률.

    화면에 표시하라고 주는 값이 아니다. `/chat` 에 `transcript_words` 로 그대로
    되돌려 보내 기록에 남기라고 주는 값이다.
    """

    word: str
    probability: float | None = None


class SttResponse(BaseModel):
    text: str
    words: list[SttWordOut]
    duration_ms: int          # 서버가 전사에 쓴 시간
    audio_seconds: float      # 오디오 길이. 지연이 길이 탓인지 구분하려고 함께 준다
    model: str


@app.post("/stt", response_model=SttResponse)
def transcribe_audio(file: UploadFile = File(...)) -> SttResponse:
    """녹음 파일 하나를 받아 전사한다. webm · wav · m4a · mp3 · ogg 를 그대로 받는다.

    - 말이 안 들리면 **빈 `text` 를 200 으로** 돌려준다. 마이크만 누르고 말을 안 한
      흔한 경우라 오류가 아니다. 화면이 "안 들렸어요" 를 그리면 된다.
    - 파일이 상한을 넘으면 413. CPU 전사는 오디오 1초당 약 0.3초를 쓰므로 긴 파일
      하나가 서버를 오래 붙잡는다.
    - STT 가 꺼져 있거나 faster-whisper 가 없으면 503. 나머지 기능은 그대로 돈다.

    동기 함수로 둔다 — 전사는 CPU 를 다 쓰는 작업이라 async 안에서 돌리면 그 동안
    이벤트 루프가 멈춰 대화 스트리밍까지 같이 끊긴다. FastAPI 가 스레드풀에서
    돌려 주는 편이 맞다.
    """
    settings = get_settings()
    limit = int(settings.stt_max_upload_mb * 1024 * 1024)

    # 통째로 읽어 놓고 크기를 재면 이미 늦다. 읽으면서 넘는 순간 끊는다.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"녹음이 너무 큽니다({settings.stt_max_upload_mb:.0f}MB 까지). "
                    "짧게 나눠서 말해 보세요."
                ),
            )
        chunks.append(chunk)

    service = get_stt_service()
    if not service.enabled or not service.installed():
        raise HTTPException(status_code=503, detail=_stt_unavailable_message(service))

    if total == 0:
        # 빈 파일. 모델을 올릴 것도 없이 '안 들렸다' 와 같은 답을 준다.
        return SttResponse(
            text="", words=[], duration_ms=0, audio_seconds=0.0, model=settings.stt_model
        )

    try:
        result = service.transcribe(io.BytesIO(b"".join(chunks)))
    except SttUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # 깨진 파일 · 오디오가 아닌 것
        logger.exception("전사 실패: %s", file.filename)
        raise HTTPException(
            status_code=400,
            detail=f"오디오를 읽지 못했습니다. 다시 녹음해 주세요. ({type(exc).__name__})",
        ) from exc

    logger.info(
        "전사: %.2fs 오디오 -> %dms, %d낱말 %r",
        result.audio_seconds,
        result.duration_ms,
        len(result.words),
        result.text[:60],
    )
    return SttResponse(
        text=result.text,
        words=[SttWordOut(**w) for w in result.words],
        duration_ms=result.duration_ms,
        audio_seconds=result.audio_seconds,
        model=result.model,
    )


def _stt_unavailable_message(service) -> str:
    """503 에 설치·설정 안내를 담는다. 여기서 막히면 다음에 뭘 할지가 보여야 한다."""
    if not service.enabled:
        return "음성 입력이 꺼져 있습니다. .env 에서 STT_ENABLED=true 로 켜고 api 를 다시 시작하세요."
    return (
        "faster-whisper 가 설치돼 있지 않습니다. "
        "`docker compose build api` 로 이미지를 다시 만든 뒤 `docker compose up -d api` 하세요."
    )


# --------------------------------------------------------------- 빈칸 채우기
#
# 완전 초보는 문장을 통째로 만들지 못한다. 한 칸만 채우게 하면 시작할 수 있다.
# 문제는 LLM 이 만들지 않는다 — 검수를 거친 예문에서 표제어를 **지워서** 만든다.
# 새로 만들어지는 정보가 없으니 새로 틀릴 것도 없다(app/tutor/cloze.py).


class PosHintOut(BaseModel):
    """빈칸의 품사 힌트. **정답 낱말은 들어 있지 않다** — 품사 이름만 나간다.

    `source` 를 함께 내보내는 이유는 화면이 근거보다 세게 말하지 못하게 하기
    위해서다. `slot` 은 관사·조동사로 자리를 좁힌 것("여기엔 명사가 들어가요"),
    `word` 는 정답 낱말이 가질 수 있는 품사 전부("이 낱말은 명사로도 동사로도
    써요")다. 그대로 쓸 문구는 `text_ko` 에 이미 담겨 있다.
    """

    pos: list[str]
    labels_ko: list[str]
    text_ko: str
    source: Literal["slot", "word"]


class ClozeOut(BaseModel):
    """학습자에게 내보내는 빈칸. **정답은 넣지 않는다** — 채점은 서버가 한다.

    `example_ko` 와 `pos_hint` 는 나중에 붙은 칸이라 기본값이 있다. 예전 화면이
    이 응답을 그대로 읽어도 깨지지 않는다.
    """

    word: str
    level: str
    meaning_ko: str
    sentence: str
    pattern: str | None = None
    reviewed: bool
    # 이 문장의 한국어 해석. **가리지 않고 그대로 보여 준다** — 시험이 아니라
    # 연습장이고, 뜻을 알아야 구나 절로도 답할 수 있다. 792/3,245 만 채워져 있어
    # 대부분 None 이고, 없으면 안 보여줄 뿐 지어내지 않는다.
    example_ko: str | None = None
    # 품사를 말할 근거가 없으면 None 이다(기능어 빈칸, WordNet 이 모르는 낱말).
    # 그때는 힌트 없이 낸다 — 힌트 없는 빈칸도 빈칸으로 성립한다.
    pos_hint: PosHintOut | None = None
    topic: str | None = None


class TopicOut(BaseModel):
    """장면 묶음 하나. 다른 회화 앱의 '유닛'에 해당한다."""

    topic: str
    # 학습자가 읽을 이름. 화면이 `{"cafe": "카페"}` 를 따로 들고 있게 하지 않는다 —
    # 그러면 팩을 하나 더 만들 때마다 서버와 화면 두 곳을 고쳐야 하고, 한쪽을
    # 빠뜨리면 화면에만 영어 이름이 남는다. 나중에 붙은 칸이라 기본값을 둔다.
    label_ko: str = ""
    total: int
    reviewed: int


class ClozeAnswerRequest(BaseModel):
    word: str
    said: str = Field(description="학습자가 말했거나 적은 답")
    explain: bool = Field(
        default=True,
        description="설명 카드를 함께 받을지. 채점만 필요하면 false 로 두면 DB 조회가 준다",
    )


class AlternativeOut(BaseModel):
    word: str
    meaning_ko: str
    pos_ko: list[str]
    reviewed: bool


class AlternativesOut(BaseModel):
    """같은 품사의 다른 낱말들. `label_ko` 가 **무엇을 근거로 모았는지**를 말한다.

    "이 자리에 올 수 있어요"가 아니다. WordNet 은 품사를 알지 그 자리에서 뜻이
    통하는지는 모르므로, 화면에는 `label_ko` 를 그대로 띄워야 한다.
    """

    basis: Literal["topic", "rank", "level"]
    label_ko: str
    words: list[AlternativeOut]


class UnverifiedOut(BaseModel):
    """아직 사람이 확인하지 않은 설명. 승인된 항목이면 이 상자 자체가 없다.

    확인된 환각 13건이 전부 `usage_note` 와 `confused_with` 에 있었다. 그래서
    같은 이름의 최상위 칸과 **자리를 갈라** 두었다 — 화면이 실수로 같은 자리에
    그릴 수 없게 구조로 막는다.
    """

    usage_note: str | None = None
    confused_with: list[str] = []
    note_ko: str


class ClozeExplainOut(BaseModel):
    """답을 낸 뒤 보여 줄 설명. 전부 `words` 행과 WordNet 에서만 온다."""

    word: str
    answer: str
    meaning_ko: str
    example: str
    example_ko: str | None = None
    pattern: str | None = None
    topic: str | None = None
    topic_ko: str | None = None
    pos: list[str] = []
    pos_ko: list[str] = []
    pos_text_ko: str | None = None
    reviewed: bool
    usage_note: str | None = None
    confused_with: list[str] = []
    unverified: UnverifiedOut | None = None
    alternatives: AlternativesOut | None = None
    hint: PosHintOut | None = None


class ClozeAnswerOut(BaseModel):
    """채점 결과. 판정이 늘었고 설명이 붙었다 — 기존 필드는 그대로다.

    `right_pos`·`wrong_pos` 는 예전에 `wrong_word` 하나로 뭉쳐 있던 것을 가른
    것이다. 품사를 비교할 수 없을 때(기능어, 사전이 모르는 낱말)는 지금도
    `wrong_word` 로 온다.
    """

    verdict: Literal[
        "correct", "wrong_form", "right_pos", "wrong_pos", "wrong_word", "not_a_word", "empty"
    ]
    ok: bool
    said: str
    answer: str
    message_ko: str
    # 실제로 판정한 낱말. 구·절로 답하면(`a pen`) 그 안의 머리 낱말이라 said 와 다르다.
    head: str | None = None
    said_pos: list[str] = []
    explain: ClozeExplainOut | None = None


@app.get("/cloze/topics", response_model=list[TopicOut])
def list_topics() -> list[TopicOut]:
    """장면 묶음 목록. 빈칸을 장면별로 낼 수 있게 UI 가 먼저 물어본다."""
    from .db import crud
    from .db.database import db_session

    with db_session() as db:
        return [
            TopicOut(topic=t, label_ko=practice.topic_ko(t) or t, total=n, reviewed=r)
            for t, n, r in crud.topics(db)
        ]


@app.get("/cloze", response_model=list[ClozeOut])
def list_cloze(
    level: str = DEFAULT_LEVEL,
    count: int = 10,
    offset: int = 0,
    speech: bool = False,
    reviewed_only: bool = False,
    topic: str | None = None,
    track: str = TRACK_GENERAL,
) -> list[ClozeOut]:
    """빈칸 문제를 빈도 순으로 준다.

    `speech=true` 면 기능어 빈칸을 뺀다 — `and` 를 마이크에 대고 말하는 건 연습이
    아니고, 짧고 강세 없는 낱말은 전사가 가장 많이 흔들린다.

    `topic` 을 주면 그 장면 묶음(cafe, hotel, health …)만 낸다. 카페 대화 직전에
    카페 단어를 푸는 게 빈도 상위 열 개를 푸는 것보다 그 대화에 실제로 도움이 된다.

    `track` 은 어느 어휘 트랙에서 낼지다. 기본은 생활 회화(`general`)이고, 토익
    어휘는 `track=toeic` 으로만 나온다. **기본값이 곧 안전장치다** — 이 값을 빼먹은
    호출은 왕초보용 낱말만 받는다. 토익 어휘 2,260개가 같은 표에 있으므로 반대로
    두면 카페 주문을 연습하는 학습자에게 `reimbursement` 가 빈칸으로 나간다.

    `level=""` 이면 레벨로 가르지 않는다. 장면 팩을 낼 때 필요하다 — 카페 60개를
    A1 으로 자르면 8개가 남아 연습이 성립하지 않는다. 팩은 그 자리에서 쓰는 말을
    모은 것이지 난이도로 묶은 것이 아니다. 이미 `crud.cloze_candidates` 가 빈
    값을 "안 가른다" 로 읽고 있었고, 단어 연습장 화면이 그 성질에 기대므로
    여기 적어 계약으로 못 박는다(tests/test_practice.py).
    """
    from .db import crud
    from .db.database import db_session

    count = max(1, min(count, 50))
    with db_session() as db:
        # 안전 판정과 음성 판정이 파이썬 쪽에 있어서 넉넉히 받아 걸러 쓴다.
        rows = crud.cloze_candidates(
            db,
            level=level,
            reviewed_only=reviewed_only,
            topic=topic,
            track=track,
            limit=(offset + count) * 6 + 60,
        )
        out: list[ClozeOut] = []
        for row in rows:
            item = cloze_mod.make_item(row)
            if item is None or not cloze_mod.is_safe_to_serve(row):
                continue
            if speech and not cloze_mod.is_speakable(item):
                continue
            out.append(_cloze_out(item))
    return out[offset : offset + count]


def _cloze_out(item: cloze_mod.ClozeItem) -> ClozeOut:
    """문제 하나를 응답으로. **`item.answer` 는 여기서 나가지 않는다.**

    나가는 것은 빈칸 문장, 낱말 뜻, 문장 해석, 품사 이름뿐이다. 해석은 답을 일부
    드러내지만 그건 결정한 것이다 — 뜻을 알아야 `pen`·`a pen`·`your pen` 처럼
    구로도 답할 수 있고, 뜻을 안 주면 왕초보에게는 과제가 성립하지 않는다.

    `word` 칸에 표제어가 그대로 들어 있는 것은 남겨 둔다. 채점할 때 어느 항목인지
    가리키는 열쇠이고(`POST /cloze/answer` 가 이 값을 받는다) 이미 그렇게 쓰이고
    있다. 대신 **정답으로 적어 낼 표면형**은 `sentence`·`pattern`·`meaning_ko`·
    `example_ko` 어디에도 없어야 한다 — `pattern` 이 2,882개에서 흘리고 있었다.
    """
    hint = cloze_mod.pos_hint(item)
    mask = cloze_mod.mask_answer
    return ClozeOut(
        word=item.word,
        level=item.level,
        meaning_ko=mask(item.meaning_ko, item.word) or "",
        sentence=item.sentence,
        pattern=mask(item.pattern, item.word),
        reviewed=item.reviewed,
        example_ko=mask(item.example_ko, item.word),
        topic=item.topic,
        pos_hint=None if hint is None else PosHintOut(**vars(hint)),
    )


def _explain_out(card: practice.Explanation) -> ClozeExplainOut:
    """설명 카드를 응답으로. 승인 전 설명은 `unverified` 상자를 벗어나지 않는다."""
    alts = card.alternatives
    return ClozeExplainOut(
        word=card.word,
        answer=card.answer,
        meaning_ko=card.meaning_ko,
        example=card.example,
        example_ko=card.example_ko,
        pattern=card.pattern,
        topic=card.topic,
        topic_ko=card.topic_ko,
        pos=list(card.pos),
        pos_ko=list(card.pos_ko),
        pos_text_ko=card.pos_text_ko,
        reviewed=card.reviewed,
        usage_note=card.usage_note,
        confused_with=list(card.confused_with),
        unverified=(
            None
            if card.unverified is None
            else UnverifiedOut(
                usage_note=card.unverified.usage_note,
                confused_with=list(card.unverified.confused_with),
                note_ko=card.unverified.note_ko,
            )
        ),
        alternatives=(
            None
            if alts is None
            else AlternativesOut(
                basis=alts.basis,
                label_ko=alts.label_ko,
                words=[AlternativeOut(**vars(w)) for w in alts.words],
            )
        ),
        hint=None if card.hint is None else PosHintOut(**vars(card.hint)),
    )


@app.post("/cloze/answer", response_model=ClozeAnswerOut)
def answer_cloze(req: ClozeAnswerRequest) -> ClozeAnswerOut:
    """채점하고, **왜 그런지 설명한다.** 정답이 클라이언트로 미리 나가지 않는다.

    새 엔드포인트로 가르지 않고 이 자리를 넓혔다. 설명을 만드는 데 필요한 것이
    채점에 이미 있는 것과 같기 때문이다 — `words` 행 하나와 빈칸 하나. 따로 두면
    화면이 답을 낼 때마다 두 번 왕복하고, 두 번째 요청은 첫 번째가 무엇을
    판정했는지 다시 계산해야 한다. 늘어난 필드는 전부 기본값이 있어서 예전
    화면은 그대로 돈다.

    `explain=false` 로 예전 크기의 응답만 받을 수도 있다. 검수 UI 처럼 채점만
    필요한 곳에서 후보 조회(같은 장면 낱말 240개 훑기)를 안 하게 하려는 것이다.
    """
    from sqlalchemy import select

    from .db.database import db_session
    from .db.models import WordRow

    with db_session() as db:
        row = db.execute(
            select(WordRow).where(WordRow.word == req.word.strip().lower())
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"모르는 단어예요: {req.word}")
        item = cloze_mod.make_item(row)
        if item is None:
            raise HTTPException(
                status_code=422, detail=f"빈칸을 만들 수 없는 항목이에요: {req.word}"
            )
        result = cloze_mod.grade(item, req.said)
        # 설명 카드는 세션 안에서 만든다 — 후보를 고르려면 DB 를 한 번 더 봐야 한다.
        card = _explain_out(practice.explain(db, row, item)) if req.explain else None

    return ClozeAnswerOut(
        verdict=result.verdict,
        ok=result.ok,
        said=result.said,
        answer=result.answer,
        message_ko=result.message_ko,
        head=result.head or None,
        said_pos=list(result.said_pos),
        explain=card,
    )


@app.post("/sessions/{session_id}/report", response_model=SessionReport)
def session_report(session_id: str) -> SessionReport:
    """세션을 종료하고 학습 리포트를 만든다.

    리포트 품질은 백엔드 영향을 많이 받는다. 로컬 모델이 아쉬우면
    LLM_BACKEND=anthropic 으로 바꿔 생성하는 걸 권한다(README 참고).
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    scenario = get_scenarios().get(session.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"시나리오가 사라졌습니다: {session.scenario_id}")

    if not any(m["role"] == "user" for m in session.messages):
        raise HTTPException(status_code=400, detail="대화가 없어 리포트를 만들 수 없습니다.")

    corrections = store.corrections(session.id)
    try:
        report = ReportService(get_client()).build(
            session_id=session.id,
            scenario=scenario,
            level=session.level,
            messages=session.messages,
            corrections=corrections,
            # 검수된 단어만 DB 에서 붙인다. 대화 경로에서 LLM 으로 만들지 않는다.
            word_tips=store.word_tips(corrections),
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    store.end(session.id)
    return report


# ─────────────────────────────────────────────── 화면 (React 빌드 결과)
#
# **반드시 맨 마지막이다.** 이 줄이 `/` 아래를 통째로 잡는 catch-all 을 등록하는데,
# 라우트는 등록 순서대로 맞춰 보므로 위의 API 들보다 앞서면 `/chat` 이 화면에
# 가려진다. 새 엔드포인트는 항상 이 줄 **위에** 추가한다.
mount_spa(app)
