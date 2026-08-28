/** 화면 전환. 라우터 라이브러리를 넣지 않은 이유와 그 대신 한 일.
 *
 * 화면이 일곱뿐이고(고르기·대화·리포트·연습장·토익·문법 문제·설정) 주소로 들어올 일이 없다 —
 * 혼자 쓰는 앱이라 링크를 공유하지 않는다. react-router 를 넣으면 코드보다
 * 설정이 많아진다.
 *
 * 다만 **폰의 뒤로 가기는 꼭 살려야 한다.** 안드로이드에서 뒤로 스와이프했는데
 * 앱이 통째로 닫히면 사용자는 대화를 잃는다. 그래서 화면을 바꿀 때
 * `history.pushState` 로 방문 기록을 한 칸 쌓고 `popstate` 로 되돌린다.
 * 주소창은 그대로 두고 상태만 태운다 — 새로고침하면 처음 화면으로 돌아간다.
 */
import { useCallback, useEffect, useState } from "react";

export type Route =
  | { name: "picker" }
  | { name: "chat"; scenarioId: string }
  /** 대화를 끝내고 보는 화면. 어느 세션인지 알아야 리포트를 만들 수 있다. */
  | { name: "report"; sessionId: string; scenarioId: string }
  /**
   * 단어 연습장. 대화와 나란한 두 번째 갈래라 첫 화면에서 바로 들어온다.
   *
   * `track` 이 있으면 장면 고르기를 건너뛰고 그 트랙을 바로 푼다 — 토익 화면에서
   * "빈칸으로 연습" 을 누른 경우다. 없으면 지금까지대로 장면부터 고른다.
   */
  | { name: "practice"; track?: string }
  /** 토익 낱말을 빈도 순으로 훑는 화면. 연습장과 달리 읽는 자리다. */
  | { name: "toeic" }
  /**
   * 토익 Part 5 형 4지선다 문법 문제. 어느 규칙을 풀지는 여기 담지 않는다 —
   * 규칙 목록을 주는 엔드포인트가 아직 없어서 서버 기본값을 그대로 받는다.
   */
  | { name: "grammar" }
  | { name: "settings" };

export const HOME: Route = { name: "picker" };

/** history.state 에 우리 값을 담을 때 쓰는 열쇠. 다른 스크립트와 섞이지 않게 이름을 준다. */
const KEY = "engtutorRoute";

function routeOf(state: unknown): Route | null {
  const value = (state as Record<string, unknown> | null)?.[KEY];
  if (!value || typeof value !== "object") return null;
  const name = (value as { name?: unknown }).name;
  if (
    name === "picker" ||
    name === "chat" ||
    name === "report" ||
    name === "practice" ||
    name === "toeic" ||
    // 이 줄을 빠뜨리면 타입은 통과하는데 뒤로가기가 조용히 첫 화면으로 떨어진다.
    // 위의 유니온과 **여기 둘 다** 고쳐야 한 화면이 산다.
    name === "grammar" ||
    name === "settings"
  ) {
    return value as Route;
  }
  return null;
}

export interface Nav {
  route: Route;
  /** 새 화면으로 들어간다. 방문 기록이 한 칸 쌓인다. */
  go: (route: Route) => void;
  /** 화면을 갈아 끼운다. 기록이 쌓이지 않는다 — 대화 → 리포트처럼 되돌아가면 안 되는 이동에 쓴다. */
  replace: (route: Route) => void;
  /** 브라우저 뒤로 가기와 같다. 폰의 뒤로 가기 제스처와 한 몸이 된다. */
  back: () => void;
}

export function useNav(): Nav {
  const [route, setRoute] = useState<Route>(() => routeOf(window.history.state) ?? HOME);

  useEffect(() => {
    // 첫 진입 기록에도 우리 값을 심어 둔다. 안 그러면 한 번 들어갔다 뒤로 왔을 때
    // state 가 비어 있어 어디로 돌아가야 할지 모른다.
    if (routeOf(window.history.state) === null) {
      window.history.replaceState({ ...window.history.state, [KEY]: HOME }, "");
    }

    const onPop = (event: PopStateEvent) => setRoute(routeOf(event.state) ?? HOME);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((next: Route) => {
    window.history.pushState({ [KEY]: next }, "");
    setRoute(next);
  }, []);

  const replace = useCallback((next: Route) => {
    window.history.replaceState({ [KEY]: next }, "");
    setRoute(next);
  }, []);

  const back = useCallback(() => window.history.back(), []);

  return { route, go, replace, back };
}
