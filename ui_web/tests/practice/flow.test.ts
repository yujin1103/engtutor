/** 연습장이 **아는 것보다 더 주장하지 않는지**, 그리고 끊기지 않는지를 본다.
 *
 * 여기 걸린 것들은 화면을 눈으로 훑어서는 안 보인다. `opensExplanation` 이
 * 특히 그렇다 — 오타를 한 번 내 봐야 "다시 해 볼까요?" 바로 아래에 정답이
 * 적혀 있는 걸 발견하게 된다. 이 프로젝트에서 제일 비싸게 배운 것이
 * "고치기 전에 재라" 라, 눈으로 못 보는 것은 시험으로 못박는다.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BLANK,
  PAGE,
  UNCHECKED_NOTE_KO,
  meaningOf,
  nextOffset,
  opensExplanation,
  shuffled,
  splitBlank,
  splitNotes,
  splitWords,
  toneOf,
} from "../../src/screens/practice/flow.ts";
import type { ClozeExplainOut, Verdict } from "../../src/api/types.ts";

// ─────────────────────────────────────────────── 빈칸 가르기

test("빈칸을 기준으로 앞뒤를 가른다", () => {
  assert.deepEqual(splitBlank("Can I have a ____ with my coffee?"), {
    before: "Can I have a ",
    after: " with my coffee?",
  });
});

test("빈칸이 문장 맨 앞에 있어도 앞쪽은 빈 문자열이지 null 이 아니다", () => {
  assert.deepEqual(splitBlank("____ is my brother."), {
    before: "",
    after: " is my brother.",
  });
});

test("서버가 두 번째 등장을 남긴 문장도 빈칸은 하나로 읽는다", () => {
  // `as ... as` 라 두 번째 as 가 남아 있는 예문이 실제로 하나 있다.
  const parts = splitBlank("He is ____ tall as his father.");
  assert.equal(parts?.after, " tall as his father.");
});

test("빈칸이 없으면 null — 문장을 통째로 그리면 된다", () => {
  assert.equal(splitBlank("No blank here."), null);
});

test("빈칸 표시는 서버(app/tutor/slot.py)와 같은 네 글자다", () => {
  // 이 값이 어긋나면 화면이 밑줄 네 개를 글자 그대로 그린다.
  assert.equal(BLANK, "____");
});

// ─────────────────────────────────────────────── 언제 답을 펴는가

test("아직 겨눠 보지도 못한 답에는 정답을 펴지 않는다", () => {
  // 서버는 오타에도 설명 카드를 함께 보낸다(왕복 두 번보다 낫다는 결정).
  // 그대로 펼치면 "다시 말해 볼까요?" 아래에 답을 적어 두는 꼴이 된다.
  assert.equal(opensExplanation("not_a_word"), false);
  assert.equal(opensExplanation("empty"), false);
});

test("겨눈 답에는 설명을 편다 — 틀렸을 때가 오히려 배우는 자리다", () => {
  for (const verdict of [
    "correct",
    "wrong_form",
    "right_pos",
    "wrong_pos",
    "wrong_word",
  ] as Verdict[]) {
    assert.equal(opensExplanation(verdict), true, verdict);
  }
});

// ─────────────────────────────────────────────── 색

test("형태만 틀렸거나 품사가 맞은 답은 실패색으로 칠하지 않는다", () => {
  // 채점이 아니라 가르치는 자리다. 빨강으로 칠하면 배운 것을 못 알아본다.
  assert.equal(toneOf("wrong_form"), "close");
  assert.equal(toneOf("right_pos"), "close");
});

test("판정 일곱 가지가 하나도 빠짐없이 색을 얻는다", () => {
  const all: Verdict[] = [
    "correct",
    "wrong_form",
    "right_pos",
    "wrong_pos",
    "wrong_word",
    "not_a_word",
    "empty",
  ];
  assert.deepEqual(all.map(toneOf), [
    "right",
    "close",
    "close",
    "miss",
    "miss",
    "again",
    "again",
  ]);
});

// ─────────────────────────────────────────────── 끊기지 않기

test("한 장을 다 풀면 다음 장으로 넘어간다", () => {
  assert.equal(nextOffset(0, PAGE), PAGE);
  assert.equal(nextOffset(PAGE, PAGE), PAGE * 2);
});

test("마지막 장이었으면 처음으로 되돌아간다 — 연습장은 끝나는 자리가 없다", () => {
  // 낱말이 셋뿐인 팩(`잡담`)이 실제로 있다. 거기서 막히면 세 문제짜리 앱이 된다.
  assert.equal(nextOffset(0, 3, 30), 0);
  assert.equal(nextOffset(60, 12, 30), 0);
});

// ─────────────────────────────────────────────── 섞기

test("섞어도 하나도 잃지 않고 겹치지도 않는다", () => {
  const items = Array.from({ length: 30 }, (_, i) => i);
  // 늘 마지막 자리를 고르는 난수. 뒤집힌 배열이 나온다.
  const out = shuffled(items, () => 0.999999);
  assert.equal(out.length, items.length);
  assert.deepEqual(out.slice().sort((a, b) => a - b), items);
});

test("원래 배열을 건드리지 않는다 — 받아 둔 장을 제자리에서 흔들면 안 된다", () => {
  const items = [1, 2, 3];
  shuffled(items, () => 0);
  assert.deepEqual(items, [1, 2, 3]);
});

// ─────────────────────────────────────────────── 확인된 것과 아직 아닌 것

/** 설명 카드 하나. 안 쓰는 칸은 서버가 주는 기본값 그대로 둔다. */
function card(over: Partial<ClozeExplainOut> = {}): ClozeExplainOut {
  return {
    word: "bagel",
    answer: "bagel",
    meaning_ko: "백일(백面包), 빵 모양이 둥글고 가운데가 빈 빵",
    example: "I'll have a bagel with cream cheese.",
    pos: ["n"],
    pos_ko: ["명사"],
    reviewed: false,
    confused_with: [],
    ...over,
  };
}

test("승인 전 후보의 뜻은 `meaning_ko` 칸으로 나올 수가 없다", () => {
  // 여기가 이번 결함의 자리다. 화면이 `reviewed` 를 안 읽어서 `straw` 의 뜻이
  // "줄기"(빨대인데) 로 승인된 `쓰임` 과 똑같은 상자에 떴다. 이제는 확인 안 된
  // 글자가 다른 칸 이름으로만 오므로 승인된 줄에 넘길 수조차 없다.
  const { checked, unchecked } = splitWords([
    { word: "straw", meaning_ko: "스트로 소프트한 줄기", pos_ko: ["명사"], reviewed: false },
    { word: "go", meaning_ko: "가다", pos_ko: ["동사"], reviewed: true },
  ]);
  assert.deepEqual(checked, [{ word: "go", pos_ko: ["동사"], meaning_ko: "가다" }]);
  assert.deepEqual(unchecked, [
    { word: "straw", pos_ko: ["명사"], unchecked_ko: "스트로 소프트한 줄기" },
  ]);
  // 확인된 줄에는 확인 전 글자가 한 자도 없다.
  assert.equal(JSON.stringify(checked).includes("줄기"), false);
});

test("무리 안의 차례는 서버가 준 그대로다 — 여기서 다시 흔들지 않는다", () => {
  // 서버가 정답 낱말로 씨앗을 고정해 섞어 둔 것이라, 같은 문제는 늘 같은 목록이어야 한다.
  const words = ["lid", "espresso", "flavor"].map((word) => ({
    word,
    meaning_ko: word,
    pos_ko: ["명사"],
    reviewed: false,
  }));
  assert.deepEqual(
    splitWords(words).unchecked.map((alt) => alt.word),
    ["lid", "espresso", "flavor"],
  );
});

test("표제어의 대표 뜻도 같은 기준을 받는다", () => {
  // 카드에서 제일 크게 읽히는 한 줄인데 이것도 배치 LLM 이 쓴 값이다.
  const before = meaningOf(card());
  assert.equal(before.checked, false);
  assert.equal(before.checked === false && before.unchecked_ko.startsWith("백일"), true);

  const after = meaningOf(card({ reviewed: true, meaning_ko: "베이글" }));
  assert.equal(after.checked === true && after.meaning_ko, "베이글");
});

test("승인 전 카드는 쓰임 설명이 승인된 자리로 못 간다", () => {
  const { checked, unchecked } = splitNotes(
    card({
      unverified: {
        usage_note: "한국어 '백일'은 축하를 의미하기 때문에 혼동하지 마세요.",
        confused_with: ["bun", "doughnut"],
        note_ko: "아직 사람이 확인하지 않은 설명이에요. 참고만 하세요.",
      },
    }),
  );
  assert.equal(checked, null);
  assert.deepEqual(unchecked?.unchecked_confused, ["bun", "doughnut"]);
  assert.equal(unchecked?.note_ko, "아직 사람이 확인하지 않은 설명이에요. 참고만 하세요.");
});

test("서버가 승인 칸에 승인 전 설명을 실어 보내도 승인된 자리에는 안 그린다", () => {
  // 서버는 지금 이렇게 보내지 않는다. 확인된 환각 13건이 전부 이 두 칸에 있었으니
  // 서버 한 겹이 뚫렸을 때 화면이 그대로 검수된 설명으로 그리는 일은 없어야 한다.
  const { checked, unchecked } = splitNotes(
    card({ usage_note: "지어낸 설명", confused_with: ["lend"] }),
  );
  assert.equal(checked, null);
  assert.equal(unchecked?.unchecked_note, "지어낸 설명");
  assert.deepEqual(unchecked?.unchecked_confused, ["lend"]);
  // 상자에 붙일 문장이 없으면 우리 문구로 채운다 — 상자만 덩그러니 뜨면 안 된다.
  assert.equal(unchecked?.note_ko, UNCHECKED_NOTE_KO);
});

test("승인된 카드의 설명은 승인된 자리로 간다", () => {
  const { checked, unchecked } = splitNotes(
    card({
      word: "go",
      reviewed: true,
      meaning_ko: "가다",
      usage_note: "가다와 오다를 혼동하지 마세요.",
      confused_with: ["come"],
    }),
  );
  assert.equal(checked?.usage_note, "가다와 오다를 혼동하지 마세요.");
  assert.deepEqual(checked?.confused_with, ["come"]);
  assert.equal(unchecked, null);
});

test("설명이 아예 없으면 상자도 안 만든다", () => {
  const { checked, unchecked } = splitNotes(card({ usage_note: "   " }));
  assert.equal(checked, null);
  assert.equal(unchecked, null);
});
