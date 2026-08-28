"""빌드한 React 앱(`ui_web/dist`)을 FastAPI 가 `/` 로 서빙한다.

**왜 별도 웹서버를 두지 않는가.** 이유가 둘이다.

  1. 앱과 API 가 **같은 출처**가 되어 CORS 가 아예 없어진다. 클라이언트 코드에
     baseURL 이 없고 `fetch("/scenarios")` 한 줄이 개발·운영에서 똑같이 돈다
     (근거는 `ui_web/src/api/paths.ts` 에 적혀 있다).
  2. **폰 브라우저는 HTTPS 가 아니면 마이크를 내주지 않는다.** 같은 출처면
     터널이 씌워 주는 인증서 한 장으로 화면과 API 가 함께 HTTPS 가 된다.
     화면과 API 를 다른 포트로 내보내면 인증서와 프록시 설정이 두 벌이 된다.

개발 중에는 이게 필요 없다 — `docker compose up -d web` 의 Vite dev 서버(5173)가
같은 일을 HMR 과 함께 해 준다. 이 파일은 **운영 배치**를 위한 것이다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

logger = logging.getLogger(__name__)

# app/web.py -> app/ -> 저장소 루트
DIST = (Path(__file__).resolve().parent.parent / "ui_web" / "dist").resolve()
INDEX = DIST / "index.html"

# 빌드 전에 화면을 열었을 때 보여 줄 안내. API 는 계속 돌고 있으므로 앱을 죽이지
# 않는다 — 대신 흰 화면 대신 "무엇을 하면 되는지"를 준다. 503 인 이유는
# 이게 고장이 아니라 **아직 준비되지 않은 상태**이기 때문이다.
_NOT_BUILT = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>아직 빌드하지 않았어요</title>
<style>body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;line-height:1.7;
max-width:36rem;margin:3rem auto;padding:0 1.25rem;color:#2c2721;background:#f7f5f2}
code{background:#eae5dd;padding:.15rem .4rem;border-radius:.25rem}
@media(prefers-color-scheme:dark){body{color:#e8e2d8;background:#17150f}
code{background:#2a261d}}</style></head><body>
<h1>화면이 아직 없어요</h1>
<p>React 앱을 빌드하지 않았습니다. API 는 정상으로 돌고 있어요.</p>
<p>빌드하려면:</p>
<p><code>docker compose run --rm web npm run build</code></p>
<p>개발 중이라면 빌드 대신 개발 서버를 쓰세요 —
<code>docker compose up -d web</code> 뒤 <code>http://localhost:5173</code>.</p>
</body></html>
"""


def _reserved_roots(app: FastAPI) -> frozenset[str]:
    """API 가 이미 차지한 첫 경로 조각들.

    손으로 목록을 적지 않고 **등록된 라우트에서 뽑는다.** 손으로 적으면
    엔드포인트를 하나 추가했을 때 여기를 같이 고치는 것을 잊고, 그러면 그 주소가
    404 대신 `index.html` 을 돌려주기 시작한다 — JSON 을 기다리던 쪽에서는
    "왜 갑자기 HTML 이 오지" 로 보여서 원인을 찾는 데 오래 걸린다.

    `/docs`·`/openapi.json`·`/redoc` 도 FastAPI 가 등록한 라우트라 자동으로 들어온다.
    """
    roots: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        head = path.strip("/").split("/")[0]
        # `{session_id}` 같은 자리표시자는 특정 주소를 잡아 두는 게 아니라 제외한다.
        if head and "{" not in head:
            roots.add(head)
    return frozenset(roots)


def _asset_file(rel: str) -> Path | None:
    """`dist` 안에 실제로 있는 파일이면 그 경로, 아니면 None.

    파일 이름이 될 수 없는 글자는 **경로를 만들기 전에** 걷어낸다. 널바이트가
    든 주소(`GET /%00`)에서 `Path.resolve()` 가 `ValueError` 를 던져 500 이
    났다 — 없는 파일을 달라고 한 것이니 404 로 답해야 하는 자리다.
    """
    if not rel or "\x00" in rel:
        return None
    try:
        candidate = (DIST / rel).resolve()
        # `..` 로 저장소 바깥(.env 같은 것)을 읽어 가는 길을 막는다.
        if not candidate.is_relative_to(DIST):
            return None
        # `is_file()` 도 파일 시스템을 건드린다. 이름이 255자를 넘으면 여기서
        # OSError 가 나므로 `resolve()` 와 같은 try 안에 둔다 — 밖에 뒀다가
        # 400자짜리 주소에서 500 이 났다.
        return candidate if candidate.is_file() else None
    except (ValueError, OSError):
        # 너무 긴 이름·잘못된 글자. 어느 쪽이든 "그런 파일 없음" 이다.
        return None


def _cache_headers(file: Path) -> dict[str, str]:
    """`index.html` 은 절대 캐시하지 않고, 이름에 해시가 붙은 것만 오래 캐시한다.

    폰 브라우저는 HTML 을 꽤 오래 들고 있는다. 옛 `index.html` 이 남으면 이미
    사라진 해시 파일을 부르게 되고, 화면은 **흰색으로 조용히 멈춘다** — 이 앱을
    다시 만든 이유가 바로 그 증상이라 여기서만은 캐시를 포기한다.
    반대로 `assets/index-BKY_qTzC.js` 는 내용이 바뀌면 이름이 바뀌므로
    영구 캐시가 안전하다. 폰의 느린 회선에서 재방문이 눈에 띄게 빨라진다.
    """
    if file.parent.name == "assets":
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-store"}


def mount_spa(app: FastAPI) -> None:
    """`/` 아래를 React 앱에 넘긴다. **반드시 API 라우트를 전부 등록한 뒤에 부른다.**

    라우트는 등록 순서대로 맞춰 보므로, 먼저 등록된 `/chat` 이 이 catch-all 보다
    항상 앞선다. 그래도 안전벨트로 `_reserved_roots` 를 한 번 더 확인한다 —
    `/sessions/999/reportt` 처럼 API 영역 안의 오타는 화면이 아니라 404 여야 한다.
    """
    reserved = _reserved_roots(app)

    if not INDEX.is_file():
        # 죽이지 않는다. 빌드는 나중에 해도 되고, 그때 재시작할 필요도 없다.
        logger.info("ui_web/dist 가 없어 화면은 안내 페이지로 나갑니다 (API 는 정상).")

    @app.get("/{spa_path:path}", include_in_schema=False)
    def spa(spa_path: str) -> Response:
        if spa_path.split("/", 1)[0] in reserved:
            raise HTTPException(status_code=404, detail="그런 주소는 없습니다.")

        # 빌드 여부를 요청할 때마다 본다. import 시점에 한 번만 보면, API 가 뜬 뒤에
        # 빌드한 경우 컨테이너를 재시작해야 화면이 나온다 — 그럴 이유가 없다.
        if not INDEX.is_file():
            return HTMLResponse(_NOT_BUILT, status_code=503)

        file = _asset_file(spa_path)
        if file is not None:
            return FileResponse(file, headers=_cache_headers(file))

        # 없는 경로는 전부 index.html 이다. SPA 라 화면 전환이 서버까지 오지 않지만,
        # 새로고침이나 홈 화면 바로가기가 `/` 가 아닌 주소로 들어올 수 있다.
        return FileResponse(INDEX, headers={"Cache-Control": "no-store"})
