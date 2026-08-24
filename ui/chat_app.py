"""Streamlit 채팅 UI.

API 는 compose 네트워크 이름(http://api:8000)으로 부른다 — localhost 가 아니다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
CHAT_TIMEOUT = 180.0    # 로컬 14B 모델은 첫 턴이 느릴 수 있다
REPORT_TIMEOUT = 300.0  # 리포트는 대화 전체를 넣으므로 더 길게

st.set_page_config(page_title="engtutor", page_icon="🗣️", layout="centered")


@st.cache_data(ttl=30)
def fetch_scenarios() -> list[dict[str, Any]]:
    res = httpx.get(f"{API_BASE_URL}/scenarios", timeout=10.0)
    res.raise_for_status()
    return res.json()


@st.cache_data(ttl=30)
def fetch_categories() -> list[dict[str, Any]]:
    """분류 목록. 개수까지 서버가 세어 내려주므로 UI 가 다시 세지 않는다."""
    res = httpx.get(f"{API_BASE_URL}/categories", timeout=10.0)
    res.raise_for_status()
    return res.json()


@st.cache_data(ttl=300)
def fetch_strictness() -> list[dict[str, Any]]:
    """교정 강도 선택지는 서버가 내려준다 — 문구를 UI 가 복제하지 않는다."""
    res = httpx.get(f"{API_BASE_URL}/strictness", timeout=10.0)
    res.raise_for_status()
    return res.json()


def fetch_health() -> dict[str, Any]:
    try:
        res = httpx.get(f"{API_BASE_URL}/healthz", timeout=10.0)
        res.raise_for_status()
        return res.json()
    except httpx.HTTPError as exc:
        return {"backend": "?", "detail": str(exc), "reachable": False}


def stream_turn(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """`/chat/stream` 의 SSE 사건을 하나씩 돌려준다.

    스트리밍을 쓰는 이유는 총 시간을 줄이려는 게 아니다 — 총 시간은 그대로다.
    빈 화면을 보는 시간을 줄이는 것이다. 왕초보에게 8초 침묵은 '고장'으로 읽힌다.
    """
    with httpx.stream(
        "POST", f"{API_BASE_URL}/chat/stream", json=payload, timeout=CHAT_TIMEOUT
    ) as res:
        if res.status_code >= 400:
            res.read()
            yield {"type": "error", "detail": f"오류 {res.status_code}: {res.text[:400]}"}
            return
        for line in res.iter_lines():
            if not line.startswith("data: "):
                continue
            try:
                yield json.loads(line[6:])
            except json.JSONDecodeError:
                continue


def start_session(scenario: dict[str, Any]) -> None:
    """대화를 처음부터 시작한다.

    레벨과 교정 강도는 여기서 건드리지 않는다. 그 둘은 사이드바 위젯이 소유하는
    값이라(`key=`), 위젯이 만들어진 뒤에 코드가 덮어쓰면 Streamlit 이 예외를 던진다.
    설정은 시나리오를 바꿔도 유지되는 게 맞기도 하다.
    """
    st.session_state.session_id = None
    st.session_state.scenario_id = scenario["id"]
    st.session_state.report = None
    st.session_state.revealed = False
    st.session_state.history = [
        {
            "role": "assistant",
            "reply": scenario["opening_line"],
            "reply_ko": scenario["opening_line_ko"],
            "corrections": [],
            "say_en": scenario["opening_say_en"],
            "say_more": scenario["opening_say_more"],
            "hint_ko": scenario["opening_hint_ko"],
        }
    ]


def _render_corrections(items: list[dict[str, Any]], *, strike: bool) -> None:
    for c in items:
        st.markdown(f"~~{c['original']}~~" if strike else f"{c['original']}")
        st.markdown(f"**→ {c['better']}**")
        st.caption(c["note"])


def render_turn(turn: dict[str, Any]) -> None:
    st.markdown(turn["reply"])
    # 왕초보는 **상대의 영어도 못 읽는다.** 힌트는 다음에 뭘 말할지를 알려줄 뿐,
    # 방금 상대가 뭐라고 했는지는 알려주지 않는다. 접어 두는 이유는 say_en 과 같다 —
    # 먼저 영어로 읽어 보고, 막히면 연다.
    if turn.get("reply_ko"):
        with st.expander("🇰🇷 해석 보기", expanded=False):
            st.markdown(turn["reply_ko"])
    corrections = turn.get("corrections") or []
    # 실제 오류와 '더 자연스럽게'를 분리한다. 왕초보에게 둘을 같은 무게로 보여주면 위축된다.
    mistakes = [c for c in corrections if c.get("kind", "mistake") == "mistake"]
    polish = [c for c in corrections if c.get("kind") == "polish"]

    if mistakes:
        with st.expander(f"✏️ 고쳐볼까요 ({len(mistakes)})", expanded=True):
            _render_corrections(mistakes, strike=True)
    if polish:
        with st.expander(f"✨ 이렇게 하면 더 자연스러워요 ({len(polish)})", expanded=False):
            _render_corrections(polish, strike=False)
    # 힌트는 말풍선이 아니라 입력창 바로 위 바에서 보여준다(render_say_bar).
    # 여기서 또 그리면 대화가 길어질 때 화면이 힌트로 뒤덮인다.


def render_report(report: dict[str, Any]) -> None:
    insight = report["insight"]

    st.subheader("📘 학습 리포트")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("주고받은 턴", report["turn_count"])
    c2.metric("고칠 것", report["mistake_count"])
    c3.metric("다듬을 것", report.get("polish_count", 0))
    c4.metric("레벨", report["level"])

    st.markdown(insight["summary_ko"])

    if insight.get("patterns_ko"):
        st.markdown("#### 🔁 반복된 실수")
        for p in insight["patterns_ko"]:
            st.markdown(f"- {p}")

    if insight.get("learned"):
        st.markdown("#### 🌱 오늘 배운 표현")
        for item in insight["learned"]:
            st.markdown(f"**{item['english']}**")
            st.caption(item["note_ko"])

    all_items = report.get("mistakes") or []
    real = [m for m in all_items if m.get("kind", "mistake") == "mistake"]
    polish = [m for m in all_items if m.get("kind") == "polish"]

    if real:
        st.markdown("#### 📝 틀린 문장 모음")
        for m in real:
            with st.container(border=True):
                st.markdown(f"~~{m['original']}~~")
                st.markdown(f"**→ {m['better']}**")
                st.caption(m["note"])

    if polish:
        with st.expander(f"✨ 더 자연스럽게 말하는 법 ({len(polish)})", expanded=False):
            for m in polish:
                st.markdown(f"{m['original']}")
                st.markdown(f"**→ {m['better']}**")
                st.caption(m["note"])

    tips = report.get("word_tips") or []
    if tips:
        st.markdown("#### 📚 오늘 나온 단어")
        st.caption("검수 완료된 단어 사전에서 가져왔어요.")
        for t in tips:
            with st.container(border=True):
                confused = f" · 헷갈리는 단어: {', '.join(t['confused_with'])}" if t["confused_with"] else ""
                st.markdown(f"**{t['word']}** — {t['meaning_ko']}{confused}")
                # 문형은 뜻보다 자주 틀리는 지점이다. 있으면 예문 바로 위에 붙여
                # '형태 -> 그 형태를 쓴 문장' 순서로 읽히게 한다.
                if t.get("pattern"):
                    st.markdown(f"`{t['pattern']}`")
                st.markdown(f"_{t['example']}_")
                st.caption(t["usage_note"])


# ---------------------------------------------------------------- 사이드바
try:
    scenarios = fetch_scenarios()
    categories = fetch_categories()
except httpx.HTTPError as exc:
    st.error(f"API 에 연결하지 못했습니다 ({API_BASE_URL}).\n\n{exc}")
    st.stop()

BY_ID = {s["id"]: s for s in scenarios}
LEVELS = ("A1", "A2", "B1")
LEVEL_LABEL = {"A1": "A1 · 왕초보", "A2": "A2 · 기초", "B1": "B1 · 조금 익숙"}


def scenario_cards(items: list[dict[str, Any]]) -> None:
    """시나리오 카드 두 줄 배치. 카드 하나가 곧 '시작' 버튼이다."""
    if not items:
        st.info("여기에 맞는 시나리오가 아직 없어요.")
        return
    cols = st.columns(2)
    for i, s in enumerate(items):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"**{s['title']}**")
                st.caption(f"{LEVEL_LABEL.get(s['level'], s['level'])}  ·  🎯 {s['goal']}")
                if st.button("시작하기", key=f"go-{s['id']}", use_container_width=True):
                    start_session(s)
                    st.rerun()


def render_picker() -> None:
    """분류를 고르고 그 안으로 들어간다.

    시나리오가 3개일 때는 목록 하나로 충분했다. 33개가 되면 평평한 목록에서
    학습자가 '뭘 골라야 하지'로 멈춘다. 그래서 한 겹 안으로 들어가는 구조로 둔다.

    (원형 배치처럼 보이게 하려면 커스텀 컴포넌트가 필요하다. 클릭을 파이썬으로
    돌려받을 방법이 없어서, 같은 '안으로 들어가는' 흐름을 카드로 구현했다.)
    """
    st.title("🗣️ 무엇을 연습할까요?")

    needle = st.text_input(
        "찾기", placeholder="카페, 택시, 병원, 환불...", key="picker_query"
    ).strip()
    if needle:
        hits = [
            s for s in scenarios
            if needle in s["title"] or needle in s["situation"] or needle in s["goal"]
        ]
        st.caption(f"'{needle}' — {len(hits)}개")
        scenario_cards(hits)
        return

    chosen = st.session_state.get("picker_category")
    if chosen is None:
        st.caption(f"상황을 하나 고르세요. 전부 {len(scenarios)}개의 대화가 있어요.")
        cols = st.columns(3)
        for i, c in enumerate(categories):
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"### {c['emoji']}  {c['label']}")
                    st.caption(f"{c['blurb']}  ·  {c['count']}개")
                    if st.button("들어가기", key=f"cat-{c['id']}", use_container_width=True):
                        st.session_state.picker_category = c["id"]
                        st.rerun()
        return

    category = next((c for c in categories if c["id"] == chosen), None)
    back, head = st.columns([1, 4], vertical_alignment="center")
    if back.button("← 뒤로", use_container_width=True):
        st.session_state.picker_category = None
        st.rerun()
    if category:
        head.markdown(f"### {category['emoji']} {category['label']}")
        st.caption(category["blurb"])
    scenario_cards([s for s in scenarios if s["category"] == chosen])


# 지금 고른 시나리오. 없으면 아래에서 고르는 화면을 그린다.
scenario = BY_ID.get(st.session_state.get("scenario_id"))

with st.sidebar:
    st.header("설정")

    # key 로 상태를 Streamlit 이 소유하게 한다. 예전에는 value= 로 세션 상태를
    # 되먹였는데, 그러면 위젯이 다시 만들어질 때 기본값으로 풀린다
    # (교정 강도를 gentle 로 두고 대화하면 balanced 로 돌아가던 버그).
    # 라벨은 여기 적지 않는다 — /strictness 가 내려주는 값만 쓴다.
    st.session_state.setdefault("level", "A1")
    st.radio(
        "레벨", options=LEVELS, horizontal=True, key="level",
        format_func=lambda v: v,
        help="A1 왕초보 · A2 기초 · B1 조금 익숙. 시나리오를 바꿔도 유지돼요.",
    )

    modes = {m["key"]: m for m in fetch_strictness()}
    st.session_state.setdefault("strictness", "balanced")
    st.select_slider(
        "교정 강도",
        options=list(modes),
        key="strictness",
        format_func=lambda k: modes[k]["label"],
    )
    st.caption(modes[st.session_state.strictness]["caption"])

    st.divider()

    if scenario is None:
        st.caption("시나리오를 고르면 여기에 상황과 목표가 나와요.")
    else:
        st.caption(f"**상황** — {scenario['situation']}")
        st.caption(f"**목표** — {scenario['goal']}")
        if scenario["level"] != st.session_state.level:
            st.caption(f"이 시나리오는 {LEVEL_LABEL[scenario['level']]} 에 맞춰 만들었어요.")

        if st.button("🗂️ 다른 시나리오 고르기", use_container_width=True):
            st.session_state.scenario_id = None
            st.session_state.pop("history", None)
            st.rerun()

        st.divider()

        has_session = bool(st.session_state.get("session_id"))
        if st.button("📘 대화 끝내고 리포트 보기", use_container_width=True, disabled=not has_session):
            with st.spinner("리포트를 만드는 중..."):
                try:
                    res = httpx.post(
                        f"{API_BASE_URL}/sessions/{st.session_state.session_id}/report",
                        timeout=REPORT_TIMEOUT,
                    )
                    res.raise_for_status()
                    st.session_state.report = res.json()
                except httpx.HTTPStatusError as exc:
                    st.error(f"오류 {exc.response.status_code}: {exc.response.text[:300]}")
                except httpx.HTTPError as exc:
                    st.error(f"리포트 생성 실패: {exc}")
            st.rerun()

        if st.button("🔄 대화 새로 시작", use_container_width=True):
            start_session(scenario)
            st.rerun()

    st.divider()
    health = fetch_health()
    icon = "🟢" if health.get("reachable") else "🔴"
    st.caption(f"{icon} {health.get('detail', '?')}")

# ---------------------------------------------------------------- 본문
if scenario is None:
    render_picker()
    st.stop()

selected_id = scenario["id"]
if "history" not in st.session_state:
    start_session(scenario)

st.title(scenario["title"])

for item in st.session_state.history:
    with st.chat_message(item["role"]):
        if item["role"] == "user":
            st.markdown(item["content"])
        else:
            render_turn(item)

def render_say_bar() -> None:
    """입력창 바로 위 '지금 말할 수 있는 것' 바.

    얼어붙는 사건이 실제로 일어나는 좌표가 화면 맨 아래다. 힌트를 말풍선 안에 두면
    대화가 길어질수록 스크롤 위로 사라진다.

    영어는 기본으로 접혀 있다 — 시도 먼저, 막히면 열기. 그래야 의존이 덜 생기고
    열었는지가 관측 가능해진다.
    """
    if st.session_state.get("report"):
        return  # 종료된 세션에는 말할 것을 제안하지 않는다
    last = next(
        (h for h in reversed(st.session_state.history) if h["role"] == "assistant"), None
    )
    if last is None:
        return

    with st.container(border=True):
        left, right = st.columns([4, 1], vertical_alignment="center")
        left.markdown(f"💡 {last.get('hint_ko', '')}")

        revealed = st.session_state.get("revealed", False)
        if not revealed:
            if right.button("🔤 답 표시", use_container_width=True, help="막혔을 때만 눌러보세요"):
                st.session_state.revealed = True
                st.rerun()
        else:
            right.caption("아래를 그대로 말해보세요")

        if revealed:
            for line in (last.get("say_en"), last.get("say_more")):
                if line:
                    st.code(line, language=None, wrap_lines=True)

        # 모델이 헤매도 바닥이 사라지지 않게 UI 상수로 보장한다.
        with st.expander("🤷 무슨 말인지 모르겠어요", expanded=False):
            st.code("Sorry, I don't understand.", language=None)
            st.caption("이 한 마디면 상대가 다시 쉽게 말해줘요. 실제 대화에서 제일 많이 쓰는 영어예요.")


render_say_bar()

if st.session_state.get("report"):
    st.divider()
    render_report(st.session_state.report)
    st.info("리포트가 나온 세션은 종료됐어요. 계속하려면 **대화 새로 시작**을 눌러주세요.")
elif user_text := st.chat_input("영어로 말해보세요"):
    st.session_state.history.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    payload = {
        "scenario_id": selected_id,
        "message": user_text,
        "session_id": st.session_state.session_id,
        "level": st.session_state.level,
        "strictness": st.session_state.get("strictness", "balanced"),
    }
    with st.chat_message("assistant"):
        box = st.empty()
        box.markdown("_생각 중..._")

        text = ""
        turn: dict[str, Any] | None = None
        error: str | None = None
        try:
            for event in stream_turn(payload):
                kind = event.get("type")
                if kind == "session":
                    st.session_state.session_id = event["session_id"]
                elif kind == "delta":
                    text += event["text"]
                    box.markdown(f"{text} ▌")  # 커서로 '아직 오는 중'을 보여준다
                elif kind == "reset":
                    # 1차 응답이 스키마 검증에 걸렸다. 보여준 글자는 버린다.
                    text = ""
                    box.markdown("_다시 정리하는 중..._")
                elif kind == "turn":
                    turn = event["turn"]
                elif kind == "error":
                    error = event["detail"]
        except httpx.HTTPError as exc:
            error = f"API 호출 실패: {exc}"

        if turn is None:
            box.empty()
            st.error(error or "응답을 받지 못했습니다.")
            st.stop()

        # 교정·힌트는 검증이 끝난 뒤에야 그린다. 반쯤 만들어진 교정은 보여주지 않는다.
        with box.container():
            render_turn(turn)

    st.session_state.history.append({"role": "assistant", **turn})
    st.session_state.revealed = False  # 새 턴이 오면 영어는 다시 접는다
    st.rerun()
