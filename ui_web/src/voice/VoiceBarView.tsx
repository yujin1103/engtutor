/** 음성 칸의 **그리는 쪽만**. 상태를 받아서 markup 을 낼 뿐, 마이크도 네트워크도 모른다.
 *
 * 왜 갈랐나. 이 화면에서 확인해야 할 것은 "어느 단계에서 무슨 글자가 뜨는가" 인데,
 * `MediaRecorder` 와 붙어 있으면 브라우저 없이는 한 단계도 그려 볼 수 없다.
 * 갈라 놓으면 여덟 단계를 전부 서버 렌더로 찍어 눈으로 확인할 수 있다.
 *
 * 대화 화면이 집어 가는 것은 `voice/index.ts` 가 내보내는 `VoiceBar` 하나다.
 * 이 파일은 그 안쪽이라 바깥에서 직접 부르지 않는다.
 *
 * 화면에서 지키는 것 셋 — 전부 이미 재고 정한 것이라 취향으로 바꾸지 않는다.
 *
 *  1. **칸의 라벨은 "말한 대로 나왔나요?" 다.** "고쳐 주세요" 로 쓰면 왕초보는 그것을
 *     "더 맞는 영어로 바꾸라" 로 읽고 **자기 오류를 스스로 지운다.** 이 앱은 그 오류를
 *     교정해 주려고 존재하므로, 물어야 할 것은 정확성이 아니라 사실 여부다.
 *  2. **낱말을 흐리게 하거나 색으로 강조하지 않는다.** 두 방법을 실제로 쟀고 둘 다
 *     소음이었다 — 확신도 표시는 정확도 20%, CTC 대조는 낱말의 31%에 깃발이 선다.
 *     정확히 찍을 수 없다는 걸 쟀으니 찍는 시늉을 하지 않고 통째로 고칠 수 있게 둔다.
 *     그래서 `words` 는 이 파일에 아예 들어오지 않는다.
 *  3. **실패는 반드시 보이게 한다.** 조용히 멈추는 것이 이 앱을 React 로 옮긴 이유다.
 */
import { useEffect, useRef } from "react";

import { formatElapsed, type VoiceState } from "./machine";

import styles from "./VoiceBar.module.css";

export interface VoiceBarViewProps {
  state: VoiceState;
  /** 대화 화면이 답을 기다리는 중. 마이크와 보내기를 잠근다. */
  disabled: boolean;
  onStart: () => void;
  onStop: () => void;
  onCancel: () => void;
  onEdit: (draft: string) => void;
  onConfirm: () => void;
}

export function VoiceBarView({
  state,
  disabled,
  onStart,
  onStop,
  onCancel,
  onEdit,
  onConfirm,
}: VoiceBarViewProps) {
  // 확인 칸은 **문장이 다 보여야** 한다. "말한 대로 나왔나요?" 는 끝까지 읽어야
  // 답할 수 있는 질문인데, 높이를 고정하면 뒷부분이 잘린 채로 [이대로 보내기] 를
  // 누르게 된다. 그러면 STT 가 잘못 들은 뒷부분이 그대로 교정 재료가 된다.
  //
  // 잘린 반 줄은 "스크롤하세요" 로 안 읽히고 렌더 오류처럼 보인다는 것도 이유다.
  // 내용에 맞춰 늘리고, 30vh 를 넘을 때만 스크롤한다(max-height 는 CSS 에).
  const draftRef = useRef<HTMLTextAreaElement>(null);
  const draft = state.phase === "review" ? state.draft : "";
  useEffect(() => {
    const el = draftRef.current;
    if (!el) return;
    // 한 번 접었다 펴야 글자를 지웠을 때도 따라 줄어든다.
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  // 이 브라우저·이 주소에서는 아예 안 되는 경우. 마이크 버튼을 그리지 않고 이유만
  // 남긴다 — 눌러도 안 되는 버튼은 "앱이 고장 났다" 로 읽힌다.
  if (state.phase === "blocked") {
    return (
      <div className={styles.blocked} role="status">
        <span aria-hidden="true">🎤</span>
        <p>{state.detail}</p>
      </div>
    );
  }

  if (state.phase === "review") {
    const ready = state.draft.trim().length > 0;
    return (
      <div className={`${styles.review} card`}>
        <label className={styles.askLabel} htmlFor="voice-draft">
          말한 대로 나왔나요?
        </label>
        <p className={styles.askCaption}>
          영어가 틀렸어도 그대로 두세요. 말한 그대로여야 교정을 받을 수 있어요.
        </p>
        <textarea
          id="voice-draft"
          ref={draftRef}
          className={styles.draft}
          value={state.draft}
          onChange={(event) => onEdit(event.target.value)}
          rows={2}
          // 영어 문장이다. 폰 자판이 한글로 뜨지 않게 lang 을 적는다.
          lang="en"
          // **자동 고침을 끈다.** iOS 는 학습자가 말한 틀린 영어를 손도 대기 전에
          // 표준형으로 바꿔 버린다. 그러면 교정할 것이 사라진다 — 이 앱의 재료가 사라진다.
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
        />
        <div className={styles.row}>
          <button type="button" className="btn" onClick={onCancel}>
            다시 말하기
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onConfirm}
            disabled={!ready || disabled}
          >
            이대로 보내기
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.bar}>
      {/* 안내는 늘 같은 자리에 뜬다. 자리가 옮겨 다니면 폰에서 놓친다. */}
      <div className={styles.notice} aria-live="polite">
        {state.phase === "empty" && (
          // 빈 전사는 오류가 아니다. 마이크만 누르고 말을 안 한 흔한 경우다.
          <p className={styles.hint}>안 들렸어요. 조금 더 크게, 다시 말해볼까요?</p>
        )}
        {/* 서버가 준 한국어를 그대로 쓴다. 413·400·503 의 detail 에는 다음에 뭘 하면
            되는지까지 들어 있어서, 우리가 고쳐 쓰면 그 안내가 사라진다. */}
        {state.phase === "failed" && <p className="alert">{state.detail}</p>}
        {state.phase === "recording" && (
          <p className={styles.hint}>말이 끝나면 멈추기를 눌러 주세요.</p>
        )}
      </div>

      {state.phase === "recording" && (
        <div className={styles.row}>
          <button type="button" className={`btn ${styles.stop}`} onClick={onStop}>
            {/* 녹음 중인 것을 색 하나로 알리지 않는다 — 점·시계·글자가 같이 말한다. */}
            <span className={styles.dot} aria-hidden="true" />
            멈추고 보내기
            <span className={styles.elapsed}>{formatElapsed(state.elapsedMs)}</span>
          </button>
          <button type="button" className={`btn ${styles.side}`} onClick={onCancel}>
            취소
          </button>
        </div>
      )}

      {state.phase === "transcribing" && (
        // 3초 발화에 1초쯤 걸린다. 그동안 뭘 하고 있는지 보여야 한다 — 멈춘 화면은
        // 이 앱이 고치려는 바로 그 증상이다.
        <button type="button" className={`btn ${styles.mic}`} disabled>
          <span className={styles.spinner} aria-hidden="true" />
          듣고 있어요…
        </button>
      )}

      {state.phase === "starting" && (
        // 권한 팝업이 떠 있는 동안. 팝업이 안 뜨는 폰도 있어서 빠져나갈 길을 함께 둔다.
        <div className={styles.row}>
          <button type="button" className={`btn ${styles.mic}`} disabled>
            마이크를 켜는 중…
          </button>
          <button type="button" className={`btn ${styles.side}`} onClick={onCancel}>
            취소
          </button>
        </div>
      )}

      {(state.phase === "idle" || state.phase === "empty" || state.phase === "failed") && (
        <button
          type="button"
          className={`btn btn-primary ${styles.mic}`}
          onClick={onStart}
          disabled={disabled}
        >
          <span aria-hidden="true">🎤</span>
          {state.phase === "idle" ? "눌러서 말해보세요" : "다시 말해보기"}
        </button>
      )}
    </div>
  );
}
