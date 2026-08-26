/** 학습 리포트 화면. 이 앱이 단순 챗봇 래퍼가 아닌 이유가 이 화면이다.
 *
 * 대화 중에 쌓인 교정을 모아 돌려주는 자리다. 여기서 하는 일은 세 가지뿐이고,
 * 실제 내용은 옆 폴더(`report/`)의 부품들이 그린다.
 *
 *  - 받아 오기: `useReport()` — 한 세션에 한 번만 부르는 이유가 그 파일에 있다.
 *  - 기다리기: `<ReportWaiting />` — 로컬 14B 라 20~60초, 길면 2~3분 걸린다.
 *  - 실패하기: `<ErrorNotice />` — **조용히 멈추지 않는다.** 이 앱을 React 로
 *    다시 만든 이유가 그것이다.
 *
 * 상태마다 아래 버튼도 바꾼다. 기다리는 중에는 눈에 띄지 않는 "나중에 볼게요" 를
 * 둔다 — 폰에서 왼쪽 위 뒤로가기는 한 손으로 닿지 않는데, 1분짜리 기다림에
 * 빠져나갈 길이 거기밖에 없으면 갇힌 느낌을 준다. 대신 강조하지 않는다.
 */
import { ErrorNotice } from "../components/Notice";
import { Screen } from "../components/Screen";
import type { ScenarioOut } from "../api/types";
import { ReportBody } from "./report/ReportBody";
import { ReportWaiting } from "./report/ReportWaiting";
import { useReport } from "./report/useReport";

import styles from "./report/report.module.css";

export interface ReportScreenProps {
  sessionId: string;
  /** 목록에서 못 찾은 시나리오일 수 있어 null 을 허용한다(제목은 리포트에도 들어 있다). */
  scenario: ScenarioOut | null;
  /** 리포트를 다 보고 처음 화면으로 돌아간다. */
  onDone: () => void;
}

export function ReportScreen({ sessionId, scenario, onDone }: ReportScreenProps) {
  const { state, retry } = useReport(sessionId);

  if (state.status === "loading") {
    return (
      <Screen
        title="오늘의 리포트"
        onBack={onDone}
        footer={
          <button type="button" className="btn btn-block" onClick={onDone}>
            나중에 볼게요
          </button>
        }
      >
        {/* 어느 대화의 리포트인지. 리포트가 오기 전에는 이것만 알 수 있다. */}
        <p className={styles.headCount} style={{ textAlign: "center" }}>
          {scenario ? scenario.title : "지난 대화"}
        </p>
        <ReportWaiting startedAt={state.startedAt} />
      </Screen>
    );
  }

  if (state.status === "failed") {
    return (
      <Screen
        title="오늘의 리포트"
        onBack={onDone}
        footer={
          <button type="button" className="btn btn-block" onClick={onDone}>
            목록으로 가기
          </button>
        }
      >
        {/* 서버가 준 한국어를 그대로 보여준다. 다시 시도하면 모델을 처음부터 다시 돌린다. */}
        <ErrorNotice detail={state.detail} onRetry={retry} retryLabel="다시 만들기" />
      </Screen>
    );
  }

  return (
    <Screen
      title="오늘의 리포트"
      onBack={onDone}
      footer={
        <button type="button" className="btn btn-primary btn-block" onClick={onDone}>
          다른 대화 하러 가기
        </button>
      }
    >
      <ReportBody report={state.report} />
    </Screen>
  );
}
