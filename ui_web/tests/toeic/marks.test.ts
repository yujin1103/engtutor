/** 토익 화면이 폰에 남기는 표시. 눈으로 못 보는 것만 못 박는다.
 *
 * 이 파일이 지키는 것은 셋이다.
 *
 * 1. **외움 표시가 목록에서 낱말을 빼지 않는다.** 여기 있는 것은 표제어 목록일
 *    뿐이고 걸러 내는 함수가 없다는 사실 자체가 계약이다. 한 번 맞혔다고 아는
 *    낱말이 되지는 않는다.
 * 2. **저장된 값이 손상돼도 화면이 죽지 않는다.** localStorage 는 사용자가 직접
 *    고칠 수 있고 다른 탭이 덮어쓸 수도 있다. 숫자 자리에 문자열이 들어 있다고
 *    앱이 안 뜨면 안 된다.
 * 3. **단어장 상한이 새로 담은 쪽을 남긴다.** 상한에 걸렸을 때 담기를 거부하면
 *    지금 외우려는 낱말을 못 담는다.
 */
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { EMPTY, MAX_SAVED, isMarked, loadMarks, saveMarks, toggle } from "../../src/screens/toeic/marks.ts";
import type { WordCardOut } from "../../src/api/types.ts";

/** node:test 에는 브라우저가 없다. 필요한 칸만 흉내 낸다. */
function fakeStorage(seed: Record<string, string> = {}) {
  const box = { ...seed };
  return {
    getItem: (key: string) => (key in box ? box[key] : null),
    setItem: (key: string, value: string) => {
      box[key] = value;
    },
    box,
  };
}

function install(storage: unknown): void {
  (globalThis as { window?: unknown }).window = { localStorage: storage };
}

beforeEach(() => install(fakeStorage()));

const CARD: WordCardOut = {
  word: "invoice",
  rank: 19,
  meaning_ko: "청구서",
  example: "Please send the invoice to our company.",
  example_ko: "청구서를 저희 회사로 보내 주세요.",
  pattern: null,
  reviewed: false,
};

// ─────────────────────────────────────────────── 넣고 빼기

test("없으면 넣고 있으면 뺀다", () => {
  assert.deepEqual(toggle([], "invoice"), ["invoice"]);
  assert.deepEqual(toggle(["invoice"], "invoice"), []);
});

test("표제어는 소문자로 맞춘다 — 서버가 주는 표기와 같아야 짝이 맞는다", () => {
  assert.deepEqual(toggle([], " Invoice "), ["invoice"]);
  assert.deepEqual(toggle(["invoice"], "INVOICE"), []);
});

test("빈 값으로는 아무것도 바뀌지 않는다", () => {
  const list = ["invoice"];
  assert.equal(toggle(list, "   "), list);
});

test("상한을 넘으면 가장 오래된 것을 밀어낸다 — 지금 담은 것이 남아야 한다", () => {
  const full = Array.from({ length: 3 }, (_, i) => `w${i}`);
  assert.deepEqual(toggle(full, "new", 3), ["w1", "w2", "new"]);
});

// ─────────────────────────────────────────────── 외움 표시

test("외움 표시는 표시일 뿐이다 — 목록을 거르는 함수가 없다", () => {
  const known = toggle([], CARD.word);
  assert.equal(isMarked(known, CARD), true);
  // 표시된 뒤에도 카드 자체는 그대로다. 화면은 이걸 옅게 그릴 뿐 빼지 않는다.
  assert.equal(CARD.word, "invoice");
});

// ─────────────────────────────────────────────── 읽고 쓰기

test("적어 둔 것을 그대로 읽어 온다", () => {
  const marks = { offset: 60, known: ["invoice"], saved: ["deadline"] };
  saveMarks(marks);
  assert.deepEqual(loadMarks(), marks);
});

test("저장된 값이 없으면 빈 표시", () => {
  assert.deepEqual(loadMarks(), EMPTY);
});

test("손상된 값이 들어 있어도 죽지 않는다", () => {
  install(fakeStorage({ "engtutor.toeic.v1": "{{{" }));
  assert.deepEqual(loadMarks(), EMPTY);
});

test("칸마다 타입이 어긋나도 쓸 수 있는 것만 건진다", () => {
  install(
    fakeStorage({
      "engtutor.toeic.v1": JSON.stringify({
        offset: "예순",
        known: ["invoice", 7, null, "INVOICE"],
        saved: "deadline",
      }),
    }),
  );
  const marks = loadMarks();
  assert.equal(marks.offset, 0); // 숫자가 아니면 처음부터
  assert.deepEqual(marks.known, ["invoice"]); // 중복과 문자열 아닌 것을 걷어낸다
  assert.deepEqual(marks.saved, []); // 배열이 아니면 없는 것으로 본다
});

test("음수 진도는 처음으로 되돌린다 — 서버에 음수 offset 을 보내지 않는다", () => {
  install(fakeStorage({ "engtutor.toeic.v1": JSON.stringify({ offset: -30 }) }));
  assert.equal(loadMarks().offset, 0);
});

test("저장된 단어장이 상한보다 길면 앞에서 자른다", () => {
  const many = Array.from({ length: MAX_SAVED + 40 }, (_, i) => `w${i}`);
  install(fakeStorage({ "engtutor.toeic.v1": JSON.stringify({ saved: many }) }));
  assert.equal(loadMarks().saved.length, MAX_SAVED);
});

test("localStorage 가 막혀 있어도 앱이 계속 간다", () => {
  install({
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("denied");
    },
  });
  assert.deepEqual(loadMarks(), EMPTY);
  saveMarks({ offset: 1, known: [], saved: [] }); // 던지지 않는다
});
