"""읽기용 낱말 목록(`GET /words`). 토익 화면이 읽는 자리다.

이 엔드포인트가 따로 있는 이유가 곧 여기서 지킬 것이다. 빈칸(`/cloze`)은 문제라서
답을 가리고, 이건 외우러 온 사람에게 보여주는 목록이라 가리는 것이 없다. 같은 표를
읽는데 나가는 모양이 반대라, 한 스키마에 깃발 하나로 두면 언젠가 반대로 세팅된 채
나간다. 그래서 갈라 두고 **가려지지 않았는지**를 여기서 못 박는다.

나머지 셋:

- **트랙 기본값이 생활 회화다.** 빼먹은 호출이 왕초보용 낱말을 받아야 한다.
  거꾸로 두면 카페 주문을 연습하는 사람에게 `reimbursement` 가 나간다.
- **다음 자리는 서버가 정한다.** 안전 판정에 걸린 행이 중간에서 빠지므로
  `offset + len(items)` 로 계산하면 그만큼씩 앞으로 밀린다. 화면이 진도를
  적어 두고 이어 받는 기능이 이 값 하나에 걸려 있다.
- **단어장은 표제어만 받아 지금 내용을 돌려준다.** 화면이 카드를 폰에 복사해 두면
  뜻을 고친 뒤에도 옛 뜻이 남는다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.models import TRACK_GENERAL, TRACK_TOEIC

from .conftest import temporary_database


@pytest.fixture()
def db(tmp_path, monkeypatch):
    with temporary_database(tmp_path / "words.db", monkeypatch) as database:
        yield database


@pytest.fixture()
def client(db) -> TestClient:
    from app.main import app

    return TestClient(app)


def _seed(database, rows: list[dict]) -> None:
    from app.db.models import WordRow

    with database.db_session() as session:
        for values in rows:
            base = {
                "level": "A1",
                "meaning_ko": "뜻이 있어요",
                "example": "",
                "usage_note": "이 낱말은 이렇게 써요. 예문을 그대로 따라 말해 보세요.",
                "confused_with": [],
                "reviewed": False,
                "track": TRACK_TOEIC,
            }
            base.update(values)
            session.add(WordRow(**base))


# 순서를 보는 시험이 쓰는 낱말. **실재하는 영어여야 한다** — 선별기가 사전에
# 없는 표제어를 잡아내므로 `w01` 같은 가짜를 쓰면 목록이 통째로 비어 버리고,
# 그러면 순서가 아니라 선별기를 시험하게 된다.
SEVEN = ["client", "memo", "fax", "invoice", "deadline", "workshop", "budget"]


def _toeic(word: str, rank: int, **over) -> dict:
    """선별기를 통과하는 최소 행. 문형은 예문에 실제로 보이는 형태로 적는다."""
    base = {
        "word": word,
        "rank": rank,
        "example": f"Please send the {word} today.",
        "example_ko": f"오늘 {word} 를 보내 주세요.",
        "pattern": f"the + {word}",
        "meaning_ko": f"{word} 의 뜻",
        "usage_note": f"{word} 는 이렇게 써요. 예문을 그대로 따라 말해 보세요.",
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ 가리지 않는다
def test_the_reading_list_does_not_mask_the_headword(client, db):
    """빈칸과 반대다. 뜻·예문·해석에서 표제어를 지우지 않는다.

    `/cloze` 는 `mask_answer` 로 이 자리를 지운다 — 답이 새면 문제가 아니게 되니까.
    여기서 같은 일을 하면 `invoice` 를 외우러 온 사람이 '___ 를 보내 주세요' 를 본다.
    """
    _seed(db, [_toeic("invoice", 19)])
    page = client.get("/words", params={"track": TRACK_TOEIC}).json()

    card = page["items"][0]
    assert card["word"] == "invoice"
    assert "invoice" in card["example"]
    assert "invoice" in card["example_ko"]
    assert "invoice" in card["meaning_ko"]


def test_the_card_has_a_rank_and_no_level(client, db):
    """축은 빈도 순위 하나다. CEFR 레벨을 같이 주면 학습자가 그걸 순서로 읽는다."""
    _seed(db, [_toeic("client", 3)])
    card = client.get("/words", params={"track": TRACK_TOEIC}).json()["items"][0]

    assert card["rank"] == 3
    assert "level" not in card


# ------------------------------------------------------------------ 트랙
def test_the_default_track_is_the_beginner_one(client, db):
    """빼먹은 호출이 왕초보용 낱말을 받아야 한다. 기본값이 곧 안전장치다."""
    _seed(
        db,
        [
            _toeic("reimbursement", 5),
            _toeic("coffee", 5, track=TRACK_GENERAL),
        ],
    )
    words = [c["word"] for c in client.get("/words").json()["items"]]

    assert words == ["coffee"]
    assert "reimbursement" not in words


def test_the_frequency_order_is_kept(client, db):
    """빈도 순이 아니면 진도를 적어 두는 일 자체가 뜻을 잃는다."""
    _seed(db, [_toeic("third", 30), _toeic("first", 3), _toeic("second", 12)])
    page = client.get("/words", params={"track": TRACK_TOEIC}).json()

    assert [c["word"] for c in page["items"]] == ["first", "second", "third"]
    assert page["total"] == 3


def test_a_row_without_an_example_is_not_counted(client, db):
    """예문이 없으면 카드로 보여줄 것이 없다. 세지도 않는다."""
    _seed(db, [_toeic("invoice", 19), _toeic("blank", 20, example="", example_ko=None)])
    page = client.get("/words", params={"track": TRACK_TOEIC}).json()

    assert page["total"] == 1
    assert [c["word"] for c in page["items"]] == ["invoice"]


# ------------------------------------------------------------------ 이어 받기
def test_the_server_decides_where_the_next_page_starts(client, db):
    """화면이 `offset + len(items)` 로 계산하면 걸러진 행만큼 앞으로 밀린다.

    진도를 적어 두고 이어 받는 기능이 이 값 하나에 걸려 있다.
    """
    _seed(db, [_toeic(word, rank) for rank, word in enumerate(SEVEN, start=1)])
    first = client.get("/words", params={"track": TRACK_TOEIC, "count": 3}).json()

    assert [c["word"] for c in first["items"]] == SEVEN[:3]
    assert first["next_offset"] == 3

    second = client.get(
        "/words", params={"track": TRACK_TOEIC, "count": 3, "offset": first["next_offset"]}
    ).json()
    assert [c["word"] for c in second["items"]] == SEVEN[3:6]


def test_the_last_page_says_it_is_the_last(client, db):
    """끝에 닿으면 `null` 이다. 화면은 이걸로 '더 보기' 를 지운다."""
    _seed(db, [_toeic("only", 1)])
    page = client.get("/words", params={"track": TRACK_TOEIC, "count": 30}).json()

    assert page["next_offset"] is None


def test_an_unsafe_row_is_skipped_without_shortening_the_page(client, db):
    """걸러진 자리만큼 뒤에서 당겨 온다. 한 장이 짧아지면 목록이 듬성해진다."""
    rows = [_toeic(word, rank) for rank, word in enumerate(SEVEN[:5], start=1)]
    # 뜻에 한자가 섞인 행. 선별기가 잡아 학습자에게 안 나간다(오늘 53행이 그랬다).
    rows[2]["meaning_ko"] = "물龙头 (수도꼭지)"
    _seed(db, rows)

    page = client.get("/words", params={"track": TRACK_TOEIC, "count": 4}).json()
    kept = SEVEN[:2] + SEVEN[3:5]
    assert [c["word"] for c in page["items"]] == kept


# ------------------------------------------------------------------ 단어장
def test_the_wordbook_answers_with_todays_content(client, db):
    """화면은 표제어만 들고 있는다. 카드를 폰에 복사해 두면 옛 뜻이 영영 남는다."""
    _seed(db, [_toeic("client", 3), _toeic("deadline", 18), _toeic("invoice", 19)])
    page = client.get("/words", params={"track": TRACK_TOEIC, "words": "invoice,client"}).json()

    assert [c["word"] for c in page["items"]] == ["client", "invoice"]  # 빈도 순
    assert page["total"] == 2
    assert page["next_offset"] is None


def test_a_word_that_no_longer_exists_is_simply_missing(client, db):
    """담아 둔 낱말이 지워졌을 수 있다. 그것 때문에 단어장이 안 열리면 안 된다."""
    _seed(db, [_toeic("client", 3)])
    page = client.get("/words", params={"track": TRACK_TOEIC, "words": "client,gone"}).json()

    assert [c["word"] for c in page["items"]] == ["client"]


def test_an_empty_wordbook_is_not_the_whole_list(client, db):
    """빈 문자열을 '조건 없음' 으로 읽으면 단어장에 2,252개가 쏟아진다."""
    _seed(db, [_toeic("client", 3), _toeic("invoice", 19)])
    page = client.get("/words", params={"track": TRACK_TOEIC, "words": ""}).json()

    assert page["items"] == []
    assert page["total"] == 0
