/** 토익 화면이 폰에 남겨 두는 것 — 어디까지 봤나, 무엇을 외웠나, 무엇을 담았나.
 *
 * **왜 서버가 아니라 localStorage 인가.** 이 앱은 지금 한 링크를 여러 사람에게
 * 나눠 주고 시연 중이다. 서버에 진도를 저장하면 그 사람들의 표시가 한 통에 섞인다
 * (계정을 만드는 건 CLAUDE.md 가 막아 둔 길이다). 폰에 두면 각자 자기 것만 본다.
 * 대신 브라우저를 지우면 사라지는데, 이 화면에서 잃는 것은 표시뿐이라 감당할 만하다.
 *
 * **담는 것은 표제어뿐이다.** 카드 내용(뜻·예문·해석)은 복사해 두지 않는다.
 * 복사해 두면 나중에 뜻을 고쳐도 그 사람의 단어장에는 옛 뜻이 영영 남는다 —
 * `squid` 의 뜻이 '감자전' 이던 시절에 담아 둔 카드가 딱 그 꼴이 된다.
 * 그래서 단어장을 열 때마다 `GET /words?words=…` 로 지금 내용을 다시 읽는다.
 *
 * React 를 부르지 않는 순수 함수만 둔다. 화면 없이 시험할 수 있어야 해서다
 * (연습장의 `practice/flow.ts` 와 같은 자리다).
 */
import type { WordCardOut } from "../../api/types";

export const STORE_KEY = "engtutor.toeic.v1";

/** 단어장 상한. 서버의 `MAX_SAVED_WORDS` 와 같은 값이어야 한 번에 다 받아 온다. */
export const MAX_SAVED = 200;

export interface Marks {
  /**
   * 마지막으로 받아 온 장의 자리. 다시 열면 여기서 **이어 받는다.**
   *
   * 목록의 몇 번째 낱말인지가 아니라 서버에게 줄 `offset` 이다. 안전 판정에
   * 걸린 행이 중간에서 빠지므로 둘은 어긋나고, 이어 받는 데 필요한 건 이쪽이다.
   */
  offset: number;
  /** 외웠다고 표시한 낱말. **목록에서 빼지 않는다** — 다시 만나야 진짜 외운 것이 된다. */
  known: string[];
  /** 단어장에 담은 낱말. */
  saved: string[];
}

export const EMPTY: Marks = { offset: 0, known: [], saved: [] };

/** 문자열 배열만 남긴다. 저장된 값이 손상돼도 화면이 죽지 않아야 한다. */
function words(value: unknown, cap: number): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    const word = item.trim().toLowerCase();
    if (word && !out.includes(word)) out.push(word);
    if (out.length === cap) break;
  }
  return out;
}

/** 저장된 표시를 읽는다. 없거나 이상하면 빈 표시. */
export function loadMarks(): Marks {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    const stored = (raw ? JSON.parse(raw) : {}) as Record<string, unknown>;
    const offset = stored.offset;
    return {
      offset: typeof offset === "number" && offset >= 0 ? Math.floor(offset) : 0,
      known: words(stored.known, Number.MAX_SAFE_INTEGER),
      saved: words(stored.saved, MAX_SAVED),
    };
  } catch {
    // 사생활 보호 모드에서는 읽기만 해도 예외가 난다. 표시 없이 계속 간다.
    return EMPTY;
  }
}

export function saveMarks(marks: Marks): void {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(marks));
  } catch {
    /* 저장 못 해도 이번 세션은 그대로 쓴다 */
  }
}

/**
 * 있으면 빼고 없으면 넣는다. 새 배열을 돌려준다(React 가 바뀐 걸 알아야 한다).
 *
 * `cap` 을 넘으면 **가장 오래된 것을 밀어낸다.** 담기를 막고 "가득 찼어요" 를
 * 띄우는 쪽도 생각했는데, 단어장은 쌓아 두는 곳이 아니라 지금 외우는 것을 담는
 * 곳이라 새로 담은 쪽을 남기는 편이 맞다.
 */
export function toggle(list: string[], word: string, cap = Number.MAX_SAFE_INTEGER): string[] {
  const key = word.trim().toLowerCase();
  if (!key) return list;
  if (list.includes(key)) return list.filter((w) => w !== key);
  const next = [...list, key];
  return next.length > cap ? next.slice(next.length - cap) : next;
}

/** 이 카드가 외운 것으로 표시돼 있나. 표시일 뿐 목록에서 빼지 않는다. */
export function isMarked(list: string[], card: WordCardOut): boolean {
  return list.includes(card.word.trim().toLowerCase());
}
