/** 철자 단서를 **한 걸음씩** 펴는 자리.
 *
 * 왜 필요한가 — 지금 빈칸이 주는 단서는 낱말 뜻·문장 해석·문형·품사뿐이고 넷 다
 * 한국어다. 영어를 아예 모르는 사람은 뜻을 다 알고도 첫 글자를 못 적는다. 그
 * 사람에게 빈칸은 문제가 아니라 벽이고, 벽 앞에서는 연습이 시작되지 않는다.
 *
 * 왜 한 번에 다 펴지 않는가 — 다 펴면 연습이 아니라 베껴 쓰기가 된다. 그래서
 * 누를 때마다 한 걸음씩 열고, 어디까지 볼지는 학습자가 정한다. 서버는 단계를
 * 다 실어 보내되 **마지막 단계까지 봐도 최소 한 글자는 밑줄로 남긴다.**
 *
 * 왜 자동으로 펴지 않는가 — 힌트가 저절로 열리면 문제를 보기 전에 답의 모양이
 * 먼저 눈에 들어온다. 처음 한 번은 문장과 뜻만 보고 겨눠 보게 둔다.
 */
import { useState } from "react";

import type { SpellHintOut } from "../../api/types";
import { hintButtonKo } from "./flow";

import styles from "./practice.module.css";

export interface SpellHintsProps {
  hints: SpellHintOut[];
  /** 문제가 바뀌면 접힌 상태로 돌아가야 한다. 부모가 이 값을 바꿔서 알린다. */
  resetKey: string;
}

export function SpellHints({ hints, resetKey }: SpellHintsProps) {
  // 몇 걸음까지 폈는가. 문제가 바뀌면 렌더 도중에 되돌린다 — 효과에서 되돌리면
  // 앞 문제의 힌트가 새 문장 밑에 한 프레임 붙어 있다가 사라진다.
  const [opened, setOpened] = useState(0);
  const [shown, setShown] = useState(resetKey);
  if (shown !== resetKey) {
    setShown(resetKey);
    setOpened(0);
  }

  // 답이 한 글자면 서버가 빈 배열을 준다 — 글자 수가 곧 정답이라 줄 것이 없다.
  if (hints.length === 0) return null;

  const label = hintButtonKo(opened, hints.length);
  return (
    <div className={styles.spell}>
      {hints.slice(0, opened).map((hint) => (
        <p key={hint.step} className={styles.spellStep}>
          <span className={styles.spellLabel}>{hint.label_ko}</span>
          <span className={styles.spellShape}>{hint.shape}</span>
          <span className={styles.spellText}>{hint.text_ko}</span>
        </p>
      ))}
      {label && (
        <button
          type="button"
          className={`btn ${styles.spellMore}`}
          onClick={() => setOpened(opened + 1)}
        >
          {label}
        </button>
      )}
    </div>
  );
}
