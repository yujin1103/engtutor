/** API 호출. 실패를 **반드시 보이게** 만드는 것이 이 파일의 일이다.
 *
 * 이 앱을 React 로 다시 만든 이유가 그거다. Streamlit 은 폰에서 웹소켓이 끊기면
 * 서버가 답을 다 만들어 DB 에 저장해 놓고도 화면이 그대로 멈춰 있었다. 사용자는
 * 앱이 고장 났는지 자기가 잘못 눌렀는지 알 수 없었다. 그래서 여기서는
 * **모든 실패 경로가 한국어 문장 하나로 끝난다.**
 *
 * 주소에 baseURL 이 없는 이유는 api/paths.ts 에 적어 뒀다.
 */
import type {
  CategoryOut,
  ChatRequest,
  ChatResponse,
  ClozeAnswerOut,
  ClozeAnswerRequest,
  ClozeOut,
  ScenarioOut,
  SessionReport,
  StrictnessOut,
  SttResponse,
  TopicOut,
  ClozeQuery,
} from "./types";

/**
 * 이 파일에서 나가는 유일한 예외 타입.
 *
 * 네트워크가 끊긴 경우까지 여기로 모은다(`status === 0`). 호출하는 쪽이
 * `TypeError` 와 HTTP 오류를 따로 처리하게 만들면, 둘 중 하나는 반드시
 * 빠뜨리고 그게 곧 "조용히 멈추는 화면" 이 된다.
 */
export class ApiError extends Error {
  /** HTTP 상태코드. 0 이면 요청이 서버까지 가지도 못한 것이다. */
  readonly status: number;
  /** 학습자에게 **그대로 보여도 되는** 한국어 문장. */
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`[${status}] ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** 요청이 취소된 것인지(화면을 떠난 것인지) 판별한다. 이건 오류로 그리면 안 된다. */
export function isAborted(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * FastAPI 의 오류 본문에서 사람이 읽을 문장을 뽑는다.
 *
 * 서버는 413(너무 김)·400(오디오 아님)·503(STT 꺼짐)의 `detail` 을 한국어로,
 * "다음에 뭘 하면 되는지"까지 담아서 보낸다. 우리가 다시 쓰지 않고 그대로 쓴다.
 * 422 만 예외로 `detail` 이 배열이라 첫 항목의 `msg` 를 꺼낸다.
 */
async function readDetail(res: Response): Promise<string> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return `서버가 이상한 답을 보냈어요 (${res.status}).`;
  }

  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown; loc?: unknown };
    const where = Array.isArray(first.loc) ? first.loc.join(".") : "";
    const msg = typeof first.msg === "string" ? first.msg : "값이 올바르지 않습니다";
    return `보낸 값이 올바르지 않아요 (${where}: ${msg}).`;
  }

  return `요청이 실패했어요 (${res.status}).`;
}

/** fetch 한 번. 네트워크 예외까지 ApiError 로 바꿔서 내보낸다. */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      // 폰 브라우저가 GET 응답을 캐시해서 옛 시나리오 목록을 보여주는 일이 없게 한다.
      cache: "no-store",
      ...init,
    });
  } catch (error) {
    if (isAborted(error)) throw error; // 취소는 오류가 아니다. 그대로 위로 던진다.
    throw new ApiError(0, "서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 해 보세요.");
  }

  if (!res.ok) throw new ApiError(res.status, await readDetail(res));

  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(res.status, "서버 응답을 읽지 못했어요. 잠시 뒤 다시 해 보세요.");
  }
}

function json(body: unknown, signal?: AbortSignal): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  };
}

// ─────────────────────────────────────────────── 조회

export function getHealth(signal?: AbortSignal): Promise<Record<string, unknown>> {
  return request("/healthz", { signal });
}

export function getScenarios(signal?: AbortSignal): Promise<ScenarioOut[]> {
  return request("/scenarios", { signal });
}

export function getCategories(signal?: AbortSignal): Promise<CategoryOut[]> {
  return request("/categories", { signal });
}

/** 교정 강도의 라벨·설명. 화면 문구를 앱에 하드코딩하지 않으려고 서버에서 받는다. */
export function getStrictness(signal?: AbortSignal): Promise<StrictnessOut[]> {
  return request("/strictness", { signal });
}

// ─────────────────────────────────────────────── 대화

/**
 * 스트리밍 없이 한 번에 받는 경로. 평소 대화는 `streamChat`(api/stream.ts) 을 쓴다 —
 * 왕초보에게 8초 침묵은 '고장' 으로 읽히기 때문이다. 이건 테스트·폴백용으로 둔다.
 */
export function postChat(req: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  return request("/chat", json(req, signal));
}

// ─────────────────────────────────────────────── 음성

/**
 * 녹음 하나를 전사한다.
 *
 * - 필드 이름은 **반드시 `file`** 이다. 서버 계약이고 바꿀 수 없다.
 * - `Content-Type` 을 손으로 넣지 않는다. FormData 를 주면 브라우저가 multipart
 *   경계문자열까지 붙여서 만들어 준다. 직접 넣으면 경계가 빠져 400 이 난다.
 * - 빈 `text` 를 200 으로 받는 일이 흔하다. 마이크만 누르고 말을 안 한 경우다.
 *   오류로 처리하지 말고 "안 들렸어요" 를 그려라.
 * - 파일 이름은 서버 로그용이다. 형식은 내용으로 판별한다(m4a·webm·wav 다 받는다).
 */
export function postStt(
  audio: Blob,
  filename: string,
  signal?: AbortSignal,
): Promise<SttResponse> {
  const form = new FormData();
  form.append("file", audio, filename);
  return request("/stt", { method: "POST", body: form, signal });
}

// ─────────────────────────────────────────────── 리포트

/** 세션을 끝내고 리포트를 만든다. POST 다 — 부르는 순간 LLM 호출이 한 번 일어난다. */
export function getReport(sessionId: string, signal?: AbortSignal): Promise<SessionReport> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/report`, {
    method: "POST",
    signal,
  });
}

// ─────────────────────────────────────────────── 빈칸 채우기

export function getClozeTopics(signal?: AbortSignal): Promise<TopicOut[]> {
  return request("/cloze/topics", { signal });
}

export function getCloze(query: ClozeQuery = {}, signal?: AbortSignal): Promise<ClozeOut[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const qs = params.toString();
  return request(`/cloze${qs ? `?${qs}` : ""}`, { signal });
}

export function postClozeAnswer(
  req: ClozeAnswerRequest,
  signal?: AbortSignal,
): Promise<ClozeAnswerOut> {
  return request("/cloze/answer", json(req, signal));
}
