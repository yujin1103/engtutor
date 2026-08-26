/** 다 받아 온 리포트를 그리는 곳. 이 앱이 단순 챗봇이 아닌 이유가 이 화면이다.
 *
 * 순서를 이렇게 잡은 이유:
 *
 *  1. **칭찬 먼저**(`summary_ko`). 틀린 문장 목록으로 화면을 열면 왕초보는
 *     스크롤을 내리기 전에 앱을 닫는다.
 *  2. **반복된 실수**(`patterns_ko`). 한 번 틀린 것보다 여러 번 틀린 것이
 *     다음에 고칠 것이다. 그래서 낱개 목록보다 위에 둔다.
 *  3. **고쳐 볼 문장**과 **더 자연스럽게** 를 **갈라서** 보여준다.
 *     서버는 `mistakes` 배열에 두 등급(`kind`)을 섞어서 준다. 화면에서 섞어
 *     놓으면 "틀렸다" 는 신호를 남발하게 되는데, polish 는 이미 통하는 영어다.
 *     기죽이지 않으려고 제목과 색을 다르게 준다.
 *  4. **오늘 배운 표현**과 **단어 팁**. 가져갈 것으로 끝낸다.
 *
 * 단어 팁(`word_tips`)은 대화 중에 만들어진 것이 아니라 **미리 만들어 사람이
 * 검수한** DB 항목이다(CLAUDE.md 3.5). 그래서 다른 칸보다 믿을 만하고, 여기서만
 * 영어 예문을 길게 보여준다.
 */
import type { ReactNode } from "react";

import type { Correction, SessionReport } from "../../api/types";

import styles from "./report.module.css";

export function ReportBody({ report }: { report: SessionReport }) {
  // 서버가 두 등급을 한 배열에 담아 준다. 화면에서 가른다.
  const mistakes = report.mistakes.filter((c) => c.kind === "mistake");
  const polish = report.mistakes.filter((c) => c.kind === "polish");

  return (
    <div className={styles.body}>
      <header className={styles.head}>
        <div className={styles.headLine}>
          <span className={styles.badge}>{report.level}</span>
          <h2 className={styles.headTitle}>{report.scenario_title}</h2>
        </div>
        <p className={styles.headCount}>{countSentence(report)}</p>
      </header>

      {/* 오늘 어땠는지 — 리포트에서 제일 먼저 읽히는 자리 */}
      <section className={styles.summary}>
        <p>{report.insight.summary_ko}</p>
      </section>

      {report.insight.patterns_ko.length > 0 && (
        <Section title="반복된 버릇" caption="같은 실수가 여러 번 나왔어요. 다음엔 여기만 신경 써 봐요.">
          <ul className={styles.patterns}>
            {report.insight.patterns_ko.map((pattern, i) => (
              <li key={i} className={styles.pattern}>
                {pattern}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section
        title="고쳐 볼 문장"
        count={mistakes.length}
        caption={mistakes.length > 0 ? "내가 한 말 → 이렇게 말하면 통해요" : undefined}
      >
        {mistakes.length > 0 ? (
          <div className={styles.cards}>
            {mistakes.map((c, i) => (
              <FixCard key={i} correction={c} tone="mistake" />
            ))}
          </div>
        ) : (
          <p className={styles.empty}>
            고칠 곳이 없었어요. 오늘 한 말은 상대가 다 알아들었어요.
          </p>
        )}
      </Section>

      {polish.length > 0 && (
        <Section
          title="이렇게 하면 더 자연스러워요"
          count={polish.length}
          // polish 는 틀린 게 아니다. 그 말을 화면에 적어 둬야 사용자가 실수로 세지 않는다.
          caption="틀린 건 아니에요. 원어민이라면 이렇게 말했을 거예요."
        >
          <div className={styles.cards}>
            {polish.map((c, i) => (
              <FixCard key={i} correction={c} tone="polish" />
            ))}
          </div>
        </Section>
      )}

      {report.insight.learned.length > 0 && (
        <Section title="오늘 배운 표현" caption="다음 대화에서 그대로 써 봐요.">
          <div className={styles.cards}>
            {report.insight.learned.map((item, i) => (
              <div key={i} className={styles.learned}>
                <p className={styles.learnedEn}>{item.english}</p>
                <p className={styles.learnedKo}>{item.note_ko}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {report.word_tips.length > 0 && (
        <Section
          title="단어 한 걸음 더"
          count={report.word_tips.length}
          caption="오늘 나온 낱말이에요. 미리 정리해 둔 설명이라 믿고 봐도 돼요."
        >
          <div className={styles.cards}>
            {report.word_tips.map((tip) => (
              <div key={tip.word} className={styles.tip}>
                <p className={styles.tipHead}>
                  <span className={styles.tipWord}>{tip.word}</span>
                  <span className={styles.tipMeaning}>{tip.meaning_ko}</span>
                </p>
                <p className={styles.tipExample}>{tip.example}</p>
                <p className={styles.tipNote}>{tip.usage_note}</p>
                {tip.confused_with.length > 0 && (
                  <p className={styles.tipConfused}>
                    헷갈리는 낱말: {tip.confused_with.join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

/** "오늘 6번 말했어요. 고칠 곳 2개, 다듬을 곳 1개를 찾았어요." 처럼 한 문장으로 만든다.
 *
 * 숫자 세 개를 타일로 늘어놓지 않은 이유: 이 앱을 쓰는 사람에게 필요한 건 지표판이
 * 아니라 "오늘 어땠는지" 한 줄이다. 0 인 항목은 아예 말하지 않는다 —
 * "고칠 곳 0개" 는 좋은 소식인데 목록으로 보면 나쁜 소식처럼 읽힌다.
 */
function countSentence(report: SessionReport): string {
  const parts: string[] = [];
  if (report.mistake_count > 0) parts.push(`고칠 곳 ${report.mistake_count}개`);
  if (report.polish_count > 0) parts.push(`다듬을 곳 ${report.polish_count}개`);
  const found = parts.length > 0 ? `${parts.join(", ")}를 찾았어요.` : "고칠 곳은 없었어요.";
  return `오늘 ${report.turn_count}번 말했어요. ${found}`;
}

function Section({
  title,
  count,
  caption,
  children,
}: {
  title: string;
  count?: number;
  caption?: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>
        {title}
        {count !== undefined && count > 0 && <span className={styles.sectionCount}>{count}</span>}
      </h3>
      {caption && <p className={styles.sectionCaption}>{caption}</p>}
      {children}
    </section>
  );
}

/** 교정 한 건. 원문 → 교정 → 이유 순서를 지킨다. */
function FixCard({ correction, tone }: { correction: Correction; tone: "mistake" | "polish" }) {
  return (
    <article className={`${styles.fix} ${tone === "polish" ? styles.fixPolish : ""}`}>
      {/* 원문을 지운 글씨(취소선)로 그리지 않는다. 자기가 한 말이 지워지는 걸 보면
          다음 턴에 입을 닫게 된다. 흐리게만 두고 아래 화살표로 방향을 준다. */}
      <p className={styles.fixOriginal}>{correction.original}</p>
      <p className={styles.fixArrow} aria-hidden="true">
        ↓
      </p>
      <p className={styles.fixBetter}>{correction.better}</p>
      <p className={styles.fixNote}>{correction.note}</p>
    </article>
  );
}
