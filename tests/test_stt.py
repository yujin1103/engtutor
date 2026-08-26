"""/stt 의 계약과 방어선.

**모델은 올리지 않는다.** 500MB 를 올리고 CPU 로 전사하면 테스트가 분 단위가 되고,
그러면 아무도 안 돌린다. 전사 품질은 녹음으로 따로 잰다(scripts/eval_stt.py).
여기서 고정하는 것은 화면이 의지하는 것들이다.

  - 응답 키 이름 (UI 가 dict 로 꺼내 쓴다)
  - 안 들렸을 때 **200 + 빈 문자열** — 400 이 아니다. 마이크만 누르고 말을 안 한
    흔한 경우라, 오류로 다루면 화면이 빨개진다.
  - 상한 초과 413, STT 꺼짐 503 — 둘 다 한국어 안내가 붙어야 한다.

전사 옵션은 코드가 아니라 측정으로 정해졌다. 되돌리기 쉬운 값들이라 여기서
못을 박아 둔다 — 특히 vad_filter 는 끄면 무음에서 없는 말이 지어진다.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.stt import LEARNER_STYLE_PROMPT, SttService, get_stt_service
from app.stt import service as stt_service


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """모델을 절대 올리지 않는 클라이언트. _ensure_model 이 불리면 그게 버그다."""

    def explode(self: SttService):  # pragma: no cover - 불리면 실패시키는 것이 목적
        raise AssertionError("테스트가 실제 모델을 올리려 했다")

    monkeypatch.setattr(SttService, "_ensure_model", explode)
    get_stt_service.cache_clear()
    yield TestClient(app)
    get_stt_service.cache_clear()


def _upload(data: bytes, name: str = "voice.webm") -> dict:
    return {"file": (name, data, "audio/webm")}


def test_빈_파일은_오류가_아니라_빈_전사다(client: TestClient) -> None:
    response = client.post("/stt", files=_upload(b""))
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == ""
    assert body["words"] == []


def test_응답_키는_UI_계약이다(client: TestClient) -> None:
    body = client.post("/stt", files=_upload(b"")).json()
    assert set(body) == {"text", "words", "duration_ms", "audio_seconds", "model"}


def test_상한을_넘으면_413_과_한국어_안내(client: TestClient, monkeypatch) -> None:
    from app.config import get_settings

    limit = int(get_settings().stt_max_upload_mb * 1024 * 1024)
    response = client.post("/stt", files=_upload(b"\x00" * (limit + 1)))
    assert response.status_code == 413
    assert "MB" in response.json()["detail"]


def test_꺼져_있으면_503_과_켜는_법(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("STT_ENABLED", "false")
    get_settings.cache_clear()
    get_stt_service.cache_clear()
    try:
        response = TestClient(app).post("/stt", files=_upload(b"\x00" * 10))
        assert response.status_code == 503
        assert "STT_ENABLED" in response.json()["detail"]
    finally:
        get_settings.cache_clear()
        get_stt_service.cache_clear()


def test_faster_whisper_가_없으면_503_과_설치_안내(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SttService, "installed", staticmethod(lambda: False))
    response = client.post("/stt", files=_upload(b"\x00" * 10))
    assert response.status_code == 503
    assert "build" in response.json()["detail"]


def test_healthz_가_STT_상태를_알려준다(client: TestClient) -> None:
    stt = client.get("/healthz").json()["stt"]
    # loaded 가 false 인 것이 정상이다 — 첫 요청 전에는 올리지 않는다.
    assert stt["loaded"] is False
    assert stt["model"]


def test_전사_옵션은_측정으로_고정됐다(monkeypatch: pytest.MonkeyPatch) -> None:
    """되돌리기 쉬운 값들이라 못을 박는다. 특히 vad_filter.

    무음에서 Whisper 가 "I'm sorry" 를 56번 지어낸 일이 실제로 있었다.
    """
    captured: dict = {}

    class FakeModel:
        def transcribe(self, audio, **options):
            captured.update(options)

            class Info:
                duration = 1.0

            return iter(()), Info()

    service = SttService()
    service._model = FakeModel()
    result = service.transcribe(b"")

    assert captured["vad_filter"] is True
    assert captured["temperature"] == 0.0
    assert captured["condition_on_previous_text"] is False
    assert captured["word_timestamps"] is True
    assert captured["language"] == "en"
    # 학습자 문체 표본. 표준 영어를 주면 모델이 학습자의 오류를 지운다.
    assert captured["initial_prompt"] == LEARNER_STYLE_PROMPT
    assert result.text == ""
    assert result.empty


def test_문체_표본에_시나리오_대사를_넣지_않는다() -> None:
    """목표 문장을 알려 주면 Whisper 가 오디오가 아니라 프롬프트를 받아 적는다.

    측정에서 틀리게 말한 10문장 중 9개가 **맞는 문장으로** 전사됐다. 그래서 이
    표본은 시나리오와 무관한 학습자 문체 문장들이어야 한다.
    """
    from app.tutor.loader import get_scenarios

    prompt = LEARNER_STYLE_PROMPT.lower()
    for scenario in get_scenarios().values():
        assert scenario.opening_line.lower() not in prompt
        assert scenario.opening_say_en.lower() not in prompt
    # 표본 자체는 학습자가 흔히 틀리는 문장이어야 한다.
    assert "I have 20 years old" in stt_service.LEARNER_STYLE_PROMPT


def test_전사_결과는_transcript_모듈이_읽는_모양이다() -> None:
    """/stt 의 words 는 그대로 /chat 의 transcript_words 로 되돌아온다."""
    from app.tutor.transcript import parse_words

    words = parse_words([{"word": " iced", "probability": 0.83}, {"word": " tea"}])
    assert [w.word for w in words] == ["iced", "tea"]
    assert words[0].probability == pytest.approx(0.83)
    assert words[1].probability is None


# --------------------------------------------------------------- 가짜 Whisper
#
# 아래 시험들은 **모델을 올리지 않는다.** faster-whisper 가 돌려주는 모양만
# 흉내 낸다: segments 는 제너레이터, 낱말은 `.word` · `.probability`, 정보는
# `.duration`. 실제 전사 품질은 녹음으로 따로 잰다(scripts/eval_stt.py).


class FakeWord:
    """faster-whisper 의 낱말. probability 를 아예 안 주는 경우도 있다."""

    def __init__(self, word: str, probability: float | None = None) -> None:
        self.word = word
        if probability is not None:
            self.probability = probability


class FakeSegment:
    def __init__(self, text: str, words: list[FakeWord] | None = None) -> None:
        self.text = text
        self.words = words or []


class FakeInfo:
    def __init__(self, duration: float) -> None:
        self.duration = duration


class FakeModel:
    """받은 인자를 기록하고 정해진 결과를 돌려준다. 터지게 만들 수도 있다."""

    def __init__(
        self,
        segments: list[FakeSegment] | None = None,
        duration: float = 3.2,
        error: Exception | None = None,
    ) -> None:
        self.segments = segments or []
        self.duration = duration
        self.error = error
        self.options: dict = {}
        self.calls = 0

    def transcribe(self, audio, **options):
        self.calls += 1
        self.options = options
        if self.error is not None:
            raise self.error
        # 진짜와 같이 제너레이터로 준다 — 소비해야 계산이 일어나는 구조라
        # 리스트로 주면 소비를 빼먹은 버그를 이 시험이 놓친다.
        return iter(self.segments), FakeInfo(self.duration)


HEARD = [
    FakeSegment(
        " I want ice americano.",
        [
            FakeWord(" I", 0.8254),
            FakeWord(" want", 0.8152),
            FakeWord(" ice", 0.3161),
            FakeWord(" americano.", 0.9466),
        ],
    )
]


@pytest.fixture
def voiced(monkeypatch: pytest.MonkeyPatch):
    """모델 자리에 가짜를 끼운 클라이언트. (TestClient, FakeModel, 서비스) 를 준다."""
    monkeypatch.setattr(SttService, "installed", staticmethod(lambda: True))
    get_stt_service.cache_clear()
    service = get_stt_service()
    model = FakeModel(list(HEARD))
    service._model = model
    try:
        yield TestClient(app), model, service
    finally:
        # 가짜를 놔두면 뒤 시험이 조용히 이걸 쓴다.
        service._model = None
        get_stt_service.cache_clear()


def test_정상_전사의_응답_모양(voiced) -> None:
    client, _, _ = voiced
    body = client.post("/stt", files=_upload(b"\x00" * 32)).json()

    assert body["text"] == "I want ice americano."
    assert [w["word"] for w in body["words"]] == [" I", " want", " ice", " americano."]
    assert body["words"][2]["probability"] == pytest.approx(0.3161)
    assert body["audio_seconds"] == pytest.approx(3.2)
    assert body["duration_ms"] >= 0
    assert body["model"]


def test_낱말_앞의_공백을_다듬지_않는다(voiced) -> None:
    """faster-whisper 는 낱말을 ` I` 처럼 준다. 이 형식이 그대로 계약이다.

    UI 는 이 배열을 손대지 않고 `/chat` 의 `transcript_words` 로 되돌려 보내고,
    `app/tutor/transcript.py` 의 `parse_words()` 가 그 형식을 그대로 읽는다.
    서버에서 미리 다듬으면 양쪽이 어긋난다.
    """
    from app.tutor.transcript import parse_words

    client, _, _ = voiced
    words = client.post("/stt", files=_upload(b"\x00" * 32)).json()["words"]

    assert all(w["word"].startswith(" ") for w in words)
    # 그대로 되돌려 보냈을 때 transcript 모듈이 읽어야 한다.
    # 앞 공백을 떼는 것은 저쪽 일이다 — 서버가 미리 하면 두 곳이 같은 일을 한다.
    assert [w.word for w in parse_words(words)] == ["I", "want", "ice", "americano."]


def test_확률이_없는_낱말은_None_으로_온다(voiced) -> None:
    """확률이 안 붙는 낱말이 있다. 그때 500 이 나면 안 된다."""
    client, model, _ = voiced
    model.segments = [FakeSegment(" Hello.", [FakeWord(" Hello.")])]

    body = client.post("/stt", files=_upload(b"\x00" * 32)).json()
    assert body["words"] == [{"word": " Hello.", "probability": None}]


def test_여러_구간은_한_문장으로_이어진다(voiced) -> None:
    client, model, _ = voiced
    model.segments = [
        FakeSegment(" I want ice americano.", [FakeWord(" I", 0.9)]),
        FakeSegment(" Large size please.", [FakeWord(" Large", 0.8)]),
    ]
    body = client.post("/stt", files=_upload(b"\x00" * 32)).json()

    assert body["text"] == "I want ice americano. Large size please."
    assert len(body["words"]) == 2


def test_안_들리면_200_에_빈_전사다(voiced) -> None:
    """vad_filter 가 다 걷어내면 구간이 하나도 없다. 오류가 아니다.

    마이크만 누르고 말을 안 한 흔한 경우다. 400 으로 돌려주면 화면이 빨개진다.
    """
    client, model, _ = voiced
    model.segments = []

    response = client.post("/stt", files=_upload(b"\x00" * 32))
    assert response.status_code == 200
    assert response.json()["text"] == ""
    assert response.json()["words"] == []


def test_오디오가_아니면_400_과_다시_녹음_안내(voiced) -> None:
    client, model, _ = voiced
    model.error = RuntimeError("Invalid data found when processing input")

    response = client.post("/stt", files=_upload(b"not audio at all"))
    assert response.status_code == 400
    assert "다시 녹음" in response.json()["detail"]


def test_전사가_한_번_터져도_다음_요청은_된다(voiced) -> None:
    """터진 자리에서 자물쇠가 잠긴 채 남으면 서버가 조용히 멎는다."""
    client, model, _ = voiced
    model.error = RuntimeError("boom")
    assert client.post("/stt", files=_upload(b"\x00" * 32)).status_code == 400

    model.error = None
    assert client.post("/stt", files=_upload(b"\x00" * 32)).status_code == 200


# --------------------------------------------------------------- 모델 로드
@contextmanager
def _service_at(monkeypatch: pytest.MonkeyPatch, directory):
    """모델 디렉터리를 임시 폴더로 돌린 서비스를 준다.

    `stt_model_dir` 은 pydantic 인스턴스 값이라 클래스에 setattr 해도 안 먹는다.
    환경변수를 바꾸고 설정 캐시를 비우는 것이 유일하게 되는 방법이고, 캐시는
    monkeypatch 가 되돌려 주지 않으므로 직접 비운다 — 안 비우면 다음 시험이
    조용히 사라진 임시 폴더를 모델 디렉터리로 쓴다.
    """
    from app.config import get_settings

    monkeypatch.setenv("STT_MODEL_DIR", str(directory))
    get_settings.cache_clear()
    try:
        yield SttService()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def _fake_whisper_module(monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    """`faster_whisper` 모듈을 통째로 가짜로 바꾼다. 500MB 를 올리지 않는다."""
    import sys
    import types

    module = types.ModuleType("faster_whisper")

    def factory(model_name, **kwargs):
        calls.append({"model": model_name, **kwargs})
        return FakeModel(list(HEARD))

    module.WhisperModel = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def test_모델은_한_번만_올린다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """두 번 불러도 로더는 한 번만. 요청마다 올리면 매번 500MB 와 2초가 날아간다.

    지연 로드와 자물쇠 안의 이중 확인이 살아 있는지를 본다.
    """
    calls: list = []
    _fake_whisper_module(monkeypatch, calls)

    with _service_at(monkeypatch, tmp_path) as service:
        service.load()
        service.load()
        service.transcribe(b"")

        assert len(calls) == 1
        assert service.loaded is True


def test_모델_디렉터리를_download_root_로_넘긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """컨테이너를 다시 만들어도 500MB 를 다시 받지 않으려면 볼륨 위에 둬야 한다."""
    calls: list = []
    _fake_whisper_module(monkeypatch, calls)

    with _service_at(monkeypatch, tmp_path) as service:
        service.load()

        assert calls[0]["download_root"] == str(tmp_path)
        assert calls[0]["model"] == service._s.stt_model
        # 정확도는 장치와 무관하다 — api 컨테이너에 CUDA 를 넣지 않는 이유다.
        assert calls[0]["device"] == service._s.stt_device


def test_꺼져_있으면_모델을_올리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """꺼진 상태에서 500MB 를 올리면 끈 의미가 없다."""
    from app.config import get_settings
    from app.stt import SttUnavailable

    calls: list = []
    _fake_whisper_module(monkeypatch, calls)
    monkeypatch.setenv("STT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        service = SttService()
        with pytest.raises(SttUnavailable, match="STT_ENABLED"):
            service.load()
        assert calls == []
    finally:
        get_settings.cache_clear()


def test_faster_whisper_가_없으면_빌드_안내로_터진다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """여기서 막힌 사람이 다음에 뭘 할지가 예외 문구에 있어야 한다."""
    import builtins

    from app.stt import SttUnavailable

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(SttUnavailable, match="build"):
        SttService().load()


# --------------------------------------------------------------- /chat 왕복
#
# /stt 의 결과가 실제로 DB 까지 가는지. 저장이 이 기능의 절반이다 — 전사와
# 학습자가 확정한 문장의 **차이**가 나중에 STT 를 믿어도 되는지에 답한다.


class _FakeLLM:
    """/chat 이 LLM 없이 돌게 하는 최소 클라이언트."""

    name = "fake"

    def describe(self) -> str:
        return "fake"

    def ping(self) -> bool:
        return True

    def chat_json(self, **_) -> dict:
        return {
            "reply": "Sure! What size?",
            "reply_ko": "네! 어떤 사이즈로 드릴까요?",
            "corrections": [
                {
                    "original": "I want ice americano",
                    "kind": "mistake",
                    "better": "Can I get an iced americano?",
                    "note": "ice 가 아니라 iced 예요.",
                }
            ],
            "say_en": "Large.",
            "say_more": "A large one, please.",
            "hint_ko": "사이즈를 물어봤어요.",
        }


@pytest.fixture
def chat_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """임시 SQLite 를 쓰는 /chat. (TestClient, database 모듈) 를 준다."""
    from .conftest import temporary_database

    with temporary_database(tmp_path / "chat.db", monkeypatch) as database:
        from app import main
        from app.session_store import SqliteSessionStore

        monkeypatch.setattr(main, "store", SqliteSessionStore())
        monkeypatch.setattr(main, "get_client", lambda: _FakeLLM())
        yield TestClient(main.app), database


def _user_row(database, session_id):
    from sqlalchemy import select

    from app.db.models import TurnRow

    with database.db_session() as db:
        return db.execute(
            select(TurnRow).where(TurnRow.session_id == session_id, TurnRow.role == "user")
        ).scalar_one()


def test_음성_턴은_확정본과_전사를_둘_다_남긴다(chat_db) -> None:
    """/stt 의 text·words 를 그대로 되돌려 보내면 DB 에 그대로 남아야 한다.

    하나만 남기면 둘의 차이가 사라진다. 그 차이가 STT 가 학습자의 틀린 영어를
    매끄럽게 고쳐 버린 흔적이다.
    """
    client, database = chat_db
    words = [
        {"word": " I", "probability": 0.8254},
        {"word": " want", "probability": 0.8152},
        {"word": " ice", "probability": 0.3161},
        {"word": " americano.", "probability": 0.9466},
    ]
    response = client.post(
        "/chat",
        json={
            "scenario_id": "cafe_order",
            "message": "I want ice americano.",   # 학습자가 확인한 문장
            "input_mode": "voice",
            "transcript": "I want ice americano.",
            "transcript_words": words,
        },
    )
    assert response.status_code == 200
    row = _user_row(database, response.json()["session_id"])

    assert row.input_mode == "voice"
    assert row.content == "I want ice americano."
    assert row.transcript == "I want ice americano."
    assert [w["word"] for w in row.transcript_words] == [" I", " want", " ice", " americano."]


def test_학습자가_전사를_고치면_둘이_달라진다(chat_db) -> None:
    """학습자가 확인 칸에서 고쳐 넣은 경우. 전사 원본이 지워지면 안 된다."""
    client, database = chat_db
    response = client.post(
        "/chat",
        json={
            "scenario_id": "cafe_order",
            "message": "I want ice americano.",
            "input_mode": "voice",
            "transcript": "I want a nice americano.",   # STT 가 잘못 들은 것
            "transcript_words": [{"word": " nice", "probability": 0.4}],
        },
    )
    row = _user_row(database, response.json()["session_id"])

    assert row.content != row.transcript
    assert row.transcript == "I want a nice americano."


def test_타자_경로는_그대로다(chat_db) -> None:
    """음성 칸이 채워지면 나중에 '음성 턴' 통계가 거짓이 된다."""
    client, database = chat_db
    response = client.post(
        "/chat", json={"scenario_id": "cafe_order", "message": "I want ice americano"}
    )
    assert response.status_code == 200
    row = _user_row(database, response.json()["session_id"])

    assert row.input_mode == "text"
    assert row.transcript is None
    assert row.transcript_words is None
