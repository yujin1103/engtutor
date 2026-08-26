/** 입력창 바로 위의 “지금 말할 수 있는 것” 바.
 *
 * **얼어붙는 사건이 실제로 일어나는 좌표가 화면 맨 아래다.** 뭘 말해야 할지
 * 모르는 순간은 입력칸에 커서를 둔 채로 오는데, 힌트를 말풍선 안에 넣어 두면
 * 대화가 길어질수록 위로 밀려 사라진다. 그래서 힌트는 스크롤되지 않는 자리,
 * 손가락 바로 위에 붙여 둔다.
 *
 * **영어는 기본으로 접혀 있다.** 먼저 스스로 해 보고 막히면 여는 순서다.
 * 처음부터 펴 두면 그대로 베껴 보내는 게 기본 동작이 되어 연습이 안 된다.
 * 접어 두면 “열었는가” 자체가 막혔다는 신호로 남는 이점도 있다.
 *
 * 펼친 영어를 **눌러서 입력칸에 넣는 기능은 일부러 넣지 않았다.** 한 번 누르면
 * 보내지는 길을 만들어 두면 왕초보는 매 턴 그 길로만 간다. 읽고 직접 치는
 * 만큼이 연습이다.
 */
import { useState } from "react";

import type { TurnResponse } from "../../api/types";

import styles from "./SayBar.module.css";

/** 모델이 헤매도 바닥이 사라지지 않게 UI 상수로 보장하는 한 마디. */
const FALLBACK_LINE = "Sorry, I don't understand.";

export interface SayBarProps {
  turn: TurnResponse;
}

/**
 * 새 턴이 오면 영어는 **다시 접혀야 한다.** 한 번 열었다고 계속 열려 있으면
 * 다음 턴부터는 스스로 해 볼 기회가 없어진다.
 *
 * 그 되돌리기를 효과(useEffect)로 하지 않고 부르는 쪽에서 `key={turn.id}` 로
 * 처리한다. 효과로 하면 이미 펼친 채로 한 번 그린 뒤에 접히는 렌더가 한 번 더
 * 돌고, 그 사이 한 프레임에 지난 턴의 답이 스친다.
 */
export function SayBar({ turn }: SayBarProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className={styles.bar}>
      <div className={styles.head}>
        <p className={styles.hint}>💡 {turn.hint_ko}</p>
        {!open && (
          <button
            type="button"
            className={styles.reveal}
            onClick={() => setOpen(true)}
            // 왕초보에게 “힌트” 는 눌러도 되는 것처럼 들린다. 막혔을 때만 열라고 적어 둔다.
            title="막혔을 때만 눌러보세요"
          >
            🔤 답 보기
          </button>
        )}
      </div>

      {open && (
        <div className={styles.lines}>
          <p className={styles.say}>{turn.say_en}</p>
          {turn.say_more && turn.say_more !== turn.say_en && (
            <p className={styles.sayMore}>{turn.say_more}</p>
          )}
          <p className={styles.fallback}>{FALLBACK_LINE}</p>
          <p className={styles.fallbackNote}>
            무슨 말인지 모르겠으면 이 한 마디면 돼요. 상대가 다시 쉽게 말해줘요.
          </p>
        </div>
      )}
    </div>
  );
}
