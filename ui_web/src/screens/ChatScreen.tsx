/** 대화 화면. 이 앱을 React 로 다시 만든 이유가 여기서 끝난다.
 *
 * 하는 일은 조립뿐이고, 실제 규칙은 세 곳에 나뉘어 있다.
 *   - `chat/useChat.ts`  — 스트림을 읽고 상태를 만든다(조용히 끝나는 경로가 없다)
 *   - `chat/Thread.tsx`  — 말풍선·해석·교정·실패를 그린다
 *   - `chat/SayBar.tsx`  — 입력창 위에서 다음에 할 말을 알려준다
 *
 * 화면 배치의 근거:
 *  - 힌트 바와 입력칸은 스크롤되지 않는 아래쪽(`footer`)에 둔다. **얼어붙는
 *    사건이 일어나는 좌표가 화면 맨 아래**이고, 힌트가 스크롤 위로 사라지면
 *    정작 필요할 때 없다.
 *  - 끝내기는 상단바 오른쪽이다. 아래쪽은 매 턴 누르는 것들 차지라, 한 판에
 *    한 번 누르는 버튼을 거기 두면 오발이 난다.
 *  - 끝내기는 한 번 더 묻는다. 리포트를 만드는 순간 세션이 닫혀 그 대화로는
 *    다시 못 돌아간다(서버가 409 로 막는다).
 */
import { useEffect, useRef, useState } from "react";

import { Screen } from "../components/Screen";
import { useSettings } from "../state/settings";
import type { ScenarioOut } from "../api/types";

import { VoiceBar } from "../voice";
import { Composer } from "./chat/Composer";
import { SayBar } from "./chat/SayBar";
import { SettingsFold } from "./settings/SettingsFold";
import { Thread } from "./chat/Thread";
import { useChat } from "./chat/useChat";
import { useStickToBottom } from "./chat/useStickToBottom";

import styles from "./ChatScreen.module.css";

export interface ChatScreenProps {
  scenario: ScenarioOut;
  onBack: () => void;
  /** 대화를 끝냈다. 리포트 화면으로 넘어간다. 세션이 아직 없으면 부르지 않는다. */
  onFinish: (sessionId: string) => void;
}

export function ChatScreen({ scenario, onBack, onFinish }: ChatScreenProps) {
  const { level, strictness } = useSettings();
  const chat = useChat({ scenario, level, strictness });

  const [text, setText] = useState("");
  const [confirming, setConfirming] = useState(false);
  // 뒤로 가기는 **끝내기보다 더 확실하게 대화를 잃는다.** 화면이 사라지면서
  // 세션 손잡이가 같이 사라져, 다시 들어가면 새 대화가 열리고 서버의 옛 세션은
  // 리포트를 못 만든 채 남는다. 그런데 끝내기만 물어보고 있었다.
  // 폰에서 뒤로 스와이프는 실수로 제일 잘 나오는 동작이라 여기서 한 번 묻는다.
  const [leaving, setLeaving] = useState(false);
  // 난이도를 바꾸려고 하던 대화를 버리게 하면 안 된다. 서버는 이제 턴마다
  // 요청의 레벨을 쓰므로(app/main.py 의 `_resolve`) 여기서 바꾸면 다음 답부터 바로 먹는다.
  const [showSettings, setShowSettings] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 글자가 한 조각 올 때마다, 말풍선이 하나 늘 때마다 바닥을 따라 내려간다.
  // (위로 올려 둔 사람은 안 끌어내린다 — 훅 안에 이유를 적어 뒀다)
  const { endRef, scrollToBottom } = useStickToBottom([
    chat.entries.length,
    chat.draft,
    chat.streaming,
  ]);

  // 실패는 **붙어 있지 않은 사람에게도** 보여야 한다. 답을 기다리는 동안 위로
  // 올려 앞의 교정을 읽던 사람이 있고, 그 사람이 끊긴 걸 못 보는 것이 우리가
  // 고치려는 바로 그 증상이다. 그래서 실패했을 때만 규칙을 어기고 끌어내린다.
  const failure = chat.failure;
  useEffect(() => {
    if (failure) scrollToBottom();
  }, [failure, scrollToBottom]);

  /** 실패한 말을 입력칸으로 되돌린다. 다시 치게 만들면 안 된다 — 그게 두 번째 좌절이다. */
  const editFailed = () => {
    const message = chat.failure?.attempt.message ?? "";
    chat.dismiss();
    setText(message);
    inputRef.current?.focus();
  };

  const sendText = (message: string) => {
    setText("");
    chat.send({ message, mode: "text" });
    // 실패 알림은 붙어 있지 않은 사람에게도 보여야 해서 여기서는 무조건 내린다.
    // 보낸 직후라 화면 아래를 보고 있을 확률이 높지만, 아닌 경우가 문제였다.
    scrollToBottom();
  };

  // 콜백 안에서는 좁혀진 타입이 유지되지 않는다. 한 번 꺼내 두고 쓴다.
  const sessionId = chat.sessionId;
  // 주고받은 말이 하나라도 있으면 잃을 것이 있다.
  const hasTalked = chat.entries.length > 0;

  return (
    <Screen
      title={scenario.title}
      onBack={() => (hasTalked ? setLeaving(true) : onBack())}
      action={
        <span className={styles.actions}>
          <button
            type="button"
            className={styles.gear}
            onClick={() => setShowSettings((v) => !v)}
            aria-expanded={showSettings}
            aria-label="난이도와 교정 강도"
            title="난이도와 교정 강도"
          >
            ⚙️
          </button>
          {/* 세션이 생기기 전에는 끝낼 것도 없다. 눌러도 아무 일 없는 버튼을
              띄워 두는 것보다 아예 없는 편이 헷갈리지 않는다. */}
          {sessionId && !confirming && !leaving ? (
            <button
              type="button"
              className={styles.finish}
              onClick={() => setConfirming(true)}
              disabled={chat.streaming}
            >
              끝내기
            </button>
          ) : null}
        </span>
      }
      footer={
        leaving ? (
          <div className={styles.confirm}>
            <p className={styles.confirmText}>
              나가면 하던 대화가 사라져요. 리포트도 만들 수 없어요.
            </p>
            <div className={styles.confirmButtons}>
              <button type="button" className="btn" onClick={() => setLeaving(false)}>
                더 할래요
              </button>
              <button type="button" className="btn btn-primary" onClick={onBack}>
                나가기
              </button>
            </div>
          </div>
        ) : confirming && sessionId ? (
          <div className={styles.confirm}>
            <p className={styles.confirmText}>
              대화를 끝내고 리포트를 볼까요? 리포트가 나오면 이 대화는 다시 이어갈 수 없어요.
            </p>
            <div className={styles.confirmButtons}>
              <button type="button" className="btn" onClick={() => setConfirming(false)}>
                더 할래요
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => onFinish(sessionId)}
              >
                끝내고 리포트 보기
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.bottom}>
            {/* 난이도는 대화를 버리지 않고 바꿀 수 있어야 한다. 어렵다고 느낀
                사람이 하던 대화를 버려야 낮출 수 있으면 그건 조절기가 아니다. */}
            {showSettings ? <SettingsFold /> : null}
            {/* key 로 턴이 바뀔 때마다 새로 만든다 — 펼쳐 둔 영어가 다음 턴에 접힌다. */}
            <SayBar key={chat.last.id} turn={chat.last.turn} />
            {/* 말하기는 `src/voice/` 가 통째로 맡는다 — 녹음, 형식 고르기, 전사 확인 칸까지.
                여기로는 **학습자가 확인·수정을 끝낸 문장만** 온다. 보내는 일과 스트리밍은
                이 화면이 소유해서, 말로 하든 타자로 치든 대화를 다루는 코드가 한 벌로 남는다.
                전사 원본(`transcript`)과 낱말 배열은 손대지 않고 그대로 서버까지 넘긴다 —
                확정 문장과의 차이가 이 STT 를 믿어도 되는지 알려 주는 유일한 신호다. */}
            <VoiceBar
              disabled={chat.streaming}
              onConfirm={(message, transcript, words) => {
                setText("");
                chat.send({ message, mode: "voice", transcript, words });
                scrollToBottom();
              }}
            />
            <Composer
              value={text}
              onChange={setText}
              onSend={sendText}
              busy={chat.streaming}
              inputRef={inputRef}
            />
          </div>
        )
      }
    >
      <Thread
        entries={chat.entries}
        streaming={chat.streaming}
        draft={chat.draft}
        rewriting={chat.rewriting}
        failure={chat.failure}
        onRetry={chat.retry}
        onEdit={editFailed}
        endRef={endRef}
      />
    </Screen>
  );
}
