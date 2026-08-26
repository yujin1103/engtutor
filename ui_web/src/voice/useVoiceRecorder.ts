/** 녹음 → `/stt` → 확인 칸. 브라우저를 만지는 부분은 전부 여기 모여 있다.
 *
 * 상태 전이 규칙은 `machine.ts` 에 순수 함수로 따로 있다(그쪽이 시험으로 덮여 있다).
 * 이 파일이 하는 일은 `MediaRecorder` 를 그 규칙에 맞게 여닫는 것뿐이다.
 *
 * **같은 녹음을 두 번 보내지 않는다.** Streamlit 에서 실제로 겪은 함정이라 자물쇠를
 * 두 개 건다 — (1) `stop` 사건은 `recording` 단계에서만 먹고(machine.ts),
 * (2) 녹음마다 번호를 매겨 이미 보낸 것은 다시 안 보낸다(`sent` 표시).
 * `MediaRecorder` 는 `ondataavailable` 를 여러 번 부를 수 있어서, 보내는 자리는
 * `onstop` 한 곳뿐이다.
 */
import { useEffect, useReducer, useRef, useState } from "react";

import { ApiError, isAborted, postStt } from "../api/client";
import { RECORDER_START_FAILED, findMicBlocker, micErrorDetail, readMicEnv } from "./blockers";
import { filenameFor, pickRecordingFormat, type RecordingFormat } from "./format";
import {
  confirmedInput,
  initialVoiceState,
  shouldAutoStop,
  voiceReducer,
  type VoiceInput,
  type VoiceState,
} from "./machine";

/** 녹음 한 번에 딸린 것들. 번호(`id`)로 "지금 살아 있는 녹음" 을 가린다 —
 *  취소한 뒤 늦게 도착한 응답이 화면을 되돌리는 것을 막는 유일한 방법이다. */
interface Live {
  id: number;
  stream: MediaStream | null;
  recorder: MediaRecorder | null;
  chunks: Blob[];
  /** 취소됐다. 결과가 와도 버린다. */
  discarded: boolean;
  /** 이미 `/stt` 로 보냈다. **두 번 보내지 않게 하는 표시.** */
  sent: boolean;
  abort: AbortController | null;
}

export interface VoiceRecorder {
  state: VoiceState;
  /** 고른 녹음 형식. 화면에 쓸 일은 없고 문제를 들여다볼 때 참고용이다. */
  format: RecordingFormat | null;
  start: () => void;
  stop: () => void;
  /** 다시 말하기 · 취소. 진행 중인 것을 전부 버린다. */
  cancel: () => void;
  edit: (draft: string) => void;
  /** 넘길 값을 돌려주고 상태를 비운다. 넘길 게 없으면 null 이고 상태도 그대로다. */
  confirm: () => VoiceInput | null;
}

function stopStream(stream: MediaStream | null): void {
  // 트랙을 안 끄면 폰 상단의 녹음 표시가 켜진 채 남고, iOS 는 다음 녹음을 거절한다.
  stream?.getTracks().forEach((track) => track.stop());
}

export function useVoiceRecorder(): VoiceRecorder {
  const [state, dispatch] = useReducer(voiceReducer, initialVoiceState);

  // 형식은 브라우저마다 한 번만 물으면 된다. 게으른 초기값으로 두면 첫 렌더에서
  // 딱 한 번 계산되고 그 뒤로는 같은 값이 유지된다.
  // (`useState` 인 이유: ref 를 렌더 중에 읽으면 안 된다. 이 값은 화면에 영향을 준다 —
  //  형식을 못 고르면 마이크 버튼 대신 이유가 뜬다.)
  const [format] = useState<RecordingFormat | null>(() =>
    pickRecordingFormat(
      typeof window !== "undefined" && typeof window.MediaRecorder === "function"
        ? // 언바운드로 넘기면 일부 브라우저에서 this 가 없어 던진다. 감싸서 넘긴다.
          (type: string) => window.MediaRecorder.isTypeSupported(type)
        : undefined,
    ),
  );

  const liveRef = useRef<Live | null>(null);
  const counterRef = useRef(0);

  // 열어 보기도 전에 안 되는 것이 확정된 경우(https 아님·권한 API 없음·형식 없음)를
  // 처음 한 번 가려낸다. 눌러도 안 되는 버튼을 보여주는 것보다 이유를 적는 편이 낫다.
  useEffect(() => {
    const blocker = findMicBlocker(readMicEnv(format));
    if (blocker) dispatch({ type: "blocked", detail: blocker.detail });
  }, [format]);

  /** 녹음을 `/stt` 로 보낸다. **여기가 보내는 유일한 자리다.** */
  function send(live: Live): void {
    stopStream(live.stream);
    if (live.sent || live.discarded) return; // 두 번째 호출은 여기서 죽는다
    live.sent = true;

    const type = live.recorder?.mimeType || format?.mimeType || "";
    const blob = new Blob(live.chunks, type ? { type } : undefined);
    const controller = new AbortController();
    live.abort = controller;

    // 0바이트여도 그냥 보낸다. 서버가 200 + 빈 text 로 답하고 화면은 "안 들렸어요" 를
    // 그린다 — 여기서 따로 가르면 같은 문구를 두 곳에서 관리하게 된다.
    postStt(blob, filenameFor(blob, format), controller.signal)
      .then((res) => {
        if (live.discarded || liveRef.current?.id !== live.id) return;
        dispatch({ type: "heard", text: res.text, words: res.words });
      })
      .catch((error: unknown) => {
        if (isAborted(error) || live.discarded || liveRef.current?.id !== live.id) return;
        // 413(너무 김)·400(오디오 아님)·503(STT 꺼짐)의 detail 은 서버가 한국어로,
        // 다음에 뭘 하면 되는지까지 담아서 준다. 우리가 다시 쓰지 않는다.
        dispatch({
          type: "failed",
          detail:
            error instanceof ApiError
              ? error.detail
              : "녹음을 보내지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      });
  }

  async function begin(): Promise<void> {
    const id = ++counterRef.current;
    const live: Live = {
      id,
      stream: null,
      recorder: null,
      chunks: [],
      discarded: false,
      sent: false,
      abort: null,
    };
    liveRef.current = live;

    let stream: MediaStream;
    try {
      // 제약을 걸지 않는다. 오류 생존율을 잰 녹음 20개가 기기 기본 설정으로 딴 것이라,
      // 여기서만 채널 수나 잡음 제거를 바꾸면 그 측정과 다른 소리를 보내게 된다.
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (!live.discarded && liveRef.current?.id === id) {
        dispatch({ type: "failed", detail: micErrorDetail(error) });
      }
      return;
    }

    // 권한 팝업이 떠 있는 동안 취소를 눌렀을 수 있다. 그러면 마이크를 즉시 도로 닫는다.
    if (live.discarded || liveRef.current?.id !== id) {
      stopStream(stream);
      return;
    }
    live.stream = stream;

    try {
      live.recorder = format?.mimeType
        ? new MediaRecorder(stream, { mimeType: format.mimeType })
        : new MediaRecorder(stream);
    } catch {
      stopStream(stream);
      dispatch({ type: "failed", detail: RECORDER_START_FAILED });
      return;
    }

    live.recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) live.chunks.push(event.data);
    };
    live.recorder.onerror = () => {
      if (!live.discarded) dispatch({ type: "failed", detail: RECORDER_START_FAILED });
      stopStream(live.stream);
    };
    // 보내는 자리는 여기 한 곳뿐이다. ondataavailable 은 여러 번 올 수 있다.
    live.recorder.onstop = () => send(live);

    try {
      live.recorder.start();
    } catch {
      stopStream(stream);
      dispatch({ type: "failed", detail: RECORDER_START_FAILED });
      return;
    }
    dispatch({ type: "started", at: Date.now() });
  }

  function start(): void {
    // 녹음·전사·확인 중에 또 누르는 것은 machine 이 막지만, 마이크를 여는 일은
    // 부수효과라 여기서도 한 번 더 막아야 한다.
    if (state.phase !== "idle" && state.phase !== "empty" && state.phase !== "failed") return;
    dispatch({ type: "press" });
    void begin();
  }

  function stop(): void {
    if (state.phase !== "recording") return; // 두 번 눌러도 한 번만 보낸다
    dispatch({ type: "stop" });
    const live = liveRef.current;
    if (!live) return;
    if (live.recorder && live.recorder.state !== "inactive") {
      live.recorder.stop(); // onstop -> send()
    } else {
      send(live);
    }
  }

  /** 진행 중인 녹음을 버린다. 화면 상태는 건드리지 않는다(취소와 화면 이탈이 함께 쓴다). */
  function discard(): void {
    const live = liveRef.current;
    liveRef.current = null;
    if (!live) return;
    live.discarded = true;
    live.abort?.abort();
    try {
      if (live.recorder && live.recorder.state !== "inactive") live.recorder.stop();
    } catch {
      // 이미 멈춘 녹음기에 stop 을 부르면 던진다. 어차피 버릴 것이라 무시한다.
    }
    stopStream(live.stream);
  }

  function cancel(): void {
    discard();
    dispatch({ type: "cancel" });
  }

  function edit(draft: string): void {
    dispatch({ type: "edit", draft });
  }

  function confirm(): VoiceInput | null {
    const input = confirmedInput(state);
    if (!input) return null;
    liveRef.current = null; // 넘긴 녹음은 잊는다. 다시 보낼 길을 끊는다.
    dispatch({ type: "confirm" });
    return input;
  }

  // 아래 두 효과는 **순서가 의미가 있다.** 먼저 최신 stop 을 ref 에 넣고, 다음 효과에서
  // 그걸 부른다. 순서를 바꾸면 한 렌더 낡은 함수를 부르게 된다.
  const stopRef = useRef<() => void>(() => {});
  useEffect(() => {
    stopRef.current = stop;
  });

  // 60초가 넘으면 스스로 멈춘다. 눌린 채 잊힌 마이크가 서버를 오래 붙잡지 않게.
  useEffect(() => {
    if (shouldAutoStop(state)) stopRef.current();
  }, [state]);

  // 경과 시간. 화면에 초를 그리는 것 말고는 쓰지 않는다.
  useEffect(() => {
    if (state.phase !== "recording") return;
    const timer = setInterval(() => dispatch({ type: "tick", at: Date.now() }), 250);
    return () => clearInterval(timer);
  }, [state.phase]);

  // 화면을 떠날 때 마이크를 반드시 닫는다. 안 닫으면 폰의 녹음 표시가 켜진 채 남는다.
  useEffect(() => {
    return () => {
      const live = liveRef.current;
      liveRef.current = null;
      if (!live) return;
      live.discarded = true;
      live.abort?.abort();
      try {
        if (live.recorder && live.recorder.state !== "inactive") live.recorder.stop();
      } catch {
        // 위와 같다.
      }
      stopStream(live.stream);
    };
  }, []);

  return { state, format, start, stop, cancel, edit, confirm };
}
