/** 글자가 흘러올 때 화면을 따라 내려 주는 훅.
 *
 * 스트리밍의 값어치는 “글자가 오는 게 보인다” 인데, 대화가 길어지면 새 말풍선이
 * 접힌 화면 아래에 그려져 결국 안 보인다. 왕초보에게 안 보이는 응답은 없는
 * 응답과 같다 — 그게 우리가 고치려는 바로 그 증상이다.
 *
 * 다만 **무조건 내리면 안 된다.** 앞의 교정을 다시 읽으려고 위로 올려 둔 사람을
 * 아래로 끌어내리면 읽던 자리를 잃는다. 그래서 “바닥에 붙어 있던 사람만” 따라
 * 내린다. 위로 올라간 순간 붙는 것이 풀리고, 다시 바닥까지 내리면 붙는다.
 *
 * 스크롤을 하는 요소는 Screen 의 가운데 칸(`main`)이지만 클래스 이름으로 찾지
 * 않는다. 공용 컴포넌트의 CSS 클래스에 기대면 그쪽을 손볼 때 여기가 조용히
 * 망가진다. 대신 부모를 거슬러 올라가며 **실제로 스크롤되는 요소**를 찾는다.
 */
import { useCallback, useEffect, useRef } from "react";
import type { RefObject } from "react";

/** 바닥에서 이만큼 안쪽까지는 “바닥에 있다” 로 친다(px). 손가락 스크롤은 정확히 멈추지 않는다. */
const BOTTOM_SLACK = 80;

function findScroller(node: HTMLElement | null): HTMLElement | null {
  for (let el = node?.parentElement ?? null; el; el = el.parentElement) {
    const overflow = getComputedStyle(el).overflowY;
    if (overflow === "auto" || overflow === "scroll") return el;
  }
  return null;
}

export interface StickToBottom {
  /** 대화 목록의 맨 끝에 두는 빈 div 에 건다. */
  endRef: RefObject<HTMLDivElement | null>;
  /** 붙어 있든 말든 지금 당장 바닥으로 내린다. 실패 알림처럼 놓치면 안 되는 것에만 쓴다. */
  scrollToBottom: () => void;
}

/**
 * @param deps 이 값들이 바뀔 때마다 (붙어 있다면) 바닥으로 따라 내린다.
 *             글자가 한 조각 올 때마다 바뀌는 값을 넣으면 된다.
 */
export function useStickToBottom(deps: readonly unknown[]): StickToBottom {
  const endRef = useRef<HTMLDivElement | null>(null);
  const stuckRef = useRef(true);

  const scrollToBottom = useCallback(() => {
    const scroller = findScroller(endRef.current);
    if (!scroller) return;
    stuckRef.current = true;
    scroller.scrollTop = scroller.scrollHeight;
  }, []);

  useEffect(() => {
    const scroller = findScroller(endRef.current);
    if (!scroller) return;

    const onScroll = () => {
      const gap = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      stuckRef.current = gap <= BOTTOM_SLACK;
    };
    // passive — 스크롤을 막을 일이 없고, 폰에서 스크롤이 끊겨 보이지 않게 한다.
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!stuckRef.current) return;
    const scroller = findScroller(endRef.current);
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
    // deps 는 호출하는 쪽이 정한다 — 이 훅은 그 값이 뭔지 알 필요가 없다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { endRef, scrollToBottom };
}
