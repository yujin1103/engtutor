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
        {/* '위' 를 붙이는 것이 이 화면의 유일한 설명이다.
         *
         * 숫자만 찍으면 학습자는 그것을 '목록의 몇 번째 카드' 로 읽는데, 실제 값은
         * 토익 낱말 전체에서 몇째로 자주 나오는지다. 그래서 첫 화면이 3, 4, 6, 8 로
         * 시작하고 첫 30장 안에서만 열두 번 끊긴다(같은 낱말이 다른 목록에도 있어
         * 자리를 비워 둔 것이 575개, 생활 회화 쪽으로 간 것이 159개다).
         *
         * 설명을 title 에 담아 두었더니 **폰에서는 아예 안 떴다** — 이 앱의 주
         * 화면이 폰이다. 담은 낱말 탭에도 같은 카드를 쓰는데 거기엔 안내 문장이
         * 아예 없어서 더 그렇다. '3위' 는 목록 안 번호로는 읽히지 않으므로
         * 툴팁 없이, 두 탭 모두에서, 터치에서도 통한다. */}
        {card.rank !== null && (
          <span className={styles.rank} title="토익에서 몇째로 자주 나오는지">
            {card.rank}위
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
