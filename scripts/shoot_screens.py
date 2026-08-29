"""폰 화면을 픽셀로 찍는다. **크롬 확장 없이 화면을 눈으로 보려고 만들었다.**

    docker compose exec api python scripts/shoot_screens.py

왜 이 파일이 있는가
-------------------
화면을 다섯 세션 동안 한 번도 눈으로 못 봤다. API 응답·빌드·시험만으로 확인했고,
그동안 화면에만 드러나는 결함이 쌓였다. 붙일 브라우저가 없으면 없는 대로 찍을 길을
만들어 두는 편이 낫다.

**api 컨테이너에서 돌린다.** 거기가 `ui_web/dist` 를 `/` 로 서빙하고, 이미지가
Debian 이라 크로미움이 얹힌다. web 컨테이너는 Alpine 이라 playwright 의 크로미움이
못 올라간다(apt-get 이 없다).

처음 한 번은 브라우저를 받아야 한다. 이미지에 굽지 않은 이유는 whisper 모델과 같다 —
개발할 때만 쓰는 500MB 를 이미지에 넣으면 빌드가 매번 무거워진다. 컨테이너를 지웠다
다시 만들면 아래를 한 번 더 돌린다:

    docker compose exec api pip install playwright
    docker compose exec api python -m playwright install --with-deps chromium

화면마다 새 컨텍스트를 여는 까닭
--------------------------------
같은 탭을 새로고침하면 **앞 화면이 그대로 다시 뜬다.** `history.pushState` 로 태운
state 가 새로고침 뒤에도 살아 있어 `useNav` 가 그것을 읽기 때문이다(nav.ts 참고).
컨텍스트를 새로 열지 않으면 두 번째 화면부터 전부 첫 화면의 사진이 된다 —
실제로 처음 돌렸을 때 그렇게 나왔다.

화면 전환은 **버튼 글자로 눌러서** 간다. history.state 를 직접 밀어 넣으면 실제
흐름을 건너뛰게 되고, 그러면 "눌러서 갔을 때만" 나는 결함을 못 본다.
"""

from __future__ import annotations

import pathlib
import sys

OUT = pathlib.Path("/workspace/.review/shots")
BASE = "http://localhost:8000/"
# 폰용 화면이라 폰 크기로 본다. device_scale_factor 2 는 레티나 — 글자가 뭉개지지 않는다.
VIEWPORT = {"width": 390, "height": 844}

# (파일 이름, [(누를 버튼 글자, 누른 뒤 기다릴 밀리초, 없어도 되는가), ...])
#
# 힌트 버튼은 몇 걸음 폈는지에 따라 글자가 바뀌므로("힌트 보기" -> "힌트 더 보기
# (2개 남음)") 부분 일치로 '힌트' 만 준다. 그리고 **걸음 수가 낱말마다 다르다** —
# 짧은 낱말은 펼 것이 두 걸음뿐이라 세 번째 누르기가 없다. 그래서 없어도 되는 걸음으로
# 둔다. 안 그러면 어떤 낱말이 뽑히느냐에 따라 이 사진만 실패한다.
TRIPS: list[tuple[str, list[tuple[str, int, bool]]]] = [
    ("01_home", []),
    ("02_grammar", [("문법 문제", 2500, False)]),
    ("03_toeic", [("토익 단어", 9000, False)]),
    ("04_practice_pick", [("단어 연습장", 2500, False)]),
    ("05_practice_cloze", [("단어 연습장", 2000, False), ("전체 낱말", 4000, False)]),
    ("06_practice_hints", [("단어 연습장", 2000, False), ("전체 낱말", 4000, False),
                           ("힌트", 700, False), ("힌트", 700, True), ("힌트", 900, True)]),
    ("07_scenarios", [("카페·식당", 2500, False)]),
    ("08_chat_intro", [("카페·식당", 1500, False), ("카페에서 음료 주문하기", 6000, False)]),
]


def main() -> int:
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(__doc__.split("처음 한 번은")[1].split("화면마다")[0].strip(), file=sys.stderr)
        print("\nplaywright 가 없습니다. 위 두 줄을 먼저 돌리세요.", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:  # 브라우저만 없는 경우와 그 밖의 고장을 갈라 알린다
            print(f"크로미움을 못 띄웠습니다: {exc}", file=sys.stderr)
            print("docker compose exec api python -m playwright install --with-deps chromium",
                  file=sys.stderr)
            return 1

        for name, steps in TRIPS:
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
            page = context.new_page()
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(1500)
            note = ""
            for label, wait_ms, optional in steps:
                button = page.get_by_role("button").filter(has_text=label).first
                try:
                    button.wait_for(state="visible", timeout=3000 if optional else 15000)
                except PWTimeout as exc:
                    if optional:
                        continue  # 없어도 되는 걸음이다 — 다 편 상태라 버튼이 사라졌다
                    # 멈추지 않는다. 못 누른 자리에서 그대로 찍어 둬야 **왜** 못 눌렀는지 보인다.
                    note = f"  <- '{label}' 을 못 눌렀다: {exc.message.splitlines()[0]}"
                    break
                button.click()
                page.wait_for_timeout(wait_ms)
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path), full_page=True)
            print(f"{path.name}  {path.stat().st_size // 1024}KB{note}")
            context.close()
        browser.close()
    print(f"\n{OUT} 에 담았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
