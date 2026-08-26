/** 레벨과 교정 강도를 고치는 접이칸. 설정 화면과 대화 화면이 같이 쓴다.
 *
 * **접어 두는 게 기본이다.** 폰 세로 화면에서 선택지 여섯 줄을 펼쳐 두면 화면의
 * 절반이 설정으로 찬다. 그렇다고 숨기면 안 된다 — 지금 어떤 레벨로 말하고 있는지
 * 모르는 채로 대화하면, 답이 어려운 게 상대 탓인지 내 설정 탓인지 알 수 없다.
 * 그래서 접힌 줄에 **지금 값을 그대로 적어 둔다.** 바꿀 때만 펼친다.
 *
 * 한 번에 하나만 펼친다. 둘 다 펼쳐지면 결국 처음의 여섯 줄로 돌아간다.
 *
 * 라디오 대신 버튼 줄을 쓴 이유: 기본 라디오는 동그라미만 눌리는데 그 지름이
 * 20px 도 안 된다. 폰에서 한 손으로 쓰려면 줄 전체가 눌려야 한다.
 *
 * 값은 `useSettings()`(state/settings.ts)에 있다. 그 프로바이더가 화면 전환보다
 * 바깥에 있어서 **대화를 시작한 뒤에 바꿔도, 화면을 옮겨도 값이 풀리지 않는다** —
 * Streamlit 에서 실제로 풀렸던 버그라 여기 적어 둔다.
 */
import { useId, useState } from "react";
import type { ReactNode } from "react";

import type { Level, StrictnessOut } from "../../api/types";
import { useSettings } from "../../state/settings";
import { useStrictnessOptions } from "./strictnessOptions";

import styles from "./SettingsFold.module.css";

/** 레벨 설명은 서버가 주지 않는다. 여기가 유일한 출처다. */
const LEVELS: { key: Level; label: string; caption: string }[] = [
  { key: "A1", label: "A1 · 왕초보", caption: "짧은 문장으로 천천히. 처음이라면 여기부터." },
  { key: "A2", label: "A2 · 기초", caption: "일상적인 이야기를 조금 더 길게." },
  { key: "B1", label: "B1 · 조금 익숙", caption: "이유를 설명하거나 부탁하는 말까지." },
];

export interface SettingsFoldProps {
  /**
   * `/strictness` 가 준 목록. App 이 이미 받아 뒀으면 넘겨주고, 못 받는 자리
   * (대화 화면 등)에서는 **빼도 된다** — 그때는 스스로 한 번 받아 온다.
   */
  strictness?: StrictnessOut[];
}

export function SettingsFold({ strictness }: SettingsFoldProps) {
  const settings = useSettings();
  const modes = useStrictnessOptions(strictness);
  const [open, setOpen] = useState<"level" | "strictness" | null>(null);

  const levelLabel = LEVELS.find((l) => l.key === settings.level)?.label ?? settings.level;
  const modeLabel = modes.find((m) => m.key === settings.strictness)?.label ?? "";

  return (
    <div className={styles.fold}>
      <Row
        label="레벨"
        value={levelLabel}
        open={open === "level"}
        onToggle={() => setOpen(open === "level" ? null : "level")}
      >
        <Options ariaLabel="레벨">
          {LEVELS.map((level) => (
            <Option
              key={level.key}
              title={level.label}
              caption={level.caption}
              checked={settings.level === level.key}
              // 고르면 접는다. 고른 값이 접힌 줄에 그대로 나타나 "적용됐다" 가 보인다.
              onPick={() => {
                settings.setLevel(level.key);
                setOpen(null);
              }}
            />
          ))}
        </Options>
      </Row>

      {/* 목록을 못 받았으면 이 칸만 조용히 빠진다. 이유는 strictnessOptions.ts 에 있다. */}
      {modes.length > 0 && (
        <Row
          label="교정 강도"
          value={modeLabel}
          open={open === "strictness"}
          onToggle={() => setOpen(open === "strictness" ? null : "strictness")}
        >
          <Options ariaLabel="교정 강도">
            {modes.map((mode) => (
              <Option
                key={mode.key}
                title={mode.label}
                caption={mode.caption}
                checked={settings.strictness === mode.key}
                onPick={() => {
                  settings.setStrictness(mode.key);
                  setOpen(null);
                }}
              />
            ))}
          </Options>
        </Row>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  open,
  onToggle,
  children,
}: {
  label: string;
  value: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const panelId = useId();

  return (
    <div className={styles.row}>
      <button
        type="button"
        className={styles.head}
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
      >
        <span className={styles.headLabel}>{label}</span>
        {/* 접혀 있어도 지금 값은 늘 보인다. 이 줄이 이 부품의 존재 이유다. */}
        <span className={styles.headValue}>{value}</span>
        <span className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`} aria-hidden="true">
          ⌄
        </span>
      </button>
      {open && (
        <div className={styles.panel} id={panelId}>
          {children}
        </div>
      )}
    </div>
  );
}

function Options({ ariaLabel, children }: { ariaLabel: string; children: ReactNode }) {
  return (
    <div className={styles.options} role="radiogroup" aria-label={ariaLabel}>
      {children}
    </div>
  );
}

function Option({
  title,
  caption,
  checked,
  onPick,
}: {
  title: string;
  caption: string;
  checked: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      className={styles.option}
      onClick={onPick}
    >
      <span className={styles.mark} aria-hidden="true" />
      <span className={styles.optionText}>
        <span className={styles.optionTitle}>{title}</span>
        <span className={styles.optionCaption}>{caption}</span>
      </span>
    </button>
  );
}
