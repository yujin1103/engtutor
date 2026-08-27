/** 문법 문제의 판단 규칙만 모은 곳. React 도 fetch 도 없다 — 브라우저 없이 시험할 수 있게.
 *
 * 연습장의 `practice/flow.ts` 와 같은 자리이고, 여기 모으는 기준도 같다 —
 * **화면을 눈으로 훑어서는 지켜졌는지 알기 어려운 것**만 둔다. 이 화면에서
 * 그런 것은 둘이다.
 *
 *  1. `markOf` — 맞힌 보기를 빨갛게 칠하지 않는가. 정답을 고르면 `answer` 와
 *     `chosen` 이 **같은 낱말**이라, 고른 것을 먼저 보면 정답이 오답 색으로
 *     칠해진다. 맞혔을 때만 나는 어긋남이라 오답을 눌러 보는 동안에는 안 보인다.
 *  2. `wrapsAround` — 마지막 장이 딱 떨어졌을 때 연습이 끝나 버리지 않는가.
 *     문제 수가 한 장 크기의 배수일 때만 빈 장을 받게 되는데, 지금 151개라
 *     한 장을 20개로 두면 안 걸리고 25개로 바꾸는 순간 걸린다.
 *
 * 보기 **차례**는 여기서도 저기서도 손대지 않는다. 서버가 문제마다 고정된
 * 자리바꿈으로 굳혀 보내므로 화면에 섞는 함수가 아예 없는 것이 계약이다
 * (연습장의 `shuffled` 에 해당하는 것이 여기 없는 이유).
 */
import type { GrammarAnswerOut } from "../../api/types";

/**
 * 빈칸 표시와 그것을 기준으로 문장을 가르는 함수.
 *
 * 연습장 것을 그대로 쓴다. 서버에서도 `app/tutor/grammar.py` 가 빈칸 문제와
 * **같은 상수**(`app/tutor/slot.py` 의 `BLANK`)를 가져다 쓰므로, 화면에서 둘로
 * 갈라 적으면 언젠가 한쪽만 고쳐진다. 그때 증상은 문장에 밑줄 네 개가 글자
 * 그대로 찍히는 것이다.
 *
 * 확장자를 붙인 이유: 이 줄은 **값**을 가져오는 유일한 화면 간 import 라
 * 타입만 지우고 끝나지 않는다. `npm test` 는 번들러 없이 `src/` 를 그대로
 * 실행하는데(node --test), 노드의 ESM 해석기는 확장자를 지어내지 않는다.
 */
export { BLANK, splitBlank } from "../practice/flow.ts";

/**
 * 한 번에 받아 오는 문제 수. 서버 상한은 50 이다.
 *
 * 연습장(30)보다 작게 둔 이유는 문제 수가 적어서다 — 지금 규칙 하나에 151개라,
 * 한 장을 크게 잡을수록 처음 받는 데만 오래 걸리고 어차피 몇 장 못 넘긴다.
 */
export const PAGE = 20;

/**
 * 다음에 받아 올 자리.
 *
 * 한 장을 다 풀면 다음 장으로 가고, **마지막 장이었으면 처음으로 되돌아간다.**
 * 연습에는 끝나는 자리가 없어야 한다. 서버가 요청한 수보다 적게 주면 그게
 * 마지막 장이라는 뜻이라, 빈 장을 한 번 더 받아 보지 않아도 안다.
 */
export function nextOffset(offset: number, got: number, page: number = PAGE): number {
  return got < page ? 0 : offset + got;
}

/**
 * 받아 온 장이 비었다. 처음으로 되돌아가야 하는가.
 *
 * `nextOffset` 이 못 잡는 한 경우가 있다 — 문제 수가 한 장 크기로 **딱 나누어
 * 떨어지면** 마지막 장이 꽉 차서 오고(`got === page`), 그래서 그 다음 장을
 * 한 번 더 받는데 그게 빈 배열이다. 그대로 두면 스무 문제를 푼 사람 앞에
 * "문제가 없어요" 가 뜬다.
 *
 * 그래서 빈 장은 두 가지로 갈라 읽는다 — 처음부터 비었으면 정말 문제가 없는
 * 것이고(모르는 규칙 이름도 여기로 온다), 풀다가 비었으면 한 바퀴 돈 것이다.
 */
export function wrapsAround(offset: number, got: number): boolean {
  return got === 0 && offset > 0;
}

// ─────────────────────────────────────────────── 판정 뒤의 표시

/** 판정 뒤 보기 하나에 붙는 표시. 판정 전에는 넷 다 `plain` 이다. */
export type Mark = "answer" | "wrong" | "plain";

/**
 * 이 보기를 어떻게 그릴지. **정답 검사가 먼저다.**
 *
 * 맞히면 `answer` 와 `chosen` 이 같은 낱말로 온다. 고른 것을 먼저 보면 그 줄이
 * `wrong` 이 되어, 맞힌 사람에게 빨간 줄과 "맞았어요" 가 함께 뜬다. 순서 한 줄
 * 차이인데 맞혔을 때만 나서 오답을 눌러 보는 동안에는 끝까지 안 보인다.
 *
 * 고른 값은 **서버가 돌려준 `chosen`** 으로 본다. 화면이 누른 글자를 들고 있다가
 * 쓰면, 서버가 다듬은 값과 어긋나는 날 화면과 채점이 다른 말을 한다.
 */
export function markOf(word: string, result: GrammarAnswerOut | null): Mark {
  if (!result) return "plain";
  const value = word.trim();
  if (value === result.answer.trim()) return "answer";
  if (value === result.chosen.trim()) return "wrong";
  return "plain";
}

/**
 * 표시에 붙는 한국어 꼬리표. **색만으로 말하지 않으려고** 있다.
 *
 * 정답 줄과 내가 고른 줄을 테두리 색으로만 갈라 놓으면 색을 못 가르는 사람에게는
 * 아무 표시가 없는 화면이 된다. `null` 이면 꼬리표를 안 그린다.
 */
export function markLabelKo(mark: Mark): string | null {
  switch (mark) {
    case "answer":
      return "정답";
    case "wrong":
      return "내가 고른 것";
    default:
      return null;
  }
}

/** 보기 앞에 붙는 번호. 토익 Part 5 가 넷이다. */
export const MARKERS = ["①", "②", "③", "④"] as const;

/**
 * 몇 번째 보기인지. 서버가 다섯째를 보내도 ① 이 두 번 나오지 않게 한다 —
 * 지금은 넷이 계약이지만, 어긋났을 때 화면이 조용히 거짓을 그리는 쪽이 나쁘다.
 */
export function markerOf(index: number): string {
  return MARKERS[index] ?? `${index + 1}.`;
}
