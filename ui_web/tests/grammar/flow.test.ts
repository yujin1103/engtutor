/** 문법 문제 화면에서 **눈으로는 안 보이는** 것만 못 박는다.
 *
 * 여기 걸린 것 둘은 둘 다 "어쩌다 한 번" 이 아니라 "특정 경우에만 늘" 인 종류다.
 *
 *  1. `markOf` — 맞혔을 때만 어긋난다. 오답을 눌러 보는 동안에는 멀쩡해 보여서,
 *     화면을 만들고 스무 번 틀려 보는 것으로는 발견되지 않는다.
 *  2. `wrapsAround` — 문제 수가 한 장 크기의 배수일 때만 어긋난다. 지금은
 *     151개라 안 걸리고, 규칙을 하나 더 넣어 160개가 되는 날 걸린다.
 *
 * "고치기 전에 재라" 가 이 프로젝트에서 제일 비싸게 배운 것이라, 눈으로 못 보는
 * 것은 시험으로 못박는다(연습장의 `tests/practice/flow.test.ts` 와 같은 자리다).
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BLANK,
  MARKERS,
  PAGE,
  markLabelKo,
  markOf,
  markerOf,
  nextOffset,
  splitBlank,
  wrapsAround,
} from "../../src/screens/grammar/flow.ts";
import type { GrammarAnswerOut } from "../../src/api/types.ts";

/** 서버가 실제로 돌려준 응답이다(`POST /grammar/answer`, id `to_infinitive#0000`). */
const WRONG: GrammarAnswerOut = {
  ok: false,
  answer: "send",
  chosen: "sending",
  message_ko: "'sending' — -ing 형(동명사)예요. to 뒤에는 늘 **동사원형**이 와요.",
  why_ko: [
    "sending — -ing 형(동명사)",
    "sender — 명사",
    "send — 동사원형  ← 정답",
    "sent — 과거형",
  ],
};

/** 같은 문제를 맞힌 경우. **`answer` 와 `chosen` 이 같은 낱말이다.** */
const RIGHT: GrammarAnswerOut = {
  ...WRONG,
  ok: true,
  chosen: "send",
  message_ko: "맞았어요. to 뒤에는 늘 **동사원형**이 와요.",
};

// ─────────────────────────────────────────────── 보기에 붙는 표시

test("맞힌 보기를 오답 색으로 칠하지 않는다 — 정답 검사가 먼저다", () => {
  // 고른 것을 먼저 보면 이 줄이 "wrong" 이 되어, 맞힌 사람에게 빨간 줄과
  // "맞았어요" 가 함께 뜬다. 순서 한 줄 차이인데 맞혔을 때만 난다.
  assert.equal(markOf("send", RIGHT), "answer");
});

test("틀렸으면 정답 줄과 내가 고른 줄이 서로 다른 표시를 받는다", () => {
  assert.equal(markOf("send", WRONG), "answer");
  assert.equal(markOf("sending", WRONG), "wrong");
});

test("고르지도 않았고 답도 아닌 보기는 아무 표시가 없다", () => {
  assert.equal(markOf("sender", WRONG), "plain");
  assert.equal(markOf("sent", WRONG), "plain");
});

test("판정 전에는 넷이 다 똑같이 보인다 — 정답이 미리 드러날 자리가 없다", () => {
  for (const word of ["sending", "sender", "send", "sent"]) {
    assert.equal(markOf(word, null), "plain");
  }
});

test("서버가 다듬어 돌려준 값과 짝이 맞는다", () => {
  // 채점한 값은 서버 것을 쓴다. 화면이 누른 글자를 들고 있다가 쓰면
  // 서버가 공백을 떼는 날 화면과 채점이 다른 말을 한다.
  assert.equal(markOf(" send ", WRONG), "answer");
  assert.equal(markOf("sending", { ...WRONG, chosen: " sending " }), "wrong");
});

test("표시마다 한국어 꼬리표가 있다 — 색만으로 말하지 않는다", () => {
  assert.equal(markLabelKo("answer"), "정답");
  assert.equal(markLabelKo("wrong"), "내가 고른 것");
  assert.equal(markLabelKo("plain"), null);
});

// ─────────────────────────────────────────────── 보기 번호

test("보기 번호는 토익 Part 5 대로 넷이다", () => {
  assert.deepEqual([...MARKERS], ["①", "②", "③", "④"]);
  assert.equal(markerOf(0), "①");
  assert.equal(markerOf(3), "④");
});

test("보기가 다섯이 되어도 ① 이 두 번 나오지 않는다", () => {
  assert.equal(markerOf(4), "5.");
});

// ─────────────────────────────────────────────── 끊기지 않고 이어 풀기

test("마지막 장이면 처음으로 되돌아간다 — 연습에 끝나는 자리가 없다", () => {
  // 151개를 20개씩 받으면 마지막 장이 11개다.
  assert.equal(nextOffset(140, 11), 0);
});

test("장이 꽉 차서 왔으면 다음 장으로 간다", () => {
  assert.equal(nextOffset(0, PAGE), PAGE);
  assert.equal(nextOffset(140, 20), 160);
});

test("딱 나누어떨어져 빈 장을 받으면 한 바퀴 돈 것이다", () => {
  // 160개를 20개씩 받으면 마지막 장(offset 140)이 꽉 차서 오고, 그래서 offset
  // 160 을 한 번 더 받는데 그게 빈 배열이다. 이때 "문제가 없어요" 를 띄우면
  // 여덟 장을 푼 사람 앞에서 연습이 끝나 버린다.
  assert.equal(wrapsAround(160, 0), true);
});

test("처음부터 비어 있으면 정말 문제가 없는 것이다 — 모르는 규칙 이름도 여기로 온다", () => {
  assert.equal(wrapsAround(0, 0), false);
});

test("문제가 온 장은 되돌리지 않는다", () => {
  assert.equal(wrapsAround(20, 20), false);
  assert.equal(wrapsAround(0, 11), false);
});

// ─────────────────────────────────────────────── 빈칸

test("빈칸 표시가 연습장·서버와 같은 네 글자다", () => {
  // 어긋나면 문장에 밑줄 네 개가 글자 그대로 찍힌다.
  assert.equal(BLANK, "____");
});

test("문법 문제의 문장도 빈칸을 기준으로 갈린다", () => {
  assert.deepEqual(splitBlank("Please remember to ____ the invoice."), {
    before: "Please remember to ",
    after: " the invoice.",
  });
});
