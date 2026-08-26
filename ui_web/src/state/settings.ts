/** 레벨과 교정 강도. 화면 둘(대화·설정)이 같이 보는 값이라 한 곳에 둔다.
 *
 * 서버가 갖고 있지 않은 값이다 — 요청마다 실어 보낸다. 그래서 앱을 껐다 켜도
 * 남아 있어야 한다(`localStorage`). 폰에서는 브라우저가 탭을 아무 때나 버리기
 * 때문에, 메모리에만 두면 대화하다 돌아왔을 때 설정이 초기화된다.
 *
 * 상태관리 라이브러리는 쓰지 않는다. 값이 둘뿐이고 바뀌는 일이 드물어
 * Context 하나로 충분하다.
 *
 * 프로바이더만 SettingsProvider.tsx 로 갈라 뒀다 — 한 파일에서 컴포넌트와
 * 그 밖의 것을 같이 내보내면 HMR 이 그 파일을 통째로 다시 얹어 상태가 날아간다.
 */
import { createContext, useContext } from "react";

import type { Level, StrictnessKey } from "../api/types";

/** 왕초보용 앱이다. 기본은 가장 낮은 레벨에서 시작한다. */
export const DEFAULT_LEVEL: Level = "A1";
export const DEFAULT_STRICTNESS: StrictnessKey = "balanced";

export interface Settings {
  level: Level;
  strictness: StrictnessKey;
  setLevel: (level: Level) => void;
  setStrictness: (strictness: StrictnessKey) => void;
}

export const SettingsContext = createContext<Settings | null>(null);

export const STORE_KEY = "engtutor.settings.v1";

const LEVELS: readonly Level[] = ["A1", "A2", "B1"];
const STRICTNESS: readonly StrictnessKey[] = ["gentle", "balanced", "strict"];

export interface StoredSettings {
  level: Level;
  strictness: StrictnessKey;
}

/** 저장된 값을 읽는다. 없거나 이상하면 기본값. */
export function loadSettings(): StoredSettings {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    const stored = (raw ? JSON.parse(raw) : {}) as { level?: unknown; strictness?: unknown };
    return {
      level: LEVELS.includes(stored.level as Level) ? (stored.level as Level) : DEFAULT_LEVEL,
      strictness: STRICTNESS.includes(stored.strictness as StrictnessKey)
        ? (stored.strictness as StrictnessKey)
        : DEFAULT_STRICTNESS,
    };
  } catch {
    // 사생활 보호 모드에서는 localStorage 를 읽기만 해도 예외가 난다.
    // 설정 하나 때문에 앱이 안 뜨면 안 되므로 기본값으로 계속 간다.
    return { level: DEFAULT_LEVEL, strictness: DEFAULT_STRICTNESS };
  }
}

export function saveSettings(settings: StoredSettings): void {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(settings));
  } catch {
    /* 저장 못 해도 이번 세션은 그대로 쓴다 */
  }
}

export function useSettings(): Settings {
  const value = useContext(SettingsContext);
  if (!value) throw new Error("useSettings 는 SettingsProvider 안에서만 쓸 수 있어요.");
  return value;
}
