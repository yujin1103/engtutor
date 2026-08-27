/** API 가 차지하고 있는 경로 목록.
 *
 * **왜 한 곳에 모으나.** 이 앱은 개발과 운영에서 API 를 **같은 주소로** 부른다.
 *
 *   - 운영: FastAPI 가 빌드 결과를 `/` 로 서빙한다. `/scenarios` 는 그냥 FastAPI 다.
 *   - 개발: Vite dev 서버가 `/` 를 잡고 있으므로, 아래 경로로 들어온 요청만
 *     `api:8000` 으로 넘긴다(vite.config.ts 의 proxy).
 *
 * 그래서 클라이언트 코드에는 baseURL 이 없다. `fetch("/scenarios")` 한 줄이
 * 양쪽에서 그대로 돈다. 환경변수로 주소를 갈아 끼우는 방식은 쓰지 않았다 —
 * 그러면 개발에서만 나는 CORS·쿠키 문제를 운영에서 다시 만나게 된다.
 *
 * vite.config.ts 가 **이 파일을 직접 import** 한다. 새 엔드포인트를 추가하면
 * 여기에만 적으면 dev 프록시까지 따라온다. 두 곳에 나눠 적으면 언젠가 어긋나고,
 * 그때 증상은 "개발에서만 404" 라 원인을 찾기 어렵다.
 */
export const API_PREFIXES = [
  "/healthz",
  "/scenarios",
  "/categories",
  "/strictness",
  "/chat", // /chat 과 /chat/stream 둘 다 — 프록시는 접두사로 맞춘다
  "/stt",
  "/cloze", // /cloze 와 /cloze/topics, /cloze/answer
  "/words", // 읽기용 낱말 목록 (토익 화면)
  "/grammar", // /grammar 와 /grammar/answer (토익 문법 문제)
  "/sessions", // /sessions/{id}/report
  // 스키마를 눈으로 확인할 때 쓴다. 운영에서도 FastAPI 가 같은 자리에 준다.
  "/openapi.json",
  "/docs",
] as const;
