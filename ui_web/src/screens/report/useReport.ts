/** 리포트 한 장을 받아 오는 일만 한다. 화면은 이 훅이 주는 상태만 그린다.
 *
 * 이 훅이 평범한 `useEffect` + `fetch` 가 아닌 이유가 셋이다.
 *
 *  1. **한 세션에 한 번만 부른다.** `POST /sessions/{id}/report` 는 부를 때마다
 *     로컬 14B 모델을 새로 돌린다. StrictMode 는 개발에서 효과를 두 번 실행하고,
 *     화면을 떠났다 돌아오면 또 한 번이다. 그때마다 GPU 가 1분씩 더 돌면
 *     기다리는 사람은 이유도 모르고 두 배를 기다린다. 그래서 진행 중인 요청과
 *     끝난 결과를 **모듈 바깥**(아래 두 Map)에 둔다.
 *  2. **화면을 떠나도 요청을 끊지 않는다.** 다른 곳에서는 언마운트 때 취소하는 게
 *     맞지만 여기서는 반대다 — 1분을 기다린 계산을 화면 전환 한 번으로 버리면
 *     돌아왔을 때 처음부터 다시 기다려야 한다. 결과는 Map 에 남고, 다시 들어오면
 *     그 자리에 있다.
 *  3. **끝없이 기다리지 않는다.** fetch 에는 기본 시간 제한이 없다. 서버가
 *     대답하지 않으면 화면은 영원히 도는 원을 보여주는데, 그게 바로 이 앱을
 *     React 로 다시 만든 이유인 "조용히 멈춘 화면" 이다. 제한 시간이 지나면
 *     한국어 문장으로 **보이게** 실패시킨다.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, getReport, isAborted } from "../../api/client";
import type { SessionReport } from "../../api/types";

/**
 * 얼마나 기다려 줄지.
 *
 * 넉넉한 값이다. 서버는 1차 응답이 스키마 검증에 걸리면 **한 번 더** 모델을
 * 돌리므로(app/report/service.py) 최악의 경우 생성 시간이 두 배가 된다.
 * 여기서 성급하게 끊으면 거의 다 만든 리포트를 버리게 된다.
 */
export const REPORT_TIMEOUT_MS = 300_000;

interface Pending {
  /** 언제 시작했는지. 화면을 떠났다 돌아와도 경과 시간이 이어지게 하려고 들고 있다. */
  startedAt: number;
  promise: Promise<SessionReport>;
  /** 제한 시간이 지나 우리가 끊은 것인지. 사용자가 떠나서 끊긴 것과 구분해야 한다. */
  timedOut: boolean;
}

const pending = new Map<string, Pending>();
const finished = new Map<string, SessionReport>();

function begin(sessionId: string): Pending {
  const controller = new AbortController();
  const entry: Pending = {
    startedAt: Date.now(),
    timedOut: false,
    // 바로 아래에서 채운다. 타이머 콜백이 entry 를 참조해야 해서 순서가 이렇게 된다.
    promise: undefined as unknown as Promise<SessionReport>,
  };

  const timer = setTimeout(() => {
    entry.timedOut = true;
    controller.abort();
  }, REPORT_TIMEOUT_MS);

  entry.promise = getReport(sessionId, controller.signal)
    .then((report) => {
      finished.set(sessionId, report);
      return report;
    })
    .catch((error: unknown) => {
      if (isAborted(error) && entry.timedOut) {
        throw new ApiError(
          0,
          "리포트를 만드는 데 너무 오래 걸리고 있어요. 컴퓨터가 바쁘거나 모델이 멈춘 것 같아요. 다시 시도해 보세요.",
        );
      }
      throw error;
    })
    .finally(() => {
      clearTimeout(timer);
      // 성공한 결과는 finished 로 옮겨 갔고, 실패한 것은 남겨 두면 안 된다 —
      // 남아 있으면 "다시 시도" 가 같은 실패를 그대로 다시 꺼내 준다.
      // 다만 **내가 넣어 둔 것일 때만** 지운다. 뒤늦게 끝난 옛 요청이 그 사이
      // 새로 시작한 요청을 지워 버리면, 화면은 도는 원만 남고 아무도 결과를 안 준다.
      if (pending.get(sessionId) === entry) pending.delete(sessionId);
    });

  // 화면이 이미 사라진 뒤에 실패하면 아무도 안 받는 거절이 된다.
  // 콘솔에 unhandled rejection 을 남기지 않으려고 빈 손잡이를 하나 붙여 둔다.
  entry.promise.catch(() => {});

  pending.set(sessionId, entry);
  return entry;
}

export type ReportState =
  /** `startedAt` 은 이 화면에 들어온 시각이 아니라 **요청이 시작된** 시각이다. */
  | { status: "loading"; startedAt: number }
  | { status: "ready"; report: SessionReport }
  | { status: "failed"; detail: string };

/** 지금 이 세션이 어디까지 와 있는지를 캐시만 보고 알아낸다. */
function currentState(sessionId: string): ReportState {
  const done = finished.get(sessionId);
  if (done) return { status: "ready", report: done };
  return { status: "loading", startedAt: pending.get(sessionId)?.startedAt ?? Date.now() };
}

export function useReport(sessionId: string): { state: ReportState; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ReportState>(() => currentState(sessionId));

  // 세션이 바뀌었거나 다시 시도를 눌렀으면 **렌더 도중에** 상태를 갈아 끼운다.
  // 효과 안에서 setState 로 되돌리면 잘못된 화면이 한 번 그려졌다 지워진다 —
  // 다 만든 리포트가 한 프레임 번쩍이는 식이라 폰에서 특히 눈에 띈다.
  const [shown, setShown] = useState({ sessionId, attempt });
  if (shown.sessionId !== sessionId || shown.attempt !== attempt) {
    setShown({ sessionId, attempt });
    setState(currentState(sessionId));
  }

  useEffect(() => {
    // 이미 받아 둔 리포트면 위에서 ready 로 잡혔다. 다시 부를 이유가 없다.
    if (finished.has(sessionId)) return;

    const entry = pending.get(sessionId) ?? begin(sessionId);

    // 요청 자체는 취소하지 않는다(파일 맨 위 2번). 화면이 사라진 뒤 setState 를
    // 부르지 않으려고 깃발만 내린다.
    let alive = true;
    entry.promise.then(
      (report) => {
        if (alive) setState({ status: "ready", report });
      },
      (error: unknown) => {
        if (!alive || isAborted(error)) return;
        setState({
          status: "failed",
          detail:
            error instanceof ApiError
              ? error.detail
              : "리포트를 만들지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      },
    );

    return () => {
      alive = false;
    };
  }, [sessionId, attempt]);

  const retry = useCallback(() => {
    // 캐시를 비워야 아래 attempt 변경이 새 요청으로 이어진다.
    finished.delete(sessionId);
    pending.delete(sessionId);
    setAttempt((n) => n + 1);
  }, [sessionId]);

  return { state, retry };
}

function elapsed(startedAt: number): number {
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
}

/** 시작 시각부터 흐른 **초**. 1초마다 다시 그린다. */
export function useElapsedSeconds(startedAt: number): number {
  const [seconds, setSeconds] = useState(() => elapsed(startedAt));

  // 시작 시각이 바뀌면(다시 시도) 렌더 도중에 0 으로 되돌린다. 효과에서 되돌리면
  // 이전 대화의 경과 시간이 한 번 스쳐 지나간다.
  const [base, setBase] = useState(startedAt);
  if (base !== startedAt) {
    setBase(startedAt);
    setSeconds(elapsed(startedAt));
  }

  useEffect(() => {
    // 세어 올리지 않고 매번 시각을 다시 읽는다 — 폰에서 화면이 꺼져 있는 동안
    // 타이머는 느려지거나 멈추지만 시간은 흐르기 때문에, 세어 두면 실제보다 짧게 나온다.
    const id = window.setInterval(() => setSeconds(elapsed(startedAt)), 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);

  return seconds;
}
