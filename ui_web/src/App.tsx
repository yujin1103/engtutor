/** 어느 화면을 그릴지 정하는 곳. 그 외의 일은 하지 않는다.
 *
 * 목록 세 개(`/scenarios` · `/categories` · `/strictness`)를 여기서 한 번만 받아
 * 화면에 넘겨준다. 화면마다 각자 받게 하지 않은 이유는 state/catalog.ts 에 적어 뒀다.
 */
import { ErrorNotice, Loading } from "./components/Notice";
import { Screen } from "./components/Screen";
import { HOME, useNav } from "./nav";
import { ChatScreen } from "./screens/ChatScreen";
import { PickerScreen } from "./screens/PickerScreen";
import { PracticeScreen } from "./screens/PracticeScreen";
import { ReportScreen } from "./screens/ReportScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { ToeicScreen } from "./screens/ToeicScreen";
import { useCatalog } from "./state/catalog";

export default function App() {
  const { route, go, replace, back } = useNav();
  const { state, retry } = useCatalog();

  if (state.status === "loading") {
    return (
      <Screen title="영어 회화 연습">
        <Loading label="연습할 상황을 불러오는 중이에요" />
      </Screen>
    );
  }

  if (state.status === "failed") {
    // API 가 죽었거나 폰이 네트워크를 놓친 경우. 빈 화면으로 두지 않는다.
    return (
      <Screen title="영어 회화 연습">
        <ErrorNotice detail={state.detail} onRetry={retry} />
      </Screen>
    );
  }

  const { catalog } = state;

  switch (route.name) {
    case "chat": {
      const scenario = catalog.byId.get(route.scenarioId);
      if (!scenario) {
        return (
          <Screen title="영어 회화 연습" onBack={() => replace(HOME)}>
            <ErrorNotice
              detail="그 대화를 찾지 못했어요. 목록에서 다시 골라 주세요."
              onRetry={() => replace(HOME)}
              retryLabel="목록으로"
            />
          </Screen>
        );
      }
      return (
        <ChatScreen
          scenario={scenario}
          onBack={back}
          // replace 다 — 리포트에서 뒤로 갔을 때 이미 끝난 세션의 대화로
          // 되돌아가면 거기서 보내는 요청은 전부 409 가 된다.
          onFinish={(sessionId) =>
            replace({ name: "report", sessionId, scenarioId: scenario.id })
          }
        />
      );
    }

    case "report":
      return (
        <ReportScreen
          sessionId={route.sessionId}
          scenario={catalog.byId.get(route.scenarioId) ?? null}
          onDone={() => replace(HOME)}
        />
      );

    case "practice":
      // 목록 셋(`catalog`)을 안 쓰는 유일한 화면이다. 연습장이 읽는 것은
      // `words` 테이블이지 시나리오가 아니다.
      //
      // `track` 이 실려 오면 장면 고르기를 건너뛴다 — 토익 화면에서 "빈칸으로
      // 연습" 을 누른 경우다. 같은 연습장을 다른 어휘로 여는 것뿐이라 화면을
      // 새로 만들지 않았다.
      return <PracticeScreen track={route.track} onBack={back} />;

    case "toeic":
      return (
        <ToeicScreen onBack={back} onPractice={() => go({ name: "practice", track: "toeic" })} />
      );

    case "settings":
      return <SettingsScreen strictness={catalog.strictness} onBack={back} />;

    case "picker":
    default:
      return (
        <PickerScreen
          catalog={catalog}
          onStart={(scenario) => go({ name: "chat", scenarioId: scenario.id })}
          onOpenPractice={() => go({ name: "practice" })}
          onOpenToeic={() => go({ name: "toeic" })}
          onOpenSettings={() => go({ name: "settings" })}
        />
      );
  }
}
