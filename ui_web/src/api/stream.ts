/** `/chat/stream` 의 SSE 를 읽는다. **이 앱이 존재하는 이유가 이 파일이다.**
 *
 * 전에 쓰던 Streamlit 은 브라우저와 웹소켓으로만 그림을 주고받았다. 폰에서 그
 * 소켓이 끊기면 서버는 답을 다 만들어 DB 에 저장까지 해 놓고도 화면이 그대로
 * 멈췄다(`/chat/stream 200 OK`, 턴 273·274 저장됨, 화면은 그대로). 사용자에게는
 * 아무 일도 일어나지 않는 것처럼 보였다.
 *
 * 그래서 여기서는 **조용히 끝나는 경로를 만들지 않는다.** 이 제너레이터는
 * 반드시 `turn` 아니면 `error` 를 마지막으로 내보내고 끝난다. 호출자가
 * 취소한 경우(화면을 떠난 경우)만 예외이고, 그건 사용자가 이미 아는 일이다.
 *
 * **EventSource 를 쓸 수 없다.** EventSource 는 GET 만 되는데 이 엔드포인트는
 * 본문에 JSON 을 담아 보내는 POST 다. 그래서 fetch + ReadableStream 으로
 * 직접 읽는다. 대신 EventSource 가 공짜로 해 주던 재연결은 없다 — 위에서 말한
 * 이유로 **재연결하지 않는 편이 맞다.** 끊긴 사실이 사용자에게 보여야 한다.
 */
import type { ChatRequest, TurnResponse } from "./types";

/** 서버가 보내는 다섯 가지 사건. app/main.py 의 `chat_stream()` 과 1:1 이다. */
export type ChatStreamEvent =
  /** 세션이 새로 만들어졌다. 저장해 뒀다 다음 요청의 `session_id` 로 넣는다. */
  | { type: "session"; session_id: string }
  /** 답이 오는 중. 이어 붙여 보여준다. */
  | { type: "delta"; text: string }
  /** 1차 응답이 스키마 검증에 걸렸다. **여태 보여준 글자를 전부 버리고** 다시 받는다. */
  | { type: "reset" }
  /** 검증이 끝난 최종 턴. 교정·힌트는 **이때만** 그린다. */
  | { type: "turn"; turn: TurnResponse }
  /** 실패. `detail` 은 학습자에게 그대로 보여도 되는 한국어 문장이다. */
  | { type: "error"; detail: string };

export interface ChatStreamOptions {
  /** 화면을 떠날 때 등. 이걸로 취소하면 오류 사건 없이 조용히 끝난다. */
  signal?: AbortSignal;
  /**
   * 이 시간(ms) 동안 **한 바이트도** 안 오면 끊고 오류를 낸다.
   *
   * 폰에서 네트워크가 바뀌면(와이파이 → LTE) 소켓이 죽어도 fetch 는 몇 분씩
   * 열려 있는 것처럼 굴 수 있다. 그 몇 분이 바로 "화면이 멈춘" 시간이다.
   * qwen3:14b 는 첫 글자까지 보통 1~2초, 전체 8초쯤 걸린다. 60초는 그 여덟 배라
   * 정상 응답을 자를 위험은 없고, 죽은 연결은 1분 안에 사용자에게 보인다.
   */
  stallMs?: number;
}

export const DEFAULT_STALL_MS = 60_000;

const MSG_CONNECT = "서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 해 보세요.";
const MSG_DROPPED = "답을 받는 도중에 연결이 끊겼어요. 다시 보내 보세요.";

function stallMessage(ms: number): string {
  return `${Math.max(1, Math.round(ms / 1000))}초 동안 서버가 아무 말이 없어서 연결을 끊었어요. 다시 보내 보세요.`;
}

/** SSE 한 줄을 사건으로 바꾼다. 우리가 아는 다섯 가지가 아니면 버린다. */
function parseLine(line: string): ChatStreamEvent | null {
  const text = line.trimEnd();
  if (!text.startsWith("data:")) return null; // 주석(`:`)·빈 줄·`event:` 등은 무시

  let payload: unknown;
  try {
    payload = JSON.parse(text.slice(5).trim());
  } catch {
    return null; // 깨진 프레임 하나 때문에 대화 전체를 죽이지는 않는다
  }

  const event = payload as { type?: unknown };
  switch (event.type) {
    case "session":
    case "delta":
    case "reset":
    case "turn":
    case "error":
      return payload as ChatStreamEvent;
    default:
      return null;
  }
}

/** 오류 응답(404 시나리오 없음 · 409 끝난 세션 등)에서 한국어 문장을 꺼낸다. */
async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    /* 본문이 JSON 이 아니면 아래로 */
  }
  return `요청이 실패했어요 (${res.status}).`;
}

/**
 * 한 턴을 보내고 사건을 순서대로 돌려준다.
 *
 * ```ts
 * for await (const event of streamChat(req, { signal })) {
 *   if (event.type === "delta") draft += event.text;
 *   if (event.type === "reset") draft = "";        // 여태 그린 글자를 버린다
 *   if (event.type === "turn") show(event.turn);   // 교정·힌트는 여기서만
 *   if (event.type === "error") showError(event.detail);
 * }
 * ```
 *
 * 주의: `error` 를 받았다고 서버가 아무 일도 안 한 것은 아니다. 연결만 끊기고
 * 턴은 이미 저장돼 있을 수 있다. 그대로 다시 보내면 같은 턴이 두 번 쌓인다 —
 * 재전송을 붙일 때 이 점을 생각할 것.
 */
export async function* streamChat(
  req: ChatRequest,
  options: ChatStreamOptions = {},
): AsyncGenerator<ChatStreamEvent, void, void> {
  const stallMs = options.stallMs ?? DEFAULT_STALL_MS;

  // 호출자의 취소와 무응답 감시를 하나로 합친다. fetch 에는 이 컨트롤러만 준다.
  const controller = new AbortController();
  const forwardAbort = () => controller.abort();
  options.signal?.addEventListener("abort", forwardAbort, { once: true });

  let stalled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const armWatchdog = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      stalled = true;
      controller.abort();
    }, stallMs);
  };

  /** 취소인지(사용자가 떠난 것) 진짜 끊긴 것인지 구분한다. */
  const cancelledByCaller = () => options.signal?.aborted === true && !stalled;

  try {
    armWatchdog();

    let res: Response;
    try {
      res = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
        signal: controller.signal,
        cache: "no-store",
      });
    } catch {
      if (cancelledByCaller()) return;
      yield { type: "error", detail: stalled ? stallMessage(stallMs) : MSG_CONNECT };
      return;
    }

    if (!res.ok) {
      yield { type: "error", detail: await readErrorDetail(res) };
      return;
    }
    if (!res.body) {
      // 이 브라우저는 스트리밍 본문을 안 준다. 조용히 멈추느니 이유를 적는다.
      yield { type: "error", detail: "이 브라우저에서는 실시간 응답을 받을 수 없어요." };
      return;
    }

    const reader = res.body.getReader();
    // stream: true — 한글은 3바이트라 청크 경계에서 잘린다. 이거 없으면 글자가 깨진다.
    const decoder = new TextDecoder();
    let buffer = "";
    /** `turn` 이나 `error` 를 이미 내보냈는가. 아니면 아래에서 오류를 만들어 낸다. */
    let settled = false;

    try {
      for (;;) {
        let chunk: ReadableStreamReadResult<Uint8Array>;
        try {
          chunk = await reader.read();
        } catch {
          if (cancelledByCaller()) return;
          settled = true;
          yield { type: "error", detail: stalled ? stallMessage(stallMs) : MSG_DROPPED };
          return;
        }

        if (chunk.done) break;
        armWatchdog(); // 뭔가 왔다. 감시 시계를 다시 감는다.

        buffer += decoder.decode(chunk.value, { stream: true });

        // 서버는 `data: {...}\n\n` 로 보낸다. 줄 단위로 자르고 남은 조각은 다음 청크에 붙인다.
        let cut: number;
        while ((cut = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 1);

          const event = parseLine(line);
          if (!event) continue;
          if (event.type === "turn" || event.type === "error") settled = true;
          yield event;
        }
      }
    } finally {
      // for-await 를 중간에 break 한 경우에도 소켓을 놓아 준다.
      reader.cancel().catch(() => {});
    }

    if (!settled) {
      // 스트림이 `turn` 없이 끝났다. **이게 옛날에 화면이 멈추던 그 상황이다.**
      if (cancelledByCaller()) return;
      yield { type: "error", detail: MSG_DROPPED };
    }
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", forwardAbort);
  }
}
