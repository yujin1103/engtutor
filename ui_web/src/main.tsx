import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { SettingsProvider } from "./state/SettingsProvider";
import "./styles/global.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root 를 찾지 못했습니다. index.html 을 확인하세요.");

createRoot(root).render(
  <StrictMode>
    {/* 레벨·교정 강도는 화면 둘이 같이 보는 값이라 제일 바깥에 둔다. */}
    <SettingsProvider>
      <App />
    </SettingsProvider>
  </StrictMode>,
);
