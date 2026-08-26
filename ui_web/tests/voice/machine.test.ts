/** 음성 입력의 상태 전이 시험.
 *
 * 브라우저 없이 돈다 — node 22 가 TypeScript 를 그대로 실행한다.
 *
 *   docker compose exec -T web sh -c "cd /workspace/ui_web && npm test"
 *
 * `src/` 밖에 두는 이유는 `tsconfig.app.json` 의 `include: ["src"]` 때문이다.
 * 시험을 그 안에 넣으면 `node:test` 타입이 없어 `npm run build` 가 깨진다.
 *
 * 여기서 지키려는 것은 **두 번 눌러도 한 번만 간다** 와 **늦게 온 응답이 학습자가
 * 고쳐 놓은 문장을 덮지 않는다** 둘이다. 실제로 겪은 버그가 그 둘이었다.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_RECORD_MS,
  confirmedInput,
  formatElapsed,
  initialVoiceState,
  shouldAutoStop,
  voiceReducer,
  type VoiceEvent,
  type VoiceState,
} from "../../src/voice/machine.ts";

const WORDS = [
  { word: " I", probability: 0.87 },
  { word: " want", probability: 0.92 },
  { word: " ice", probability: 0.35 },
  { word: " americano.", probability: 0.98 },
];

/** 사건을 죽 흘려보낸다. 실제 화면이 하는 일이 이것뿐이다. */
function run(events: VoiceEvent[], from: VoiceState = initialVoiceState): VoiceState {
  return events.reduce(voiceReducer, from);
}

/** 녹음 → 전사까지 마친, 확인 칸이 떠 있는 상태. */
function reviewing(text = "I want ice americano"): VoiceState {
  return run([
    { type: "press" },
    { type: "started", at: 1000 },
    { type: "stop" },
    { type: "heard", text, words: WORDS },
  ]);
}

test("마이크를 누르면 권한을 묻는 단계로 간다", () => {
  assert.equal(voiceReducer(initialVoiceState, { type: "press" }).phase, "starting");
});

test("녹음·전사·확인 중에는 마이크가 다시 안 눌린다", () => {
  const recording = run([{ type: "press" }, { type: "started", at: 0 }]);
  assert.equal(voiceReducer(recording, { type: "press" }).phase, "recording");

  const transcribing = voiceReducer(recording, { type: "stop" });
  assert.equal(voiceReducer(transcribing, { type: "press" }).phase, "transcribing");

  assert.equal(voiceReducer(reviewing(), { type: "press" }).phase, "review");
});

test("안 들렸거나 실패한 뒤에는 다시 누를 수 있다", () => {
  const empty = run([{ type: "press" }, { type: "started", at: 0 }, { type: "stop" }, { type: "heard", text: "", words: [] }]);
  assert.equal(empty.phase, "empty");
  assert.equal(voiceReducer(empty, { type: "press" }).phase, "starting");

  const failed: VoiceState = { phase: "failed", detail: "마이크를 열지 못했어요." };
  assert.equal(voiceReducer(failed, { type: "press" }).phase, "starting");
});

test("막힌 브라우저에서는 눌러도 아무 일도 안 일어난다", () => {
  const blocked: VoiceState = { phase: "blocked", detail: "https 가 아니에요." };
  assert.deepEqual(voiceReducer(blocked, { type: "press" }), blocked);
  // 취소로도 풀리지 않는다 — 환경이 바뀐 게 아니기 때문이다.
  assert.deepEqual(voiceReducer(blocked, { type: "cancel" }), blocked);
});

test("취소한 뒤 늦게 도착한 허락은 버린다 (모르는 새 마이크가 켜지지 않게)", () => {
  const state = run([{ type: "press" }, { type: "cancel" }, { type: "started", at: 500 }]);
  assert.equal(state.phase, "idle");
});

test("경과 시간은 녹음 중에만 흐른다", () => {
  const recording = run([{ type: "press" }, { type: "started", at: 1000 }, { type: "tick", at: 4500 }]);
  assert.equal(recording.phase === "recording" && recording.elapsedMs, 3500);

  // 시계가 뒤로 갈 수도 있다(기기 시각 보정). 음수로 그리지 않는다.
  const back = voiceReducer(recording, { type: "tick", at: 900 });
  assert.equal(back.phase === "recording" && back.elapsedMs, 0);

  const idle = voiceReducer(initialVoiceState, { type: "tick", at: 9999 });
  assert.equal(idle.phase, "idle");
});

test("멈추기를 두 번 눌러도 전사는 한 번만 시작된다", () => {
  const recording = run([{ type: "press" }, { type: "started", at: 0 }]);
  const first = voiceReducer(recording, { type: "stop" });
  assert.equal(first.phase, "transcribing");
  // 두 번째 stop 은 아무 상태도 만들지 않는다. **같은 녹음을 두 번 보내지 않는 자리다.**
  assert.equal(voiceReducer(first, { type: "stop" }), first);
});

test("받아쓴 글이 있으면 확인 칸에 그대로 들어간다", () => {
  const state = reviewing("  I want ice americano  ");
  assert.equal(state.phase, "review");
  if (state.phase !== "review") return;
  // 들은 것과 초안이 같은 값에서 출발한다. 학습자가 고치면 둘이 갈라진다.
  assert.equal(state.heard, "I want ice americano");
  assert.equal(state.draft, "I want ice americano");
  assert.deepEqual(state.words, WORDS);
});

test("빈 전사는 오류가 아니라 '안 들렸어요' 다", () => {
  const state = run([{ type: "press" }, { type: "started", at: 0 }, { type: "stop" }, { type: "heard", text: "   ", words: [] }]);
  assert.equal(state.phase, "empty");
});

test("늦게 온 전사 결과가 확인 칸을 덮지 않는다", () => {
  const state = voiceReducer(reviewing("I want ice americano"), {
    type: "heard",
    text: "something else",
    words: [],
  });
  assert.equal(state.phase === "review" && state.draft, "I want ice americano");
});

test("실패가 와도 학습자가 고쳐 놓은 문장은 지우지 않는다", () => {
  const edited = voiceReducer(reviewing(), { type: "edit", draft: "I want ice americano please" });
  const state = voiceReducer(edited, { type: "failed", detail: "녹음을 보내지 못했어요." });
  assert.equal(state.phase === "review" && state.draft, "I want ice americano please");
});

test("확인 칸 밖에서는 고칠 것이 없다", () => {
  assert.equal(voiceReducer(initialVoiceState, { type: "edit", draft: "hi" }).phase, "idle");
});

test("보내기는 학습자가 확정한 문장과 STT 가 들은 것을 함께 넘긴다", () => {
  // STT 가 매끄럽게 고쳐 적은 것을 학습자가 자기 말대로 되돌린 경우 —
  // 이 앱이 가장 알고 싶은 기록이다.
  const state = voiceReducer(reviewing("I want an iced americano"), {
    type: "edit",
    draft: "  I want ice americano  ",
  });
  assert.deepEqual(confirmedInput(state), {
    message: "I want ice americano",
    transcript: "I want an iced americano",
    transcript_words: WORDS,
  });
});

test("보내기를 두 번 눌러도 두 번 가지 않는다", () => {
  const state = reviewing();
  assert.ok(confirmedInput(state));
  const sent = voiceReducer(state, { type: "confirm" });
  assert.equal(sent.phase, "idle");
  // 넘길 것이 남아 있지 않다.
  assert.equal(confirmedInput(sent), null);
  assert.equal(voiceReducer(sent, { type: "confirm" }).phase, "idle");
});

test("칸을 비우면 보낼 수 없다 (서버가 1자 이상을 요구한다)", () => {
  const blank = voiceReducer(reviewing(), { type: "edit", draft: "   " });
  assert.equal(confirmedInput(blank), null);
  // 상태도 그대로 남아 학습자가 다시 채워 넣을 수 있다.
  assert.equal(voiceReducer(blank, { type: "confirm" }).phase, "review");
});

test("다시 말하기는 어느 단계에서든 처음으로 되돌린다", () => {
  for (const state of [reviewing(), run([{ type: "press" }, { type: "started", at: 0 }])]) {
    assert.equal(voiceReducer(state, { type: "cancel" }).phase, "idle");
  }
});

test("60초가 넘으면 스스로 멈춘다", () => {
  const recording = run([{ type: "press" }, { type: "started", at: 0 }]);
  assert.equal(shouldAutoStop(recording), false);
  assert.equal(shouldAutoStop(voiceReducer(recording, { type: "tick", at: MAX_RECORD_MS - 1 })), false);
  assert.equal(shouldAutoStop(voiceReducer(recording, { type: "tick", at: MAX_RECORD_MS })), true);
  // 녹음 중이 아닌 단계에서는 멈출 것이 없다.
  assert.equal(shouldAutoStop({ phase: "transcribing" }), false);
});

test("경과 시간은 분:초로 그린다", () => {
  assert.equal(formatElapsed(0), "0:00");
  assert.equal(formatElapsed(7400), "0:07");
  assert.equal(formatElapsed(61_000), "1:01");
  assert.equal(formatElapsed(-5), "0:00");
});

test("한 바퀴 전부 — 중간에 두 번씩 눌러도 결과가 같다", () => {
  const state = run([
    { type: "press" },
    { type: "press" }, // 조바심에 두 번
    { type: "started", at: 0 },
    { type: "tick", at: 3000 },
    { type: "stop" },
    { type: "stop" }, // 또 두 번
    { type: "heard", text: "I want ice americano", words: WORDS },
    { type: "edit", draft: "I want ice americano" },
  ]);
  assert.deepEqual(confirmedInput(state), {
    message: "I want ice americano",
    transcript: "I want ice americano",
    transcript_words: WORDS,
  });
  assert.equal(voiceReducer(state, { type: "confirm" }).phase, "idle");
});
