/** 대화 한 판의 상태와 `/chat/stream` 읽기. **이 앱을 다시 만든 이유가 여기 모여 있다.**
 *
 * 예전 Streamlit 화면은 폰에서 웹소켓이 끊기면 서버가 답을 다 만들어 DB 에
 * 저장까지 해 놓고도 화면이 그대로 멈췄다. 사용자에게는 아무 일도 일어나지
 * 않는 것처럼 보였고, 그게 “앱이 죽었다” 로 읽혔다.
 *
 * 그래서 여기에는 **조용히 끝나는 경로가 없다.** 스트림이 어떻게 끝나든 화면에는
 * 셋 중 하나가 남는다 — 완성된 턴, 한국어 실패 문구, 아니면 사용자가 직접 떠난
 * 것(그건 사용자가 이미 안다). `streamChat()` 이 `turn` 아니면 `error` 로 끝난다고
 * 보장하지만, 그 계약이 깨지는 경우까지 여기서 한 번 더 막는다(`settled` 검사).
 *
 * 지키는 규칙 셋(제품 규칙이라 협상 대상이 아니다):
 *  1. `reply` 에 교정을 섞지 않는다. 교정은 `corrections`, 힌트는 `hint_ko` 다.
 *  2. `reset` 이 오면 여태 그린 글자를 **버린다.** 검증에 걸린 답을 남기지 않는다.
 *  3. 교정·힌트는 `turn` 이 온 뒤에만 그린다. 반쯤 만들어진 교정은 보여주지 않는다.
 *
 * **상태를 바꾸는 부분(`chatReducer`)을 훅 밖의 순수 함수로 빼 놓았다.** 브라우저
 * 없이도 진짜 서버의 사건을 그대로 흘려 넣어 결과 상태를 확인할 수 있어야 하기
 * 때문이다. 위 세 규칙은 눈으로 봐서는 지켜졌는지 알기 어렵고(특히 2번), 이
 * 프로젝트에서 제일 비싸게 배운 것이 “고치기 전에 재라” 다.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";

import { DEFAULT_STALL_MS, streamChat } from "../../api/stream";
import type { ChatStreamEvent } from "../../api/stream";
import type {
  ChatRequest,
  InputMode,
  Level,
  ScenarioOut,
  StrictnessKey,
  SttWordOut,
  TurnResponse,
} from "../../api/types";

/**
 * 첫 턴은 더 오래 기다려 준다.
 *
 * `stallMs` 는 전체 시간이 아니라 **한 바이트도 안 오는 시간**이다. 평소 턴은
 * 첫 글자까지 1~2초라 60초면 서른 배 여유지만, 앱을 켜고 처음 보내는 턴은
 * ollama 가 9GB 짜리 qwen3:14b 를 디스크에서 VRAM 으로 올리는 시간이 앞에
 * 붙는다. 그동안은 정말 한 바이트도 오지 않는다. 그 한 번을 60초로 자르면
 * 멀쩡한 서버를 놓고 “연결이 끊겼어요” 를 띄우게 된다.
 *
 * 그렇다고 무한정 매달리지는 않는다. 죽은 연결은 2분 안에 사용자에게 보인다.
 */
export const COLD_STALL_MS = 120_000;

/** 스트림이 `turn` 도 `error` 도 없이 끝났을 때. 계약상 오면 안 되는 자리다. */
export const MSG_SILENT = "답이 오다가 끊겼어요. 다시 보내 보세요.";

/** 학습자가 한 번 보내는 것. 타자면 `mode: "text"`, 말이면 전사 원본이 함께 온다. */
export interface Attempt {
  /** 학습자가 **확정한** 문장. 음성이라도 전사 그대로가 아니라 확인·수정을 거친 것이다. */
  message: string;
  mode: InputMode;
  /** 음성일 때만. STT 가 원래 들은 문장. */
  transcript?: string;
  /** 음성일 때만. `/stt` 가 준 words 배열을 그대로 되돌려 보낸다. */
  words?: SttWordOut[];
}

/** 화면에 쌓이는 한 줄. */
export type ChatEntry =
  | { id: number; role: "ai"; turn: TurnResponse }
  | { id: number; role: "me"; text: string; mode: InputMode; failed: boolean };

export interface Failure {
  /** 서버(또는 stream.ts)가 준 한국어 문장. 다시 쓰지 않고 그대로 보여준다. */
  detail: string;
  /** 실패한 시도. 다시 보내기·고쳐 쓰기에 쓴다. */
  attempt: Attempt;
  /** 어느 말풍선이 실패했는지. 다시 보낼 때 새로 만들지 않고 이걸 되살린다. */
  meId: number;
  /**
   * 글자가 오다가 끊겼는가.
   *
   * 이걸 구분하는 이유가 있다. delta 가 한 번이라도 왔다면 서버는 이미 답을
   * 만들고 있었고, 연결만 끊긴 채 턴이 저장됐을 수 있다. 그대로 다시 보내면
   * 같은 말이 두 번 쌓인다. 아무것도 못 받았으면 그 걱정이 없다.
   * 화면은 이 값이 참일 때만 경고를 덧붙인다.
   */
  midway: boolean;
}

export interface ChatState {
  entries: ChatEntry[];
  /** 첫 턴을 보내야 생긴다. 이게 없으면 리포트를 만들 수 없다. */
  sessionId: string | null;
  /** 답을 기다리는 중. 입력칸을 잠그는 기준이다. */
  streaming: boolean;
  /** 여태 흘러온 영어. `reset` 이 오면 비워진다. */
  draft: string;
  /** `reset` 을 받아 서버가 답을 다시 쓰는 중. 글자가 사라진 이유를 화면에 적으려고 둔다. */
  rewriting: boolean;
  failure: Failure | null;
}

/**
 * 상태를 바꾸는 모든 경우.
 *
 * `id` 는 리듀서가 만들지 않고 부르는 쪽이 넣어 준다. 리듀서를 순수하게 두면
 * 서버 사건을 그대로 흘려 넣어 결과를 확인할 수 있다 — 그게 이 구조의 목적이다.
 */
export type ChatAction =
  /** 보내기 시작. 없던 말풍선이면 새로 만들고, 실패했던 말풍선이면 표시를 걷어낸다. */
  | { type: "begin"; meId: number; text: string; mode: InputMode }
  /** 서버가 보낸 사건 하나. `id` 는 `turn` 일 때 새 말풍선에 쓸 번호다. */
  | { type: "event"; event: ChatStreamEvent; id: number; meId: number; attempt: Attempt; midway: boolean }
  /** 스트림이 사건 없이 끝났거나 예외가 났다. 조용히 넘어가지 않는 마지막 그물. */
  | { type: "fail"; detail: string; meId: number; attempt: Attempt; midway: boolean }
  /** 스트림이 끝났다(성공이든 실패든). 입력칸을 다시 연다. */
  | { type: "done" }
  /** 실패한 말풍선을 지운다(고쳐 쓰기). */
  | { type: "dismiss"; meId: number };

/** 실패를 상태에 새긴다. 사건이 `error` 로 왔든 우리가 만들어 냈든 같은 모양이다. */
function markFailed(
  state: ChatState,
  detail: string,
  meId: number,
  attempt: Attempt,
  midway: boolean,
): ChatState {
  return {
    ...state,
    // 검증도 안 끝난 조각은 남기지 않는다. 반쯤 만들어진 영어가 화면에 남으면
    // 학습자는 그게 맞는 문장인 줄 알고 외운다.
    draft: "",
    rewriting: false,
    entries: state.entries.map((e) =>
      e.id === meId && e.role === "me" ? { ...e, failed: true } : e,
    ),
    failure: { detail, attempt, meId, midway },
  };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "begin": {
      const known = state.entries.some((e) => e.id === action.meId);
      const entries = known
        ? // 다시 보내기다. 말풍선을 새로 만들지 않고 그 자리를 되살린다 — 보낼
          // 때마다 같은 말이 쌓이면 학습자는 자기가 여러 번 말한 줄 안다.
          state.entries.map((e) =>
            e.id === action.meId && e.role === "me" ? { ...e, failed: false } : e,
          )
        : [
            ...state.entries,
            {
              id: action.meId,
              role: "me" as const,
              text: action.text,
              mode: action.mode,
              failed: false,
            },
          ];
      return { ...state, entries, streaming: true, draft: "", rewriting: false, failure: null };
    }

    case "event": {
      const { event } = action;
      switch (event.type) {
        case "session":
          return { ...state, sessionId: event.session_id };

        case "delta":
          return { ...state, draft: state.draft + event.text };

        case "reset":
          // 1차 응답이 스키마 검증에 걸렸다. 여태 보여준 글자를 전부 버린다.
          return { ...state, draft: "", rewriting: true };

        case "turn":
          // 흘러가던 글자를 지우는 것과 최종 턴을 붙이는 것을 **한 번에** 한다.
          // 나눠 놓으면 그 사이 한 프레임에 같은 문장이 두 번 보인다.
          return {
            ...state,
            draft: "",
            rewriting: false,
            entries: [...state.entries, { id: action.id, role: "ai", turn: event.turn }],
          };

        case "error":
          return markFailed(state, event.detail, action.meId, action.attempt, action.midway);
      }
      return state;
    }

    case "fail":
      return markFailed(state, action.detail, action.meId, action.attempt, action.midway);

    case "done":
      return { ...state, streaming: false };

    case "dismiss":
      return {
        ...state,
        entries: state.entries.filter((e) => e.id !== action.meId),
        failure: null,
      };
  }
}

/** 시나리오의 첫 대사를 턴 모양으로 바꾼다.
 *
 * 진짜 턴과 같은 모양으로 만들어 두면 말풍선·해석·힌트를 그리는 코드가 하나로
 * 끝난다. 첫 대사만 따로 그리면 그쪽에서만 해석 보기가 빠지는 식으로 어긋난다. */
export function openingTurn(scenario: ScenarioOut): TurnResponse {
  return {
    reply: scenario.opening_line,
    reply_ko: scenario.opening_line_ko,
    corrections: [],
    say_en: scenario.opening_say_en,
    say_more: scenario.opening_say_more,
    hint_ko: scenario.opening_hint_ko,
  };
}

/** 대화를 시작하는 상태. 첫 대사가 이미 들어 있다. */
export function initialChatState(scenario: ScenarioOut): ChatState {
  return {
    entries: [{ id: 0, role: "ai", turn: openingTurn(scenario) }],
    sessionId: null,
    streaming: false,
    draft: "",
    rewriting: false,
    failure: null,
  };
}

/** 마지막 AI 턴. 첫 항목이 늘 AI 라 결과가 없을 수 없다. */
export function lastAiTurn(
  entries: ChatEntry[],
  scenario: ScenarioOut,
): { id: number; turn: TurnResponse } {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (entry.role === "ai") return { id: entry.id, turn: entry.turn };
  }
  return { id: 0, turn: openingTurn(scenario) }; // 타입을 위한 바닥
}

// ─────────────────────────────────────────────── 훅

export interface Chat extends ChatState {
  /** 말할 것을 제안하는 바가 쓴다. 첫 대사가 있어 항상 존재한다. */
  last: { id: number; turn: TurnResponse };
  send: (attempt: Attempt) => void;
  /** 실패한 그 말을 그대로 다시 보낸다. */
  retry: () => void;
  /** 실패한 말풍선을 지운다. 입력칸으로 되돌리는 건 화면 쪽 일이다. */
  dismiss: () => void;
}

export interface UseChatOptions {
  scenario: ScenarioOut;
  level: Level;
  strictness: StrictnessKey;
}

export function useChat({ scenario, level, strictness }: UseChatOptions): Chat {
  const [state, dispatch] = useReducer(chatReducer, scenario, initialChatState);

  const idRef = useRef(0);
  const sessionRef = useRef<string | null>(null);
  /** 상태 말고 ref 로도 들고 있는다 — 연타 막기는 렌더를 기다릴 수 없다. */
  const streamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  // 설정은 요청마다 실어 보내는 값일 뿐이라, 바뀌었다고 send 를 다시 만들 이유가 없다.
  const settingsRef = useRef({ level, strictness });
  useEffect(() => {
    settingsRef.current = { level, strictness };
  }, [level, strictness]);

  // 화면을 떠나면 읽던 스트림을 놓아 준다. 이 취소는 오류로 그리지 않는다 —
  // 사용자가 스스로 나간 것이라 이미 알고 있다(streamChat 도 조용히 끝낸다).
  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(
    async (attempt: Attempt, meId: number) => {
      streamingRef.current = true;
      dispatch({ type: "begin", meId, text: attempt.message, mode: attempt.mode });

      const controller = new AbortController();
      abortRef.current = controller;

      const { level: lv, strictness: st } = settingsRef.current;
      const voice = attempt.mode === "voice";
      const req: ChatRequest = {
        scenario_id: scenario.id,
        message: attempt.message,
        session_id: sessionRef.current,
        level: lv,
        strictness: st,
        input_mode: attempt.mode,
        transcript: voice ? attempt.transcript ?? null : null,
        transcript_words: voice ? attempt.words ?? null : null,
      };
      // 세션이 아직 없으면 이번이 첫 턴이다 — 모델 적재 시간을 감안해 더 기다린다.
      const stallMs = sessionRef.current ? DEFAULT_STALL_MS : COLD_STALL_MS;

      let midway = false;
      let settled = false;

      try {
        for await (const event of streamChat(req, { signal: controller.signal, stallMs })) {
          if (event.type === "delta") midway = true;
          // 다음 요청에 넣을 세션은 렌더를 기다릴 수 없어 ref 에도 적어 둔다.
          if (event.type === "session") sessionRef.current = event.session_id;
          if (event.type === "turn" || event.type === "error") settled = true;
          dispatch({ type: "event", event, id: ++idRef.current, meId, attempt, midway });
        }

        // 여기 오면 스트림이 `turn` 도 `error` 도 없이 끝난 것이다. streamChat 의
        // 계약상 일어나면 안 되지만, **일어났을 때 화면이 멈추는 쪽으로 실패하지
        // 않게** 한 겹 더 둔다. 조용한 실패가 이 프로젝트의 원래 병이다.
        if (!settled && !controller.signal.aborted) {
          dispatch({ type: "fail", detail: MSG_SILENT, meId, attempt, midway });
        }
      } catch {
        // streamChat 은 던지지 않기로 되어 있다. 그래도 던졌다면 화면에 적는다.
        if (!controller.signal.aborted) {
          dispatch({ type: "fail", detail: MSG_SILENT, meId, attempt, midway });
        }
      } finally {
        streamingRef.current = false;
        dispatch({ type: "done" });
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [scenario.id],
  );

  const send = useCallback(
    (attempt: Attempt) => {
      const message = attempt.message.trim();
      // 서버 제약이 1~1000자다. 빈 문장은 보내 봐야 422 로 돌아온다.
      if (!message || streamingRef.current) return;
      void run({ ...attempt, message }, ++idRef.current);
    },
    [run],
  );

  const failure = state.failure;

  const retry = useCallback(() => {
    if (!failure || streamingRef.current) return;
    void run(failure.attempt, failure.meId);
  }, [failure, run]);

  const dismiss = useCallback(() => {
    if (failure) dispatch({ type: "dismiss", meId: failure.meId });
  }, [failure]);

  return { ...state, last: lastAiTurn(state.entries, scenario), send, retry, dismiss };
}
