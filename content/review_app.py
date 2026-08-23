"""단어 콘텐츠 검수 UI (Streamlit).

배치 생성 결과는 reviewed=false 로 들어온다. 여기서 사람이 고치고 승인해야
리포트에 노출된다. DB 를 직접 읽고 쓴다 — 검수는 단일 사용자 내부 도구라
API 를 한 겹 더 둘 이유가 없다.

실행:
    docker compose up -d review     # http://localhost:8502
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402

PAGE_SIZE = 10

st.set_page_config(page_title="engtutor 단어 검수", page_icon="📚", layout="wide")
init_db()

# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.header("필터")
    view = st.radio("보기", ["미검수", "검수 완료", "전체"], index=0)
    query = st.text_input("검색 (단어 · 뜻)", placeholder="borrow")
    st.caption("생성은 AI, 검수는 사람, 서빙은 DB")

reviewed_filter = {"미검수": False, "검수 완료": True, "전체": None}[view]

with db_session() as db:
    total = crud.count_words(db)
    pending = crud.count_words(db, reviewed=False)
    approved = crud.count_words(db, reviewed=True)

st.title("📚 단어 콘텐츠 검수")
c1, c2, c3 = st.columns(3)
c1.metric("전체", total)
c2.metric("미검수", pending)
c3.metric("검수 완료", approved)

if total == 0:
    st.info(
        "아직 생성된 단어가 없습니다. 먼저 배치를 돌리세요.\n\n"
        "```\ndocker compose exec api python content/batch_generate.py --limit 20\n```"
    )
    st.stop()

st.progress(approved / total if total else 0.0, text=f"검수 진행률 {approved}/{total}")
st.divider()

# ---------------------------------------------------------------- 목록
page = st.number_input("페이지", min_value=1, value=1, step=1) - 1

with db_session() as db:
    rows = crud.list_words(
        db, reviewed=reviewed_filter, query=query or None, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )
    items = [
        {
            "id": r.id,
            "word": r.word,
            "level": r.level,
            "meaning_ko": r.meaning_ko,
            "example": r.example,
            "usage_note": r.usage_note,
            "confused_with": ", ".join(r.confused_with or []),
            "reviewed": r.reviewed,
        }
        for r in rows
    ]

if not items:
    st.warning("조건에 맞는 항목이 없습니다.")
    st.stop()

for item in items:
    badge = "✅" if item["reviewed"] else "⏳"
    with st.expander(f"{badge} **{item['word']}** — {item['meaning_ko']}", expanded=not item["reviewed"]):
        with st.form(key=f"word-{item['id']}"):
            col_a, col_b = st.columns([1, 3])
            level = col_a.selectbox(
                "레벨", ["A1", "A2", "B1"],
                index=["A1", "A2", "B1"].index(item["level"]) if item["level"] in ("A1", "A2", "B1") else 0,
                key=f"level-{item['id']}",
            )
            meaning = col_b.text_input("뜻 (한국어)", value=item["meaning_ko"], key=f"meaning-{item['id']}")
            example = st.text_input("예문 (영어, 8단어 이내)", value=item["example"], key=f"ex-{item['id']}")
            usage = st.text_area(
                "사용 노트 (한국어)", value=item["usage_note"], height=90, key=f"usage-{item['id']}"
            )
            confused = st.text_input(
                "혼동 단어 (쉼표로 구분)", value=item["confused_with"], key=f"conf-{item['id']}"
            )
            approve = st.checkbox("검수 완료로 표시", value=item["reviewed"], key=f"rev-{item['id']}")

            saved = st.form_submit_button("저장", type="primary")
            if saved:
                with db_session() as db:
                    crud.save_word_edits(
                        db,
                        item["id"],
                        level=level,
                        meaning_ko=meaning.strip(),
                        example=example.strip(),
                        usage_note=usage.strip(),
                        confused_with=[w.strip() for w in confused.split(",") if w.strip()],
                        reviewed=approve,
                    )
                st.success(f"'{item['word']}' 저장했습니다.")
                st.rerun()
