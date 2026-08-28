"""FastAPI 엔트리포인트."""

from __future__ import annotations

import io
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

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
from .tutor import grammar
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


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 를 만들다 서버가 부서지지 않게 한다.

    기본 처리기는 **문제가 된 입력을 오류 본문에 그대로 되넣는다.** 그래서 짝이
    없는 서로게이트 문자(`\\ud800` 하나만 있고 뒤따르는 짝이 없는 것)를 보내면,
    검증은 제대로 거절해 놓고 그 거절 사유를 JSON 으로 옮기다 UnicodeEncodeError
    가 나서 **500** 이 나간다. 거절당할 값을 보냈는데 "서버가 부서졌다" 는 답이
    돌아오는 셈이라, 진짜 고장과 구별이 안 된다.

    한 엔드포인트의 문제가 아니다 — 되돌려 보내는 칸이 있는 자리는 전부 같다
    (`/cloze/answer` 의 `said`·`word` 도 똑같이 500 이었다). 그래서 필드마다
    막지 않고 이 문 하나에서 막는다.

    입력을 되넣기는 하되 **옮길 수 있는 글자로 바꿔서** 넣는다. 무엇이 잘못됐는지
    알려 주는 것이 검증 오류의 일이므로 값을 통째로 지우지는 않는다.
    """
    # **본문 모양은 그대로 둔다.** FastAPI 기본 처리기는 `{"detail": [...]}` 를 주고
    # 화면(`ui_web/src/api/client.ts`)이 그 `detail` 배열의 첫 항목에서 `msg` 를
    # 꺼내 오류 문구를 만든다. 처음에 `exc.errors()` 를 그대로 실어 보냈다가
    # 최상위가 배열이 되어, 500 을 막으면서 오류 문구 경로를 죽였다.
    return JSONResponse(status_code=422, content={"detail": _utf8_safe(exc.errors())})


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """4xx 도 같은 이유로 부서진다. `detail` 에 학습자가 보낸 값이 들어가기 때문이다.

    `raise HTTPException(404, f"모르는 단어예요: {req.word}")` 처럼 입력을 그대로
    끼워 넣는 자리가 여럿이고, 그 값이 JSON 으로 못 옮기는 글자면 404 대신 500 이
    나간다. 자리마다 다듬는 대신 나가는 문 하나에서 다듬는다.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _utf8_safe(exc.detail)},
        headers=getattr(exc, "headers", None),
    )


# 오류 본문에 되싣는 글자의 상한. 무엇이 잘못됐는지 보여 주기에는 넉넉하고,
# 되비추기로 쓰기에는 쓸모없는 길이다.
MAX_ECHO_CHARS = 200


def _utf8_safe(value: object) -> object:
    """JSON 으로 옮길 수 없는 글자를 바꾸고, **되싣는 값의 길이를 자른다.**

    자르는 이유: `max_length` 를 걸어도 5MB 가 그대로 돌아온다. pydantic 이
    거절한 값을 `errors()[0]["input"]` 에 통째로 담고 처리기가 그걸 실어 보내기
    때문이다. 필드마다 제한을 붙이는 것으로는 못 막고, 되싣는 자리에서 잘라야 한다.

    실측(자르기 전): chat message 5MB → 422 인데 본문 5,000,152B. 시범으로 바깥에
    열어 두는 앱이라 무제한 응답을 남겨 둘 자리가 아니다.
    """
    if isinstance(value, str):
        out = value.encode("utf-8", "replace").decode("utf-8")
        if len(out) > MAX_ECHO_CHARS:
            return out[:MAX_ECHO_CHARS] + f"… ({len(out)}자)"
        return out
    if isinstance(value, dict):
        return {str(k): _utf8_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_utf8_safe(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # ValueError 같은 것이 섞여 온다. 글자로 바꾼 뒤 같은 검사를 한 번 더 받는다.
    return _utf8_safe(str(value))


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
    # 길이를 막는다. 이 값은 404 의 detail 에 그대로 들어가는데, 제한이 없어서
    # 5MB 를 보내면 5MB 가 돌아왔다.
    scenario_id: str = Field(min_length=1, max_length=64)
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
    # 상한을 둔다. 이 칸만 열려 있어서 5만 항목이 200 으로 통과해 DB 에 들어갔다.
    # 한 발화의 낱말 수라 200 이면 넉넉하다.
    transcript_words: list[dict] | None = Field(default=None, max_length=200)


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


class SpellHintOut(BaseModel):
    """철자 단서 한 걸음. **정답을 통째로 드러내는 단계는 없다.**

    영어를 아예 모르는 학습자를 위한 것이다. 지금 빈칸이 주는 단서는 낱말 뜻·문장
    해석·문형·품사뿐이고 넷 다 한국어라, 알파벳을 못 읽는 사람은 뜻을 다 알고도
    첫 글자를 못 적는다. 그 사람에게 빈칸은 문제가 아니라 벽이다.

    응답에 단계를 다 실어 보내고 **언제 보여줄지는 화면이 정한다.** 한 걸음마다
    서버에 다시 묻게 하면 답을 적는 도중에 왕복이 생기고, 무엇보다 이건 시험이
    아니라 연습장이다 — 해석을 가리지 않고 그대로 보여 주기로 한 것과 같은 결정이다.
    """

    step: int
    label_ko: str
    text_ko: str
    # 아직 안 드러난 글자를 밑줄로 둔 모양. `s _ _ _`
    shape: str


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
    # 철자 단서. 답이 한 글자면 빈 목록이다 — 글자 수가 곧 정답이라 줄 것이 없다.
    spell_hints: list[SpellHintOut] = []


class TopicOut(BaseModel):
    """장면 묶음 하나. 다른 회화 앱의 '유닛'에 해당한다."""

    topic: str
    # 학습자가 읽을 이름. 화면이 `{"cafe": "카페"}` 를 따로 들고 있게 하지 않는다 —
    # 그러면 팩을 하나 더 만들 때마다 서버와 화면 두 곳을 고쳐야 하고, 한쪽을
    # 빠뜨리면 화면에만 영어 이름이 남는다. 나중에 붙은 칸이라 기본값을 둔다.
    label_ko: str = ""
    total: int
    reviewed: int


class WordCardOut(BaseModel):
    """읽기용 낱말 하나. 빈칸(`ClozeOut`)과 달리 **가리는 것이 없다.**

    두 화면이 같은 표를 읽지만 하는 일이 반대다. 연습장은 답을 숨겨야 하므로
    `mask_answer` 로 뜻·해석에서 표제어 흔적을 지우고, 이 목록은 그 낱말을
    외우러 온 사람에게 보여주는 것이라 지울 것이 없다. 그래서 스키마를 따로 뒀다 —
    한 스키마에 `masked` 같은 깃발을 두면 언젠가 반대로 세팅된 채 나간다.

    CEFR 레벨은 넣지 않는다. 토익 어휘는 난이도로 묶은 목록이 아니라 **빈도로 줄
    세운** 목록이고, 같은 화면에 A1/B1 딱지가 붙으면 학습자가 그걸 순서로 읽는다.
    축은 `rank` 하나다.
    """

    word: str
    # 빈도 순위. 1이 가장 자주 쓰인다. **트랙 안에서만 뜻이 있는 값이다.**
    rank: int | None = None
    meaning_ko: str
    example: str
    # 예문 그 문장의 해석. 2,252개 중 2,128개만 채워져 있어 없을 수 있다.
    example_ko: str | None = None
    pattern: str | None = None
    reviewed: bool


class WordPageOut(BaseModel):
    """낱말 목록 한 장.

    `next_offset` 을 서버가 정해 준다. 안전 판정에 걸린 행이 중간에서 빠지므로
    화면이 `offset + len(items)` 로 다음 자리를 계산하면 그만큼씩 앞으로
    밀린다 — 걸러진 낱말 자리에 다음 낱말이 당겨져 오는 게 아니라, 그 뒤가
    통째로 건너뛰어진다. 끝에 닿으면 `null` 이다.
    """

    total: int
    next_offset: int | None
    items: list[WordCardOut]


class ClozeAnswerRequest(BaseModel):
    """두 칸 다 **응답으로 되돌아 나가는** 값이라 들어올 때 다듬는다.

    `said` 는 학습자가 자유롭게 적는 칸이라 문법 문제처럼 모양을 못 박을 수 없다.
    그래도 길이는 막는다 — 5MB 를 보내면 5MB 가 그대로 돌아 나오던 자리다.

    짝 없는 서로게이트는 여기서 안 다듬는다. 한때 `field_validator` 로 걷어냈는데
    **그 코드에 닿는 길이 없었다** — pydantic-core 가 그보다 먼저 `string_unicode`
    로 거절해서 검증자가 돌지 않는다. 그 뒤는 오류 처리기(`_validation_error`)가
    맡는다.
    """

    word: str = Field(min_length=1, max_length=64)
    said: str = Field(max_length=200, description="학습자가 말했거나 적은 답")
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


def _word_card(row) -> WordCardOut:
    """행 하나를 읽기용 카드로. 가리는 것이 없다(`_cloze_out` 과 반대다)."""
    return WordCardOut(
        word=row.word,
        rank=row.rank,
        meaning_ko=row.meaning_ko,
        example=row.example,
        example_ko=row.example_ko,
        pattern=row.pattern,
        reviewed=row.reviewed,
    )


# 단어장 한 번에 받아올 수 있는 낱말 수. 폰 주소 길이(대개 2,000자 안팎)를 넘기지
# 않으려는 값이기도 하다 — 표제어 평균 8자면 200개가 1,800자쯤이다.
MAX_SAVED_WORDS = 200


@app.get("/words", response_model=WordPageOut)
def list_words(
    track: str = TRACK_GENERAL,
    offset: int = 0,
    count: int = 30,
    words: str | None = None,
) -> WordPageOut:
    """한 트랙의 낱말을 **빈도 순으로 읽기용** 한 장씩 준다.

    빈칸(`/cloze`)과 무엇이 다른가. 저쪽은 문제라서 답을 숨기고 한 번에 한 문장씩
    나가지만, 이건 외우러 온 사람이 죽 훑는 목록이라 뜻·예문·해석을 그대로 보여
    준다. 같은 표를 읽어도 나가는 모양이 반대다.

    `track` 기본값이 생활 회화인 것은 `/cloze` 와 같은 이유다 — 빼먹은 호출이
    왕초보용 낱말을 받게 해 둔다. 토익 어휘는 `track=toeic` 으로만 나온다.

    안전 판정(`is_safe_to_serve`)을 여기서도 통과시킨다. 목록에 보이는 낱말은
    그 자리에서 "빈칸으로 연습" 을 누를 수 있어야 하는데, 두 화면의 문이 다르면
    목록에 있던 낱말이 연습에서는 안 나온다. 걸러진 만큼 다음 자리를 당겨
    채우려고 넉넉히 읽어 온다.

    `words` 는 **단어장**이 쓴다. 사용자가 담아 둔 낱말을 쉼표로 이어 보내면 그것들만
    빈도 순으로 돌려준다. 화면이 카드 내용을 통째로 저장하지 않고 표제어만 들고
    있게 하려는 것이다 — 내용을 폰에 복사해 두면 오늘처럼 뜻을 고친 뒤에도
    `squid` 의 단어장 카드에는 '감자전' 이 영영 남는다.
    """
    from .db import crud
    from .db.database import db_session

    count = max(1, min(count, 60))
    with db_session() as db:
        if words is not None:
            wanted = [w.strip().lower() for w in words.split(",") if w.strip()][:MAX_SAVED_WORDS]
            rows = crud.words_named(db, wanted, track=track) if wanted else []
            picked = [_word_card(r) for r in rows if cloze_mod.is_safe_to_serve(r)]
            return WordPageOut(total=len(picked), next_offset=None, items=picked)

        total = crud.track_total(db, track=track)
        items: list[WordCardOut] = []
        cursor = max(0, offset)
        # 걸러지는 비율이 토익 트랙에서 6.7% 라 두 배면 거의 늘 한 장을 채운다.
        # 못 채우면 아래 while 이 한 번 더 읽는다.
        while len(items) < count and cursor < total:
            rows = crud.words_by_rank(db, track=track, offset=cursor, limit=count * 2)
            if not rows:
                break
            for row in rows:
                cursor += 1
                if not cloze_mod.is_safe_to_serve(row):
                    continue
                items.append(_word_card(row))
                if len(items) == count:
                    break
        return WordPageOut(
            total=total,
            next_offset=cursor if cursor < total and items else None,
            items=items,
        )


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
        spell_hints=[SpellHintOut(**vars(h)) for h in cloze_mod.spell_hints(item)],
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


# ─────────────────────────────────────────────── 문법 문제 (토익 Part 5 형)


class GrammarChoiceOut(BaseModel):
    """보기 하나. **어느 모양인지는 보내지 않는다** — 그게 곧 정답이다.

    보기 옆에 모양 이름을 적어 두면 규칙을 아는 학습자가 문장을 안 읽고도 고를 수
    있다. 이름은 채점한 뒤에야 나간다.

    독스트링에 규칙을 그대로 적지 않는 이유도 같다 — 이 글은 `/openapi.json` 에
    실려 나가고, 그 문서를 열면 규칙이 한국어로 적혀 있게 된다.
    """

    word: str


class GrammarOut(BaseModel):
    """학습자에게 내보내는 문제. **정답을 넣지 않는다** — 채점은 서버가 한다.

    보기 순서도 여기서 굳혀 보낸다. 화면이 섞으면 새로고침마다 답의 자리가 달라져
    서버가 채점한 것과 학습자가 본 화면이 어긋난다.
    """

    id: str
    rule: str
    sentence: str
    sentence_ko: str
    choices: list[GrammarChoiceOut]


class GrammarAnswerRequest(BaseModel):
    """**두 칸 다 모양을 못 박는다.** 그러지 않으면 그대로 되돌아 나가는 값이다.

    되돌려 보내는 칸에 아무거나 받으면 두 가지가 샌다. 5MB 를 보내면 404 의
    detail 에 5MB 가 담겨 돌아오고, 짝이 없는 서로게이트 문자를 보내면 JSON 으로
    옮기다 500 이 난다. 학습자가 낼 수 있는 값이 아니라 굳이 만들어 보내는
    값이지만, 500 은 "서버가 부서졌다" 는 뜻이라 그렇게 답하면 안 된다.

    `id` 는 16진수 열두 자이고 `chosen` 은 영어 낱말이라 둘 다 모양이 정해져
    있다. 정해져 있는 것은 pydantic 이 문 앞에서 거르게 둔다.
    """

    id: str = Field(
        max_length=64,
        pattern=r"^[0-9a-f]{12}$",
        description="GET /grammar 가 준 문제 id",
    )
    chosen: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z'\- ]*$",
        description="학습자가 고른 보기의 낱말",
    )


class GrammarAnswerOut(BaseModel):
    ok: bool
    answer: str
    chosen: str
    # 이 문제가 묻는 규칙의 이름("to 다음에는 동사원형"). **채점 뒤에만 나간다.**
    #
    # 문제와 함께 보내면 그게 곧 답이다 — 규칙이 하나뿐이라 제목만 읽어도
    # 동사원형을 고르면 된다. 정답을 응답에서 빼고 id 에서 지우고 보기 순서까지
    # 소금으로 가려 놓고, 제목 한 칸으로 평문으로 내주고 있었다.
    rule_title: str = ""
    message_ko: str
    # 보기 넷이 각각 어느 모양인지. 채점한 뒤에만 나간다.
    why_ko: list[str]


@lru_cache(maxsize=1)
def _grammar_index() -> dict[str, tuple[str, grammar.GrammarItem]]:
    """문제 id → (규칙 이름, 문제). 채점이 id 하나로 문제를 되찾게 한다.

    문제는 데이터 파일에서 결정론적으로 만들어지므로 미리 다 펼쳐 둬도 된다
    (지금 134개다). 화면이 문제를 통째로 돌려보내게 하면 학습자가 보기를 바꿔
    보낼 수 있어서, **서버가 갖고 있는 것으로만 채점한다.**
    """
    out: dict[str, tuple[str, grammar.GrammarItem]] = {}
    for name, rule in grammar.rules().items():
        for item in grammar.items_of(rule):
            out[item.id] = (name, item)
    return out


@app.get("/grammar", response_model=list[GrammarOut])
def list_grammar(rule: str = "to_infinitive", count: int = 10, offset: int = 0) -> list[GrammarOut]:
    """문법 문제를 준다. 모르는 규칙 이름이면 빈 배열이다(404 가 아니다).

    빈 배열로 두는 이유는 화면이 이것을 목록으로 그리기 때문이다 — 규칙이 아직
    없는 것과 문제가 떨어진 것을 화면에서 같게 다루는 편이 낫다.
    """
    known = grammar.rules()
    if rule not in known:
        return []
    count = max(1, min(count, 50))
    offset = max(0, offset)
    picked = grammar.items_of(known[rule])[offset : offset + count]
    return [
        GrammarOut(
            id=item.id,
            rule=item.rule,
            sentence=item.sentence,
            sentence_ko=item.sentence_ko,
            choices=[GrammarChoiceOut(word=c.word) for c in item.choices],
        )
        for item in picked
    ]


@app.post("/grammar/answer", response_model=GrammarAnswerOut)
def answer_grammar(req: GrammarAnswerRequest) -> GrammarAnswerOut:
    """채점하고 왜 그런지 말한다. 보기 넷의 정체는 여기서 처음 나간다."""
    found = _grammar_index().get(req.id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"모르는 문제예요: {req.id}")
    rule_name, item = found
    verdict = grammar.grade(item, req.chosen, grammar.rules()[rule_name])
    rule = grammar.rules()[rule_name]
    return GrammarAnswerOut(
        ok=verdict.ok,
        answer=verdict.answer,
        chosen=verdict.chosen,
        # 보기 중에서 고르지 않았으면 아직 답한 것이 아니라 규칙 이름도 안 준다.
        rule_title=rule.title if verdict.answer else "",
        message_ko=verdict.message_ko,
        why_ko=verdict.why_ko,
    )


# ─────────────────────────────────────────────── 화면 (React 빌드 결과)
#
# **반드시 맨 마지막이다.** 이 줄이 `/` 아래를 통째로 잡는 catch-all 을 등록하는데,
# 라우트는 등록 순서대로 맞춰 보므로 위의 API 들보다 앞서면 `/chat` 이 화면에
# 가려진다. 새 엔드포인트는 항상 이 줄 **위에** 추가한다.
mount_spa(app)
