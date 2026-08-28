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

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.tutor import grammar
from app.tutor.slot import BLANK


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _first_id(client: TestClient) -> str:
    """첫 문제의 id. id 가 내용 해시라 시험이 값을 적어 둘 수 없다."""
    return client.get("/grammar", params={"count": 1}).json()[0]["id"]


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


def test_item_id_is_not_a_hash_of_the_answer(rule: grammar.Rule) -> None:
    """**해시로 지어도 샌다.** 후보가 넷뿐이라 넷을 쳐 보면 맞는다.

    두 번째 판이 `sha256(f"{rule}:{frame}:{verb}")[:12]` 였다. 되읽기는 어렵지만
    씨앗 재료가 전부 응답에서 복원된다 — 규칙 이름은 그대로 있고, 틀은 문장의
    빈칸을 되돌리면 되고, 동사는 보기 넷 중 하나다. 네 번 해시해 id 와 맞는 것을
    고르면 정답이 나온다. **되읽기 어려운 것과 맞혀 보기 어려운 것은 다른 성질이다.**
    """
    for item in grammar.items_of(rule):
        frame_text = item.sentence.replace(BLANK, "___")
        for choice in item.choices:
            seed = f"{item.rule}:{frame_text}:{choice.word}".encode()
            assert item.id != hashlib.sha256(seed).hexdigest()[:12]


def test_choice_order_cannot_be_recomputed_from_the_response(rule: grammar.Rule) -> None:
    """**보기 순서도 정답에 대한 커밋먼트가 되면 안 된다.**

    첫 판은 id 앞 8자와 보기 순서를 **같은 다이제스트**에서 뽑았다.
    `hexdigest()[:8]` 과 `digest()[0:4]` 는 같은 바이트라, 공개된 id 만으로
    자리바꿈을 그대로 계산할 수 있었다. 정답은 늘 섞기 전 0번이라
    `order.index(0)` 이 곧 답의 자리였고 151개가 전부 뚫렸다.

    id 를 자리 번호로 바꿔도 순서 자체는 남는다 — 씨앗 재료가 응답에 다 있기
    때문이다. 그래서 순서는 **응답 밖의 값**을 섞어 만든다. 소금 없이 계산한
    순서가 실제 순서와 같으면 그 방어가 없다는 뜻이다.
    """
    matched = 0
    for item in grammar.items_of(rule):
        frame_text = item.sentence.replace(BLANK, "___")
        digest = hashlib.sha256(f"{item.rule}:{frame_text}:{item.answer}".encode()).digest()
        slots = list(range(grammar.CHOICE_COUNT))
        unsalted = [slots.pop(digest[i] % len(slots)) for i in range(grammar.CHOICE_COUNT)]
        # 섞기 전 0번이 정답이므로, 소금 없는 자리바꿈이 맞다면 답의 자리가 드러난다.
        if unsalted.index(0) == [c.word for c in item.choices].index(item.answer):
            matched += 1
    # 넷 중 하나는 우연히 맞는다. 전부 맞으면 소금이 안 걸린 것이다.
    assert matched < len(grammar.items_of(rule))


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
    """같은 문장이 이어 나오면 학습자는 문장을 안 읽고 보기만 본다.

    이 문제 유형에서 그건 연습을 통째로 무의미하게 만든다.

    **목록 전체를 본다.** 처음 열 개만 보던 시험이 실제 결함을 놓쳤다 — 한 바퀴씩
    도는 방식은 틀마다 동사 수가 달라 짧은 틀이 먼저 바닥나고, 147번부터 남은 긴 틀
    둘이 번갈아 나왔다. 앞은 멀쩡하고 뒤가 무너지는 종류라 앞만 보면 안 보인다.
    """
    sentences = [i.sentence for i in grammar.items_of(rule)]
    repeats = [
        (n, sentences[n]) for n in range(1, len(sentences)) if sentences[n] == sentences[n - 1]
    ]
    assert repeats == [], f"같은 문장이 이어 나옵니다: {repeats[:5]}"


def test_no_frame_takes_over_the_tail(rule: grammar.Rule) -> None:
    """목록 끝에서 몇 안 되는 틀이 판을 잡으면 안 된다.

    이어 나오는 것만 막으면 두 틀이 갈마드는 꼴은 그대로 통과한다. 그래서 마지막
    스무 개에 서로 다른 문장이 얼마나 있는지도 본다.
    """
    tail = [i.sentence for i in grammar.items_of(rule)[-20:]]
    assert len(set(tail)) >= len(tail) // 2, f"끝에서 문장이 몰립니다: {sorted(set(tail))}"


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


def test_grading_hides_the_answer_when_nothing_was_chosen(rule: grammar.Rule) -> None:
    """**보기에 없는 값에는 정답을 알려 주지 않는다.**

    처음에는 무엇을 보내든 답과 해설을 돌려줬다. 그러면 아무 글자나 한 번 보내는
    것만으로 문제마다 답을 받아 갈 수 있어서, 응답에서 정답을 빼고 id 에서 지우고
    보기 순서까지 소금으로 가린 것이 이 한 줄에서 무너진다.

    보안만의 문제도 아니다 — 보기 중에서 고르지 않았으면 답한 것이 아니고,
    답하지 않은 사람에게 답을 펴면 연습 한 번이 통째로 사라진다.
    """
    item = grammar.items_of(rule)[0]
    verdict = grammar.grade(item, "zzzz", rule)
    assert not verdict.ok
    assert verdict.answer == ""
    assert verdict.why_ko == []
    assert item.answer not in verdict.message_ko


def test_answering_with_junk_does_not_hand_out_the_answer(client: TestClient) -> None:
    """엔드포인트까지 확인한다 — 141문제를 한 번씩 찍어 답을 모을 수 없어야 한다."""
    rows = client.get("/grammar", params={"count": 5}).json()
    for row in rows:
        body = client.post("/grammar/answer", json={"id": row["id"], "chosen": "zzzz"}).json()
        assert body["answer"] == ""
        assert body["why_ko"] == []


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
    """보기 넷을 다 눌러 보면 하나만 맞고, 맞은 쪽이 넷의 정체를 다 알려 준다."""
    row = client.get("/grammar", params={"count": 1}).json()[0]
    graded = [
        client.post("/grammar/answer", json={"id": row["id"], "chosen": c["word"]}).json()
        for c in row["choices"]
    ]
    right = [g for g in graded if g["ok"]]
    assert len(right) == 1, "정답이 하나여야 합니다"
    assert len(right[0]["why_ko"]) == grammar.CHOICE_COUNT
    for wrong in (g for g in graded if not g["ok"]):
        # 오답에도 해설은 준다 — 왜 아닌지 알아야 다음에 안 틀린다.
        assert len(wrong["why_ko"]) == grammar.CHOICE_COUNT
        assert wrong["answer"] == right[0]["answer"]


def test_answering_an_unknown_id_is_404(client: TestClient) -> None:
    """모양은 맞는데 그런 문제가 없을 때가 404 다."""
    res = client.post("/grammar/answer", json={"id": "0" * 12, "chosen": "send"})
    assert res.status_code == 404


@pytest.mark.parametrize("chosen", ["", "x" * 500, "1234", "보내다"])
def test_odd_answers_are_refused_not_a_crash(client: TestClient, chosen: str) -> None:
    """**500 이 나오면 안 된다.** 거절할 값을 보냈는데 "서버가 부서졌다" 는 답이
    돌아오면 진짜 고장과 구별이 안 된다.
    """
    res = client.post("/grammar/answer", json={"id": _first_id(client), "chosen": chosen})
    assert res.status_code < 500


@pytest.mark.parametrize("field", ["id", "chosen"])
def test_a_lone_surrogate_does_not_crash_the_server(client: TestClient, field: str) -> None:
    """짝 없는 서로게이트가 실제로 500 을 냈다.

    검증은 제대로 거절해 놓고, **그 거절 사유를 JSON 으로 옮기다** 터졌다. 오류를
    만들다 난 고장이라 필드마다 막아서는 안 잡히고, 오류가 나가는 문에서 잡아야
    했다. `/cloze/answer` 도 같은 입력에 500 이었다.

    `json=` 으로는 보낼 수조차 없다(테스트 클라이언트가 먼저 막는다). 실제 공격자는
    바이트로 보내므로 여기서도 본문을 직접 만든다.
    """
    body = {"id": _first_id(client), "chosen": "send"}
    raw = '{"id": "%s", "chosen": "%s"}' % (
        "\\ud800" if field == "id" else body["id"],
        "\\ud800" if field == "chosen" else body["chosen"],
    )
    res = client.post(
        "/grammar/answer", content=raw.encode(), headers={"Content-Type": "application/json"}
    )
    assert res.status_code < 500


@pytest.mark.parametrize("bad_id", ["deadbeef", "a" * 5000, "", "../../etc/passwd"])
def test_odd_ids_are_refused_not_a_crash(client: TestClient, bad_id: str) -> None:
    """id 는 `to_infinitive#0007` 꼴로 고정이다. 5MB 를 보내면 5MB 가 돌아 나왔다."""
    res = client.post("/grammar/answer", json={"id": bad_id, "chosen": "send"})
    assert res.status_code < 500
    assert res.status_code != 200


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


def test_a_rule_file_naming_its_verbs_wrong_fails_loudly(tmp_path, monkeypatch) -> None:
    """형태 표에 없는 동사를 부르면 **시작할 때** 멈춘다.

    그냥 두면 `make_item` 이 None 을 돌려주고 그 짝이 조용히 사라진다 — 오타
    하나로 열 문제가 없어져도 아무도 모르고, 화면에는 그냥 문제가 좀 적게 나온다.
    """
    (tmp_path / "typo_rule.yaml").write_text(
        "rule: typo_rule\ntitle: 틀\nexplain_ko: 설명\n"
        "frames:\n  - text: 'Please try to ___ it.'\n    ko: '~해 보세요.'\n"
        "    verbs: [sned]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(grammar, "RULES_DIR", tmp_path)
    grammar.rules.cache_clear()
    try:
        with pytest.raises(ValueError, match="형태 표"):
            grammar.rules()
    finally:
        grammar.rules.cache_clear()


def test_a_rule_file_whose_name_disagrees_fails_loudly(tmp_path, monkeypatch) -> None:
    """파일 이름과 안의 이름이 어긋나면 사람이 파일을 못 찾는다."""
    (tmp_path / "one_name.yaml").write_text(
        "rule: another_name\ntitle: 틀\nexplain_ko: 설명\n"
        "frames:\n  - text: 'Please try to ___ it.'\n    ko: '~해 보세요.'\n"
        "    verbs: [send]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(grammar, "RULES_DIR", tmp_path)
    grammar.rules.cache_clear()
    try:
        with pytest.raises(ValueError, match="파일명"):
            grammar.rules()
    finally:
        grammar.rules.cache_clear()


def test_explanations_carry_no_markdown(rule: grammar.Rule) -> None:
    """**화면에 마크다운 렌더러가 없다.** 별표를 쓰면 별표가 그대로 찍힌다.

    `explain_ko` 에 `**동사원형**` 이라고 적어 뒀는데, `ui_web` 어디에도 마크다운을
    그리는 코드가 없고 `PracticeRun` 은 `message_ko` 를 JSX 평문으로 그린다.
    서버가 만드는 다른 `message_ko`(빈칸 연습장) 중 별표를 담은 것은 하나도 없다.
    """
    item = grammar.items_of(rule)[0]
    texts = [rule.explain_ko, rule.title, item.sentence_ko]
    texts += [grammar.grade(item, c.word, rule).message_ko for c in item.choices]
    texts += grammar.grade(item, item.answer, rule).why_ko
    for text in texts:
        assert "**" not in text, f"별표가 그대로 나갑니다: {text}"
        assert "`" not in text


def test_the_rule_explanation_does_not_name_forms_that_are_not_shown(rule: grammar.Rule) -> None:
    """해설이 **화면에 없는 모양**을 부르면 안 된다.

    처음 문구가 "-ing 형도, -s 형도, 과거형도 아니에요" 였다. 그런데 실제 보기에
    명사가 114개, 형용사가 13개 나오는데 해설은 이 둘을 한 번도 안 불렀고,
    거꾸로 3인칭 단수형은 116개 문제에서 화면에 있지도 않은데 매번 언급됐다.
    학습자가 `sender` 를 고르면 방금 고른 것이 왜 안 되는지를 해설이 안 다룬다.

    모양 이름을 하나하나 부르는 대신 규칙만 말하게 했다. 학습자가 고른 것이
    무엇인지는 `grade` 가 따로 짚어 준다.
    """
    named = {name for kind, name in grammar.FORM_KO.items() if kind != "base"}
    for name in named:
        assert name not in rule.explain_ko, f"해설이 '{name}' 를 못 박아 부릅니다"


def test_frames_do_not_share_a_korean_gloss(rule: grammar.Rule) -> None:
    """틀마다 해석이 달라야 한다 — 같으면 영어의 차이가 한국어에서 사라진다.

    실제로 "Please remember to" 와 "Don't forget to" 가 둘 다 "~하는 것을 잊지
    마세요" 였고, agreed·decided·promised 가 셋 다 "~하기로 했어요" 였다.
    학습자는 서로 다른 영어 표현을 같은 한국어로 읽게 된다.
    """
    kos = [f.ko for f in rule.frames]
    dupes = sorted({k for k in kos if kos.count(k) > 1})
    assert dupes == [], f"해석이 겹치는 틀: {dupes}"


def test_every_frame_gloss_marks_the_blank(rule: grammar.Rule) -> None:
    """해석에도 낱말 자리가 비어 있어야 한다. 안 비면 답을 한국어로 알려 준다."""
    for frame in rule.frames:
        assert "~" in frame.ko, f"빈자리 표시가 없는 해석: {frame.ko}"


def test_validation_errors_keep_the_body_shape_the_screen_reads(client: TestClient) -> None:
    """422 본문은 `{"detail": [...]}` 여야 한다.

    500 을 막으려고 처리기를 새로 달면서 `exc.errors()` 를 그대로 실어 보냈더니
    최상위가 배열이 됐다. 화면(`ui_web/src/api/client.ts`)은 `detail` 배열의 첫
    항목에서 `msg` 를 꺼내 오류 문구를 만드는데, 그 경로가 통째로 죽었다 —
    **500 은 막고 오류 문구는 부순 셈**이라 고치기 전보다 알아채기 어렵다.
    """
    body = client.post("/grammar/answer", json={"id": "nope", "chosen": "send"}).json()
    assert isinstance(body, dict), "최상위가 객체여야 합니다"
    assert isinstance(body["detail"], list)
    assert "msg" in body["detail"][0]


def test_http_errors_keep_a_string_detail(client: TestClient) -> None:
    """404·409 의 `detail` 은 글자 하나다. 화면이 그대로 띄운다."""
    body = client.post(
        "/grammar/answer", json={"id": "0" * 12, "chosen": "send"}
    ).json()
    assert isinstance(body["detail"], str)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/grammar/answer", {"id": "a" * 100_000, "chosen": "send"}),
        ("/cloze/answer", {"word": "borrow", "said": "a" * 100_000}),
        ("/cloze/answer", {"word": "a" * 100_000, "said": "x"}),
        ("/chat", {"scenario_id": "a" * 100_000, "message": "hi"}),
        ("/chat", {"scenario_id": "cafe_order", "message": "a" * 100_000}),
    ],
)
def test_errors_do_not_echo_the_whole_request_back(
    client: TestClient, path: str, body: dict
) -> None:
    """**거절한 값을 통째로 되돌려 보내지 않는다.**

    `max_length` 를 걸어도 5MB 가 그대로 돌아왔다 — pydantic 이 거절한 값을
    `errors()[0]["input"]` 에 담고 처리기가 그걸 실어 보내기 때문이다. 필드마다
    제한을 붙이는 것으로는 못 막고, 되싣는 자리에서 잘라야 한다.

    시범으로 바깥에 열어 두는 앱이라(README 의 '외부에 잠깐 열기') 무제한 응답을
    남겨 둘 자리가 아니다.
    """
    res = client.post(path, json=body)
    assert res.status_code < 500
    assert len(res.content) < 4096, f"응답이 {len(res.content):,}B 입니다"


def test_the_form_table_does_not_collect_dead_rows() -> None:
    """형태 표에 **아무 틀도 안 부르는 줄**이 쌓이면 안 된다.

    한쪽 방향만 막아 두면 반대쪽이 조용히 는다. `rules()` 는 "틀이 부르는데 표에
    없는 동사" 를 시작할 때 잡지만 그 반대는 아무 데도 안 걸려서, 틀에서 동사를
    빼는 동안 표에 여덟 줄이 남았다. 남은 줄은 검수한 것처럼 보이는데 실제로는
    화면에 나가지도 않고 다시 읽히지도 않는다.

    개수를 세어 두는 이유: 규칙을 하나 더 만들 때 쓸 재료일 수 있어 시작을
    막지는 않되, 늘어나면 알아채야 한다.
    """
    dead = grammar.unused_forms()
    assert len(dead) <= 8, f"안 쓰이는 형태 표 줄이 늘었습니다: {dead}"
