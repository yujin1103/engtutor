/** 설정 값을 앱 전체에 흘려보내는 프로바이더. 값과 규칙은 settings.ts 에 있다. */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { Level, StrictnessKey } from "../api/types";
import { SettingsContext, loadSettings, saveSettings } from "./settings";
import type { Settings } from "./settings";

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(loadSettings);

  useEffect(() => saveSettings(state), [state]);

  const setLevel = useCallback((level: Level) => setState((prev) => ({ ...prev, level })), []);
  const setStrictness = useCallback(
    (strictness: StrictnessKey) => setState((prev) => ({ ...prev, strictness })),
    [],
  );

  const value = useMemo<Settings>(
    () => ({ ...state, setLevel, setStrictness }),
    [state, setLevel, setStrictness],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}
