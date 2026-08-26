/** 연습장의 판단 규칙만 모은 곳. React 도 fetch 도 없다 — 브라우저 없이 시험할 수 있게.
 *
 * 여기 있는 것들은 **화면을 보고는 지켜졌는지 알기 어려운** 규칙들이다. 특히
 * `opensExplanation` 이 그렇다. 서버가 `not_a_word` 에 "다시 말해 볼까요?" 라고
 * 적어 놓고도 설명 카드는 **함께 보낸다**(응답을 한 번에 만드는 편이 왕복 두 번보다
 * 낫다는 서버 쪽 결정이다). 화면이 그걸 그대로 펼치면, 다시 해 보라고 해 놓고
 * 바로 아래에 답을 적어 두는 꼴이 된다. 그 어긋남은 눈으로 훑어서는 안 보이고
 * 오타를 한 번 내 봐야 보인다. 그래서 규칙으로 못박고 시험한다.
 */
import type { AlternativeOut, ClozeExplainOut, Verdict } from "../../api/types";

/** 서버가 빈칸을 뚫을 때 쓰는 표시. `app/tutor/slot.py` 의 `BLANK` 와 같아야 한다. */
export const BLANK = "____";

/** 한 번에 받아 오는 문제 수. 서버 상한이 50 이다. */
export const PAGE = 30;

/**
 * 빈칸을 기준으로 문장을 앞뒤로 가른다. 빈칸이 없으면 `null` —
 * 그때는 문장을 그냥 통째로 그린다(빈칸 없는 문장도 읽을 수는 있다).
 *
 * 첫 번째 빈칸만 본다. `He is ____ tall as his father.` 처럼 서버가 두 번째
 * 등장을 남겨 두는 문장이 하나 있는데, 그것도 빈칸은 하나뿐이다.
 */
export function splitBlank(sentence: string): { before: string; after: string } | null {
  const at = sentence.indexOf(BLANK);
  if (at < 0) return null;
  return { before: sentence.slice(0, at), after: sentence.slice(at + BLANK.length) };
}

/**
 * 설명 카드를 펼쳐도 되는가.
 *
 * `empty`·`not_a_word` 는 서버가 "다시 말해 볼까요?" 로 끝내는 판정이다. 아직
 * 답을 겨눠 보지도 못한 상태라 여기서 정답을 펴 버리면 연습 한 번이 통째로
 * 사라진다. 대신 화면이 '답 보기' 를 따로 두어 **학습자가 원할 때** 편다.
 */
export function opensExplanation(verdict: Verdict): boolean {
  return verdict !== "empty" && verdict !== "not_a_word";
}

/**
 * 판정을 색 넷으로 줄인다. **색까지만 한다** — 문구는 서버의 `message_ko` 뿐이다.
 *
 * `close` 가 따로 있는 이유: 형태만 틀렸거나(`cookies`) 품사는 맞은(`soap`) 답은
 * 틀린 게 아니라 **거의 온 것**이다. 이 앱은 채점이 아니라 가르치는 자리라
 * 그 둘을 빨간색으로 칠하면 학습자가 배운 것을 못 알아본다.
 */
export type Tone = "right" | "close" | "miss" | "again";

export function toneOf(verdict: Verdict): Tone {
  switch (verdict) {
    case "correct":
      return "right";
    case "wrong_form":
    case "right_pos":
      return "close";
    case "empty":
    case "not_a_word":
      return "again";
    default:
      return "miss";
  }
}

/**
 * 문제 순서를 섞는다.
 *
 * 서버는 **빈도 순**으로 준다. 그 순서에는 이유가 있어서(열 문제만 풀고 그만둬도
 * 그 열 개가 가장 자주 쓰는 낱말이어야 한다) 한 장(`PAGE` 개) 안에서만 섞는다 —
 * 장은 그대로 빈도 순으로 넘어가고 장 안의 순서만 바뀐다. 안 섞으면 앱을 열
 * 때마다 같은 문제를 같은 차례로 만나 두 번째부터는 외운 것을 되뇌게 된다.
 *
 * 난수를 인자로 받는 이유는 시험 때문이다. `Math.random` 을 안에서 부르면
 * "정말 전부 남아 있는가"(하나도 안 잃고 안 겹치는가)를 확인할 수 없다.
 */
export function shuffled<T>(items: readonly T[], random: () => number = Math.random): T[] {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * 다음에 받아 올 자리.
 *
 * 한 장을 다 풀면 다음 장으로 넘어가고, **마지막 장이었으면 처음으로 되돌아간다.**
 * 연습장은 끝나는 자리가 없어야 한다 — 낱말이 셋뿐인 팩(`잡담`)도 있는데, 거기서
 * "문제가 없어요" 로 막히면 그 팩은 세 문제짜리 앱이 된다. 서버가 요청한 수보다
 * 적게 주면 그게 마지막 장이라는 뜻이라, 빈 장을 한 번 더 받아 보지 않아도 안다.
 */
export function nextOffset(offset: number, got: number, page: number = PAGE): number {
  return got < page ? 0 : offset + got;
}

// ─────────────────────────────────────────────── 확인된 것과 아직 아닌 것

/**
 * 미검수 값을 승인된 값과 **가르는** 자리. 화면이 아니라 여기서 가른다.
 *
 * 서버는 `reviewed` 를 후보 하나하나에까지 실어 보낸다. 그런데 설명 카드가 그
 * 칸을 한 번도 읽지 않고 후보 여섯의 뜻을 승인된 `쓰임` 과 **똑같은 상자**에
 * 그리고 있었다. 그래서 `straw` 를 "줄기" 라고 가르쳤다 — 빨대인데. 표본 300장에
 * 뜬 후보 1,680개 중 1,678개가 미검수였으니 어쩌다 한 번이 아니라 늘 그랬다.
 *
 * 서버 쪽 설계 문장이 "플래그 하나로 두면 언젠가 누군가 그 플래그를 안 본다"
 * 였는데, 후보 목록에서는 그 말대로 됐다. 그래서 여기서는 플래그를 **칸 이름**
 * 으로 바꾼다. 확인 안 된 글자는 `unchecked_ko` 에만 담기므로 승인된 줄을 그리는
 * 컴포넌트에 넘길 수가 없다 — 넘기면 컴파일이 먼저 막는다. 안 보고 지나갈 수가
 * 없다는 게 요점이다. 안 보면 글자 자체가 손에 안 잡힌다.
 *
 * 숨기지는 않는다. 3,245개 중 승인이 4개라 숨기면 기능이 통째로 빈다. 자리와
 * 모양을 가르고, 무엇인지 한국어로 말해 준다.
 */

/** 미검수 뜻에 붙이는 말. **겁주지 않는다** — 틀렸다는 게 아니라 아직 안 봤다는 것이다. */
export const UNCHECKED_MEANING_KO = "아직 사람이 확인하지 않은 뜻이에요.";

/** 미검수 후보 목록에 붙이는 말. 낱말이 아니라 **뜻**이 확인 전이라는 뜻이다. */
export const UNCHECKED_WORDS_KO = "아래 뜻은 아직 사람이 확인하지 않았어요.";

/**
 * 미검수 설명에 붙이는 말. 서버가 `unverified.note_ko` 로 같은 문구를 보내 주고
 * 평소에는 그걸 그대로 쓴다 — 이건 상자가 서버 실수로 문구 없이 왔을 때만 쓴다.
 */
export const UNCHECKED_NOTE_KO = "아직 사람이 확인하지 않은 설명이에요. 참고만 하세요.";

/** 사람이 확인한 낱말 한 줄. **`meaning_ko` 라는 칸은 확인된 쪽에만 있다.** */
export interface CheckedWord {
  word: string;
  pos_ko: string[];
  meaning_ko: string;
}

/** 아직 확인하지 않은 낱말 한 줄. 글자가 `unchecked_ko` 에 들어 있어 자리를 못 바꾼다. */
export interface UncheckedWord {
  word: string;
  pos_ko: string[];
  unchecked_ko: string;
}

/**
 * 후보 목록을 확인 여부로 가른다. 각 무리 안의 차례는 서버가 준 그대로 둔다 —
 * 서버가 정답 낱말로 씨앗을 고정해 섞어 둔 것이라 여기서 다시 흔들면 같은
 * 문제가 볼 때마다 다른 목록이 된다.
 */
export function splitWords(words: readonly AlternativeOut[]): {
  checked: CheckedWord[];
  unchecked: UncheckedWord[];
} {
  const checked: CheckedWord[] = [];
  const unchecked: UncheckedWord[] = [];
  for (const alt of words) {
    if (alt.reviewed) {
      checked.push({ word: alt.word, pos_ko: alt.pos_ko, meaning_ko: alt.meaning_ko });
    } else {
      unchecked.push({ word: alt.word, pos_ko: alt.pos_ko, unchecked_ko: alt.meaning_ko });
    }
  }
  return { checked, unchecked };
}

/**
 * 표제어의 대표 뜻. 후보들과 같은 기준을 받는다.
 *
 * 카드에서 제일 크게 읽히는 한 줄인데 이것도 배치 LLM 이 쓴 값이다 —
 * `bagel` 의 뜻이 `백일(백面包)` 로 저장돼 있는 걸 실제로 화면에서 봤다.
 * 확인된 것처럼 보이지만 않으면 되므로 지우지 않고 자리만 옮긴다.
 */
export type Meaning =
  | { checked: true; meaning_ko: string }
  | { checked: false; unchecked_ko: string };

export function meaningOf(card: Pick<ClozeExplainOut, "reviewed" | "meaning_ko">): Meaning {
  return card.reviewed
    ? { checked: true, meaning_ko: card.meaning_ko }
    : { checked: false, unchecked_ko: card.meaning_ko };
}

/** 사람이 검수한 설명. 이 모양이 된 것만 승인된 자리에 그린다. */
export interface CheckedNotes {
  usage_note: string | null;
  confused_with: string[];
}

/** 아직 확인하지 않은 설명. 여기도 칸 이름이 다르다. */
export interface UncheckedNotes {
  unchecked_note: string | null;
  unchecked_confused: string[];
  /** 상자에 함께 띄울 한 문장. 서버 것을 그대로 쓰고, 없을 때만 우리 문구다. */
  note_ko: string;
}

function trimmed(text: string | null | undefined): string | null {
  const out = (text ?? "").trim();
  return out.length > 0 ? out : null;
}

/**
 * 쓰임 설명을 확인 여부로 가른다.
 *
 * 서버가 이미 갈라서 보내는데 화면에서 또 가르는 이유: 확인된 환각 13건이 전부
 * 이 두 칸에 있었다. 서버가 언젠가 `reviewed=false` 인 행의 `usage_note` 를
 * 승인 칸에 실어 보내면 화면은 그걸 검수된 설명으로 그린다. 그래서 여기서는
 * **`reviewed` 가 참일 때만** 승인 칸을 승인된 자리로 보내고, 아니면 미검수
 * 상자로 옮긴다. 두 겹이 다 뚫려야 거짓이 통과한다.
 */
export function splitNotes(card: ClozeExplainOut): {
  checked: CheckedNotes | null;
  unchecked: UncheckedNotes | null;
} {
  const note = trimmed(card.usage_note);
  const confused = card.confused_with.filter((word) => word.trim().length > 0);
  const box = card.unverified ?? null;

  const checked =
    card.reviewed && (note || confused.length > 0)
      ? { usage_note: note, confused_with: confused }
      : null;

  // 승인 전이면 승인 칸의 값도 이쪽으로 넘어온다. 둘 다 채워져 오는 일은 서버에
  // 없지만, 그때는 서버가 스스로 갈라 담은 상자 쪽을 믿는다.
  const boxNote = trimmed(box?.usage_note);
  const boxConfused = (box?.confused_with ?? []).filter((word) => word.trim().length > 0);
  const uncheckedNote = boxNote ?? (card.reviewed ? null : note);
  const uncheckedConfused = card.reviewed
    ? boxConfused
    : [...new Set([...boxConfused, ...confused])];

  const unchecked =
    uncheckedNote || uncheckedConfused.length > 0
      ? {
          unchecked_note: uncheckedNote,
          unchecked_confused: uncheckedConfused,
          note_ko: trimmed(box?.note_ko) ?? UNCHECKED_NOTE_KO,
        }
      : null;

  return { checked, unchecked };
}
