/** 문제를 끊기지 않게 대 주는 일만 한다. 채점은 여기 없다.
 *
 * **연달아 풀 수 있어야 한다**가 이 훅의 존재 이유다. 한 문제마다 서버에 다녀오면
 * 폰의 느린 회선에서 답을 낼 때마다 빈 화면을 한 번씩 보게 되고, 그러면 연습장이
 * 아니라 퀴즈 한 판이 된다. 그래서 한 장(`PAGE` 개)을 미리 받아 두고 다 풀었을
 * 때만 다음 장을 받는다.
 *
 * 장면(topic)이 바뀌는 경우는 여기서 다루지 않는다. 화면이 `key` 로 이 훅을 통째로
 * 새로 세운다 — 팩을 바꾸는 건 다른 연습을 시작하는 것이지 이어 푸는 게 아니다.
 *
 * 받아 온 것에 **어느 요청의 결과인지**(`nonce`)를 붙여 들고 있는다. 그래야
 * "지금 기다리는 중" 을 효과 안에서 `setState` 로 만들지 않고 렌더에서 계산할 수
 * 있고, 늦게 도착한 옛 응답이 새 장을 덮어쓰는 일도 없다.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, getCloze, isAborted } from "../../api/client";
import type { ClozeOut } from "../../api/types";
import { PAGE, nextOffset, shuffled } from "./flow";

export type DeckState =
  | { status: "loading" }
  | { status: "failed"; detail: string }
  /** 이 팩에서 낼 수 있는 문제가 하나도 없다. 팩이 아주 작으면 실제로 일어난다. */
  | { status: "empty" }
  | { status: "ready"; item: ClozeOut; seen: number };

export interface Deck {
  state: DeckState;
  /** 다음 문제로. 장을 다 풀었으면 다음 장을 받아 온다. */
  next: () => void;
  /** 받아 오기가 실패했을 때 같은 자리를 다시. */
  retry: () => void;
}

/**
 * 몇 장째를 받을지.
 *
 * `nonce` 는 **같은 자리를 다시 받게** 하려고 있다 — 팩이 한 장보다 짧으면 다 푼
 * 뒤 돌아갈 곳이 offset 0, 곧 지금 있는 자리라 offset 만으로는 값이 안 바뀌어
 * 효과가 다시 돌지 않는다. 요청마다 하나씩 올라가므로 응답의 짝을 맞추는 데도 쓴다.
 */
interface Request {
  offset: number;
  nonce: number;
}

interface Page {
  nonce: number;
  items: ClozeOut[];
}

interface Failure {
  nonce: number;
  detail: string;
}

export function useDeck(topic: string | null, track?: string): Deck {
  const [request, setRequest] = useState<Request>({ offset: 0, nonce: 0 });
  const [page, setPage] = useState<Page | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [cursor, setCursor] = useState(0);
  /** 지금까지 만난 문제 수. 몇 번째를 풀고 있는지 보여주려고 센다. */
  const [seen, setSeen] = useState(1);

  const { offset, nonce } = request;

  useEffect(() => {
    const controller = new AbortController();

    getCloze(
      {
        // 장면을 안 골랐으면 전체 어휘다.
        topic: topic ?? undefined,
        // **빈 문자열이 "레벨로 안 가른다"** 다. 팩은 그 자리에서 쓰는 말을 모은
        // 것이지 난이도로 묶은 것이 아니라, A1 으로 자르면 카페 60개가 8개로
        // 줄어 연습이 성립하지 않는다. (app/main.py `list_cloze` 에 적혀 있다)
        level: "",
        // 기능어 빈칸을 뺀다. `I like coffee ____ tea.` 의 답은 `and` 인데,
        // 그 자리에는 품사 힌트도 설명도 붙일 게 없어서 연습장이 할 말이 없다.
        speech: true,
        // 안 주면 서버 기본값인 생활 회화 트랙이다. 토익 화면에서 들어올 때만
        // 'toeic' 이 실린다 — 기본값이 안전장치라 여기서 지어내지 않는다.
        track,
        count: PAGE,
        offset,
      },
      controller.signal,
    ).then(
      (items) => {
        // 빈도 순 그대로 두면 앱을 열 때마다 같은 차례를 만난다. 장 안에서만 섞는다.
        setPage({ nonce, items: shuffled(items) });
        setCursor(0);
      },
      (error: unknown) => {
        if (isAborted(error)) return; // 화면을 떠난 것. 오류가 아니다.
        setFailure({
          nonce,
          detail:
            error instanceof ApiError
              ? error.detail
              : "문제를 받아 오지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      },
    );

    return () => controller.abort();
  }, [topic, track, offset, nonce]);

  // 지금 요청의 결과만 본다. 짝이 안 맞으면 아직 기다리는 중이다.
  const fresh = page && page.nonce === nonce ? page : null;
  const broke = failure && failure.nonce === nonce ? failure : null;

  const next = useCallback(() => {
    if (!fresh) return;
    setSeen((n) => n + 1);
    if (cursor + 1 < fresh.items.length) {
      setCursor(cursor + 1);
      return;
    }
    setRequest((prev) => ({
      offset: nextOffset(prev.offset, fresh.items.length),
      nonce: prev.nonce + 1,
    }));
  }, [fresh, cursor]);

  const retry = useCallback(() => {
    setRequest((prev) => ({ ...prev, nonce: prev.nonce + 1 }));
  }, []);

  const state: DeckState = broke
    ? { status: "failed", detail: broke.detail }
    : !fresh
      ? { status: "loading" }
      : fresh.items.length === 0
        ? { status: "empty" }
        : { status: "ready", item: fresh.items[cursor], seen };

  return { state, next, retry };
}
