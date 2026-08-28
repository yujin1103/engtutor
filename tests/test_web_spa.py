"""FastAPI 가 React 빌드를 `/` 로 서빙할 때 **API 를 가리지 않는지**.

이 테스트가 있는 이유. `app/web.py` 는 `/` 아래 전부를 잡는 catch-all 을 건다.
등록 순서 하나만 어긋나면 `/chat` 이 JSON 대신 `index.html` 을 돌려주기
시작하는데, 그러면 앱은 "서버 응답을 읽지 못했어요" 만 반복하고 왜 그런지는
아무 데도 안 적힌다. 눈으로 찾기 제일 어려운 종류의 고장이라 여기서 못 박는다.

빌드 결과(`ui_web/dist`)가 있든 없든 같은 결과가 나와야 하므로, 화면 쪽 테스트는
가짜 dist 를 만들어 `app.web` 의 경로를 갈아 끼운다 — 테스트가 `npm run build`
여부에 따라 통과했다 말았다 하면 아무도 안 믿는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """빌드가 끝난 척하는 가짜 `dist`."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>가짜</title>", encoding="utf-8")
    (dist / "assets" / "index-DEADBEEF.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("절대 나가면 안 되는 것", encoding="utf-8")
    monkeypatch.setattr(web, "DIST", dist.resolve())
    monkeypatch.setattr(web, "INDEX", (dist / "index.html").resolve())
    return dist


# ─────────────────────────────────────────────── API 가 먼저다

@pytest.mark.parametrize("path", ["/scenarios", "/categories", "/strictness", "/grammar"])
def test_API_경로는_화면에_가려지지_않는다(client: TestClient, path: str, built: Path) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_문서와_스키마도_가려지지_않는다(client: TestClient, built: Path) -> None:
    """`/openapi.json` 은 ui_web/src/api/types.ts 의 출처다. 이게 HTML 이 되면
    타입을 다시 맞출 방법이 사라진다."""
    assert client.get("/openapi.json").json()["info"]["title"] == "engtutor"
    assert "<html" in client.get("/docs").text.lower()


def test_API_영역_안의_오타는_404_지_화면이_아니다(client: TestClient, built: Path) -> None:
    """`/sessions/1/reportt` 처럼 API 를 부르려다 틀린 것에 화면을 주면,
    부른 쪽은 JSON 을 기다리다 파싱 오류만 본다. 무엇이 틀렸는지 말해 준다."""
    for path in ["/sessions/1/reportt", "/chat/nope", "/scenarios/none"]:
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"].startswith("application/json"), path


def test_catch_all_은_스키마에_나오지_않는다() -> None:
    """`/{spa_path}` 가 openapi 에 섞이면 클라이언트 타입을 뽑을 때 걸리적거린다."""
    assert not any("spa_path" in path for path in app.openapi()["paths"])


# ─────────────────────────────────────────────── 화면

def test_모르는_주소는_index_html_로_보낸다(client: TestClient, built: Path) -> None:
    """SPA 라 `/settings` 같은 주소로 새로고침해도 화면이 떠야 한다."""
    for path in ["/", "/settings", "/report/abc"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "가짜" in response.text, path


def test_index_html_은_캐시하지_않는다(client: TestClient, built: Path) -> None:
    """폰이 옛 HTML 을 들고 있으면 이미 사라진 해시 파일을 부르고 흰 화면이 된다 —
    이 앱을 다시 만든 이유가 바로 그 '조용히 멈춘 화면' 이다."""
    assert client.get("/").headers["cache-control"] == "no-store"


def test_해시_붙은_파일만_오래_캐시한다(client: TestClient, built: Path) -> None:
    response = client.get("/assets/index-DEADBEEF.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_dist_밖의_파일은_내주지_않는다(built: Path) -> None:
    """`..` 로 .env 를 읽어 가는 길을 막는다."""
    assert web._asset_file("../secret.txt") is None
    assert web._asset_file("assets/../../secret.txt") is None
    assert web._asset_file("index.html") is not None


# ─────────────────────────────────────────────── 아직 빌드 안 했을 때

def test_빌드_전에도_API_는_돈다(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dist 가 없다고 앱이 죽으면 안 된다. 화면만 안내로 바뀐다."""
    monkeypatch.setattr(web, "DIST", (tmp_path / "없음").resolve())
    monkeypatch.setattr(web, "INDEX", (tmp_path / "없음" / "index.html").resolve())

    assert client.get("/scenarios").status_code == 200

    response = client.get("/")
    assert response.status_code == 503
    # 흰 화면 대신 **무엇을 하면 되는지**를 준다.
    assert "npm run build" in response.text
    assert "빌드" in response.text


def test_빌드_여부는_요청할_때마다_본다(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API 를 띄운 뒤에 빌드한 경우 재시작 없이 화면이 나와야 한다."""
    dist = tmp_path / "dist"
    monkeypatch.setattr(web, "DIST", dist.resolve())
    monkeypatch.setattr(web, "INDEX", (dist / "index.html").resolve())
    assert client.get("/").status_code == 503

    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>나중에 빌드함", encoding="utf-8")
    assert "나중에 빌드함" in client.get("/").text


@pytest.mark.parametrize(
    "path",
    [
        "/%00",
        "/assets/%00.js",
        "/" + "a" * 400,
        "/assets/" + "b" * 300 + ".js",
        "/../.env",
        "/%2e%2e/.env",
    ],
)
def test_odd_paths_do_not_crash_the_server(client, path: str) -> None:
    """주소가 이상해도 **500 이 나면 안 된다.**

    실제로 둘이 났다. `GET /%00` 은 `Path.resolve()` 가 널바이트에 ValueError 를
    던졌고, 400자짜리 주소는 `is_file()` 이 OSError(File name too long)를 던졌다.
    둘 다 "그런 파일 없음" 으로 답해야 하는 자리인데 "서버가 부서졌다" 로 답했다.

    `..` 로 저장소 바깥을 읽어 가는 길도 함께 확인한다 — SPA 껍데기가 나가야지
    `.env` 가 나가면 안 된다.
    """
    res = client.get(path)
    assert res.status_code < 500
    assert "ANTHROPIC_API_KEY" not in res.text
