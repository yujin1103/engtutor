/** 보기 넷. **footer 가 아니라 본문에 있다.**
 *
 * 연습장은 답을 타자로 쳐서 입력칸이 footer(엄지 근처)에 있지만, 여기서 고르는
 * 일은 문장을 읽는 일과 떨어질 수 없다 — `to ____ the invoice` 를 보면서
 * `send`·`sending`·`sender`·`sent` 를 견주는 것이 이 문제의 전부다. 보기를
 * 아래에 고정해 두면 문장과 보기가 화면에서 갈라지고, 그러면 문장을 안 읽고
 * 보기만 보게 된다.
 *
 * **차례를 섞지 않는다.** 서버가 문제 id 를 씨앗으로 굳혀 보낸 순서 그대로다.
 * 여기서 섞으면 같은 문제를 다시 열 때마다 답의 자리가 옮겨 다닌다.
 */
import type { GrammarAnswerOut, GrammarChoiceOut } from "../../api/types";
import { markLabelKo, markOf, markerOf } from "./flow";

import styles from "./grammar.module.css";

export interface ChoiceListProps {
  choices: GrammarChoiceOut[];
  /** 판정 결과. `null` 이면 아직 답을 안 낸 것이라 넷이 똑같이 보인다. */
  result: GrammarAnswerOut | null;
  /** 채점을 기다리는 동안 눌린 보기. 누른 것이 눈에 남아 있어야 두 번 안 누른다. */
  pending: string | null;
  /** 답을 냈거나 채점 중이면 못 누른다. */
  locked: boolean;
  onPick: (word: string) => void;
}

export function ChoiceList({ choices, result, pending, locked, onPick }: ChoiceListProps) {
  return (
    <div className={styles.choices}>
      {choices.map((choice, index) => {
        const mark = markOf(choice.word, result);
        const label = markLabelKo(mark);
        return (
          <button
            key={choice.word}
            type="button"
            className={`${styles.choice} ${styles[mark]} ${
              pending === choice.word ? styles.picking : ""
            }`}
            disabled={locked}
            onClick={() => onPick(choice.word)}
          >
            <span className={styles.marker} aria-hidden="true">
              {markerOf(index)}
            </span>
            <span className={styles.choiceWord}>{choice.word}</span>
            {/* 색만으로 말하지 않는다. 꼬리표 문구는 flow.markLabelKo 에 있다. */}
            {label && <span className={styles.choiceTag}>{label}</span>}
          </button>
        );
      })}
    </div>
  );
}
