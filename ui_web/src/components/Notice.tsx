/** 기다리는 중 · 실패했을 때 보여주는 두 조각.
 *
 * 화면마다 따로 만들지 않고 여기 모아 둔다. **실패를 그리는 코드가 화면마다
 * 다르면 어딘가는 반드시 빠뜨리기 때문이다.** 빠뜨린 자리가 곧 예전 Streamlit
 * 처럼 "아무 일도 안 일어난 것처럼 보이는" 화면이 된다.
 */
import styles from "./Notice.module.css";

export function Loading({ label = "불러오는 중이에요" }: { label?: string }) {
  return (
    <div className={styles.wrap} role="status" aria-live="polite">
      <div className={styles.spinner} />
      <p className={styles.detail}>{label}</p>
    </div>
  );
}

export interface ErrorNoticeProps {
  /** 서버가 준 한국어 문장을 **그대로** 넣는다. 다시 쓰지 않는다 —
      서버 쪽 detail 에는 다음에 뭘 하면 되는지까지 들어 있다. */
  detail: string;
  onRetry?: () => void;
  retryLabel?: string;
}

export function ErrorNotice({ detail, onRetry, retryLabel = "다시 시도" }: ErrorNoticeProps) {
  return (
    <div className={styles.wrap} role="alert">
      <p style={{ fontSize: "2rem", lineHeight: 1 }} aria-hidden="true">
        😥
      </p>
      <p className={styles.detail}>{detail}</p>
      {onRetry && (
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
