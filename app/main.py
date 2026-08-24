"""FastAPI 엔트리포인트."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .db.database import init_db
from .llm.base import LLMError
from .llm.factory import get_client
from .report.schemas import SessionReport
from .report.service import ReportService
from .session_store import SqliteSessionStore
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
    level: str
    situation: str
    goal: str
    opening_line: str
    opening_say_en: str
    opening_say_more: str
    opening_hint_ko: str

    @classmethod
    def of(cls, scenario: Scenario) -> "ScenarioOut":
        return cls(**scenario.model_dump())


class ChatRequest(BaseModel):
    scenario_id: str
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    level: Literal["A1", "A2"] | None = None
    strictness: Strictness = DEFAULT_STRICTNESS


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
    }


@app.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios() -> list[ScenarioOut]:
    return [ScenarioOut.of(s) for s in get_scenarios().values()]


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
    else:
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
    store.record_turn(session_id, user_text=req.message, turn=turn)
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
