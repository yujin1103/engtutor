/** 화면 넷이 공통으로 쓰는 껍데기 — 위에 바, 가운데 스크롤, 아래 고정 자리.
 *
 * 폰에서 한 손으로 쓰는 것을 기준으로 삼았다. 그래서 (1) 스크롤은 가운데만 하고
 * 제목과 뒤로가기는 늘 같은 자리에 있으며, (2) 입력창·시작 버튼처럼 자주 누르는
 * 것은 `footer` 로 내려 엄지 근처에 둔다.
 */
import type { ReactNode } from "react";

import styles from "./Screen.module.css";

export interface ScreenProps {
  title: ReactNode;
  /** 주면 왼쪽에 뒤로가기가 생긴다. 없으면 그 자리는 비운다. */
  onBack?: () => void;
  /** 바 오른쪽 (설정 버튼 등). */
  action?: ReactNode;
  /** 화면 맨 아래 고정 영역 (입력칸·주요 버튼). */
  footer?: ReactNode;
  children: ReactNode;
}

export function Screen({ title, onBack, action, footer, children }: ScreenProps) {
  return (
    <div className={styles.screen}>
      <header className={styles.bar}>
        {onBack && (
          <button type="button" className={styles.back} onClick={onBack} aria-label="뒤로">
            ←
          </button>
        )}
        <h1 className={styles.title}>{title}</h1>
        {action && <div className={styles.action}>{action}</div>}
      </header>

      <main className={styles.body}>{children}</main>

      {footer && <footer className={styles.footer}>{footer}</footer>}
    </div>
  );
}
