"""FastAPI 엔트리포인트."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
    opening_hint_ko: str

    @classmethod
    def of(cls, scenario: Scenario) -> "ScenarioOut":
        return cls(**scenario.model_dump())


class ChatRequest(BaseModel):
    scenario_id: str
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    level: Literal["A1", "A2"] | None = None


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


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
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

    service = TutorService(get_client())
    try:
        turn = service.respond(
            scenario=scenario,
            level=session.level,
            history=session.messages,
            user_text=req.message,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    store.record_turn(session.id, user_text=req.message, turn=turn)
    return ChatResponse(session_id=session.id, turn=turn)


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
