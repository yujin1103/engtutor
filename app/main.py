"""FastAPI 엔트리포인트."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .llm.base import LLMError
from .llm.factory import get_client
from .session_store import InMemorySessionStore
from .tutor.loader import Scenario, get_scenarios
from .tutor.schemas import TurnResponse
from .tutor.service import TutorService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="engtutor", version="0.1.0")
store = InMemorySessionStore()


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

    store.append(session.id, {"role": "user", "content": req.message})
    store.append(session.id, {"role": "assistant", "content": turn.reply})
    return ChatResponse(session_id=session.id, turn=turn)
