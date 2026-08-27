/** 낱말 목록을 장 단위로 이어 받는다. 표시(외움·단어장)는 여기 없다.
 *
 * 연습장의 `useDeck` 과 성격이 반대다. 저쪽은 한 번에 한 문제를 보여주려고 미리
 * 받아 두는 대기줄이고, 이쪽은 **받은 것을 쌓아 죽 보여준다.** 그래서 다음 장을
 * 자동으로 당겨오지 않는다 — "더 보기" 를 누를 때만 한 장 더 받는다. 스크롤로
 * 무한히 당겨오면 2,102개를 읽는 동안 사용자가 자기가 어디쯤인지 잃는다.
 *
 * 다음 자리(`next_offset`)는 **서버가 정해 준다.** 안전 판정에 걸린 행이 중간에서
 * 빠지므로 `offset + items.length` 로 계산하면 그만큼씩 앞으로 밀린다.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, getWords, isAborted } from "../../api/client";
import type { WordCardOut } from "../../api/types";

/** 한 장에 몇 개. 서버 상한은 60 이다. */
export const PAGE = 30;

export type ListState =
  | { status: "loading" }
  | { status: "failed"; detail: string }
  | {
      status: "ready";
      cards: WordCardOut[];
      total: number;
      /** 마지막 장까지 왔다. */
      done: boolean;
      /** 처음이 아니라 **적어 둔 자리에서 이어 받았다.** 화면이 "처음부터" 를 띄운다. */
      resumed: boolean;
      /** 지금 한 장을 더 받는 중. 버튼을 두 번 누르지 못하게 한다. */
      busy: boolean;
    };

export interface WordList {
  state: ListState;
  /** 다음 장을 이어 받는다. 마지막 장이면 아무 일도 하지 않는다. */
  more: () => void;
  /** 맨 앞으로. 이어 받기가 마음에 안 들 때 쓴다. */
  restart: () => void;
  retry: () => void;
  /** 지금까지 받아 온 마지막 자리. 화면이 이걸 폰에 적어 두고 다음에 이어 받는다. */
  offset: number;
}

interface Request {
  offset: number;
  /** 같은 자리를 다시 받게 하려고 둔다(재시도). 응답 짝 맞추기에도 쓴다. */
  nonce: number;
  /** 이 응답을 쌓을 것인가, 갈아 끼울 것인가. 처음 열 때와 재시도는 갈아 끼운다. */
  append: boolean;
}

/**
 * @param track 어느 트랙을 볼지. 서버 기본값은 생활 회화라 토익 화면은 반드시 준다.
 * @param start 처음 받아올 자리. 폰에 적어 둔 진도가 여기로 들어온다.
 */
export function useWordList(track: string, start = 0): WordList {
  const [request, setRequest] = useState<Request>({ offset: start, nonce: 0, append: false });
  const [cards, setCards] = useState<WordCardOut[]>([]);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [failure, setFailure] = useState<{ nonce: number; detail: string } | null>(null);
  /** 응답이 도착한 요청의 번호. 렌더에서 "기다리는 중" 을 계산하려고 둔다. */
  const [arrived, setArrived] = useState(-1);
  /**
   * 이어 받기 시작한 자리. 목록이 그 앞을 건너뛰었다는 걸 화면이 알아야 한다.
   *
   * ref 가 아니라 state 다 — 렌더에서 읽는 값이라 ref 에 두면 화면이 안 따라온다.
   * "처음부터" 를 누르면 0 으로 되돌린다.
   */
  const [from, setFrom] = useState(start);

  const { offset, nonce, append } = request;

  useEffect(() => {
    const controller = new AbortController();
    getWords({ track, offset, count: PAGE }, controller.signal).then(
      (page) => {
        setCards((prev) => (append ? [...prev, ...page.items] : page.items));
        setTotal(page.total);
        setNext(page.next_offset);
        setArrived(nonce);
      },
      (error: unknown) => {
        if (isAborted(error)) return; // 화면을 떠난 것. 오류가 아니다.
        setFailure({
          nonce,
          detail:
            error instanceof ApiError
              ? error.detail
              : "낱말을 받아 오지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      },
    );
    return () => controller.abort();
  }, [track, offset, nonce, append]);

  const broke = failure && failure.nonce === nonce ? failure : null;
  const waiting = arrived !== nonce;

  const more = useCallback(() => {
    if (next === null) return;
    setRequest({ offset: next, nonce: nonce + 1, append: true });
  }, [next, nonce]);

  const restart = useCallback(() => {
    setFrom(0);
    setFailure(null);
    setRequest((prev) => ({ offset: 0, nonce: prev.nonce + 1, append: false }));
  }, []);

  const retry = useCallback(() => {
    setFailure(null);
    setRequest((prev) => ({ ...prev, nonce: prev.nonce + 1 }));
  }, []);

  const state: ListState = broke
    ? { status: "failed", detail: broke.detail }
    : waiting && cards.length === 0
      ? { status: "loading" }
      : {
          status: "ready",
          cards,
          total,
          done: next === null,
          resumed: from > 0,
          busy: waiting,
        };

  return { state, more, restart, retry, offset: next ?? offset };
}

export type BookState =
  | { status: "loading" }
  | { status: "failed"; detail: string }
  | { status: "ready"; cards: WordCardOut[] };

/**
 * 단어장. 담아 둔 표제어로 **지금 내용**을 다시 읽어 온다.
 *
 * 카드를 폰에 복사해 두지 않는 이유는 `marks.ts` 에 적어 뒀다 — 한 줄로 줄이면,
 * 뜻을 고쳐도 옛 뜻이 남지 않게 하려는 것이다.
 *
 * 담은 것이 없으면 요청을 보내지 않는다. 빈 목록을 물어보는 왕복이 없어야 폰에서
 * 단어장 탭이 즉시 열린다.
 */
export function useWordbook(track: string, saved: string[]): BookState {
  const key = saved.join(",");
  // 받아 온 것에 **어느 요청의 결과인지**를 붙여 둔다. 그래야 "기다리는 중" 을
  // 효과 안에서 setState 로 만들지 않고 렌더에서 계산할 수 있다(useDeck 과 같은 수).
  const [page, setPage] = useState<{ key: string; cards: WordCardOut[] } | null>(null);
  const [failure, setFailure] = useState<{ key: string; detail: string } | null>(null);

  useEffect(() => {
    if (!key) return; // 담은 것이 없으면 물어볼 것도 없다.
    const controller = new AbortController();
    getWords({ track, words: key }, controller.signal).then(
      (received) => setPage({ key, cards: received.items }),
      (error: unknown) => {
        if (isAborted(error)) return;
        setFailure({
          key,
          detail:
            error instanceof ApiError
              ? error.detail
              : "단어장을 받아 오지 못했어요. 잠시 뒤 다시 해 보세요.",
        });
      },
    );
    return () => controller.abort();
  }, [track, key]);

  if (!key) return { status: "ready", cards: [] };
  if (failure && failure.key === key) return { status: "failed", detail: failure.detail };
  if (page && page.key === key) return { status: "ready", cards: page.cards };
  return { status: "loading" };
}
