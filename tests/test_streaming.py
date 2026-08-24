"""스트리밍 경로.

지키려는 성질은 두 가지다.
1. reply 가 완성되기 전부터 흘러나온다 (빈 화면 시간 단축)
2. 그럼에도 최종 결과는 비스트리밍과 **똑같이** pydantic 검증을 통과한 것만 나간다
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.llm import ollama_client as ollama_mod
from app.llm.base import LLMClient, LLMError, StreamChunk
from app.llm.ollama_client import OllamaClient
from app.tutor.loader import get_scenarios
from app.tutor.schemas import TurnResponse
from app.tutor.service import TutorService

GOOD = {
    "reply": "Sure! What size would you like?",
    "reply_ko": "네! 어떤 사이즈로 드릴까요?",
    "corrections": [
        {
            "original": "I want ice americano",
            "kind": "mistake",
            "better": "Can I get an iced americano?",
            "note": "주문할 땐 Can I get 이 자연스러워요.",
        }
    ],
    "say_en": "Large.",
    "say_more": "A large one, please.",
    "hint_ko": "사이즈를 물어봤어요.",
}
BAD = {"reply": "Sure!", "hint_ko": "..."}  # 필수 필드 누락 -> 검증 실패


# ------------------------------------------------------------------ 가짜 백엔드
class FakeClient(LLMClient):
    """호출될 때마다 `payloads` 를 하나씩 소비한다."""

    name = "fake"

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self.payloads = list(payloads)
        self.calls: list[str] = []

    def describe(self) -> str:
        return "fake"

    def ping(self) -> bool:
        return True

    def chat_json(self, **_: Any) -> dict[str, Any]:
        self.calls.append("json")
        return self.payloads.pop(0)

    def chat_json_stream(self, *, stream_field: str = "reply", **_: Any):
        self.calls.append("stream")
        data = self.payloads.pop(0)
        for ch in str(data.get(stream_field, "")):
            yield StreamChunk(delta=ch, done=False, data=None)
        yield StreamChunk(delta="", done=True, data=data)


@pytest.fixture()
def scenario():
    return get_scenarios()["cafe_order"]


def _run(service: TutorService, scenario) -> list[dict[str, Any]]:
    return list(
        service.respond_stream(scenario=scenario, level="A1", history=[], user_text="hi")
    )


def _service(client: LLMClient) -> TutorService:
    return TutorService(client)


# ------------------------------------------------------------------ 서비스 계층
def test_stream_emits_deltas_then_a_validated_turn(scenario):
    events = _run(_service(FakeClient(GOOD)), scenario)

    assert events[-1]["type"] == "turn"
    assert isinstance(events[-1]["turn"], TurnResponse)

    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas, "델타가 하나도 안 나왔다 — 스트리밍이 아니다"
    assert "".join(deltas) == GOOD["reply"]


def test_first_delta_arrives_before_the_turn(scenario):
    """순서가 뒤집히면 화면에 먼저 띄울 것이 없어 스트리밍의 의미가 사라진다."""
    events = _run(_service(FakeClient(GOOD)), scenario)
    kinds = [e["type"] for e in events]
    assert kinds.index("delta") < kinds.index("turn")


def test_failed_stream_resets_then_repairs(scenario):
    """1차가 검증에 걸리면 보여준 글자를 취소하고, 재시도 결과를 준다."""
    client = FakeClient(BAD, GOOD)
    events = _run(_service(client), scenario)

    kinds = [e["type"] for e in events]
    assert "reset" in kinds, "폐기 신호 없이 재시도하면 화면과 DB 내용이 어긋난다"
    assert kinds.index("reset") < kinds.index("turn")
    assert events[-1]["turn"].reply == GOOD["reply"]
    # 재시도는 스트리밍이 아니라 수리 프롬프트를 붙인 일반 호출이어야 한다
    assert client.calls == ["stream", "json"]


def test_both_attempts_failing_raises(scenario):
    service = _service(FakeClient(BAD, BAD))
    with pytest.raises(LLMError):
        _run(service, scenario)


def test_stream_and_respond_agree(scenario):
    """같은 응답이면 두 경로의 결과가 같아야 한다 — 스트리밍은 표시 시점만 바꾼다."""
    streamed = _run(_service(FakeClient(GOOD)), scenario)[-1]["turn"]
    plain = _service(FakeClient(GOOD)).respond(
        scenario=scenario, level="A1", history=[], user_text="hi"
    )
    assert streamed.model_dump() == plain.model_dump()


def test_default_implementation_covers_non_streaming_backends():
    """스트리밍을 구현하지 않은 백엔드(Anthropic)도 같은 계약으로 동작해야 한다."""

    class PlainOnly(LLMClient):
        name = "plain"

        def describe(self) -> str:
            return "plain"

        def ping(self) -> bool:
            return True

        def chat_json(self, **_: Any) -> dict[str, Any]:
            return GOOD

    chunks = list(
        PlainOnly().chat_json_stream(system="s", messages=[], schema={}, stream_field="reply")
    )
    assert chunks[0]["delta"] == GOOD["reply"]
    assert chunks[-1]["done"] and chunks[-1]["data"] == GOOD


# ------------------------------------------------------------------ Ollama 클라이언트
def _mock_ollama(monkeypatch, body: bytes, *, chunk_size: int = 5) -> list[dict[str, Any]]:
    """NDJSON 응답을 임의 바이트 경계로 잘라 흘려보내는 가짜 Ollama."""
    seen: list[dict[str, Any]] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))

        def gen():
            for i in range(0, len(body), chunk_size):
                yield body[i : i + chunk_size]

        return httpx.Response(200, content=gen())

    def factory(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ollama_mod.httpx, "Client", factory)
    return seen


def _ndjson(text: str, *, piece: int = 4) -> bytes:
    lines = [
        json.dumps({"message": {"content": text[i : i + piece]}, "done": False})
        for i in range(0, len(text), piece)
    ]
    lines.append(json.dumps({"message": {"content": ""}, "done": True}))
    return "".join(line + "\n" for line in lines).encode()


def test_ollama_stream_parses_ndjson_across_chunk_boundaries(monkeypatch):
    """줄이 바이트 경계에서 잘려도 조립돼야 한다 — 실제 소켓에서 늘 일어난다."""
    full = json.dumps(GOOD, ensure_ascii=False)
    seen = _mock_ollama(monkeypatch, _ndjson(full), chunk_size=3)

    client = OllamaClient(base_url="http://ollama:11434", model="qwen3:14b")
    chunks = list(
        client.chat_json_stream(system="sys", messages=[], schema={"type": "object"})
    )

    assert seen[0]["stream"] is True, "stream 플래그를 안 켜면 스트리밍이 아니다"
    assert seen[0]["format"] == {"type": "object"}, "스키마 강제는 스트리밍에서도 유지된다"

    assert "".join(c["delta"] for c in chunks) == GOOD["reply"]
    assert chunks[-1]["done"] and chunks[-1]["data"] == GOOD


def test_ollama_stream_extracts_korean_field(monkeypatch):
    _mock_ollama(monkeypatch, _ndjson(json.dumps(GOOD, ensure_ascii=False)))
    client = OllamaClient(base_url="http://ollama:11434", model="qwen3:14b")
    chunks = list(
        client.chat_json_stream(
            system="sys", messages=[], schema={}, stream_field="hint_ko"
        )
    )
    assert "".join(c["delta"] for c in chunks) == GOOD["hint_ko"]


def test_ollama_stream_surfaces_server_error(monkeypatch):
    body = json.dumps({"error": "model not found"}).encode() + b"\n"
    _mock_ollama(monkeypatch, body)
    client = OllamaClient(base_url="http://ollama:11434", model="nope")
    with pytest.raises(LLMError, match="model not found"):
        list(client.chat_json_stream(system="s", messages=[], schema={}))


# ------------------------------------------------------------------ SSE 엔드포인트
def _collect_sse(response) -> list[dict[str, Any]]:
    async def drain() -> list[str]:
        return [chunk async for chunk in response.body_iterator]

    events = []
    for frame in "".join(asyncio.run(drain())).split("\n\n"):
        if frame.startswith("data: "):
            events.append(json.loads(frame[6:]))
    return events


@pytest.fixture()
def sse_app(monkeypatch):
    """DB 를 건드리지 않고 /chat/stream 을 호출할 수 있게 갈아끼운다."""
    from app import main
    from app.session_store import InMemorySessionStore

    client = FakeClient(GOOD)
    monkeypatch.setattr(main, "store", InMemorySessionStore())
    monkeypatch.setattr(main, "get_client", lambda: client)
    return main, client


def test_sse_frames_are_well_formed(sse_app):
    main, _ = sse_app
    req = main.ChatRequest(scenario_id="cafe_order", message="I want ice americano")
    events = _collect_sse(main.chat_stream(req))

    assert events[0]["type"] == "session" and events[0]["session_id"]
    assert events[-1]["type"] == "turn"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == GOOD["reply"]

    turn = events[-1]["turn"]
    assert set(turn) == {
        "reply", "reply_ko", "corrections", "say_en", "say_more", "hint_ko",
    }
    assert turn["hint_ko"] == GOOD["hint_ko"]


def test_sse_never_breaks_framing_on_newlines(sse_app):
    """reply 에 개행이 있어도 SSE 프레임이 쪼개지면 안 된다."""
    main, client = sse_app
    client.payloads = [{**GOOD, "reply": "Line one.\n\nLine two."}]

    req = main.ChatRequest(scenario_id="cafe_order", message="hi")
    events = _collect_sse(main.chat_stream(req))
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Line one.\n\nLine two."


def test_sse_stores_the_turn_once(sse_app):
    main, _ = sse_app
    req = main.ChatRequest(scenario_id="cafe_order", message="I want ice americano")
    events = _collect_sse(main.chat_stream(req))

    session_id = events[0]["session_id"]
    stored = main.store.get(session_id)
    assert [m["role"] for m in stored.messages] == ["user", "assistant"]
    assert stored.messages[1]["content"] == GOOD["reply"]


def test_sse_gentle_mode_drops_polish(sse_app):
    """유연 모드 서버측 필터가 스트리밍 경로에서도 걸리는지."""
    main, client = sse_app
    client.payloads = [
        {
            **GOOD,
            "corrections": [
                *GOOD["corrections"],
                {
                    "original": "Large",
                    "kind": "polish",
                    "better": "Large, please.",
                    "note": "please 를 붙이면 부드러워요.",
                },
            ],
        }
    ]

    req = main.ChatRequest(scenario_id="cafe_order", message="hi", strictness="gentle")
    events = _collect_sse(main.chat_stream(req))
    kinds = [c["kind"] for c in events[-1]["turn"]["corrections"]]
    assert kinds == ["mistake"]


def test_sse_reports_errors_as_events(sse_app):
    """스트림은 이미 200 으로 시작됐으므로 상태코드가 아니라 사건으로 알려야 한다."""
    main, client = sse_app
    client.payloads = [BAD, BAD]

    req = main.ChatRequest(scenario_id="cafe_order", message="hi")
    events = _collect_sse(main.chat_stream(req))
    assert events[-1]["type"] == "error"
    assert not any(e["type"] == "turn" for e in events)


def test_unknown_scenario_still_returns_404(sse_app):
    """스트리밍이 시작되기 전에 검사해야 정상 HTTP 오류를 줄 수 있다."""
    from fastapi import HTTPException

    main, _ = sse_app
    req = main.ChatRequest(scenario_id="nope", message="hi")
    with pytest.raises(HTTPException) as exc:
        main.chat_stream(req)
    assert exc.value.status_code == 404
