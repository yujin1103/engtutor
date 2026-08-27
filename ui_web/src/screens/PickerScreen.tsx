/** 무엇을 연습할지 고르는 화면. 앱을 열면 여기부터 나온다.
 *
 * 시나리오가 33개다. 평평한 목록으로 두면 왕초보가 "뭘 골라야 하지" 에서 멈춘다.
 * 그래서 **분류를 먼저 고르고 그 안으로 들어가는** 구조로 둔다(Streamlit 쪽
 * `render_picker()` 와 같은 흐름). 찾는 게 분명한 사람을 위해 검색도 둔다.
 *
 * 마지막 한 겹이 중요하다. 고르자마자 대화로 들어가지 않고 **먼저 보여준다** —
 * 어떤 상황인지, 뭘 해내야 하는지, 그리고 상대가 뭐라고 말을 걸어올지와 그 뜻.
 * 첫 문장을 모르는 채로 대화에 던져지면 왕초보는 그 자리에서 멈춘다.
 *
 * 안쪽 단계(분류 → 목록 → 상세)는 브라우저 방문 기록에 쌓지 않고 이 화면의
 * 내부 상태로만 둔다. 고르는 도중의 한 걸음까지 기록에 넣으면, 대화하다
 * 뒤로 갔을 때 고르기 화면을 여러 번 지나가야 앱을 벗어난다.
 */
import { useMemo, useState } from "react";

import { Screen } from "../components/Screen";
import type { Catalog } from "../state/catalog";
import { useSettings } from "../state/settings";
import type { ScenarioOut } from "../api/types";

import styles from "./PickerScreen.module.css";

export interface PickerScreenProps {
  catalog: Catalog;
  /** 시나리오를 확정했다. 대화 화면으로 넘어간다. */
  onStart: (scenario: ScenarioOut) => void;
  /** 단어 연습장으로. 대화와 **나란한** 갈래라 여기서 바로 들어간다. */
  onOpenPractice: () => void;
  /** 토익 낱말 목록으로. 연습장과 나란한 갈래다 — 하나는 풀고 하나는 훑는다. */
  onOpenToeic: () => void;
  onOpenSettings: () => void;
}

export function PickerScreen({
  catalog,
  onStart,
  onOpenPractice,
  onOpenToeic,
  onOpenSettings,
}: PickerScreenProps) {
  const { level } = useSettings();
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ScenarioOut | null>(null);

  const needle = query.trim();

  const hits = useMemo(() => {
    if (!needle) return [];
    // 한국어로 찾는다. 제목·상황·목표 어디에 걸려도 된다 — "환불" 처럼
    // 제목에는 없고 목표 문장에만 있는 낱말로 찾는 경우가 많다.
    return catalog.scenarios.filter(
      (s) =>
        s.title.includes(needle) || s.situation.includes(needle) || s.goal.includes(needle),
    );
  }, [catalog.scenarios, needle]);

  // ─────────────────────────────── 고른 뒤: 들어가기 전에 보여주는 화면
  if (selected) {
    const parent = catalog.categories.find((c) => c.id === selected.category);
    return (
      <Screen
        // 바에는 분류를 적는다. 시나리오 제목은 바로 아래 큰 글씨로 또 나오는데,
        // 좁은 화면에서 같은 문장이 두 줄로 겹치면 읽는 사람이 두 번 읽게 된다.
        title={parent ? parent.emoji + " " + parent.label : "연습할 상황"}
        onBack={() => setSelected(null)}
        footer={
          <button
            type="button"
            className="btn btn-primary btn-block"
            onClick={() => onStart(selected)}
          >
            이 상황으로 시작하기
          </button>
        }
      >
        <div className={styles.detail}>
          <div className={styles.detailHead}>
            <span className={styles.badge}>{selected.level}</span>
            <span className={styles.detailTitle}>{selected.title}</span>
          </div>

          <div className={styles.block}>
            <div className={styles.blockLabel}>어떤 상황인가요</div>
            <p>{selected.situation}</p>
          </div>

          <div className={styles.block}>
            <div className={styles.blockLabel}>이걸 해내면 성공</div>
            <p>{selected.goal}</p>
          </div>

          <div>
            <div className={styles.blockLabel}>상대가 먼저 이렇게 말해요</div>
            <div className={styles.opening}>
              <p className={styles.openingEn}>{selected.opening_line}</p>
              <p className={styles.openingKo}>{selected.opening_line_ko}</p>
            </div>
          </div>

          {selected.level !== level && (
            <p className={styles.levelNote}>
              이 대화는 {selected.level} 에 맞춰 만들었어요. 지금 설정한 레벨은 {level} 이라
              조금 더 {selected.level < level ? "쉽게" : "어렵게"} 느껴질 수 있어요.
            </p>
          )}
        </div>
      </Screen>
    );
  }

  // ─────────────────────────────── 분류 하나를 열어 본 화면
  if (categoryId) {
    const category = catalog.categories.find((c) => c.id === categoryId);
    const inCategory = catalog.scenarios.filter((s) => s.category === categoryId);
    return (
      <Screen
        title={category ? `${category.emoji} ${category.label}` : "시나리오"}
        onBack={() => setCategoryId(null)}
      >
        {category && <p className="muted" style={{ marginBottom: "var(--gap)" }}>{category.blurb}</p>}
        <ScenarioList scenarios={inCategory} onPick={setSelected} />
      </Screen>
    );
  }

  // ─────────────────────────────── 처음 화면
  return (
    <Screen
      title="무엇을 연습할까요?"
      action={
        <button
          type="button"
          className="btn"
          style={{ padding: "0 12px" }}
          onClick={onOpenSettings}
        >
          설정
        </button>
      }
    >
      <input
        className={styles.search}
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="카페, 택시, 병원, 환불…"
        aria-label="시나리오 찾기"
        // 영어 앱이지만 검색어는 한국어다. 폰에서 영문 자판이 먼저 뜨면 한 번 더 눌러야 한다.
        inputMode="search"
        enterKeyHint="search"
      />

      {needle ? (
        <>
          <p className="muted" style={{ marginBottom: "var(--gap)" }}>
            '{needle}' — {hits.length}개
          </p>
          <ScenarioList scenarios={hits} onPick={setSelected} />
        </>
      ) : (
        <>
          {/* 대화와 나란한 두 번째 갈래. 분류 카드들 위에 따로 세운다 —
              분류 격자 안에 끼워 넣으면 "일곱 번째 상황" 으로 읽힌다.
              검색 중일 때는 안 보인다. 찾는 대화가 분명한 사람의 결과 목록
              위에 다른 갈래를 밀어 넣을 이유가 없다. */}
          <button type="button" className={styles.practice} onClick={onOpenPractice}>
            <span className={styles.practiceEmoji} aria-hidden="true">
              ✏️
            </span>
            <span className={styles.practiceText}>
              <span className={styles.practiceTitle}>단어 연습장</span>
              <span className={styles.practiceBlurb}>
                빈칸에 들어갈 말을 찾고, 왜 그런지까지 배워요
              </span>
            </span>
            <span className={styles.chevron} aria-hidden="true">
              ›
            </span>
          </button>

          {/* 세 번째 갈래. 연습장 바로 아래에 같은 모양으로 세운다 — 둘 다
              "대화 말고 낱말" 쪽이고, 하나는 풀고 하나는 훑는다. */}
          <button type="button" className={styles.practice} onClick={onOpenToeic}>
            <span className={styles.practiceEmoji} aria-hidden="true">
              📗
            </span>
            <span className={styles.practiceText}>
              <span className={styles.practiceTitle}>토익 단어</span>
              <span className={styles.practiceBlurb}>
                시험에 자주 나오는 차례로 훑고, 단어장에 담아요
              </span>
            </span>
            <span className={styles.chevron} aria-hidden="true">
              ›
            </span>
          </button>

          <p className={`muted ${styles.lead}`}>
            상황을 하나 고르세요. 전부 {catalog.scenarios.length}개의 대화가 있어요.
          </p>
          <div className={styles.categories}>
            {catalog.categories.map((category) => (
              <button
                key={category.id}
                type="button"
                className={styles.category}
                onClick={() => setCategoryId(category.id)}
              >
                <span className={styles.emoji} aria-hidden="true">
                  {category.emoji}
                </span>
                <span className={styles.categoryLabel}>{category.label}</span>
                <span className={styles.blurb}>{category.blurb}</span>
                <span className={styles.count}>{category.count}개</span>
              </button>
            ))}
          </div>
        </>
      )}
    </Screen>
  );
}

function ScenarioList({
  scenarios,
  onPick,
}: {
  scenarios: ScenarioOut[];
  onPick: (scenario: ScenarioOut) => void;
}) {
  if (scenarios.length === 0) {
    return <p className={styles.empty}>찾는 대화가 없어요. 다른 낱말로 찾아보세요.</p>;
  }

  return (
    <div className={styles.list}>
      {scenarios.map((scenario) => (
        <button
          key={scenario.id}
          type="button"
          className={styles.row}
          onClick={() => onPick(scenario)}
        >
          <span className={styles.badge}>{scenario.level}</span>
          <span className={styles.rowText}>
            <span className={styles.rowTitle}>{scenario.title}</span>
            <span className={styles.rowGoal} style={{ display: "block" }}>
              {scenario.goal}
            </span>
          </span>
          <span className={styles.chevron} aria-hidden="true">
            ›
          </span>
        </button>
      ))}
    </div>
  );
}
