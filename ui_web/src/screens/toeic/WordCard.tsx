/** 낱말 한 장. 읽는 카드라 가리는 것이 없다.
 *
 * 위에 붙는 것은 CEFR 레벨이 아니라 **빈도 순위**다. 토익 어휘는 난이도로 묶은
 * 목록이 아니라 자주 나오는 차례로 줄 세운 목록이라, 같은 화면에 A1/B1 딱지가
 * 붙으면 학습자가 그걸 순서로 읽는다. 축이 둘이면 둘 다 안 읽힌다.
 *
 * 외움 표시는 **표시일 뿐 목록에서 빼지 않는다.** 한 번 맞혔다고 아는 낱말이
 * 되지는 않고, 다시 만나야 진짜 외운 것이 된다. 그래서 지운 것처럼 보이게 하지
 * 않고 카드를 옅게만 만든다.
 */
import type { WordCardOut } from "../../api/types";

import styles from "./toeic.module.css";

export interface WordCardProps {
  card: WordCardOut;
  known: boolean;
  saved: boolean;
  onToggleKnown: (word: string) => void;
  onToggleSaved: (word: string) => void;
}

export function WordCard({ card, known, saved, onToggleKnown, onToggleSaved }: WordCardProps) {
  return (
    <article className={`${styles.card} ${known ? styles.knownCard : ""}`}>
      <header className={styles.head}>
        {card.rank !== null && (
          <span className={styles.rank} title="자주 쓰이는 차례">
            {card.rank}
          </span>
        )}
        <h2 className={styles.word}>{card.word}</h2>
        <div className={styles.marks}>
          <button
            type="button"
            className={`${styles.mark} ${saved ? styles.markOn : ""}`}
            aria-pressed={saved}
            onClick={() => onToggleSaved(card.word)}
          >
            {saved ? "★ 담음" : "☆ 단어장"}
          </button>
          <button
            type="button"
            className={`${styles.mark} ${known ? styles.markOn : ""}`}
            aria-pressed={known}
            onClick={() => onToggleKnown(card.word)}
          >
            {known ? "✓ 외움" : "외움"}
          </button>
        </div>
      </header>

      <p className={styles.meaning}>{card.meaning_ko}</p>

      <p className={styles.example}>{card.example}</p>
      {/* 해석은 2,252개 중 2,128개만 채워져 있다. 없으면 안 보여줄 뿐 지어내지 않는다. */}
      {card.example_ko && <p className={styles.exampleKo}>{card.example_ko}</p>}
    </article>
  );
}
