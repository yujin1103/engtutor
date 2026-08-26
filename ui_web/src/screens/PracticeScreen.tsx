/** 단어 연습장. 대화와 나란한 두 번째 갈래다.
 *
 * 여기 있는 것은 겹 두 개뿐이다 — 무엇을 풀지 고르고(`DeckPicker`), 고른 것을
 * 연달아 푼다(`PracticeRun`). 그 사이를 브라우저 방문 기록에 쌓지 않고 이 화면의
 * 내부 상태로만 둔다. 고르기 한 걸음까지 기록에 넣으면, 스무 문제를 풀고 뒤로
 * 갔을 때 고르기 화면을 한 번 더 지나가야 앱을 벗어난다(대화 쪽 `PickerScreen`
 * 과 같은 판단이다).
 *
 * 장면 목록은 앱 시작 때 받는 `catalog` 에 넣지 않고 여기서 받는다. 연습장을 한
 * 번도 안 여는 사람에게까지 첫 화면이 뜨는 시간을 늘릴 이유가 없다.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, getClozeTopics, isAborted } from "../api/client";
import type { TopicOut } from "../api/types";
import { ErrorNotice, Loading } from "../components/Notice";
import { Screen } from "../components/Screen";
import { DeckPicker } from "./practice/DeckPicker";
import { PracticeRun } from "./practice/PracticeRun";

const TITLE = "단어 연습장";

/** 지금 풀고 있는 것. `topic` 이 null 이면 전체 어휘다. */
interface Chosen {
  topic: string | null;
  label: string;
}

type TopicsState =
  | { status: "loading" }
  | { status: "ready"; topics: TopicOut[] }
  | { status: "failed"; detail: string };

export function PracticeScreen({ onBack }: { onBack: () => void }) {
  const [state, setState] = useState<TopicsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [chosen, setChosen] = useState<Chosen | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getClozeTopics(controller.signal).then(
      (topics) => setState({ status: "ready", topics }),
      (error: unknown) => {
        if (isAborted(error)) return; // 화면을 떠난 것. 오류가 아니다.
        setState({
          status: "failed",
          detail:
            error instanceof ApiError
              ? error.detail
              : "장면 목록을 받아 오지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      },
    );
    return () => controller.abort();
  }, [attempt]);

  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  if (chosen) {
    return (
      <PracticeRun
        // 팩을 바꾸는 건 이어 푸는 게 아니라 다른 연습을 시작하는 것이다.
        // key 로 문제 대기줄을 통째로 새로 세운다.
        key={chosen.topic ?? "*"}
        topic={chosen.topic}
        label={chosen.label}
        onBack={() => setChosen(null)}
      />
    );
  }

  return (
    <Screen title={TITLE} onBack={onBack}>
      {state.status === "loading" && <Loading label="장면을 불러오는 중이에요" />}
      {state.status === "failed" && <ErrorNotice detail={state.detail} onRetry={retry} />}
      {state.status === "ready" && (
        <DeckPicker
          topics={state.topics}
          onPick={(topic, label) => setChosen({ topic, label })}
        />
      )}
    </Screen>
  );
}
