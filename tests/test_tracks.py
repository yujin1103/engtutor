"""어휘 트랙 — 생활 회화(general)와 토익(toeic)을 가르는 칸.

왜 칸이 하나 더 필요했나
------------------------
`words` 는 한 통이었다. 거기에 TSL·BSL 2,260개를 부으면 **카페 주문을 연습하는
왕초보에게 `reimbursement` 가 빈칸으로 나간다.** 반대로 토익 화면에는 `and`·`the`
같은 NGSL 상위 낱말이 채워진다. 둘은 어휘도, 예문이 놓이는 자리도 다른 물건이다.

그래서 이 시험이 지키는 것은 두 방향의 같은 약속 하나다.

    기존 3,245개가 토익 목록에 섞여 나오지 않는다.
    토익 2,260개가 왕초보 연습장에 나오지 않는다.

칸을 더한 것만으로는 그 약속이 지켜지지 않는다 — 조회하는 쪽이 매번 트랙을
기억해야 하면 언젠가 한 곳이 빠뜨린다. 그래서 **기본값**으로 막았고, 여기서
시험하는 것도 대부분 "안 넘긴 호출이 무엇을 받는가" 다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.content.generator import load_rank_offset, load_track, load_wordlist
from app.content.schemas import WordEntry
from app.db.models import TRACK_GENERAL, TRACK_TOEIC

from .conftest import temporary_database

DATA = Path(__file__).resolve().parent.parent / "content" / "data"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    with temporary_database(tmp_path / "tracks.db", monkeypatch) as database:
        yield database


def _entry(word: str = "latte", **over) -> WordEntry:
    """생활 회화 쪽 항목 하나."""
    base = {
        "word": word,
        "level": "A2",
        "meaning_ko": "라떼 (우유를 넣은 커피)",
        "pattern": "a/the + latte",
        "example": "I'll have a latte, please.",
        "usage_note": "우유가 들어간 커피예요. 아메리카노와 달리 우유가 많이 들어가요.",
        "confused_with": [],
    }
    base.update(over)
    return WordEntry(**base)


def _toeic(word: str = "invoice", **over) -> WordEntry:
    """토익 쪽 항목 하나. 예문이 일터의 문장이라는 점이 다르다."""
    base = {
        "word": word,
        "level": "B1",
        "meaning_ko": "송장, 청구서",
        "pattern": "send/pay + the invoice",
        "example": "Please send the invoice by Friday.",
        "usage_note": "거래 대금을 청구하는 서류예요. 영수증(receipt)은 이미 낸 돈의 증거라 달라요.",
        "confused_with": [],
    }
    base.update(over)
    return WordEntry(**base)


# ------------------------------------------------------------------ 목록이 트랙을 선언한다
def test_a_list_declares_its_own_track(tmp_path: Path):
    """트랙은 목록의 성질이지 실행할 때 고르는 것이 아니다.

    플래그로 두면 한 번 빠뜨렸을 때 토익 어휘 2,260개가 왕초보 트랙으로 통째로
    쏟아지고, 트랙은 행을 만들 때만 정해지므로 되돌리려면 지우고 다시 만들어야 한다.
    """
    path = tmp_path / "toeic.csv"
    path.write_text("# 토익 목록\n# track: toeic\ninvoice,1\ndeadline,2\n", encoding="utf-8")
    assert load_track(path) == TRACK_TOEIC


def test_a_plain_list_declares_no_track(tmp_path: Path):
    """선언이 없으면 None. 호출부가 기본 트랙(생활 회화)으로 읽는다."""
    path = tmp_path / "ngsl.csv"
    path.write_text("# NGSL headwords\nbe,1\nand,2\n", encoding="utf-8")
    assert load_track(path) is None


def test_the_declaration_is_only_read_from_the_header(tmp_path: Path):
    """단어가 시작된 뒤의 `# track:` 은 선언이 아니다.

    `# topic:` 은 그 아래 단어들에만 걸리는 구간 선언이라 파일 중간에 나와도 된다.
    트랙은 파일 전체의 성질이라 규칙이 다르다 — 중간에서도 읽으면 한 파일이 두
    트랙을 가진 것처럼 보이고, 어느 쪽이 이기는지 아무도 모르게 된다.
    """
    path = tmp_path / "mixed.csv"
    path.write_text("# 목록\nbe,1\n# track: toeic\ninvoice,2\n", encoding="utf-8")
    assert load_track(path) is None


def test_the_lists_on_disk_still_declare_what_they_are():
    """실제 파일의 선언을 계약으로 못 박는다.

    이 줄들을 지우면 다음에 배치를 돌리는 사람이 토익 어휘를 왕초보 트랙에 쏟는다.
    실패했을 때 원인이 보이도록 시험을 파일에 직접 건다.
    """
    assert load_track(DATA / "tsl.csv") == TRACK_TOEIC
    assert load_track(DATA / "bsl.csv") == TRACK_TOEIC
    assert load_track(DATA / "ngsl.csv") is None
    # BSL 은 TSL 1,250 뒤에 이어 붙인다. 둘 다 1위부터 매겨진 다른 코퍼스의 순위라
    # 그냥 합치면 토익 트랙에 1위가 둘 생긴다.
    assert load_rank_offset(DATA / "tsl.csv") == 0
    assert load_rank_offset(DATA / "bsl.csv") == 1250


def test_the_toeic_lists_keep_their_multiword_headwords_visible():
    """`ice cream`·`résumé` 는 표제어로 받지 않는다 — **조용히는 아니다.**

    이 저장소에는 하이픈 표제어가 토큰 분해에서 통째로 떨어진 전례가 있다.
    `e-book`·`by-law` 는 살아야 하고, 한 낱말이 아닌 것은 빠지되 세어져야 한다.
    """
    words = load_wordlist(DATA / "tsl.csv")
    assert "e-book" in words and "by-law" in words and "o'clock" in words
    assert "ice cream" not in words and "résumé" not in words
    # 1,250개 중 받지 못하는 4개(ice cream, résumé, café, entrée)를 뺀 수.
    assert len(words) == 1246


# ------------------------------------------------------------------ 저장
def test_a_new_entry_lands_in_the_track_it_was_generated_for(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _toeic(), track=TRACK_TOEIC, rank=19)
        crud.upsert_word(s, _entry())

    with db.db_session() as s:
        rows = {r.word: r for r in crud.list_words(s)}
        assert rows["invoice"].track == TRACK_TOEIC
        assert rows["invoice"].rank == 19
        # 트랙을 안 넘긴 호출은 생활 회화다. 지금까지의 목록이 전부 그것이었다.
        assert rows["latte"].track == TRACK_GENERAL


def test_a_word_that_already_exists_keeps_its_first_track(db):
    """TSL 1,250개 중 149개가 이미 장면 팩으로 들어와 있다 — `airport`·`refund`·`lobby`.

    토익 목록에 있다는 이유로 트랙을 옮기면 카페·공항 팩에서 그 낱말이 사라진다.
    먼저 들어간 트랙이 그 낱말의 트랙이고, 배치가 다시 돌아도 바뀌지 않는다.
    """
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(word="airport", example="The airport is very far.",
                                   meaning_ko="공항", pattern="at/to the + airport",
                                   usage_note="비행기를 타는 곳이에요. 역(station)과 헷갈리지 마세요."),
                         topic="transport")

    with db.db_session() as s:
        crud.upsert_word(
            s,
            _entry(word="airport", example="The airport is very far.", meaning_ko="공항 (다시 생성)",
                   pattern="at/to the + airport",
                   usage_note="비행기를 타는 곳이에요. 역(station)과 헷갈리지 마세요."),
            track=TRACK_TOEIC,
            rank=5,
        )

    with db.db_session() as s:
        row = crud.list_words(s)[0]
        assert row.track == TRACK_GENERAL  # 옮기지 않는다
        assert row.topic == "transport"  # 팩에 그대로 남는다
        assert row.rank is None  # 남의 트랙 순위를 적지 않는다
        assert row.meaning_ko == "공항 (다시 생성)"  # 내용은 갱신된다


def test_a_rank_is_written_when_the_row_is_created(db):
    """새로 만든 행이 순위 없이 들어가면 토익 화면의 정렬 축이 통째로 빈다.

    `assign_ranks` 는 **이미 있는** 행만 고치므로 그것만으로는 첫 생성 때 순위가
    비어 있었다. 예전에는 배치를 두 번 돌려야 채워졌다.
    """
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _toeic(), track=TRACK_TOEIC, rank=19)
    with db.db_session() as s:
        assert crud.list_words(s)[0].rank == 19


# ------------------------------------------------------------------ 순위는 트랙 안에서만
def test_assign_ranks_only_touches_its_own_track(db):
    """TSL 로 순위를 매기면서 생활 회화 쪽 순서를 건드리면 안 된다.

    트랙을 안 좁히면 이미 장면 팩에 있는 `vacation` 이 TSL 2위를 얻어 NGSL 2위
    (`and`)와 나란히 서고, 왕초보 연습장의 출제 순서가 조용히 뒤집힌다.
    """
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(word="vacation", meaning_ko="휴가",
                                   pattern="on + vacation", example="I am on vacation now.",
                                   usage_note="쉬는 기간이에요. 영국에서는 holiday 를 더 써요."))
        crud.upsert_word(s, _toeic(), track=TRACK_TOEIC)

    with db.db_session() as s:
        changed = crud.assign_ranks(s, ["vacation", "invoice"], track=TRACK_TOEIC)

    with db.db_session() as s:
        rows = {r.word: r for r in crud.list_words(s)}
        assert changed == 1
        assert rows["invoice"].rank == 2
        assert rows["vacation"].rank is None


def test_the_second_list_is_appended_behind_the_first(db):
    """BSL 순위는 1251 부터다. 두 목록의 1위가 겹치면 정렬이 뒤엉킨다.

    이어 붙인 번호가 "BSL 이 TSL 보다 덜 쓰인다"는 뜻은 아니다. 화면에 낼 순서이고,
    토익 화면에서는 시험 어휘(TSL)가 먼저 나오는 것이 맞아서 이 순서를 골랐다.
    """
    from app.db import crud

    assert crud.rank_map(["equity", "hedge"], offset=1250) == {"equity": 1251, "hedge": 1252}

    with db.db_session() as s:
        crud.upsert_word(s, _toeic(word="equity", meaning_ko="자기자본, 지분",
                                   pattern="equity: 불가산명사", example="Our equity grew last year.",
                                   usage_note="회사 가치에서 빚을 뺀 몫이에요. 주식(stock)과는 다른 말이에요."),
                         track=TRACK_TOEIC)
    with db.db_session() as s:
        crud.assign_ranks(s, ["equity"], track=TRACK_TOEIC, offset=1250)
    with db.db_session() as s:
        assert crud.list_words(s)[0].rank == 1251


def test_the_appended_list_does_not_re_rank_what_the_first_one_ranked(db):
    """두 목록은 겹친다. 뒤에 붙는 목록이 앞의 순위를 덮으면 첫 장이 통째로 바뀐다.

    실측: BSL 1,744개 중 525개가 이미 TSL 에 있는 낱말이었다. 이 빗장 없이 BSL 을
    돌렸더니 TOEIC 3위 `client` 가 1264위, TSL 15위 `goods` 가 1252위로 밀렸다.
    한 트랙에서 한 낱말의 순위는 **그것을 먼저 실은 목록**이 정한다.
    """
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _toeic(), track=TRACK_TOEIC, rank=19)  # TSL 19위

    with db.db_session() as s:
        # BSL 목록에도 `invoice` 가 있다. 이어 붙이는 판이므로 건드리면 안 된다.
        changed = crud.assign_ranks(
            s, ["invoice"], track=TRACK_TOEIC, offset=1250, keep_existing=True
        )

    with db.db_session() as s:
        assert changed == 0
        assert crud.list_words(s)[0].rank == 19


# ------------------------------------------------------------------ 조회가 트랙을 넘지 않는다
def _seed_both(db) -> None:
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(), topic="cafe", rank=7)
        crud.upsert_word(s, _toeic(), track=TRACK_TOEIC, rank=19)


def test_the_practice_list_never_serves_toeic_words(db):
    """이 시험이 이 파일의 이유다. 트랙을 안 넘긴 호출은 왕초보 낱말만 받는다."""
    from app.db import crud

    _seed_both(db)
    with db.db_session() as s:
        assert [r.word for r in crud.cloze_candidates(s, level="")] == ["latte"]


def test_the_toeic_list_never_serves_the_conversation_words(db):
    from app.db import crud

    _seed_both(db)
    with db.db_session() as s:
        rows = crud.cloze_candidates(s, level="", track=TRACK_TOEIC)
        assert [r.word for r in rows] == ["invoice"]


def test_alternatives_stay_inside_the_track(db):
    """후보 목록의 이름표("비슷하게 자주 쓰는 명사예요")는 트랙 안에서만 참이다.

    NGSL 300위와 TSL 300위는 다른 코퍼스의 300위라 나란히 놓을 근거가 없다.
    화면이 그 문구를 그대로 띄우므로 이 조건이 곧 그 문구의 참·거짓이다.
    """
    from app.db import crud

    _seed_both(db)
    with db.db_session() as s:
        general = crud.cloze_alternatives(s, word="coffee", topic=None, rank=10, level="A2")
        toeic = crud.cloze_alternatives(
            s, word="coffee", topic=None, rank=10, level="A2", track=TRACK_TOEIC
        )
    assert [r.word for r in general] == ["latte"]
    assert [r.word for r in toeic] == ["invoice"]


def test_topics_do_not_count_the_other_track(db):
    """장면 목록은 왕초보 화면의 것이다. 토익 쪽에 장면이 붙어도 섞여 나오면 안 된다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(), topic="cafe")
        crud.upsert_word(s, _toeic(), topic="meeting", track=TRACK_TOEIC)

    with db.db_session() as s:
        assert crud.topics(s) == [("cafe", 1, 0)]
        assert crud.topics(s, track=TRACK_TOEIC) == [("meeting", 1, 0)]
        assert len(crud.topics(s, track=None)) == 2


def test_counting_and_backfilling_can_be_narrowed_to_a_track(db):
    """해석 백필은 트랙 하나만 돌릴 수 있어야 한다 — 토익 2,260개를 채우는 동안
    생활 회화 쪽 순서에 끼어들 이유가 없다."""
    from app.db import crud

    _seed_both(db)
    with db.db_session() as s:
        assert crud.count_words(s) == 2
        assert crud.count_words(s, track=TRACK_TOEIC) == 1
        assert crud.words_missing_example_ko(s, track=TRACK_TOEIC) == ["invoice"]
        assert crud.words_missing_example_ko(s, track=TRACK_GENERAL) == ["latte"]
        assert sorted(crud.words_missing_example_ko(s)) == ["invoice", "latte"]
        assert crud.list_words(s, track=TRACK_TOEIC)[0].word == "invoice"


# ------------------------------------------------------------------ API
@pytest.fixture()
def client(db):
    from app import main

    return TestClient(main.app)


def test_the_cloze_endpoint_defaults_to_the_beginner_track(client, db):
    """`track` 을 안 준 요청이 안전한 쪽을 받는다. 기본값이 곧 안전장치다."""
    _seed_both(db)
    words = [c["word"] for c in client.get("/cloze", params={"level": "", "count": 20}).json()]
    assert words == ["latte"]


def test_the_cloze_endpoint_can_ask_for_the_toeic_track(client, db):
    _seed_both(db)
    words = [
        c["word"]
        for c in client.get(
            "/cloze", params={"level": "", "count": 20, "track": TRACK_TOEIC}
        ).json()
    ]
    assert words == ["invoice"]


def test_the_explanation_card_of_a_toeic_word_stays_in_its_track(client, db):
    """채점 뒤 설명 카드에 붙는 후보도 트랙을 넘으면 안 된다."""
    _seed_both(db)
    card = client.post(
        "/cloze/answer", json={"word": "invoice", "said": "invoice", "explain": True}
    ).json()["explain"]
    others = (card.get("alternatives") or {}).get("words", [])
    assert "latte" not in [w["word"] for w in others]
