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
    """기본 정렬이 '의심 순'이므로 첫 항목에 🔴 가 붙어 있어야 한다."""
    titles = [e.label for e in review.expander]
    assert titles, "항목이 하나도 렌더되지 않았습니다"
    assert "🔴" in titles[0], f"첫 항목에 위험 표시가 없습니다: {titles[0]!r}"


def test_review_shows_frequency_rank(review):
    """빈도 순위가 화면에 보여야 검수자가 우선순위를 판단할 수 있다."""
    titles = " ".join(e.label for e in review.expander)
    assert "#" in titles


def test_review_form_has_every_editable_field(review):
    labels = [i.label for i in review.text_input] + [a.label for a in review.text_area]
    joined = " ".join(labels)
    for field in ("뜻", "예문", "사용 노트", "혼동 단어"):
        assert field in joined, f"'{field}' 입력란이 없습니다"


# ------------------------------------------------------------------ 채팅 페이지
@pytest.fixture(scope="module")
def chat():
    at = AppTest.from_file(CHAT_APP, default_timeout=300)
    at.run()
    assert not at.exception, f"채팅 페이지가 터졌습니다: {at.exception}"
    return at


@pytest.mark.live
def test_chat_page_renders(chat):
    assert chat.title, "제목이 없습니다"


@pytest.mark.live
def test_chat_sidebar_has_the_three_controls(chat):
    assert [s.label for s in chat.sidebar.selectbox] == ["시나리오"]
    assert any(r.label == "레벨" for r in chat.sidebar.radio)
    assert any(s.label == "교정 강도" for s in chat.sidebar.select_slider)


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
    at = AppTest.from_file(CHAT_APP, default_timeout=300)
    at.run()
    button = next(b for b in at.button if b.label == "🔤 답 표시")
    button.click().run()
    assert not at.exception

    shown = [c.value for c in at.code]
    assert len(shown) >= 3, f"영어 두 줄이 안 나왔습니다: {shown}"
    assert "🔤 답 표시" not in [b.label for b in at.button], "누른 뒤에도 버튼이 남아 있습니다"


@pytest.mark.live
def test_sending_a_message_streams_and_renders_a_turn():
    """스트리밍 경로를 UI 째로 통과시킨다. 여기서 터지면 실사용에서 터진다."""
    at = AppTest.from_file(CHAT_APP, default_timeout=300)
    at.run()
    at.chat_input[0].set_value("I want ice americano").run()
    assert not at.exception, f"전송 중 터졌습니다: {at.exception}"

    text = _texts(at)
    assert "고쳐볼까요" in text or "자연스러워요" in text or "💡" in text
    # 스트리밍 커서가 화면에 남아 있으면 안 된다 (최종 렌더로 교체돼야 함)
    assert "▌" not in text, "스트리밍 커서가 남았습니다"
