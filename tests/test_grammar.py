"""토익 Part 5 형 4지선다 문법 문제(app/tutor/grammar.py).

이 문제 유형이 빈칸 연습장과 다른 점이 곧 여기서 고정할 것이다.

**정답이 새어 나갈 길이 셋이나 있다.** 응답 필드, 문제 id, 보기 순서. 셋 다 막혀
있어야 하고 셋 다 실수하기 쉽다 — 실제로 만드는 도중 id 를 사람이 읽을 수 있게
지었다가(`to_infinitive:Please remember...:send`) 응답에서 정답을 뺀 것이 아무
소용이 없어진 적이 있다.

**보기에 지어낸 낱말이 들어가면 안 된다.** 이 프로젝트가 낱말 콘텐츠에서 계속
싸워 온 것이 그것이고(docs/hallucinations.md), 사용자가 처음 든 예시부터
`sendable` 이라는 실재하지 않는 낱말을 담고 있었다. 그래서 형태를 코드가 만들지
않고 검수된 데이터에서만 가져오는지 못 박는다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.tutor import grammar
from app.tutor.slot import BLANK


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def rule() -> grammar.Rule:
    return grammar.rules()["to_infinitive"]


# ── 데이터 파일 자체 ────────────────────────────────────────────────


def test_every_frame_verb_has_reviewed_forms(rule: grammar.Rule) -> None:
    """틀이 부르는 동사는 모두 형태 표에 있어야 한다.

    없으면 그 짝이 **말없이 사라진다** — make_item 이 None 을 돌려주고 문제 수만
    줄어든다. 오타 하나로 열 문제가 조용히 없어지는 것을 막는다.
    """
    forms = grammar.verb_forms()
    missing = sorted({v for f in rule.frames for v in f.verbs if v not in forms})
    assert missing == [], f"형태 표에 없는 동사: {missing}"


def test_every_frame_has_the_blank_marker(rule: grammar.Rule) -> None:
    """빈칸 자리가 없는 틀은 문제가 되지 않는다."""
    for frame in rule.frames:
        assert "___" in frame.text, f"빈칸이 없는 틀: {frame.text}"


def test_every_frame_puts_the_blank_right_after_to(rule: grammar.Rule) -> None:
    """이 규칙의 틀은 반드시 `to ___` 여야 한다.

    규칙이 'to 뒤에는 동사원형' 이므로 빈칸이 to 바로 뒤가 아니면 그 근거가
    성립하지 않는다. 틀을 손으로 쓰다 보면 이걸 어기기 쉽다.
    """
    for frame in rule.frames:
        assert "to ___" in frame.text, f"to 바로 뒤가 아닌 빈칸: {frame.text}"


def test_no_verb_form_repeats_within_one_verb() -> None:
    """한 동사의 모양들이 서로 달라야 한다.

    같으면 보기 넷 중 둘이 같은 글자가 되어 답이 둘인 문제가 된다.
    `change` 의 명사형이 동사와 같은 글자인 것처럼 실제로 겹치는 낱말이 있어서,
    형태 표를 쓸 때 과거형·3인칭으로 갈아 끼웠다.
    """
    for word, forms in grammar.verb_forms().items():
        values = [v for _, v in forms.named()] + [word]
        assert len(values) == len(set(values)), f"{word} 의 모양이 겹칩니다: {values}"


def test_forms_are_not_generated_by_spelling_rules() -> None:
    """모양은 규칙이 아니라 검수된 표에서 온다 — 표에 없으면 문제도 없다.

    왜 이걸 시험으로 못 박는가: 규칙으로 만들면 `openning`·`builded`·`attachs`
    가 나온다. WordNet 으로 걸러지지도 않는다 — `morphy('builded')` 도
    `lexicon.known('sended')` 도 참이다(접미사를 떼어 원형에 닿기만 하면
    통과시킨다). -ing 의 자음 겹치기는 강세에 달려 있어 규칙 자체가 없다.
    """
    rule = grammar.rules()["to_infinitive"]
    frame = grammar.Frame(text="Please try to ___ it.", ko="그것을 ~해 보세요.", verbs=["zzz"])
    assert grammar.make_item(rule, frame, "zzz") is None


# ── 문제 만들기 ────────────────────────────────────────────────────


def test_items_have_four_distinct_choices_including_the_answer(rule: grammar.Rule) -> None:
    items = grammar.items_of(rule)
    assert items, "문제가 하나도 만들어지지 않았습니다"
    for item in items:
        words = [c.word for c in item.choices]
        assert len(words) == grammar.CHOICE_COUNT
        assert len(set(words)) == grammar.CHOICE_COUNT, f"보기가 겹칩니다: {words}"
        assert item.answer in words


def test_the_answer_is_always_the_bare_verb(rule: grammar.Rule) -> None:
    """정답은 늘 동사원형이다. 그것이 이 규칙이 가르치는 전부다."""
    for item in grammar.items_of(rule):
        base = [c.word for c in item.choices if c.kind == "base"]
        assert base == [item.answer]


def test_the_sentence_carries_the_blank(rule: grammar.Rule) -> None:
    for item in grammar.items_of(rule):
        assert BLANK in item.sentence
        assert "___" not in item.sentence.replace(BLANK, "")


def test_item_ids_are_unique(rule: grammar.Rule) -> None:
    """id 가 겹치면 채점이 다른 문제를 집는다."""
    ids = [i.id for i in grammar.items_of(rule)]
    assert len(ids) == len(set(ids))


def test_item_id_does_not_spell_out_the_answer(rule: grammar.Rule) -> None:
    """**id 로 정답을 알 수 있으면 안 된다.**

    처음에는 `to_infinitive:Please remember to ___ the invoice.:send` 처럼 읽을 수
    있는 id 를 썼는데, 그러면 응답에서 정답을 뺀 것이 아무 소용이 없다 — 화면이
    id 만 보고 답을 안다.
    """
    for item in grammar.items_of(rule):
        assert item.answer not in item.id
        assert item.sentence_ko not in item.id


def test_choice_order_is_stable_across_calls(rule: grammar.Rule) -> None:
    """같은 문제는 언제 봐도 보기 순서가 같다.

    매번 섞으면 학습자가 같은 문제를 다시 풀 때 답이 옮겨 다니고, 무엇보다
    서버가 채점한 것과 화면이 본 것이 어긋날 수 있다.
    """
    first = {i.id: [c.word for c in i.choices] for i in grammar.items_of(rule)}
    second = {i.id: [c.word for c in i.choices] for i in grammar.items_of(rule)}
    assert first == second


def test_the_answer_is_not_always_in_the_same_slot(rule: grammar.Rule) -> None:
    """정답의 자리가 흩어져야 한다 — 늘 첫 번째면 문장을 안 읽고도 맞힌다."""
    slots = [
        [c.word for c in item.choices].index(item.answer) for item in grammar.items_of(rule)
    ]
    assert len(set(slots)) == grammar.CHOICE_COUNT, f"정답이 쓰는 자리: {sorted(set(slots))}"


def test_consecutive_items_do_not_repeat_one_frame(rule: grammar.Rule) -> None:
    """처음 열 문제가 같은 문장이면 학습자는 문장을 안 읽고 보기만 본다.

    이 문제 유형에서 그건 연습을 통째로 무의미하게 만든다. 그래서 틀을 돌아가며 낸다.
    """
    first_ten = [i.sentence for i in grammar.items_of(rule)[:10]]
    assert len(set(first_ten)) == len(first_ten), f"같은 문장이 겹칩니다: {first_ten}"


# ── 채점 ───────────────────────────────────────────────────────────


def test_grading_accepts_the_bare_verb(rule: grammar.Rule) -> None:
    item = grammar.items_of(rule)[0]
    verdict = grammar.grade(item, item.answer, rule)
    assert verdict.ok
    assert rule.explain_ko in verdict.message_ko


def test_grading_names_the_form_the_learner_chose(rule: grammar.Rule) -> None:
    """왜 틀렸는지 말해 준다. 맞히는 것보다 아는 것이 목적이다."""
    item = grammar.items_of(rule)[0]
    wrong = next(c for c in item.choices if c.word != item.answer)
    verdict = grammar.grade(item, wrong.word, rule)
    assert not verdict.ok
    assert wrong.word in verdict.message_ko
    assert grammar.FORM_KO[wrong.kind] in verdict.message_ko


def test_grading_explains_every_choice(rule: grammar.Rule) -> None:
    item = grammar.items_of(rule)[0]
    verdict = grammar.grade(item, item.answer, rule)
    assert len(verdict.why_ko) == grammar.CHOICE_COUNT
    for choice in item.choices:
        assert any(choice.word in line for line in verdict.why_ko)


def test_grading_ignores_surrounding_space(rule: grammar.Rule) -> None:
    item = grammar.items_of(rule)[0]
    assert grammar.grade(item, f"  {item.answer} ", rule).ok


def test_grading_rejects_a_word_that_is_not_a_choice(rule: grammar.Rule) -> None:
    """보기에 없는 것을 보내도 500 이 아니라 오답이다."""
    item = grammar.items_of(rule)[0]
    verdict = grammar.grade(item, "zzzz", rule)
    assert not verdict.ok
    assert "보기" in verdict.message_ko


@pytest.mark.parametrize(
    ("name", "expected"),
    [("과거형", "이에요"), ("명사", "예요"), ("동사원형", "이에요"), ("형용사", "예요")],
)
def test_copula_follows_the_final_consonant(name: str, expected: str) -> None:
    """'과거형이에요' 와 '명사예요'. 받침이 있으면 이에요, 없으면 예요."""
    assert grammar._copula(name) == expected


# ── 엔드포인트 ─────────────────────────────────────────────────────


def test_listing_never_reveals_the_answer(client: TestClient) -> None:
    """**정답이 응답 어디에도 없어야 한다.** 필드로도, id 로도, 순서로도."""
    body = client.get("/grammar", params={"count": 50}).json()
    assert body
    assert "answer" not in {k for row in body for k in row}
    for row in body:
        assert "kind" not in {k for c in row["choices"] for k in c}


def test_listing_hides_which_form_each_choice_is(client: TestClient) -> None:
    """보기 옆에 '동명사' 라고 적어 두면 문장을 안 읽고도 고를 수 있다."""
    body = client.get("/grammar", params={"count": 5}).json()
    for row in body:
        for choice in row["choices"]:
            assert set(choice) == {"word"}


def test_answering_grades_and_explains(client: TestClient) -> None:
    row = client.get("/grammar", params={"count": 1}).json()[0]
    graded = client.post("/grammar/answer", json={"id": row["id"], "chosen": "zzz"}).json()
    right = client.post(
        "/grammar/answer", json={"id": row["id"], "chosen": graded["answer"]}
    ).json()
    assert not graded["ok"]
    assert right["ok"]
    assert len(right["why_ko"]) == grammar.CHOICE_COUNT


def test_answering_an_unknown_id_is_404(client: TestClient) -> None:
    res = client.post("/grammar/answer", json={"id": "deadbeef", "chosen": "send"})
    assert res.status_code == 404


def test_unknown_rule_is_an_empty_list_not_an_error(client: TestClient) -> None:
    """화면이 이것을 목록으로 그린다. 규칙이 없는 것과 문제가 떨어진 것을 같게 다룬다."""
    res = client.get("/grammar", params={"rule": "no_such_rule"})
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.parametrize(
    ("count", "offset"), [(0, 0), (-1, 0), (999, 0), (10, -5), (10, 99999)]
)
def test_listing_survives_odd_paging(client: TestClient, count: int, offset: int) -> None:
    res = client.get("/grammar", params={"count": count, "offset": offset})
    assert res.status_code == 200
    assert len(res.json()) <= 50


def test_paging_does_not_skip_or_repeat(client: TestClient) -> None:
    first = client.get("/grammar", params={"count": 10, "offset": 0}).json()
    second = client.get("/grammar", params={"count": 10, "offset": 10}).json()
    ids = [r["id"] for r in first] + [r["id"] for r in second]
    assert len(ids) == len(set(ids))
