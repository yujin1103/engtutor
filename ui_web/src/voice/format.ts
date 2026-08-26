/** 어떤 형식으로 녹음할지 고른다.
 *
 * **여기서 iOS 가 제일 위험하다.** `MediaRecorder` 는 브라우저마다 내놓는 형식이
 * 다르다 — 크롬·파이어폭스·안드로이드는 `audio/webm`, **iOS 사파리는 `audio/mp4`**
 * 다. webm 을 고정으로 박아 두면 아이폰에서 생성자가 그 자리에서 던지거나, 더 나쁘게는
 * 0바이트 녹음이 만들어져 "말했는데 아무 일도 안 일어난다" 가 된다. 우리가 React 로
 * 넘어온 이유가 바로 그 증상이라 여기서 되풀이할 수 없다.
 *
 * 그래서 **묻고 고른다** — `MediaRecorder.isTypeSupported` 로 실제 지원 여부를 확인하고,
 * 하나도 안 되면 `null` 을 돌려 화면이 이유를 적게 한다. 조용히 실패시키지 않는다.
 *
 * 서버는 형식을 **내용으로** 판별한다(m4a·webm·wav 를 실제로 넣어 200 을 확인했다).
 * 그러니 파일 이름은 서버 로그에서 알아보기 위한 것이고, 확장자만 형식에 맞춰 준다.
 */

/** 녹음 하나를 만들 때 쓸 형식. */
export interface RecordingFormat {
  /** `new MediaRecorder(stream, { mimeType })` 에 넣을 값. 빈 문자열이면 브라우저에 맡긴다. */
  mimeType: string;
  /** 파일 이름에 붙일 확장자 (점 없이). */
  extension: string;
}

/**
 * 앞에 있는 것부터 시도한다.
 *
 * opus 를 먼저 두는 이유는 같은 길이에서 파일이 가장 작아서다 — 폰 데이터로 올리는
 * 데다 서버가 10MB 에서 413 을 준다. mp4(AAC)는 iOS 사파리의 **유일한** 선택지라
 * 반드시 목록에 있어야 한다. wav 는 거의 아무도 지원하지 않지만 서버가 받으므로
 * 마지막 예비로 남긴다.
 */
const CANDIDATES: readonly RecordingFormat[] = [
  { mimeType: "audio/webm;codecs=opus", extension: "webm" },
  { mimeType: "audio/webm", extension: "webm" },
  { mimeType: "audio/mp4;codecs=mp4a.40.2", extension: "m4a" }, // iOS 사파리
  { mimeType: "audio/mp4", extension: "m4a" },
  { mimeType: "audio/ogg;codecs=opus", extension: "ogg" },
  { mimeType: "audio/wav", extension: "wav" },
];

/**
 * 이 브라우저가 만들 수 있는 형식을 고른다. 하나도 없으면 `null`.
 *
 * `isTypeSupported` 를 인자로 받는 이유는 브라우저 없이 시험하기 위해서다. 실제
 * 호출부는 `MediaRecorder.isTypeSupported` 를 그대로 넘긴다.
 *
 * 함수 자체가 없는 경우(아주 오래된 사파리)에는 `{ mimeType: "" }` 로 **브라우저에
 * 맡긴다.** 지원 여부를 물을 방법이 없을 뿐 녹음은 될 수 있고, 여기서 null 을 돌리면
 * 될 일을 미리 막아 버린다. 실제로 만들어진 형식은 `filenameFor()` 가 Blob 에서 다시 읽는다.
 */
export function pickRecordingFormat(
  isTypeSupported?: ((type: string) => boolean) | undefined,
): RecordingFormat | null {
  if (typeof isTypeSupported !== "function") {
    return { mimeType: "", extension: "webm" };
  }
  for (const candidate of CANDIDATES) {
    // 브라우저가 이 함수에서 던지는 경우가 있어(옛 사파리) 통째로 감싼다.
    try {
      if (isTypeSupported(candidate.mimeType)) return candidate;
    } catch {
      continue;
    }
  }
  return null;
}

/** MIME 타입에서 확장자를 되짚는다. `audio/webm;codecs=opus` 처럼 파라미터가 붙어 온다. */
export function extensionForMime(mime: string): string | null {
  const base = mime.split(";")[0]?.trim().toLowerCase() ?? "";
  if (base === "audio/webm" || base === "video/webm") return "webm";
  if (base === "audio/mp4" || base === "video/mp4") return "m4a";
  if (base === "audio/ogg" || base === "video/ogg") return "ogg";
  if (base === "audio/wav" || base === "audio/x-wav" || base === "audio/wave") return "wav";
  if (base === "audio/mpeg") return "mp3";
  return null;
}

/**
 * 올릴 파일 이름.
 *
 * **고른 형식이 아니라 실제로 만들어진 Blob 에서 읽는다.** 브라우저가 우리가 준
 * mimeType 을 무시하고 제 형식으로 만드는 일이 있어서, 고를 때의 확장자를 그대로
 * 쓰면 로그에서 형식을 오해하게 된다. Blob 이 형식을 안 알려줄 때만 고른 값을 쓴다.
 */
export function filenameFor(blob: Blob, chosen: RecordingFormat | null): string {
  const extension = extensionForMime(blob.type) ?? chosen?.extension ?? "webm";
  return `speech.${extension}`;
}
