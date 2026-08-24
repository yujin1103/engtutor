"""Streamlit 채팅 UI.

API 는 compose 네트워크 이름(http://api:8000)으로 부른다 — localhost 가 아니다.
"""

from __future__ import annotations

import os
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


def start_session(scenario: dict[str, Any], level: str) -> None:
    st.session_state.session_id = None
    st.session_state.scenario_id = scenario["id"]
    st.session_state.level = level
    st.session_state.report = None
    st.session_state.revealed = False
    st.session_state.history = [
        {
            "role": "assistant",
            "reply": scenario["opening_line"],
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
                st.markdown(f"_{t['example']}_")
                st.caption(t["usage_note"])


# ---------------------------------------------------------------- 사이드바
try:
    scenarios = fetch_scenarios()
except httpx.HTTPError as exc:
    st.error(f"API 에 연결하지 못했습니다 ({API_BASE_URL}).\n\n{exc}")
    st.stop()

with st.sidebar:
    st.header("설정")
    titles = {s["id"]: s["title"] for s in scenarios}
    selected_id = st.selectbox("시나리오", options=list(titles), format_func=lambda i: titles[i])
    scenario = next(s for s in scenarios if s["id"] == selected_id)

    level = st.radio("레벨", options=["A1", "A2"], horizontal=True, index=0 if scenario["level"] == "A1" else 1)

    modes = {m["key"]: m for m in fetch_strictness()}
    strictness = st.select_slider(
        "교정 강도",
        options=list(modes),
        value=st.session_state.get("strictness", "balanced"),
        format_func=lambda k: modes[k]["label"],
    )
    st.session_state.strictness = strictness
    st.caption(modes[strictness]["caption"])

    st.caption(f"**상황** — {scenario['situation']}")
    st.caption(f"**목표** — {scenario['goal']}")

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
        start_session(scenario, level)
        st.rerun()

    st.divider()
    health = fetch_health()
    icon = "🟢" if health.get("reachable") else "🔴"
    st.caption(f"{icon} {health.get('detail', '?')}")

# ---------------------------------------------------------------- 본문
if "history" not in st.session_state or st.session_state.get("scenario_id") != selected_id:
    start_session(scenario, level)
st.session_state.level = level

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
        with st.spinner("생각 중..."):
            try:
                res = httpx.post(f"{API_BASE_URL}/chat", json=payload, timeout=CHAT_TIMEOUT)
                res.raise_for_status()
                data = res.json()
            except httpx.HTTPStatusError as exc:
                st.error(f"오류 {exc.response.status_code}: {exc.response.text[:400]}")
                st.stop()
            except httpx.HTTPError as exc:
                st.error(f"API 호출 실패: {exc}")
                st.stop()

        st.session_state.session_id = data["session_id"]
        turn = data["turn"]
        render_turn(turn)

    st.session_state.history.append({"role": "assistant", **turn})
    st.session_state.revealed = False  # 새 턴이 오면 영어는 다시 접는다
    st.rerun()
