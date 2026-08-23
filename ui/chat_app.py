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
    st.session_state.history = [
        {
            "role": "assistant",
            "reply": scenario["opening_line"],
            "corrections": [],
            "hint_ko": scenario["opening_hint_ko"],
        }
    ]


def render_turn(turn: dict[str, Any]) -> None:
    st.markdown(turn["reply"])
    corrections = turn.get("corrections") or []
    if corrections:
        with st.expander(f"✏️ 이렇게 말하면 더 자연스러워요 ({len(corrections)})", expanded=True):
            for c in corrections:
                st.markdown(f"~~{c['original']}~~")
                st.markdown(f"**→ {c['better']}**")
                st.caption(c["note"])
    if turn.get("hint_ko"):
        st.info(f"💡 {turn['hint_ko']}")


def render_report(report: dict[str, Any]) -> None:
    insight = report["insight"]

    st.subheader("📘 학습 리포트")
    c1, c2, c3 = st.columns(3)
    c1.metric("주고받은 턴", report["turn_count"])
    c2.metric("교정 받은 문장", report["mistake_count"])
    c3.metric("레벨", report["level"])

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

    if report.get("mistakes"):
        st.markdown("#### 📝 틀린 문장 모음")
        for m in report["mistakes"]:
            with st.container(border=True):
                st.markdown(f"~~{m['original']}~~")
                st.markdown(f"**→ {m['better']}**")
                st.caption(m["note"])


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
