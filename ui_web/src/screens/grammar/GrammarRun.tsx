/** 문법 문제를 **연달아** 푸는 자리. 이 화면의 성격이 여기서 정해진다.
 *
 * 채점이 목적이 아니라 가르치는 게 목적이다. 그래서 지켜야 하는 것 셋:
 *
 *  1. **누르면 바로 채점한다.** 보기를 고르고 '확인' 을 또 누르게 하지 않는다 —
 *     넷 중 하나를 고르는 일에 확인 한 걸음을 더 두면, 스무 문제를 푸는 동안
 *     스무 번 헛손질을 시키는 셈이다. 대신 누른 것이 곧바로 눌린 티가 나야 한다.
 *  2. **틀려도 끝이 아니다.** 판정이 나면 정답이 문장에 들어가고, 보기 넷이
 *     각각 무엇이었는지가 아래에 펼쳐진다. 그러고 나서 다음 문제로 간다.
 *  3. **한 문제 풀고 화면을 나가지 않는다.** 문제는 `useGrammarDeck` 이 미리
 *     받아 두고 여기서는 자리만 옮긴다.
 *
 * 답이 드러나는 자리는 하나로 묶어 둔다(`judged`). 연습장에서 빈칸과 설명 카드를
 * 각자 판단하게 뒀다가 "다시 말해 볼까요?" 바로 위 문장에 답이 적혀 있던 적이
 * 있다. 여기서는 판정 결과 자체가 없으면 어느 자리에도 답이 없다 — 서버가
 * 문제를 줄 때 정답을 아예 안 보내기 때문이다.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, isAborted, postGrammarAnswer } from "../../api/client";
import { ErrorNotice, Loading } from "../../components/Notice";
import { Screen } from "../../components/Screen";
import type { GrammarAnswerOut } from "../../api/types";
import { ChoiceList } from "./ChoiceList";
import { splitBlank } from "./flow";
import { useGrammarDeck } from "./useGrammarDeck";
import { WhyCard } from "./WhyCard";

import styles from "./grammar.module.css";

export interface GrammarRunProps {
  /** 화면 위에 적을 이름. 무엇을 푸는지는 `rule_title` 이 본문에서 따로 말한다. */
  title: string;
  onBack: () => void;
}

type Phase =
  | { at: "asking" }
  /** `chosen` 은 눌린 보기. 기다리는 동안 눌린 티를 내려고 들고 있는다. */
  | { at: "checking"; chosen: string }
  | { at: "judged"; result: GrammarAnswerOut }
  | { at: "broken"; detail: string };

export function GrammarRun({ title, onBack }: GrammarRunProps) {
  const { state, next, retry } = useGrammarDeck();
  const [phase, setPhase] = useState<Phase>({ at: "asking" });
  // 채점 요청 하나. 화면을 떠나면 끊는다 — 답이 돌아와 봐야 그릴 곳이 없다.
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  // 문제가 바뀌면 판정을 **렌더 도중에** 비운다. 효과에서 비우면 앞 문제의
  // 판정이 새 문장 밑에 한 프레임 붙어 있다가 사라진다 — 폰에서 특히 눈에
  // 띄고, 잠깐이지만 "다음 문제인데 답이 이미 나와 있다" 로 읽힌다.
  const stamp = state.status === "ready" ? `${state.item.id}#${state.seen}` : "";
  const [shown, setShown] = useState(stamp);
  if (shown !== stamp) {
    setShown(stamp);
    setPhase({ at: "asking" });
  }

  const id = state.status === "ready" ? state.item.id : null;

  const check = useCallback(
    (chosen: string) => {
      if (!id) return;
      setPhase({ at: "checking", chosen });
      pending.current?.abort();
      const controller = new AbortController();
      pending.current = controller;
      postGrammarAnswer({ id, chosen }, controller.signal).then(
        (result) => setPhase({ at: "judged", result }),
        (error: unknown) => {
          if (isAborted(error)) return;
          setPhase({
            at: "broken",
            detail:
              error instanceof ApiError
                ? error.detail
                : "채점하지 못했어요. 잠시 뒤 다시 해 보세요.",
          });
        },
      );
    },
    [id],
  );

  if (state.status === "loading") {
    return (
      <Screen title={title} onBack={onBack}>
        <Loading label="문제를 준비하고 있어요" />
      </Screen>
    );
  }

  if (state.status === "failed") {
    return (
      <Screen title={title} onBack={onBack}>
        <ErrorNotice detail={state.detail} onRetry={retry} />
      </Screen>
    );
  }

  if (state.status === "empty") {
    return (
      <Screen title={title} onBack={onBack}>
        <p className={styles.empty}>아직 낼 수 있는 문법 문제가 없어요.</p>
      </Screen>
    );
  }

  const item = state.item;
  const judged = phase.at === "judged" ? phase.result : null;

  return (
    <Screen
      title={title}
      onBack={onBack}
      footer={
        // 보기는 본문에 있다. 아래 고정 자리는 판정이 난 뒤에만 쓴다 —
        // 답을 내기 전에 '다음 문제' 가 보이면 건너뛰기 버튼으로 읽힌다.
        judged ? (
          <button type="button" className="btn btn-primary btn-block" onClick={next}>
            다음 문제
          </button>
        ) : undefined
      }
    >
      <p className={styles.meta}>{state.seen}번째 문제</p>

      <div className={styles.question}>
        <p className={styles.sentence}>
          {/* 판정 뒤에는 빈칸 자리에 **정답**이 들어간다. 고른 것이 아니라
              정답을 끼워야 문장이 온전해지고, 그 온전한 문장을 한 번 읽는 것이
              이 문제의 배울 거리다. 무엇을 골랐는지는 보기 목록이 말한다. */}
          <Sentence sentence={item.sentence} filled={judged?.answer || null} />
        </p>

        {/* 낱말 자리를 '~' 로 비워 둔 뜻. 가리지 않는다 — 이 문제가 묻는 것은
            뜻이 아니라 형태라, 뜻을 다 알려 줘도 문제가 그대로 성립한다. */}
        <p className={styles.gloss}>{item.sentence_ko}</p>
      </div>

      <ChoiceList
        choices={item.choices}
        result={judged}
        pending={phase.at === "checking" ? phase.chosen : null}
        locked={phase.at === "checking" || judged !== null}
        onPick={check}
      />

      {phase.at === "broken" && (
        <>
          <div className="alert" style={{ marginTop: "var(--gap)" }} role="alert">
            {phase.detail}
          </div>
          {/* **막다른 길을 막는다.** 채점이 404 로 실패하면(데이터가 바뀌어
              서버가 그 문제를 더 모를 때) 판정이 없어서 footer 의 '다음 문제'
              가 안 나오고, 보기는 잠기지 않아 다시 눌러도 또 404 다. 뒤로 가기
              말고는 나갈 길이 없었다. */}
          <button type="button" className="btn btn-block" onClick={next}>
            다음 문제
          </button>
        </>
      )}

      {judged && (
        <>
          {/* 판정과 규칙 설명이 한 문장에 담겨 온다. 다시 쓰지 않는다 —
              "맞았어요" 뒤에 붙는 규칙 설명이 이 화면이 가르치려는 것이다. */}
          {/* 무엇을 배운 것인지. **채점 뒤에만 온다** — 문제와 함께 주면
              "to 다음에는 동사원형" 이라는 제목이 곧 답이라, 문장을 안 읽고도
              보기 넷 중 원형을 고르면 된다. 서버가 준 이름을 그대로 쓴다.
              화면이 규칙 이름 표를 들고 있으면 규칙을 하나 더 만들 때 한쪽을
              빠뜨리게 된다. */}
          {judged.rule_title && <p className={styles.rule}>{judged.rule_title}</p>}

          <p
            className={`${styles.verdict} ${judged.ok ? styles.right : styles.miss}`}
            role="status"
          >
            {judged.message_ko}
          </p>

          <WhyCard why={judged.why_ko} />
        </>
      )}
    </Screen>
  );
}

/** 빈칸 문장 한 줄. 판정 뒤에는 **같은 자리에** 정답이 들어간다 — 눈이 옮겨
    다니지 않아야 "아, 여기였구나" 가 한 번에 온다. (연습장과 같은 판단이다) */
function Sentence({ sentence, filled }: { sentence: string; filled: string | null }) {
  const parts = splitBlank(sentence);
  // 빈칸이 없는 문장은 서버가 낼 리 없지만, 그때도 문장은 읽을 수 있어야 한다.
  if (!parts) return <>{sentence}</>;
  return (
    <>
      {parts.before}
      {filled ? (
        <span className={styles.filled}>{filled}</span>
      ) : (
        <span className={styles.blank} role="img" aria-label="빈칸" />
      )}
      {parts.after}
    </>
  );
}
