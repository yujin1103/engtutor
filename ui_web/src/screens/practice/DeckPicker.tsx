/** 무엇을 연습할지 고르는 첫 겹. 장면(topic)이 곧 팩이다.
 *
 * 낱말 3,245개를 한 줄로 늘어놓으면 왕초보는 어디서 시작할지 모른다. 그래서
 * 대화 쪽이 분류를 먼저 고르게 하는 것과 같은 구조를 쓴다 — 장면을 먼저 고르고
 * 그 안을 푼다. 카페 대화 직전에 카페 낱말을 푸는 게 빈도 상위 열 개를 푸는
 * 것보다 그 대화에 실제로 도움이 된다.
 *
 * 큰 팩을 위에 둔다. 서버는 영어 이름 순으로 주는데(airport, cafe, daily …)
 * 그 차례는 학습자에게 아무 뜻이 없고, 낱말이 셋뿐인 팩이 맨 위에 오면 첫
 * 연습이 세 문제 만에 한 바퀴 돈다.
 */
import type { TopicOut } from "../../api/types";

import styles from "./practice.module.css";

export interface DeckPickerProps {
  topics: TopicOut[];
  /** `null` 이면 장면을 안 고른 것 — 전체 어휘. */
  onPick: (topic: string | null, label: string) => void;
}

/** 장면을 안 고르고 그냥 푸는 갈래의 이름. 화면 위에도 이 이름이 올라간다. */
export const ALL_LABEL = "전체 낱말";

export function DeckPicker({ topics, onPick }: DeckPickerProps) {
  const packs = topics.slice().sort((a, b) => b.total - a.total);

  return (
    <>
      <p className={`muted ${styles.lead}`}>
        빈칸에 들어갈 말을 써 보고, 왜 그런지까지 같이 봐요. 낱말 하나여도 되고
        구여도 돼요.
      </p>

      <button type="button" className={styles.all} onClick={() => onPick(null, ALL_LABEL)}>
        <span className={styles.allTitle}>{ALL_LABEL}</span>
        <span className="muted">자주 쓰는 낱말부터 나와요</span>
      </button>

      <div className={styles.topics}>
        {packs.map((pack) => (
          <button
            key={pack.topic}
            type="button"
            className={styles.topic}
            onClick={() => onPick(pack.topic, pack.label_ko)}
          >
            {/* 서버가 준 이름이다. 화면이 {"cafe": "카페"} 표를 따로 들고 있으면
                팩을 하나 더 만들 때 한쪽을 빠뜨리게 된다. */}
            <span className={styles.topicLabel}>{pack.label_ko}</span>
            <span className={styles.count}>낱말 {pack.total}개</span>
          </button>
        ))}
      </div>
    </>
  );
}
