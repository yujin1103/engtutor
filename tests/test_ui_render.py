"""두 Streamlit 페이지를 실제로 실행해 렌더링을 확인한다.

지금까지 UI 검증은 "py_compile 이 통과한다" 수준이었다. 그건 문법만 본 것이라
`st.columns(..., vertical_alignment=...)` 처럼 런타임에만 터지는 것을 못 잡는다.

AppTest 는 브라우저 없이 스크립트를 끝까지 실행하고 렌더된 요소 트리를 준다.
버튼 클릭 후 재실행까지 되므로 '답 표시' 같은 상호작용도 검증된다.

채팅 페이지는 API 를 실제로 부르므로 `--live` 뒤에 둔다.
검수 페이지는 DB 만 읽어 항상 실행한다.

실행:
    docker compose exec -T ui pytest tests/test_ui_render.py -q
    docker compose exec -T ui pytest tests/test_ui_render.py -q --live   # 채팅 포함
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from pathlib import Path

# AppTest 는 상대 경로를 **호출한 파일** 기준으로 푼다. 절대 경로로 못 박는다.
ROOT = Path(__file__).resolve().parent.parent
CHAT_APP = str(ROOT / "ui" / "chat_app.py")
REVIEW_APP = str(ROOT / "content" / "review_app.py")


def _texts(at) -> str:
    """렌더된 모든 텍스트를 한 덩어리로. 문구가 실제로 화면에 있는지 보려는 것."""
    parts: list[str] = []
    for block in (at.markdown, at.caption, at.title, at.subheader, at.header):
        parts += [e.value for e in block]
    parts += [e.label for e in at.button]
    parts += [e.value for e in at.code]
    return "\n".join(str(p) for p in parts)


# ------------------------------------------------------------------ 검수 페이지
@pytest.fixture(scope="module")
def review():
    at = AppTest.from_file(REVIEW_APP, default_timeout=180)
    at.run()
    assert not at.exception, f"검수 페이지가 터졌습니다: {at.exception}"
    return at


def test_review_page_renders(review):
    assert "단어 콘텐츠 검수" in _texts(review)


def test_review_shows_the_four_metrics(review):
    labels = [m.label for m in review.metric]
    assert labels == ["전체", "미검수", "검수 완료", "🔴 지적된 미검수"]


def test_review_counts_match_the_database(review):
    from app.db import crud
    from app.db.database import db_session, init_db

    init_db()
    with db_session() as db:
        total = crud.count_words(db)
        approved = crud.count_words(db, reviewed=True)

    values = {m.label: m.value for m in review.metric}
    assert int(str(values["전체"]).replace(",", "")) == total
    assert int(str(values["검수 완료"]).replace(",", "")) == approved


def test_review_offers_both_sort_orders(review):
    orders = [r for r in review.radio if r.label == "정렬"]
    assert orders, "정렬 라디오가 없습니다"
    assert orders[0].options == ["의심 순", "빈도 순"]


def test_review_puts_the_riskiest_item_first(review):
    """기본 정렬이 '의심 순'이므로 심각한 것이 앞에 와야 한다.

    처음에는 `"🔴" in titles[0]` 로 못 박아 뒀다가 바꿨다. 이 페이지는 살아 있는
    DB 를 읽는데, 검수와 재생성으로 🔴 항목이 0개가 되자 시험이 깨졌다.
    **고쳐야 할 것이 없어졌다는 이유로 실패하는 시험**은 정렬을 검증하는 게 아니라
    데이터 상태를 검증하는 것이다. 검증할 성질은 '순서'다.
    """
    rank = {"🔴": 3, "🟡": 2, "⚪": 1}
    titles = [e.label for e in review.expander]
    assert titles, "항목이 하나도 렌더되지 않았습니다"

    worst = [max((rank[c] for c in t if c in rank), default=0) for t in titles]
    assert worst == sorted(worst, reverse=True), (
        f"의심 순 정렬이 뒤집혔습니다: {[t[:24] for t in titles]}"
    )


def test_review_shows_frequency_rank(review):
    """빈도 순위가 화면에 보여야 검수자가 우선순위를 판단할 수 있다."""
    titles = " ".join(e.label for e in review.expander)
    assert "#" in titles


def test_review_form_has_every_editable_field(review):
    labels = [i.label for i in review.text_input] + [a.label for a in review.text_area]
    joined = " ".join(labels)
    for field in ("뜻", "문형", "예문", "사용 노트", "혼동 단어"):
        assert field in joined, f"'{field}' 입력란이 없습니다"


# ------------------------------------------------------------------ 시나리오 고르기
def test_picker_comes_before_the_chat():
    """시나리오가 33개다. 평평한 목록으로는 못 고르므로 분류를 먼저 보여준다."""
    at = AppTest.from_file(CHAT_APP, default_timeout=180)
    at.run()
    assert not at.exception, f"첫 화면이 터졌습니다: {at.exception}"
    assert "무엇을 연습할까요" in _texts(at)
    assert not at.chat_input, "고르기 전에는 입력창이 없어야 합니다"


def test_drilling_into_a_category_then_starting():
    """분류 -> 시나리오 -> 대화. 안으로 한 겹씩 들어가는 흐름이 실제로 이어지는가."""
    at = AppTest.from_file(CHAT_APP, default_timeout=180)
    at.run()
    at.button(key="cat-trouble").click().run()
    assert not at.exception

    at.button(key="go-pharmacy").click().run()
    assert not at.exception
    assert at.title[0].value == "약국에서 약 사기"
    assert at.chat_input, "대화 화면에는 입력창이 있어야 합니다"


def test_settings_survive_choosing_a_scenario():
    """교정 강도를 바꿔 두고 대화를 시작하면 그 값이 유지돼야 한다.

    예전에는 위젯에 key 없이 value= 로 세션 상태를 되먹였다. 그러면 위젯이 다시
    만들어질 때 기본값으로 풀려서, 강도를 낮춰 놓고 대화를 시작하면 도로 올라갔다.
    """
    at = AppTest.from_file(CHAT_APP, default_timeout=180)
    at.run()
    at.select_slider(key="strictness").set_value("gentle").run()
    at.radio(key="level").set_value("B1").run()

    at.button(key="cat-food").click().run()
    at.button(key="go-cafe_order").click().run()

    assert at.session_state["strictness"] == "gentle", "교정 강도가 풀렸습니다"
    assert at.session_state["level"] == "B1", "레벨이 풀렸습니다"
    assert at.select_slider(key="strictness").value == "gentle"
    assert at.radio(key="level").value == "B1"


# ------------------------------------------------------------------ 채팅 페이지
def _open_chat(timeout: int = 300):
    """선택 화면을 지나 대화 화면까지 들어간다.

    첫 화면이 시나리오 고르기로 바뀌었으므로, 대화를 보려면 두 번 눌러야 한다.
    """
    at = AppTest.from_file(CHAT_APP, default_timeout=timeout)
    at.run()
    at.button(key="cat-food").click().run()
    at.button(key="go-cafe_order").click().run()
    assert not at.exception, f"대화 화면 진입에서 터졌습니다: {at.exception}"
    return at


@pytest.fixture(scope="module")
def chat():
    return _open_chat()


@pytest.mark.live
def test_chat_page_renders(chat):
    assert chat.title, "제목이 없습니다"


@pytest.mark.live
def test_chat_sidebar_has_the_controls(chat):
    """시나리오 선택은 사이드바 목록에서 본문 드릴다운으로 옮겼다.

    사이드바에는 대화 내내 바꿀 수 있어야 하는 것만 남긴다 — 레벨과 교정 강도,
    그리고 다른 시나리오로 나가는 문.
    """
    assert not chat.sidebar.selectbox, "시나리오 목록이 사이드바에 남아 있습니다"
    assert any(r.label == "레벨" for r in chat.sidebar.radio)
    assert any(s.label == "교정 강도" for s in chat.sidebar.select_slider)
    assert any("다른 시나리오" in b.label for b in chat.sidebar.button)


@pytest.mark.live
def test_strictness_labels_come_from_the_server(chat):
    """UI 가 라벨을 복제하지 않는다는 계약.

    AppTest 의 options 는 format_func 를 거친 **화면에 뜨는 값**이다.
    그래서 서버 응답과 직접 비교하면 계약이 그대로 검증된다 — UI 파일에
    문구가 하드코딩돼 있으면 여기서 어긋난다.
    """
    import os

    import httpx

    base = os.getenv("API_BASE_URL", "http://api:8000")
    served = [m["label"] for m in httpx.get(f"{base}/strictness", timeout=10).json()]

    slider = next(s for s in chat.sidebar.select_slider if s.label == "교정 강도")
    assert list(slider.options) == served, "화면 문구가 서버가 내려준 라벨과 다릅니다"


@pytest.mark.live
def test_say_bar_shows_a_hint_and_hides_the_answer(chat):
    """왕초보 설계의 핵심: 힌트는 보이고 영어는 눌러야 나온다."""
    text = _texts(chat)
    assert "💡" in text, "힌트가 안 보입니다"
    assert "🔤 답 표시" in [b.label for b in chat.button], "답 표시 버튼이 없습니다"

    shown = [c.value for c in chat.code]
    assert "Sorry, I don't understand." in shown, "바닥 문장이 없습니다"
    # 첫 발화의 say_en 은 버튼을 누르기 전에는 나오면 안 된다
    assert len(shown) == 1, f"누르기 전에 영어가 노출됐습니다: {shown}"


@pytest.mark.live
def test_pressing_the_button_reveals_the_english():
    at = _open_chat()
    button = next(b for b in at.button if b.label == "🔤 답 표시")
    button.click().run()
    assert not at.exception

    shown = [c.value for c in at.code]
    assert len(shown) >= 3, f"영어 두 줄이 안 나왔습니다: {shown}"
    assert "🔤 답 표시" not in [b.label for b in at.button], "누른 뒤에도 버튼이 남아 있습니다"


@pytest.mark.live
def test_sending_a_message_streams_and_renders_a_turn():
    """스트리밍 경로를 UI 째로 통과시킨다. 여기서 터지면 실사용에서 터진다."""
    at = _open_chat()
    at.chat_input[0].set_value("I want ice americano").run()
    assert not at.exception, f"전송 중 터졌습니다: {at.exception}"

    text = _texts(at)
    assert "고쳐볼까요" in text or "자연스러워요" in text or "💡" in text
    # 스트리밍 커서가 화면에 남아 있으면 안 된다 (최종 렌더로 교체돼야 함)
    assert "▌" not in text, "스트리밍 커서가 남았습니다"
