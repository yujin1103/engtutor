"""외부에 열어 둔 진입점이 **열어야 할 것만** 열었는지 바깥에서 확인한다.

왜 필요한가
-----------
"암호를 걸었다"와 "암호가 실제로 걸려 있다"는 다른 말이다. 설정 파일을 읽어
확인하는 건 의도를 확인하는 것이고, 이 스크립트는 결과를 확인한다.

검사 항목
---------
1. 채팅 UI 가 암호 없이 열리지 않는가      (401 이어야 정상)
2. 암호를 주면 실제로 열리는가             (200 + 첫 화면 자산이 전부 내려오는가)
3. 평문 HTTP 가 HTTPS 로 넘어가는가        (301/308)
4. 검수 UI·API·Ollama 포트가 바깥에서 닿는가 (전부 닫혀 있어야 정상)

4번이 이 스크립트의 핵심이다. 터널은 ui:8501 하나만 넘기도록 되어 있지만,
공유기나 VCN 규칙을 잘못 건드리면 8502(검수 UI, DB 직접 쓰기)가 그대로 열린다.

실행:
    python scripts/check_exposure.py engtutor.duckdns.org
    python scripts/check_exposure.py engtutor.duckdns.org --user demo --password ****
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx


def _status(url: str, user: str, password: str) -> int:
    """자산 하나의 응답 코드. 동시 요청용이라 클라이언트를 따로 연다."""
    try:
        with httpx.Client(timeout=30) as client:
            return client.get(url, auth=(user, password)).status_code
    except httpx.HTTPError:
        return 0

# 절대 바깥에서 닿으면 안 되는 것들. 뚫려 있으면 무엇이 위험한지 함께 적는다.
FORBIDDEN_PORTS = {
    8502: "검수 UI — DB 를 직접 쓰는 화면입니다",
    8000: "FastAPI — 인증이 없습니다",
    11434: "Ollama — 모델을 마음대로 부를 수 있습니다",
    8501: "Streamlit 직결 — nginx(암호)를 건너뜁니다",
}

OK, BAD = "✅", "❌"


def port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="공개 도메인 (예: engtutor.duckdns.org)")
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    base = f"https://{args.host}"
    failures: list[str] = []

    print(f"\n{args.host} 점검")
    print("=" * 60)

    # 1. 암호 없이 열리면 안 된다.
    try:
        res = httpx.get(base, timeout=15, follow_redirects=False)
        if res.status_code == 401:
            print(f"  {OK} 암호 없이는 안 열립니다 (401)")
        else:
            print(f"  {BAD} 암호 없이 {res.status_code} 로 응답합니다 — 누구나 GPU 를 씁니다")
            failures.append("암호가 걸려 있지 않습니다")
    except httpx.HTTPError as exc:
        print(f"  {BAD} 접속 실패: {exc}")
        failures.append("HTTPS 로 접속되지 않습니다")

    # 2. 암호를 주면 열려야 한다.
    if args.user and args.password:
        try:
            res = httpx.get(base, auth=(args.user, args.password), timeout=30)
            if res.status_code == 200:
                print(f"  {OK} 암호를 주면 열립니다 (200)")
            elif res.status_code == 503:
                print(f"  {BAD} 503 — 터널이 끊겨 있습니다 (집에서 tunnel 컨테이너를 확인하세요)")
                failures.append("터널이 연결되어 있지 않습니다")
            else:
                print(f"  {BAD} 암호를 줬는데 {res.status_code} 입니다")
                failures.append(f"인증 후 {res.status_code}")
        except httpx.HTTPError as exc:
            print(f"  {BAD} 인증 요청 실패: {exc}")
            failures.append("인증 요청이 실패했습니다")
    else:
        print("  ·  암호를 주지 않아 2번은 건너뜁니다 (--user/--password)")

    # 2-b. 브라우저처럼 자산을 한꺼번에 던져 본다.
    #
    # `/` 만 받아 보면 200 이라 정상으로 보이는데, 실제로는 첫 화면의 JS 가
    # 속도 제한에 잘려 하얀 화면이 뜬 적이 있다(112개 중 71개가 503).
    # 사람이 브라우저로 열어야만 보이는 고장이라 여기서 대신 본다.
    if args.user and args.password:
        try:
            body = httpx.get(base, auth=(args.user, args.password), timeout=30).text
            refs = [
                f"{base}/{a.lstrip('./')}"
                for a in re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', body)
            ]
            if refs:
                with ThreadPoolExecutor(max_workers=32) as pool:
                    codes = list(pool.map(lambda u: _status(u, args.user, args.password), refs))
                bad = Counter(c for c in codes if c != 200)
                if bad:
                    print(f"  {BAD} 자산 {len(refs)}개 중 {sum(bad.values())}개 실패: {dict(bad)}")
                    print("       속도 제한(limit_req)에 첫 화면이 걸리면 화면이 하얗게 뜹니다")
                    failures.append("정적 자산이 동시 요청에서 잘립니다")
                else:
                    print(f"  {OK} 첫 화면 자산 {len(refs)}개가 동시 요청에서 전부 통과합니다")
        except httpx.HTTPError as exc:
            print(f"  {BAD} 자산 점검 실패: {exc}")

    # 3. 평문 HTTP 는 HTTPS 로 넘어가야 한다. 안 그러면 암호가 평문으로 흐른다.
    try:
        res = httpx.get(f"http://{args.host}", timeout=15, follow_redirects=False)
        if res.status_code in (301, 302, 307, 308) and res.headers.get("location", "").startswith("https"):
            print(f"  {OK} HTTP 는 HTTPS 로 넘깁니다 ({res.status_code})")
        else:
            print(f"  {BAD} HTTP 가 그대로 응답합니다 ({res.status_code}) — 암호가 평문으로 흐릅니다")
            failures.append("HTTP 가 HTTPS 로 넘어가지 않습니다")
    except httpx.HTTPError:
        print(f"  {OK} HTTP(80) 가 닫혀 있습니다")

    # 4. 나머지는 전부 닫혀 있어야 한다.
    print("\n  바깥에서 닿으면 안 되는 포트")
    for port, why in sorted(FORBIDDEN_PORTS.items()):
        if port_open(args.host, port):
            print(f"    {BAD} {port} 열려 있음 — {why}")
            failures.append(f"{port} 포트가 열려 있습니다")
        else:
            print(f"    {OK} {port} 닫힘")

    print("\n" + "=" * 60)
    if failures:
        print(f"{BAD} 문제 {len(failures)}건")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"{OK} 열어야 할 것만 열려 있습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
