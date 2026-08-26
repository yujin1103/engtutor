/** 교정 강도 목록(`/strictness`)을 **화면 문구 없이** 손에 넣는 방법.
 *
 * 라벨과 설명은 서버가 준 것만 쓴다. 앱에 같은 문구를 적어 두면 서버 쪽 정의가
 * 바뀌었을 때 둘이 다른 말을 하게 되고, 그건 화면에서만 티가 난다.
 *
 * 보통은 App 이 앱 시작할 때 한 번 받아 둔 것을 `props` 로 내려 준다. 그런데
 * 설정 접이칸은 그 목록을 못 받는 자리(대화 화면은 App 에서 `scenario`·`onBack`·
 * `onFinish` 만 받는다)에도 끼워 넣을 수 있어야 한다. 그래서 props 가 없으면
 * 스스로 한 번 받아 오고, 그 결과를 **모듈 바깥**에 캐시해 어디에 몇 개를 끼워
 * 넣든 요청은 한 번으로 끝낸다.
 */
import { useEffect, useState } from "react";

import { getStrictness } from "../../api/client";
import type { StrictnessOut } from "../../api/types";

let cached: StrictnessOut[] | null = null;
let inflight: Promise<StrictnessOut[]> | null = null;

function fetchOnce(): Promise<StrictnessOut[]> {
  if (cached) return Promise.resolve(cached);
  inflight ??= getStrictness()
    .then((list) => {
      cached = list;
      return list;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/**
 * `given` 이 있으면 그대로 쓰고, 없으면 받아 온다.
 *
 * 실패해도 오류를 그리지 않고 빈 배열을 준다. 교정 강도를 못 받은 것 때문에
 * 대화 화면 전체를 오류로 덮으면 안 되기 때문이다 — 그때는 접이칸의 그 칸만
 * 조용히 빠지고, 값은 이미 저장된 것(기본 balanced)이 그대로 나간다.
 */
export function useStrictnessOptions(given?: StrictnessOut[]): StrictnessOut[] {
  const [fetched, setFetched] = useState<StrictnessOut[]>(() => cached ?? []);

  useEffect(() => {
    if (given) return; // 이미 받은 걸 넘겨받았다. 또 부르지 않는다.

    let alive = true;
    fetchOnce().then(
      (list) => {
        if (alive) setFetched(list);
      },
      () => {
        /* 못 받아도 이 칸만 빠진다. 위 주석 참고. */
      },
    );

    return () => {
      alive = false;
    };
  }, [given]);

  return given ?? fetched;
}
