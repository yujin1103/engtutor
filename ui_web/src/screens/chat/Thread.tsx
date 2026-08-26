/** 주고받은 말을 그리는 곳.
 *
 * 화면에 그리는 순서를 제품 규칙이 정해 준다.
 *
 *  - **`reply` 는 홀로 있는다.** 교정을 말풍선 안에 섞으면 대화가 끊긴다.
 *    교정은 말풍선 **아래**, 접어 두는 칸에 따로 그린다.
 *  - **왕초보는 상대의 영어도 못 읽는다.** 그래서 해석을 볼 수 있게 두되
 *    기본은 접는다 — 먼저 영어로 읽어 보고 막히면 여는 순서다.
 *  - **고칠 것(mistake)과 다듬을 것(polish)을 같은 무게로 보여주지 않는다.**
 *    왕초보에게 둘을 나란히 늘어놓으면 “내 영어는 전부 틀렸다” 로 읽혀 위축된다.
 *    고칠 것만 펴 놓고, 다듬을 것은 접어 둔다.
 *
 * 접고 펴는 데 `<details>` 를 쓴다. 상태를 들고 있을 필요가 없고, 폰의 화면
 * 읽기 기능이 “접힘/펼침” 을 스스로 읽어 준다. 직접 만들면 둘 다 손해다.
 */
import { useEffect, useState } from "react";
import type { Ref } from "react";

import type { Correction, TurnResponse } from "../../api/types";
import type { ChatEntry, Failure } from "./useChat";

import styles from "./Thread.module.css";

// ─────────────────────────────────────────────── 교정

function CorrectionItems({ items, strike }: { items: Correction[]; strike: boolean }) {
  return (
    <ul className={styles.corrections}>
      {items.map((c, i) => (
        // 같은 원문이 두 번 나올 수 있어 index 를 섞어 열쇠로 쓴다. 목록이 다시
        // 정렬되거나 중간에 끼어드는 일이 없어서 이걸로 충분하다.
        <li key={`${c.original}-${i}`} className={styles.correction}>
          <p className={strike ? styles.original : styles.originalPlain}>{c.original}</p>
          <p className={styles.better}>→ {c.better}</p>
          <p className={styles.note}>{c.note}</p>
        </li>
      ))}
    </ul>
  );
}

function Corrections({ items }: { items: Correction[] }) {
  // kind 가 없으면 mistake 로 본다 — 옛 기록과 섞여도 조용히 빠지지 않게.
  const mistakes = items.filter((c) => c.kind !== "polish");
  const polish = items.filter((c) => c.kind === "polish");
  if (mistakes.length === 0 && polish.length === 0) return null;

  return (
    <div className={styles.fixes}>
      {mistakes.length > 0 && (
        <details className={styles.fold} open>
          <summary className={styles.summary}>✏️ 고쳐볼까요 ({mistakes.length})</summary>
          <CorrectionItems items={mistakes} strike />
        </details>
      )}
      {polish.length > 0 && (
        <details className={styles.fold}>
          <summary className={styles.summary}>
            ✨ 이렇게 하면 더 자연스러워요 ({polish.length})
          </summary>
          <CorrectionItems items={polish} strike={false} />
        </details>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────── 말풍선

function AiTurn({ turn }: { turn: TurnResponse }) {
  return (
    <div className={styles.aiBlock}>
      <div className={styles.aiBubble}>
        <p className={styles.aiText}>{turn.reply}</p>
      </div>
      {turn.reply_ko && (
        <details className={styles.fold}>
          <summary className={styles.summary}>🇰🇷 해석 보기</summary>
          <p className={styles.foldBody}>{turn.reply_ko}</p>
        </details>
      )}
      <Corrections items={turn.corrections} />
    </div>
  );
}

function MyLine({ text, voice, failed }: { text: string; voice: boolean; failed: boolean }) {
  return (
    <div className={`${styles.myBubble} ${failed ? styles.myFailed : ""}`}>
      {voice && (
        <span className={styles.mic} aria-label="말해서 보낸 문장">
          🎙
        </span>
      )}
      <span>{text}</span>
    </div>
  );
}

// ─────────────────────────────────────────────── 기다리는 중

/** 이 컴포넌트가 살아 있는 동안의 초. 답을 기다릴 때만 붙었다 떨어진다. */
function useSeconds(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, []);
  return seconds;
}

/**
 * 답이 오는 중에 보여주는 자리.
 *
 * 글자가 왔으면 그 글자를, 아직이면 점 세 개와 **몇 초째인지**를 보여준다.
 * 초를 적는 게 핵심이다 — 왕초보에게 8초 침묵은 “고장” 으로 읽히는데, 숫자가
 * 올라가고 있으면 적어도 “돌아가고 있다” 는 것은 보인다. 로컬 14B 는 처음 한 번
 * 모델을 올리느라 오래 걸리므로 그 사실도 20초쯤에 적어 준다.
 */
function Pending({ draft, rewriting }: { draft: string; rewriting: boolean }) {
  const seconds = useSeconds();

  return (
    <div className={styles.aiBlock}>
      <div className={styles.aiBubble}>
        {draft ? (
          <p className={styles.aiText}>
            {draft}
            <span className={styles.caret} aria-hidden="true" />
          </p>
        ) : (
          <p className={styles.dots} aria-hidden="true">
            <span />
            <span />
            <span />
          </p>
        )}
      </div>

      <p className={styles.waiting} role="status">
        {rewriting
          ? // reset 이 오면 여태 그린 글자를 버린다. 글자가 갑자기 사라진 이유를
            // 적어 주지 않으면 사용자는 앱이 뭔가 잘못한 줄 안다.
            "답을 다시 쓰고 있어요"
          : seconds >= 4
            ? `답을 만들고 있어요 · ${seconds}초`
            : "답을 만들고 있어요"}
      </p>
      {seconds >= 20 && !draft && (
        <p className={styles.waitingMore}>
          처음 한 번은 모델을 불러오느라 오래 걸릴 수 있어요. 조금만 기다려 주세요.
        </p>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────── 실패

/**
 * 끊겼을 때 보여주는 칸. **이 화면이 존재하는 이유가 이 조각이다.**
 *
 * 예전에는 여기서 아무것도 안 나왔고, 사용자는 앱이 죽은 줄 알았다. 그래서
 * (1) 무슨 일인지 서버가 준 한국어 그대로, (2) 다음에 뭘 할 수 있는지를
 * 버튼으로, (3) 다시 보내면 같은 말이 두 번 쌓일 수 있다는 사실까지 적는다.
 *
 * (3)은 아무 때나 적지 않는다. 글자가 오다가 끊긴 경우에만 서버가 이미 답을
 * 만들어 저장했을 수 있다. 한 글자도 못 받았으면 그 걱정이 없으니 말하지 않는다.
 */
function FailureNotice({
  failure,
  onRetry,
  onEdit,
}: {
  failure: Failure;
  onRetry: () => void;
  onEdit: () => void;
}) {
  return (
    <div className={styles.failure} role="alert">
      <p className={styles.failureText}>{failure.detail}</p>
      {failure.midway && (
        <p className={styles.failureHint}>
          서버는 이미 답을 만들어 저장했을 수도 있어요. 다시 보내면 같은 말이 두 번 들어갈 수
          있어요.
        </p>
      )}
      <div className={styles.failureButtons}>
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          다시 보내기
        </button>
        <button type="button" className="btn" onClick={onEdit}>
          고쳐 쓰기
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────── 목록

export interface ThreadProps {
  entries: ChatEntry[];
  streaming: boolean;
  draft: string;
  rewriting: boolean;
  failure: Failure | null;
  onRetry: () => void;
  /** 실패한 말을 입력칸으로 되돌린다. */
  onEdit: () => void;
  endRef: Ref<HTMLDivElement>;
}

export function Thread({
  entries,
  streaming,
  draft,
  rewriting,
  failure,
  onRetry,
  onEdit,
  endRef,
}: ThreadProps) {
  return (
    <div className={styles.thread}>
      {entries.map((entry) =>
        entry.role === "ai" ? (
          <AiTurn key={entry.id} turn={entry.turn} />
        ) : (
          <MyLine
            key={entry.id}
            text={entry.text}
            voice={entry.mode === "voice"}
            failed={entry.failed}
          />
        ),
      )}

      {streaming && <Pending draft={draft} rewriting={rewriting} />}
      {failure && <FailureNotice failure={failure} onRetry={onRetry} onEdit={onEdit} />}

      {/* 바닥을 잡아 두는 자리. 여기에 붙어 있어야 새 글자를 따라 내려갈 수 있다. */}
      <div ref={endRef} />
    </div>
  );
}
