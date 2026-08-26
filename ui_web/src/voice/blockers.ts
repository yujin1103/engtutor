/** 마이크가 안 열리는 이유를 **구분해서** 한국어로 적는다.
 *
 * 왕초보에게 "마이크를 쓸 수 없습니다" 한 줄은 아무 쓸모가 없다. 주소가 https 가
 * 아니어서인지, 권한을 거부해 둔 것인지, 다른 앱이 마이크를 잡고 있는지에 따라
 * **다음에 할 일이 완전히 다르다.** 그래서 경우마다 문장을 따로 쓰고, 문장마다
 * "그래서 뭘 누르면 되는지" 를 넣는다.
 *
 * 가장 흔한 함정은 첫 번째다 — **폰 브라우저는 https 가 아니면 마이크를 아예 안 내준다.**
 * LAN 주소(`http://192.168.x.x:5173`)로 열면 버튼은 보이는데 눌러도 안 된다.
 * 앱을 FastAPI 가 터널 뒤에서 통째로 서빙하는 것이 이 문제의 해결책이고, 그래도
 * 누군가는 http 주소로 들어올 테니 그때 이 문장이 뜬다.
 */
import type { RecordingFormat } from "./format";

/** 마이크를 열어 보기도 전에 이미 안 되는 것이 확정된 경우. */
export interface MicBlocker {
  /** 시험과 로그에서 구분하려고 붙인 이름. 화면에는 `detail` 만 나간다. */
  kind: "insecure" | "no-media" | "no-recorder" | "no-format";
  /** 학습자에게 그대로 보여주는 한국어. */
  detail: string;
}

/** 브라우저에서 읽어 온 사실들. 시험에서 손으로 만들 수 있게 값만 받는다. */
export interface MicEnv {
  /** https 이거나 localhost 인가. */
  secure: boolean;
  /** `navigator.mediaDevices?.getUserMedia` 가 있는가. */
  hasGetUserMedia: boolean;
  /** `window.MediaRecorder` 가 있는가. */
  hasRecorder: boolean;
  /** 고른 녹음 형식. 하나도 못 골랐으면 null. */
  format: RecordingFormat | null;
}

/** 지금 브라우저에서 녹음을 시작해도 되는지. 안 되면 이유를, 되면 null. */
export function findMicBlocker(env: MicEnv): MicBlocker | null {
  // 순서가 중요하다. https 가 아니면 mediaDevices 자체가 없으므로, 그냥 두면
  // "브라우저가 낡았다" 는 엉뚱한 안내가 나간다. 주소부터 본다.
  if (!env.secure) {
    return {
      kind: "insecure",
      detail:
        "이 주소로는 마이크를 쓸 수 없어요. 주소가 https:// 로 시작해야 마이크가 열려요. " +
        "안내받은 https 주소로 다시 들어와 주세요. 그동안은 타자로 답할 수 있어요.",
    };
  }
  if (!env.hasGetUserMedia) {
    return {
      kind: "no-media",
      detail:
        "이 브라우저에서는 마이크를 열 수 없어요. 사파리나 크롬으로 같은 주소를 열어 보세요. " +
        "그동안은 타자로 답할 수 있어요.",
    };
  }
  if (!env.hasRecorder) {
    return {
      kind: "no-recorder",
      detail:
        "이 브라우저에는 녹음 기능이 없어요. 브라우저를 최신 버전으로 올리거나 " +
        "사파리·크롬으로 열어 보세요. 그동안은 타자로 답할 수 있어요.",
    };
  }
  if (!env.format) {
    return {
      kind: "no-format",
      detail:
        "이 브라우저가 만드는 녹음 형식을 저희가 받을 수 없어요. 사파리나 크롬으로 " +
        "열어 보세요. 그동안은 타자로 답할 수 있어요.",
    };
  }
  return null;
}

/**
 * `getUserMedia` 가 거부·실패했을 때의 한국어 문장.
 *
 * 이름으로 가른다. 브라우저가 주는 `message` 는 영어인 데다("Permission denied")
 * 왕초보에게 아무것도 알려주지 않아 화면에 쓰지 않는다.
 */
export function micErrorDetail(error: unknown): string {
  const name = error instanceof Error ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      // 한 번 거부하면 브라우저가 다시 묻지 않는다. 어디를 눌러 되돌리는지까지 적는다.
      return (
        "마이크 사용이 거부돼 있어요. 주소창 왼쪽의 자물쇠(아이폰은 '아A')를 눌러 " +
        "마이크를 '허용' 으로 바꾼 뒤 다시 눌러 주세요."
      );
    case "NotFoundError":
    case "OverconstrainedError":
      return "마이크를 찾지 못했어요. 이어폰이나 마이크가 연결돼 있는지 확인하고 다시 눌러 주세요.";
    case "NotReadableError":
      return "다른 앱이 마이크를 쓰고 있어요. 통화나 녹음 앱을 끄고 다시 눌러 주세요.";
    default:
      return "마이크를 열지 못했어요. 다시 눌러 보고, 계속 안 되면 타자로 답해 주세요.";
  }
}

/** 실제 브라우저에서 `MicEnv` 를 읽는다. 이 함수만 window 를 만진다. */
export function readMicEnv(format: RecordingFormat | null): MicEnv {
  return {
    secure: typeof window !== "undefined" && window.isSecureContext,
    hasGetUserMedia:
      typeof navigator !== "undefined" &&
      typeof navigator.mediaDevices?.getUserMedia === "function",
    hasRecorder: typeof window !== "undefined" && typeof window.MediaRecorder === "function",
    format,
  };
}

/** 마이크는 열렸는데 녹음기가 안 만들어진 경우. 형식 문제일 때가 대부분이라
 *  새로고침이 실제로 먹히는 일이 많다. 막다른 골목으로 두지 않고 대안을 함께 준다. */
export const RECORDER_START_FAILED =
  "녹음을 시작하지 못했어요. 화면을 새로고침하고 다시 눌러 주세요. " +
  "계속 안 되면 타자로 답할 수 있어요.";
