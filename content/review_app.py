"""단어 콘텐츠 검수 UI (Streamlit).

배치 생성 결과는 reviewed=false 로 들어온다. 여기서 사람이 고치고 승인해야
리포트에 노출된다. DB 를 직접 읽고 쓴다 — 검수는 단일 사용자 내부 도구라
API 를 한 겹 더 둘 이유가 없다.

NGSL 2,801개를 앞에서부터 훑으면 12시간이다. 그래서 **선별기가 의심스러운
순서를 매기고**(app/content/screening.py), 사람은 나쁜 것부터 본다.
선별기는 승인하지 않는다 — 순서만 매긴다. 승인은 여전히 사람만 한다.

실행:
    docker compose up -d review     # http://localhost:8502
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content.screening import Finding, risk_score, screen_all, worst_severity  # noqa: E402
from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402

PAGE_SIZE = 10
ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}

st.set_page_config(page_title="engtutor 단어 검수", page_icon="📚", layout="wide")
init_db()


@st.cache_data(ttl=300, show_spinner="단어를 불러오는 중...")
def load_words() -> list[dict]:
    """빈도 순(NGSL 순위)으로 돌려준다.

    이게 기본 순서라야 '의심 순' 정렬이 안정 정렬로 뒤를 이어받아, 지적이 없는
    항목들 사이에서는 자주 쓰는 단어가 먼저 온다. 검수를 중간에 멈춰도
    가장 쓸모 있는 것부터 승인돼 있어야 한다.
    """
    with db_session() as db:
        rows = [
            {
                "id": r.id,
                "word": r.word,
                "level": r.level,
                "meaning_ko": r.meaning_ko,
                "pattern": r.pattern,
                "example": r.example,
                "usage_note": r.usage_note,
                "confused_with": list(r.confused_with or []),
                "rank": r.rank,
                "reviewed": r.reviewed,
                "reviewed_by": r.reviewed_by,
            }
            for r in crud.list_words(db, limit=100_000)
        ]
    # 순위가 없는 항목(목록 밖 단어)은 뒤로 보낸다.
    rows.sort(key=lambda w: (w["rank"] is None, w["rank"] or 0, w["word"]))
    return rows


@st.cache_data(ttl=300, show_spinner="선별하는 중...")
def load_findings() -> dict[str, list[dict]]:
    """표제어 -> 지적 목록. 복제 검사 때문에 **전체**를 한 번에 봐야 한다."""
    rows = [type("W", (), dict(w))() for w in load_words()]
    return {
        word: [{"code": f.code, "severity": f.severity, "message": f.message} for f in found]
        for word, found in screen_all(rows).items()
    }


def _score(found: list[dict]) -> int:
    return risk_score([Finding(**f) for f in found])


def _worst(found: list[dict]) -> str | None:
    return worst_severity([Finding(**f) for f in found])


words = load_words()
findings = load_findings() if words else {}

# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.header("필터")
    view = st.radio("보기", ["미검수", "검수 완료", "전체"], index=0)
    order = st.radio(
        "정렬",
        ["의심 순", "빈도 순"],
        index=0,
        help="의심 순: 선별기가 지적한 항목이 먼저, 그 다음은 빈도 순. 빈도 순: NGSL 순위대로(자주 쓰는 단어 먼저).",
    )
    flagged_only = st.checkbox("지적된 것만 보기", value=False)
    query = st.text_input("검색 (단어 · 뜻)", placeholder="borrow")
    st.divider()
    st.caption("생성은 AI, 검수는 사람, 서빙은 DB")
    st.caption("선별기는 순서만 매깁니다. 승인은 사람만 합니다.")

st.title("📚 단어 콘텐츠 검수")

total = len(words)
approved = sum(1 for w in words if w["reviewed"])
flagged = sum(1 for w in words if findings.get(w["word"]) and not w["reviewed"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("전체", total)
c2.metric("미검수", total - approved)
c3.metric("검수 완료", approved)
c4.metric("🔴 지적된 미검수", flagged)

if total == 0:
    st.info(
        "아직 생성된 단어가 없습니다. 먼저 배치를 돌리세요.\n\n"
        "```\ndocker compose exec api python content/batch_generate.py --limit 20\n```"
    )
    st.stop()

st.progress(approved / total, text=f"검수 진행률 {approved}/{total}")

no_pattern = sum(1 for w in words if not (w["pattern"] or "").strip())
if no_pattern:
    st.caption(
        f"문형이 비어 있는 항목 **{no_pattern}개**. 한 줄씩 손으로 채울 일이 아니라 배치가 채울 일이라 "
        "선별기는 이걸 지적하지 않습니다 — "
        "`docker compose exec api python content/batch_generate.py --missing-pattern`"
    )
if flagged:
    st.caption(
        f"미검수 {total - approved}개 중 **{flagged}개**가 지적됐습니다. "
        "'의심 순'으로 그것들부터 보고, 나머지는 빠르게 넘기면 됩니다."
    )
st.divider()

# ---------------------------------------------------------------- 목록
wanted_reviewed = {"미검수": False, "검수 완료": True, "전체": None}[view]
needle = query.strip().lower()

items = [
    w
    for w in words
    if (wanted_reviewed is None or w["reviewed"] is wanted_reviewed)
    and (not needle or needle in w["word"].lower() or needle in w["meaning_ko"].lower())
    and (not flagged_only or findings.get(w["word"]))
]

if order == "의심 순":
    # 파이썬 정렬은 안정적이라, 점수가 같으면 원래(빈도) 순서가 유지된다.
    items.sort(key=lambda w: _score(findings.get(w["word"], [])), reverse=True)

if not items:
    st.warning("조건에 맞는 항목이 없습니다.")
    st.stop()

pages = max(1, -(-len(items) // PAGE_SIZE))
page = st.number_input(f"페이지 (전체 {pages}쪽 · {len(items)}개)", 1, pages, 1, step=1) - 1
page_items = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

for item in page_items:
    found = findings.get(item["word"], [])
    worst = _worst(found)
    marks = "".join(ICON[f["severity"]] for f in found[:3])
    badge = "✅" if item["reviewed"] else "⏳"
    place = f"#{item['rank']}" if item["rank"] else "—"
    title = f"{badge} {marks} **{item['word']}** `{place}` — {item['meaning_ko']}"

    with st.expander(title, expanded=not item["reviewed"] and worst is not None):
        if item["reviewed"]:
            # 출처를 안 남기면 '검수됨'이 사람이 본 건지 모델이 본 건지 알 수 없다.
            st.caption(f"✅ 검수 완료 · {item['reviewed_by'] or '출처 미상 (기록 이전에 승인)'}")
        if found:
            for f in found:
                st.markdown(f"{ICON[f['severity']]} {f['message']}  `{f['code']}`")
            st.caption("선별기가 규칙으로 짚은 것입니다. 실제 판단은 직접 하세요.")

        with st.form(key=f"word-{item['id']}"):
            col_a, col_b = st.columns([1, 3])
            level = col_a.selectbox(
                "레벨", ["A1", "A2", "B1"],
                index=["A1", "A2", "B1"].index(item["level"]) if item["level"] in ("A1", "A2", "B1") else 0,
                key=f"level-{item['id']}",
            )
            meaning = col_b.text_input("뜻 (한국어)", value=item["meaning_ko"], key=f"meaning-{item['id']}")
            pattern = st.text_input(
                "문형 (형태만 · 예: listen to + 목적어)",
                value=item["pattern"] or "",
                key=f"pat-{item['id']}",
                help="뜻이 아니라 이 단어가 문장에서 취하는 형태입니다. 괄호 안은 선택 사항으로 봅니다.",
            )
            example = st.text_input(
                "예문 (영어, 8단어 이내 · 표제어를 실제로 쓸 것)",
                value=item["example"],
                key=f"ex-{item['id']}",
            )
            usage = st.text_area(
                "사용 노트 (한국어)", value=item["usage_note"], height=90, key=f"usage-{item['id']}"
            )
            confused = st.text_input(
                "혼동 단어 (쉼표로 구분)",
                value=", ".join(item["confused_with"]),
                key=f"conf-{item['id']}",
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
                        # 빈 칸은 NULL 로 남긴다. 빈 문자열이면 '채워야 할 문형'을
                        # 세는 쿼리에서 빠져 배치가 영영 채우지 않는다.
                        pattern=pattern.strip() or None,
                        example=example.strip(),
                        usage_note=usage.strip(),
                        confused_with=[w.strip() for w in confused.split(",") if w.strip()],
                        reviewed=approve,
                        # 이 화면에서 누른 건 사람이다. 출처를 안 남기면 나중에
                        # "검수됨"이 사람이 본 건지 모델이 본 건지 알 수 없다.
                        reviewed_by="human" if approve else None,
                    )
                # 저장하면 선별 결과(복제 검사 포함)가 달라진다. 캐시를 통째로 버린다.
                st.cache_data.clear()
                st.success(f"'{item['word']}' 저장했습니다.")
                st.rerun()
