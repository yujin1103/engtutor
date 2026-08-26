/** 형식 고르기와 "왜 마이크가 안 열리는가" 안내 시험.
 *
 * 브라우저 없이 돈다. `isTypeSupported` 를 인자로 받게 만들어 둔 덕분에 아이폰
 * 사파리(= mp4 만 되는 브라우저)를 여기서 그대로 흉내 낼 수 있다. 실제 아이폰으로
 * 확인하기 전에 최소한 **형식을 잘못 골라 0바이트 녹음을 만드는 일**은 여기서 막는다.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  RECORDER_START_FAILED,
  findMicBlocker,
  micErrorDetail,
  type MicEnv,
} from "../../src/voice/blockers.ts";
import {
  extensionForMime,
  filenameFor,
  pickRecordingFormat,
} from "../../src/voice/format.ts";

/** 정해진 목록만 지원하는 브라우저를 흉내 낸다. */
function supports(...types: string[]) {
  return (type: string) => types.includes(type);
}

test("크롬·안드로이드: webm/opus 를 고른다", () => {
  const format = pickRecordingFormat(supports("audio/webm;codecs=opus", "audio/webm"));
  assert.deepEqual(format, { mimeType: "audio/webm;codecs=opus", extension: "webm" });
});

test("iOS 사파리: mp4 밖에 없으면 mp4 를 고르고 확장자는 m4a 다", () => {
  // 여기가 이 파일의 존재 이유다. webm 을 고정으로 박아 두면 아이폰에서 녹음이
  // 통째로 안 되거나 0바이트가 된다.
  const format = pickRecordingFormat(supports("audio/mp4"));
  assert.deepEqual(format, { mimeType: "audio/mp4", extension: "m4a" });
});

test("아무 형식도 지원하지 않으면 null — 화면이 이유를 적을 수 있게", () => {
  assert.equal(pickRecordingFormat(supports()), null);
});

test("isTypeSupported 가 아예 없으면 브라우저에 맡긴다", () => {
  // 물을 방법이 없을 뿐 녹음은 될 수 있다. 여기서 null 을 돌리면 될 일을 미리 막는다.
  assert.deepEqual(pickRecordingFormat(undefined), { mimeType: "", extension: "webm" });
});

test("isTypeSupported 가 던지는 브라우저에서도 다음 후보로 넘어간다", () => {
  const format = pickRecordingFormat((type: string) => {
    if (type.startsWith("audio/webm")) throw new Error("옛 사파리는 여기서 던진다");
    return type === "audio/mp4";
  });
  assert.deepEqual(format, { mimeType: "audio/mp4", extension: "m4a" });
});

test("MIME 에서 확장자를 되짚는다 (codecs 파라미터가 붙어 와도)", () => {
  assert.equal(extensionForMime("audio/webm;codecs=opus"), "webm");
  assert.equal(extensionForMime("audio/mp4"), "m4a");
  assert.equal(extensionForMime("AUDIO/WAV"), "wav");
  assert.equal(extensionForMime("audio/ogg; codecs=opus"), "ogg");
  assert.equal(extensionForMime("application/octet-stream"), null);
});

test("파일 이름은 고른 형식이 아니라 실제로 만들어진 녹음에서 읽는다", () => {
  const chosen = { mimeType: "audio/webm;codecs=opus", extension: "webm" };
  // 브라우저가 우리가 준 형식을 무시하고 제 형식으로 만드는 일이 있다.
  assert.equal(filenameFor(new Blob([], { type: "audio/mp4" }), chosen), "speech.m4a");
  // Blob 이 형식을 안 알려줄 때만 고른 값을 쓴다.
  assert.equal(filenameFor(new Blob([]), chosen), "speech.webm");
  assert.equal(filenameFor(new Blob([]), null), "speech.webm");
});

// ─────────────────────────────────────────────── 왜 안 되는지 적기

const OK: MicEnv = {
  secure: true,
  hasGetUserMedia: true,
  hasRecorder: true,
  format: { mimeType: "audio/webm", extension: "webm" },
};

test("다 갖춰졌으면 막을 이유가 없다", () => {
  assert.equal(findMicBlocker(OK), null);
});

test("http 주소가 가장 먼저다 — 그게 진짜 이유이기 때문이다", () => {
  // https 가 아니면 mediaDevices 자체가 없다. 순서를 바꾸면 "브라우저가 낡았다" 는
  // 엉뚱한 안내가 나가고, 학습자는 브라우저를 새로 깔다 시간을 버린다.
  const blocker = findMicBlocker({ ...OK, secure: false, hasGetUserMedia: false });
  assert.equal(blocker?.kind, "insecure");
  assert.match(blocker.detail, /https/);
});

test("경우마다 다른 이유가 나온다", () => {
  assert.equal(findMicBlocker({ ...OK, hasGetUserMedia: false })?.kind, "no-media");
  assert.equal(findMicBlocker({ ...OK, hasRecorder: false })?.kind, "no-recorder");
  assert.equal(findMicBlocker({ ...OK, format: null })?.kind, "no-format");
});

test("막혔을 때의 안내에는 반드시 다음에 할 일이 들어 있다", () => {
  for (const env of [
    { ...OK, secure: false },
    { ...OK, hasGetUserMedia: false },
    { ...OK, hasRecorder: false },
    { ...OK, format: null },
  ]) {
    const blocker = findMicBlocker(env);
    assert.ok(blocker, "막혔는데 이유가 없다");
    // 왕초보가 읽고 뭘 할지 알아야 한다. 마이크가 막혀도 타자로는 계속할 수 있다는
    // 사실을 매번 알려 준다 — 이걸 빼면 대화가 거기서 끝난다.
    assert.match(blocker.detail, /타자로/);
    assert.ok(!/[A-Za-z]{6,}/.test(blocker.detail.replace(/https?/g, "")), "영어 설명이 섞였다");
  }
});

test("권한 거부와 마이크 없음과 다른 앱 점유를 구분해서 안내한다", () => {
  const denied = micErrorDetail(Object.assign(new Error("x"), { name: "NotAllowedError" }));
  assert.match(denied, /허용/);

  const missing = micErrorDetail(Object.assign(new Error("x"), { name: "NotFoundError" }));
  assert.match(missing, /찾지 못했어요/);

  const busy = micErrorDetail(Object.assign(new Error("x"), { name: "NotReadableError" }));
  assert.match(busy, /다른 앱/);

  // 셋이 서로 다른 문장이어야 한다. 같은 문장이면 구분한 의미가 없다.
  assert.equal(new Set([denied, missing, busy]).size, 3);

  // 모르는 오류에도 다음에 할 일을 준다.
  assert.match(micErrorDetail(new Error("무슨 일인지 모름")), /다시 눌러/);
  assert.match(micErrorDetail(undefined), /타자로/);
});

test("녹음기가 안 만들어졌을 때의 문장도 막다른 골목이 아니다", () => {
  assert.match(RECORDER_START_FAILED, /새로고침/);
  assert.match(RECORDER_START_FAILED, /타자로/);
});
