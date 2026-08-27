/** 문제를 끊기지 않게 대 주는 일만 한다. 채점은 여기 없다.
 *
 * 뼈대는 연습장의 `practice/useDeck.ts` 와 같다 — 한 장(`PAGE` 개)을 미리 받아
 * 두고 다 풀었을 때만 다음 장을 받는다. 한 문제마다 서버에 다녀오면 폰의 느린
 * 회선에서 답을 낼 때마다 빈 화면을 한 번씩 보게 되고, 그러면 연습장이 아니라
 * 퀴즈 한 판이 된다. 받아 온 것에 **어느 요청의 결과인지**(`nonce`)를 붙여
 * 들고 있어서, 늦게 도착한 옛 응답이 새 장을 덮어쓰지 않는다.
 *
 * 연습장과 다른 것 하나 — **받아 온 문제를 섞지 않는다.** 연습장은 빈도 순으로
 * 오는 것을 장 안에서 섞지만, 여기서는 서버가 문장 틀을 **돌아가며** 낸 차례로
 * 준다(`app/tutor/grammar.py` 의 `items_of`). 그 차례가 곧 "같은 문장을 열 번
 * 연달아 보지 않게" 한 것이라, 화면이 다시 섞으면 서버가 해 둔 일이 풀린다.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, getGrammar, isAborted } from "../../api/client";
import type { GrammarOut } from "../../api/types";
import { PAGE, nextOffset, wrapsAround } from "./flow";

export type GrammarDeckState =
  | { status: "loading" }
  | { status: "failed"; detail: string }
  /** 이 규칙으로 낼 수 있는 문제가 하나도 없다. 모르는 규칙 이름도 여기로 온다. */
  | { status: "empty" }
  | { status: "ready"; item: GrammarOut; seen: number };

export interface GrammarDeck {
  state: GrammarDeckState;
  /** 다음 문제로. 장을 다 풀었으면 다음 장을 받아 온다. */
  next: () => void;
  /** 받아 오기가 실패했을 때 같은 자리를 다시. */
  retry: () => void;
}

/**
 * 몇 장째를 받을지.
 *
 * `nonce` 는 **같은 자리를 다시 받게** 하려고 있다 — 한 바퀴 돌아 offset 0 으로
 * 되돌아가면 offset 만으로는 값이 안 바뀌어 효과가 다시 돌지 않는다. 요청마다
 * 하나씩 올라가므로 응답의 짝을 맞추는 데도 쓴다.
 */
interface Request {
  offset: number;
  nonce: number;
}

interface Page {
  nonce: number;
  items: GrammarOut[];
}

interface Failure {
  nonce: number;
  detail: string;
}

export function useGrammarDeck(rule?: string): GrammarDeck {
  const [request, setRequest] = useState<Request>({ offset: 0, nonce: 0 });
  const [page, setPage] = useState<Page | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [cursor, setCursor] = useState(0);
  /** 지금까지 만난 문제 수. 몇 번째를 풀고 있는지 보여주려고 센다. */
  const [seen, setSeen] = useState(1);

  const { offset, nonce } = request;

  useEffect(() => {
    const controller = new AbortController();

    getGrammar(
      {
        // 안 주면 서버 기본값이다. 화면이 규칙 이름을 지어내지 않는 이유는
        // api/types.ts 의 `GrammarQuery` 에 적어 뒀다.
        rule,
        count: PAGE,
        offset,
      },
      controller.signal,
    ).then(
      (items) => {
        if (wrapsAround(offset, items.length)) {
          // 문제 수가 한 장 크기로 딱 나누어떨어져 빈 장을 받은 것이다.
          // "문제가 없어요" 가 아니라 한 바퀴 돈 것이라 처음으로 되돌린다.
          setRequest((prev) => ({ offset: 0, nonce: prev.nonce + 1 }));
          return;
        }
        setPage({ nonce, items });
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
  }, [rule, offset, nonce]);

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

  const state: GrammarDeckState = broke
    ? { status: "failed", detail: broke.detail }
    : !fresh
      ? { status: "loading" }
      : fresh.items.length === 0
        ? { status: "empty" }
        : { status: "ready", item: fresh.items[cursor], seen };

  return { state, next, retry };
}
