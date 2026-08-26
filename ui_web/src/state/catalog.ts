/** 앱을 열자마자 한 번 받아 오는 목록들 — 시나리오 33개, 분류 6개, 교정 강도 3개.
 *
 * 화면마다 따로 부르지 않고 앱이 시작할 때 한 번만 받는다. 이유가 둘이다.
 *
 *  1. 셋 다 서버에서 거의 안 변한다(YAML 파일과 상수). 화면을 옮길 때마다
 *     다시 받으면 폰의 느린 회선에서 전환이 눈에 띄게 끊긴다.
 *  2. **API 가 죽었을 때 화면 한 곳에서만 실패하게 하려고.** 화면 넷이 각자
 *     실패를 그리면 어딘가는 반드시 빠뜨리고, 그게 "조용히 멈춘 화면" 이 된다.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, getCategories, getScenarios, getStrictness, isAborted } from "../api/client";
import type { CategoryOut, ScenarioOut, StrictnessOut } from "../api/types";

export interface Catalog {
  scenarios: ScenarioOut[];
  /** id 로 바로 찾기. 대화·리포트 화면이 시나리오 하나만 필요할 때 쓴다. */
  byId: Map<string, ScenarioOut>;
  categories: CategoryOut[];
  strictness: StrictnessOut[];
}

export type CatalogState =
  | { status: "loading" }
  | { status: "ready"; catalog: Catalog }
  | { status: "failed"; detail: string };

export function useCatalog(): { state: CatalogState; retry: () => void } {
  const [state, setState] = useState<CatalogState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    (async () => {
      try {
        // 셋을 동시에 받는다. 순서대로 받을 이유가 없고, 폰에서는 왕복 한 번이 곧 지연이다.
        const [scenarios, categories, strictness] = await Promise.all([
          getScenarios(controller.signal),
          getCategories(controller.signal),
          getStrictness(controller.signal),
        ]);
        setState({
          status: "ready",
          catalog: {
            scenarios,
            byId: new Map(scenarios.map((s) => [s.id, s])),
            categories,
            strictness,
          },
        });
      } catch (error) {
        if (isAborted(error)) return; // 화면을 떠난 것. 오류가 아니다.
        setState({
          status: "failed",
          detail:
            error instanceof ApiError
              ? error.detail
              : "목록을 받아 오지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      }
    })();

    return () => controller.abort();
  }, [attempt]);

  // 다시 받기는 "누른 순간" 시작한다. 화면을 loading 으로 되돌리는 것도 여기서 한다 —
  // 효과 안에서 setState 를 부르면 렌더가 한 번 더 도는 것을 React 가 경고한다.
  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);
  return { state, retry };
}
