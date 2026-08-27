/** 보기 넷이 각각 무엇이었는지. **여기서 문장을 지어내지 않는다.**
 *
 * 이 화면이 가르치려는 것은 "정답이 send 다" 가 아니라 "to 뒤에는 동사원형이고
 * 나머지 셋은 각각 -ing 형·명사·과거형이다" 다. 그 네 줄을 서버가 이미 문장으로
 * 만들어 보낸다(`why_ko`). 화면은 **그대로 띄운다.**
 *
 * 다시 짓지 않는 이유는 연습장의 `ExplainCard` 와 같다 — 모양 이름을 화면에서
 * 붙이려면 낱말과 모양을 짝지어야 하는데, 그 짝은 서버만 안다(보기에는 낱말만
 * 실려 온다). 화면이 철자를 보고 추측하면 `sender` 를 '3인칭 단수형' 이라고
 * 가르치게 된다.
 *
 * 정답 표시(`← 정답`)도 서버 문장 안에 이미 들어 있어서 여기서 덧붙이지 않는다.
 * 어느 줄이 정답인지는 위의 보기 목록이 색과 꼬리표로 이미 말했다.
 */
import styles from "./grammar.module.css";

export function WhyCard({ why }: { why: string[] }) {
  if (why.length === 0) return null;

  return (
    <section className={styles.section}>
      <div className={styles.label}>보기 넷은 각각</div>
      <ul className={styles.why}>
        {why.map((line) => (
          <li key={line} className={styles.whyRow}>
            {line}
          </li>
        ))}
      </ul>
    </section>
  );
}
