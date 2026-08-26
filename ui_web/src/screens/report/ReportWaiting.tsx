/** 리포트를 기다리는 동안 보여주는 화면.
 *
 * 로컬 14B 모델이 만들기 때문에 **보통 20~60초, 길면 2~3분** 걸린다. 왕초보에게
 * 그 침묵은 "고장" 으로 읽힌다. 그래서 두 가지를 반드시 보여준다.
 *
 *  1. **몇 초가 지났는지.** 숫자가 1초마다 올라가는 것만으로 "멈추지 않았다" 가
 *     전달된다. 도는 원만 있으면 30초쯤부터는 멈춘 것과 구별되지 않는다.
 *  2. **왜 느린지.** 이 리포트는 인터넷 너머가 아니라 이 컴퓨터 안에서 만들어진다.
 *     이유를 알면 기다림이 고장으로 읽히지 않는다.
 *
 * 진행 막대는 일부러 넣지 않았다. 서버는 모델을 한 번 부를 뿐이라 "몇 퍼센트"
 * 라는 값이 존재하지 않는다. 없는 진행도를 지어내 그리면 그게 멈췄을 때 사용자를
 * 두 번 속이게 된다. 대신 아래 문구는 **경과 시간에만** 반응한다 — 지어낸 값이 아니다.
 */
import { useElapsedSeconds } from "./useReport";

import styles from "./report.module.css";

/** 흐른 시간에 따라 바뀌는 안심 문구. 서버 진행 상황이 아니라 시계에만 반응한다. */
function reassurance(seconds: number): string {
  if (seconds < 20) return "오늘 대화를 처음부터 다시 읽고 있어요.";
  if (seconds < 60) return "거의 다 됐어요. 보통 이만큼 걸려요.";
  if (seconds < 120) return "조금 더 걸리고 있어요. 이 화면을 그대로 두세요.";
  return "아직 만들고 있어요. 컴퓨터가 바쁘면 2~3분까지 걸리기도 해요.";
}

export function ReportWaiting({ startedAt }: { startedAt: number }) {
  const seconds = useElapsedSeconds(startedAt);

  return (
    <div className={styles.waiting} role="status" aria-live="polite">
      <div className={styles.waitingSpinner} aria-hidden="true" />
      <p className={styles.waitingTitle}>오늘 대화를 정리하고 있어요</p>
      <p className={styles.waitingNote}>{reassurance(seconds)}</p>
      {/* 초 세기. aria-live 를 끄지 않으면 화면 낭독기가 1초마다 숫자를 읽는다. */}
      <p className={styles.waitingClock} aria-live="off">
        {seconds}초
      </p>
      <p className={styles.waitingWhy}>
        리포트는 이 컴퓨터 안에서 직접 만들어요. 밖으로 보내지 않는 대신 조금 느려요.
      </p>
    </div>
  );
}
