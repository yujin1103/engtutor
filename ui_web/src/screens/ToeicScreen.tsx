/** 토익 낱말을 빈도 순으로 훑는 화면. 연습장과 달리 **읽는** 자리다.
 *
 * 왜 연습장과 화면을 갈랐나. 푸는 것과 훑는 것은 다른 일이다 — 2,102개를 빈칸으로
 * 만나면 한 번에 한 문장씩만 보이고, 시험을 보러 온 사람이 원하는 "어디까지
 * 봤는지" 도 남지 않는다. 그래서 목록은 목록대로 두고, 그 자리에서 같은 낱말을
 * 빈칸으로 풀고 싶으면 **기존 연습장을 토익 트랙으로 연다**(화면을 새로 만들지
 * 않는다. 푸는 방식이 같은데 화면이 둘이면 둘 다 낡는다).
 *
 * 표시 셋(어디까지 봤나·외움·단어장)은 전부 폰에 남는다. 그 이유는 marks.ts 에.
 */
import { useCallback, useState } from "react";

import { ErrorNotice, Loading } from "../components/Notice";
import { Screen } from "../components/Screen";
import { WordCard } from "./toeic/WordCard";
import { MAX_SAVED, loadMarks, saveMarks, toggle } from "./toeic/marks";
import { useWordbook, useWordList } from "./toeic/useWordList";

import styles from "./toeic/toeic.module.css";

/** 이 화면이 보는 어휘 트랙. 서버 기본값은 생활 회화라 반드시 실어 보낸다. */
export const TRACK = "toeic";

const TITLE = "토익 단어";

type Tab = "list" | "book";

export interface ToeicScreenProps {
  onBack: () => void;
  /** 같은 낱말을 빈칸으로. 기존 연습장을 이 트랙으로 연다. */
  onPractice: () => void;
}

export function ToeicScreen({ onBack, onPractice }: ToeicScreenProps) {
  // 폰에 적어 둔 표시는 첫 렌더 때 한 번만 읽는다. 매 렌더마다 읽으면 목록을
  // 스크롤하는 동안 localStorage 를 수백 번 두드리게 된다.
  const [marks, setMarks] = useState(loadMarks);
  const [tab, setTab] = useState<Tab>("list");
  const list = useWordList(TRACK, marks.offset);
  const book = useWordbook(TRACK, tab === "book" ? marks.saved : []);

  /**
   * 다음 장을 받으면서 **그 자리를 적어 둔다.**
   *
   * 효과에서 적지 않는 이유가 있다. 진도가 바뀌는 사건은 "더 보기를 눌렀다" 하나뿐이라
   * 그 자리에서 적는 것이 맞고, 효과에 두면 렌더가 한 번 더 돈다. 적는 값은 지금
   * 화면이 아니라 **다음에 받을 자리**다 — 다시 열었을 때 방금 본 장 다음이 나온다.
   */
  const more = useCallback(() => {
    const at = list.offset;
    list.more();
    setMarks((prev) => {
      if (prev.offset === at) return prev;
      const next = { ...prev, offset: at };
      saveMarks(next);
      return next;
    });
  }, [list]);

  const mark = useCallback((key: "known" | "saved", word: string) => {
    setMarks((prev) => {
      const next = {
        ...prev,
        [key]: toggle(prev[key], word, key === "saved" ? MAX_SAVED : undefined),
      };
      saveMarks(next);
      return next;
    });
  }, []);

  const onKnown = useCallback((word: string) => mark("known", word), [mark]);
  const onSaved = useCallback((word: string) => mark("saved", word), [mark]);

  const cards = (
    <>
      {tab === "list" && list.state.status === "ready" && (
        <>
          <p className={`muted ${styles.lead}`}>
            자주 나오는 차례로 놓았어요. 낱말 앞 숫자는 토익 낱말 가운데 몇째로 자주
            나오는지라, 중간 번호가 비기도 해요.
            {list.state.resumed && (
              <>
                {" "}
                보던 자리에서 이어 왔어요.{" "}
                <button type="button" className={styles.link} onClick={list.restart}>
                  처음부터 보기
                </button>
              </>
            )}
          </p>
          {list.state.cards.map((card) => (
            <WordCard
              key={card.word}
              card={card}
              known={marks.known.includes(card.word)}
              saved={marks.saved.includes(card.word)}
              onToggleKnown={onKnown}
              onToggleSaved={onSaved}
            />
          ))}
          {list.state.done ? (
            <p className={`muted ${styles.end}`}>여기까지예요.</p>
          ) : (
            <button
              type="button"
              className={styles.more}
              onClick={more}
              disabled={list.state.busy}
            >
              {list.state.busy ? "받아 오는 중…" : "더 보기"}
            </button>
          )}
        </>
      )}

      {tab === "book" && book.status === "ready" && book.cards.length === 0 && (
        <p className={`muted ${styles.empty}`}>
          아직 담은 낱말이 없어요. 목록에서 ☆ 를 누르면 여기에 모여요.
        </p>
      )}
      {tab === "book" &&
        book.status === "ready" &&
        book.cards.map((card) => (
          <WordCard
            key={card.word}
            card={card}
            known={marks.known.includes(card.word)}
            saved
            onToggleKnown={onKnown}
            onToggleSaved={onSaved}
          />
        ))}
    </>
  );

  return (
    <Screen
      title={TITLE}
      onBack={onBack}
      footer={
        <button type="button" className="btn btn-primary btn-block" onClick={onPractice}>
          빈칸으로 연습하기
        </button>
      }
    >
      <div className={styles.tabs} role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "list"}
          className={`${styles.tab} ${tab === "list" ? styles.tabOn : ""}`}
          onClick={() => setTab("list")}
        >
          전체
          {list.state.status === "ready" && (
            <span className={styles.count}>{list.state.total.toLocaleString("ko-KR")}</span>
          )}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "book"}
          className={`${styles.tab} ${tab === "book" ? styles.tabOn : ""}`}
          onClick={() => setTab("book")}
        >
          단어장
          <span className={styles.count}>{marks.saved.length}</span>
        </button>
      </div>

      {tab === "list" && list.state.status === "loading" && (
        <Loading label="낱말을 불러오는 중이에요" />
      )}
      {tab === "list" && list.state.status === "failed" && (
        <ErrorNotice detail={list.state.detail} onRetry={list.retry} />
      )}
      {tab === "book" && book.status === "loading" && (
        <Loading label="단어장을 불러오는 중이에요" />
      )}
      {tab === "book" && book.status === "failed" && <ErrorNotice detail={book.detail} />}

      {cards}
    </Screen>
  );
}
