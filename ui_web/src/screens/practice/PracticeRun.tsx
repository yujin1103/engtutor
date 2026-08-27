/** 한 팩을 **연달아** 푸는 자리. 이 화면의 성격이 여기서 정해진다.
 *
 * 채점이 목적이 아니라 가르치는 게 목적이다. 그래서 지켜야 하는 것 셋:
 *
 *  1. **틀려도 끝이 아니다.** 답을 내면 늘 두 갈래가 함께 열린다 — 다시 풀기와
 *     다음 문제. 틀린 답에 "다음" 만 남겨 두면 채점표가 되고, 맞은 답에
 *     "다시" 만 남겨 두면 연습이 끊긴다.
 *  2. **한 문제 풀고 화면을 나가지 않는다.** 문제는 `useDeck` 이 미리 받아 두고
 *     여기서는 자리만 옮긴다.
 *  3. **아직 겨눠 보지도 못한 답에는 정답을 펴지 않는다.** 서버는 오타(`not_a_word`)
 *     에도 설명 카드를 함께 보내지만(왕복 두 번보다 낫다는 서버 쪽 결정),
 *     그대로 펼치면 "다시 말해 볼까요?" 아래에 답을 적어 두는 꼴이 된다.
 *     그 판단은 `flow.opensExplanation` 이 하고 시험이 붙어 있다.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, RefObject } from "react";

import { ApiError, isAborted, postClozeAnswer } from "../../api/client";
import { ErrorNotice, Loading } from "../../components/Notice";
import { Screen } from "../../components/Screen";
import type { ClozeAnswerOut } from "../../api/types";
import { ExplainCard } from "./ExplainCard";
import { opensExplanation, splitBlank, toneOf } from "./flow";
import { SpellHints } from "./SpellHints";
import { useDeck } from "./useDeck";

import styles from "./practice.module.css";

export interface PracticeRunProps {
  /** `null` 이면 장면을 안 고른 것 — 전체 어휘를 빈도 순으로 푼다. */
  topic: string | null;
  /** 화면 위에 적을 이름("카페", "전체 낱말"). */
  label: string;
  /**
   * 어느 어휘 트랙을 풀지. 안 주면 서버 기본값인 생활 회화다.
   * 토익 화면이 이 자리를 써서 **같은 연습장을 토익 낱말로 연다** — 화면을
   * 하나 더 만들지 않는다. 푸는 방식이 같은데 화면이 둘이면 둘 다 낡는다.
   */
  track?: string;
  onBack: () => void;
}

type Phase =
  | { at: "asking" }
  | { at: "checking" }
  /** `open` 은 설명을 펼쳤는가. 오타·빈 답이면 닫힌 채로 시작한다. */
  | { at: "judged"; result: ClozeAnswerOut; open: boolean }
  | { at: "broken"; detail: string };

export function PracticeRun({ topic, label, track, onBack }: PracticeRunProps) {
  const { state, next, retry } = useDeck(topic, track);
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>({ at: "asking" });
  const inputRef = useRef<HTMLInputElement | null>(null);
  // 채점 요청 하나. 화면을 떠나면 끊는다 — 답이 돌아와 봐야 그릴 곳이 없다.
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  // 문제가 바뀌면 답 칸과 판정을 **렌더 도중에** 비운다. 효과에서 비우면 앞
  // 문제의 판정이 새 문장 밑에 한 프레임 붙어 있다가 사라진다 — 폰에서 특히
  // 눈에 띄고, 잠깐이지만 "다음 문제인데 답이 이미 나와 있다" 로 읽힌다.
  const stamp = state.status === "ready" ? `${state.item.word}#${state.seen}` : "";
  const [shown, setShown] = useState(stamp);
  if (shown !== stamp) {
    setShown(stamp);
    setText("");
    setPhase({ at: "asking" });
  }

  const word = state.status === "ready" ? state.item.word : null;

  const check = useCallback(
    (said: string) => {
      if (!word) return;
      setPhase({ at: "checking" });
      pending.current?.abort();
      const controller = new AbortController();
      pending.current = controller;
      postClozeAnswer({ word, said }, controller.signal).then(
        (result) => {
          setPhase({ at: "judged", result, open: opensExplanation(result.verdict) });
        },
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
    [word],
  );

  // 같은 문제를 다시. 답 칸을 비우고 판정을 접는다 — 정답을 본 뒤라도 한 번
  // 직접 써 보는 것이 읽고 넘어가는 것보다 남는다.
  const again = useCallback(() => {
    setText("");
    setPhase({ at: "asking" });
    inputRef.current?.focus();
  }, []);

  if (state.status === "loading") {
    return (
      <Screen title={label} onBack={onBack}>
        <Loading label="문제를 준비하고 있어요" />
      </Screen>
    );
  }

  if (state.status === "failed") {
    return (
      <Screen title={label} onBack={onBack}>
        <ErrorNotice detail={state.detail} onRetry={retry} />
      </Screen>
    );
  }

  if (state.status === "empty") {
    return (
      <Screen title={label} onBack={onBack}>
        <p className={styles.empty}>
          이 장면에는 아직 낼 수 있는 문제가 없어요. 다른 장면을 골라 보세요.
        </p>
      </Screen>
    );
  }

  const item = state.item;
  const judged = phase.at === "judged" ? phase : null;
  /**
   * 정답을 드러내도 되는가.
   *
   * **스위치가 하나여야 한다.** 답이 드러나는 자리가 둘이다 — 빈칸에 채워지는
   * 낱말과 설명 카드. 처음에는 설명 카드만 접어 뒀는데, 빈칸이 그대로 채워지는
   * 바람에 "다시 말해 볼까요?" 바로 위 문장에 답이 적혀 있었다(실제로 붙여
   * 돌려 보고 나서야 발견했다). 둘을 각자 판단하게 두면 다음에 자리가 하나 더
   * 늘 때 또 어긋난다.
   */
  const revealed = judged !== null && judged.open;

  return (
    <Screen
      title={label}
      onBack={onBack}
      footer={
        judged ? (
          <div className={styles.actions}>
            <button type="button" className="btn" onClick={again}>
              다시 풀기
            </button>
            <button type="button" className="btn btn-primary" onClick={next}>
              다음 문제
            </button>
          </div>
        ) : (
          <AnswerForm
            value={text}
            onChange={setText}
            onSubmit={check}
            busy={phase.at === "checking"}
            inputRef={inputRef}
          />
        )
      }
    >
      <p className={styles.meta}>{state.seen}번째 문제</p>

      <div className={styles.question}>
        <p className={styles.sentence}>
          <Sentence
            sentence={item.sentence}
            filled={revealed && judged ? judged.result.answer : null}
          />
        </p>

        {/* 해석은 가리지 않고 그대로 준다. 답이 일부 드러나지만 그게 결정한
            것이다 — 뜻을 알아야 `pen`·`a pen`·`your pen` 처럼 구로도 답할 수
            있고, 뜻을 안 주면 왕초보에게는 과제가 성립하지 않는다.
            없는 문제도 많다(3,245개 중 792개만 채워져 있다). 없으면 안 그린다. */}
        {item.example_ko && <p className={styles.gloss}>{item.example_ko}</p>}

        {/* 서버가 만든 문장을 그대로. 자리를 좁힌 것("여기엔 명사가 들어가요")과
            낱말의 품사를 말한 것("이 낱말은 명사로도 동사로도 써요")은 근거가
            달라서, 화면이 `labels_ko` 로 문장을 지으면 둘이 뒤섞인다. */}
        {item.pos_hint && <p className={styles.hint}>{item.pos_hint.text_ko}</p>}

        {/* 철자 단서. 답을 내기 전까지만 보여 준다 — 판정이 나온 뒤에는 정답이
            이미 문장에 들어가 있어서 힌트가 남아 있으면 그 자리만 어수선해진다.
            자동으로 펴지 않는 이유는 SpellHints 에 적어 두었다. */}
        {!judged && (
          <SpellHints hints={item.spell_hints ?? []} resetKey={stamp} />
        )}
      </div>

      {phase.at === "broken" && (
        <div className="alert" style={{ marginTop: "var(--gap)" }} role="alert">
          {phase.detail}
        </div>
      )}

      {judged && (
        <>
          <p className={`${styles.verdict} ${styles[toneOf(judged.result.verdict)]}`} role="status">
            {judged.result.message_ko}
          </p>

          {revealed ? (
            judged.result.explain && <ExplainCard card={judged.result.explain} />
          ) : (
            // 오타·빈 답이라 서버가 "다시 해 볼까요?" 로 끝낸 자리다. 답은 이미
            // 받아 뒀지만 **학습자가 원할 때만** 편다.
            <div className={styles.reveal}>
              <button
                type="button"
                className="btn btn-block"
                onClick={() => setPhase({ ...judged, open: true })}
              >
                답 보기
              </button>
            </div>
          )}
        </>
      )}
    </Screen>
  );
}

/** 빈칸 문장 한 줄. 답을 낸 뒤에는 **같은 자리에** 답이 들어간다 — 눈이 옮겨
    다니지 않아야 "아, 여기였구나" 가 한 번에 온다. */
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

/** 답 쓰는 칸. 브라우저의 자동 고침을 전부 끈다 — 대화 쪽 `Composer` 와 같은
    이유다. 폰 키보드가 `pen` 을 `Pen` 으로 고쳐 버리면 학습자가 실제로 쓴 것이
    서버에 닿지 않는다. `<form>` 으로 감싼 것은 키보드의 확인 키를 쓰려는 것. */
function AnswerForm({
  value,
  onChange,
  onSubmit,
  busy,
  inputRef,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (said: string) => void;
  busy: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
}) {
  const ready = value.trim().length > 0 && !busy;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    onSubmit(value.trim());
  };

  return (
    <form className={styles.form} onSubmit={submit}>
      <input
        ref={inputRef}
        className={styles.input}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        // 낱말 하나만 받는 칸이 아니다. 사용자가 명시적으로 원한 것이라
        // 안내 문구에서부터 구·절이 된다고 말한다.
        placeholder="낱말 하나여도, 구여도 좋아요"
        aria-label="빈칸에 들어갈 말"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="done"
      />
      <button type="submit" className="btn btn-primary" disabled={!ready}>
        {busy ? "확인 중" : "확인"}
      </button>
    </form>
  );
}
