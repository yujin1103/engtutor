import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 프록시로 넘길 경로를 앱 코드와 **같은 파일**에서 읽는다. 두 곳에 나눠 적으면
// 엔드포인트를 하나 추가했을 때 개발에서만 404 가 나고, 원인을 찾는 데 오래 걸린다.
// (확장자를 붙인 이유: 이 파일은 nodenext 모듈 해석으로 검사돼 확장자가 필요하다)
import { API_PREFIXES } from "./src/api/paths.ts";

/**
 * 개발에서도 **한 오리진**으로 만든다.
 *
 * 운영에서는 FastAPI 가 빌드 결과를 `/` 로 서빙하므로 앱과 API 가 같은 출처다.
 * 개발에서 `http://api:8000` 을 직접 부르면 그때만 CORS 를 뚫어야 하고, 그렇게
 * 맞춘 코드는 운영에서 한 번 더 손봐야 한다. 그래서 dev 서버가 `/scenarios`
 * 같은 요청만 골라 api 컨테이너로 넘긴다 — 클라이언트 코드에는 baseURL 이 없고,
 * 개발·운영에서 글자 하나 다르지 않다.
 *
 * `/chat/stream` 은 SSE 다. Vite 의 프록시는 응답을 그대로 흘려보내므로 따로
 * 버퍼링을 끌 것이 없다(FastAPI 쪽에서 이미 `X-Accel-Buffering: no` 를 붙여 준다).
 */
export default defineConfig({
  plugins: [react()],
  server: {
    // 컨테이너 밖(호스트 브라우저)에서 들어와야 하므로 루프백에만 묶으면 안 된다.
    host: "0.0.0.0",
    port: 5173,
    // 포트가 밀려서 5174 로 뜨면 compose 의 포트 매핑이 헛돈다. 차라리 실패하게 둔다.
    strictPort: true,

    // NTFS -> WSL2 경계에서는 inotify 이벤트가 컨테이너까지 넘어오지 않는다.
    // 폴링을 켜지 않으면 파일을 고쳐도 HMR 이 돌지 않는다.
    // (api 서비스의 WATCHFILES_FORCE_POLLING 과 같은 이유다)
    watch:
      process.env.VITE_WATCH_POLLING === "true"
        ? { usePolling: true, interval: 400 }
        : undefined,

    proxy: Object.fromEntries(
      API_PREFIXES.map((prefix) => [
        prefix,
        {
          // 컨테이너 안에서는 compose 서비스명으로 부른다. 호스트에서 직접
          // `npm run dev` 를 돌린다면 VITE_API_PROXY_TARGET=http://localhost:8000.
          target: process.env.VITE_API_PROXY_TARGET ?? "http://api:8000",
          changeOrigin: true,
        },
      ]),
    ),
  },
});
