/** 음성 입력의 상태 전이. **브라우저를 하나도 모르는 순수 함수다.**
 *
 * 왜 따로 떼었나. 이 화면의 버그는 전부 "지금 어느 단계인가" 를 잘못 알아서 생긴다 —
 * 녹음이 두 번 시작되거나, **같은 녹음을 두 번 보내거나**(Streamlit 에서 실제로 겪었다),
 * 전사를 기다리는 사이에 누른 버튼이 학습자가 고쳐 놓은 문장을 지워 버리거나.
 * `MediaRecorder` 와 얽혀 있으면 이걸 시험할 수 없어서 브라우저가 필요 없는 자리로 뺐다.
 * `tests/voice/machine.test.ts` 가 아래 규칙을 통째로 덮는다.
 *
 * **핵심 규칙 하나**: 지금 단계에서 말이 안 되는 사건은 **조용히 무시하고 상태를 그대로 둔다.**
 * "보내기" 는 `recording` 에서만, "받아썼다" 는 `transcribing` 에서만 먹는다. 두 번 눌러도
 * 두 번째는 아무 일도 일으키지 못한다 — 중복 전송을 막는 자리가 여기 한 군데다.
 */
import type { SttWordOut } from "../api/types";

/** 60초. 이보다 길게 말할 일이 없고(왕초보의 한 턴은 한 문장이다), 서버는 CPU 로
 *  전사하므로 오디오 1초당 0.3초를 쓴다. 길게 눌린 채 잊힌 마이크가 서버를 오래
 *  붙잡지 않게 스스로 멈춘다. 10MB 제한(413)에 걸리기 전에 끊는 뜻도 있다. */
export const MAX_RECORD_MS = 60_000;

/** 부모(대화 화면)에게 넘기는 것. 필드 이름이 `ChatRequest` 와 같아서 그대로 펼쳐 넣으면 된다. */
export interface VoiceInput {
  /** 학습자가 **확정한** 문장. 고쳤으면 고친 것. */
  message: string;
  /** STT 가 원래 들은 것. 화면에는 안 쓰고 기록으로만 간다 — 둘의 차이가 이 앱이 가장 알고 싶은 값이다. */
  transcript: string;
  /** `/stt` 가 준 낱말 배열을 **그대로** 되돌려 보낸다. */
  transcript_words: SttWordOut[];
}

export type VoiceState =
  /** 마이크 버튼만 있는 평상시. */
  | { phase: "idle" }
  /** 이 브라우저·이 주소에서는 아예 안 된다. `detail` 에 이유와 다음 할 일이 들어 있다. */
  | { phase: "blocked"; detail: string }
  /** 권한을 묻는 중(브라우저 팝업이 떠 있다). */
  | { phase: "starting" }
  /** 녹음 중. `elapsedMs` 는 화면에 경과 시간을 그리는 데만 쓴다. */
  | { phase: "recording"; startedAt: number; elapsedMs: number }
  /** `/stt` 에 보내 놓고 기다리는 중. 3초 발화에 1초쯤 걸린다. */
  | { phase: "transcribing" }
  /** 받아썼다. 학습자가 확인하는 단계. */
  | { phase: "review"; heard: string; words: SttWordOut[]; draft: string }
  /** 200 인데 글자가 없다. **오류가 아니다** — 마이크만 누르고 말을 안 한 경우다. */
  | { phase: "empty" }
  /** 실패. `detail` 은 서버가 준 한국어를 그대로 쓴다. */
  | { phase: "failed"; detail: string };

export const initialVoiceState: VoiceState = { phase: "idle" };

export type VoiceEvent =
  /** 마이크를 눌렀다. */
  | { type: "press" }
  /** 권한이 통과돼 녹음이 시작됐다. */
  | { type: "started"; at: number }
  /** 열어 보기 전에 이미 안 되는 것이 확정됐다. */
  | { type: "blocked"; detail: string }
  /** 무언가 실패했다. */
  | { type: "failed"; detail: string }
  /** 시계가 갔다. */
  | { type: "tick"; at: number }
  /** 멈춤을 눌렀다(= 이 녹음을 전사로 보낸다). */
  | { type: "stop" }
  /** `/stt` 응답이 왔다. */
  | { type: "heard"; text: string; words: SttWordOut[] }
  /** 확인 칸을 고쳤다. */
  | { type: "edit"; draft: string }
  /** 부모에게 넘겼다. */
  | { type: "confirm" }
  /** 다시 말하기 · 취소. */
  | { type: "cancel" };

/** 마이크를 새로 누를 수 있는 단계. 녹음·전사·확인 중에는 누를 수 없어야 한다. */
function canPress(phase: VoiceState["phase"]): boolean {
  return phase === "idle" || phase === "empty" || phase === "failed";
}

export function voiceReducer(state: VoiceState, event: VoiceEvent): VoiceState {
  switch (event.type) {
    case "press":
      // blocked 에서는 눌러도 열리지 않는다. 이미 이유를 띄워 놨으니 그대로 둔다.
      return canPress(state.phase) ? { phase: "starting" } : state;

    case "started":
      // 취소를 눌러 idle 로 돌아간 뒤 늦게 도착한 허락은 버린다. 안 그러면
      // 학습자가 모르는 사이에 마이크가 켜져 있게 된다.
      return state.phase === "starting"
        ? { phase: "recording", startedAt: event.at, elapsedMs: 0 }
        : state;

    case "blocked":
      return { phase: "blocked", detail: event.detail };

    case "failed":
      // 확인 칸에 학습자가 고쳐 놓은 문장이 있으면 그건 무슨 일이 있어도 지우지 않는다.
      return state.phase === "review" ? state : { phase: "failed", detail: event.detail };

    case "tick":
      return state.phase === "recording"
        ? { ...state, elapsedMs: Math.max(0, event.at - state.startedAt) }
        : state;

    case "stop":
      // **중복 전송을 막는 자리.** 두 번째 stop 은 여기서 죽는다.
      return state.phase === "recording" ? { phase: "transcribing" } : state;

    case "heard": {
      if (state.phase !== "transcribing") return state;
      const heard = event.text.trim();
      // 빈 전사는 오류가 아니다. 무음에서 지어낸 말을 서버의 vad_filter 가 막아 준 정상 동작이다.
      if (!heard) return { phase: "empty" };
      return { phase: "review", heard, words: event.words, draft: heard };
    }

    case "edit":
      // 공백을 그대로 둔다. 타자 중간의 스페이스를 지워 버리면 칸이 제멋대로 움직인다.
      return state.phase === "review" ? { ...state, draft: event.draft } : state;

    case "confirm":
      // 빈 문장은 보낼 수 없다(서버가 1자 이상을 요구한다). 넘길 게 없으면 그대로 둔다.
      return confirmedInput(state) ? { phase: "idle" } : state;

    case "cancel":
      // blocked 는 환경 탓이라 취소로 풀리지 않는다. 풀어 주면 눌러도 안 되는 버튼이 되살아난다.
      return state.phase === "blocked" ? state : { phase: "idle" };

    default:
      return state;
  }
}

/**
 * 지금 부모에게 넘길 수 있는 값. 넘길 게 없으면 null.
 *
 * 화면과 따로 시험할 수 있게 떼어 놨다 — **무엇을 보내는가**가 이 컴포넌트의
 * 계약 전부이기 때문이다.
 */
export function confirmedInput(state: VoiceState): VoiceInput | null {
  if (state.phase !== "review") return null;
  const message = state.draft.trim();
  if (!message) return null;
  return { message, transcript: state.heard, transcript_words: state.words };
}

/** 스스로 멈춰야 하는가. 화면은 시계만 흘려보내고 판단은 여기서 한다. */
export function shouldAutoStop(state: VoiceState): boolean {
  return state.phase === "recording" && state.elapsedMs >= MAX_RECORD_MS;
}

/** `0:07` 처럼 그린다. 경과 시간은 초 단위면 충분하다. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
