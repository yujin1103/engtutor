/** 타자로 답하는 칸. 화면 맨 아래, 엄지 닿는 자리에 고정된다.
 *
 * **브라우저의 자동 고침을 전부 끈다.** 폰 키보드는 `ice americano` 를
 * `Ice Americano` 로, `I go store` 를 제 나름대로 손봐 준다. 그러면 학습자가
 * 실제로 쓴 오류가 서버에 닿기 전에 사라지고, 이 앱은 **그 오류를 교정해 주려고
 * 존재하므로** 교정할 것이 없어진다. 음성 확인 칸의 라벨을 “고쳐 주세요” 가
 * 아니라 “말한 대로 나왔나요?” 로 쓰는 것과 같은 이유다.
 *
 * `<form>` 으로 감싼 이유는 폰 키보드의 확인 키를 쓰기 위해서다. 이걸 안 하면
 * 학습자가 키보드를 내리고 보내기 버튼을 다시 찾아 눌러야 한다.
 */
import { useRef } from "react";
import type { FormEvent, RefObject } from "react";

import styles from "./Composer.module.css";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (message: string) => void;
  /** 답을 기다리는 중. 한 턴에 두 번 보내는 것을 막는다. */
  busy: boolean;
  /** 실패한 말을 되돌려 놓았을 때 여기로 커서를 보내려고 화면 쪽에서 들고 있는다. */
  inputRef?: RefObject<HTMLInputElement | null>;
}

export function Composer({ value, onChange, onSend, busy, inputRef }: ComposerProps) {
  const fallbackRef = useRef<HTMLInputElement | null>(null);
  const ref = inputRef ?? fallbackRef;
  const ready = value.trim().length > 0 && !busy;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    onSend(value.trim());
    // 키보드를 내리지 않는다. 왕초보는 한 턴에 여러 번 고쳐 쓰는데, 보낼 때마다
    // 키보드가 접히면 다음 말을 시작하는 데만 두 번 더 눌러야 한다.
    ref.current?.focus();
  };

  return (
    <form className={styles.composer} onSubmit={submit}>
      <input
        ref={ref}
        className={styles.input}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="영어로 말해보세요"
        aria-label="영어로 답하기"
        // 서버 제약과 같은 값. 넘겨 보내 봐야 422 로 돌아온다.
        maxLength={1000}
        // **답을 기다리는 동안에도 칸은 잠그지 않는다.** 입력칸을 disabled 로
        // 두면 폰 키보드가 내려가고 포커스가 풀려서, 다음 말을 하려면 다시
        // 두 번을 눌러야 한다. 두 번 보내는 것만 막으면 되고 그건 버튼이 한다.
        // 기다리는 중이라는 사실은 대화 쪽 말풍선이 이미 말하고 있다.
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="send"
      />
      <button type="submit" className="btn btn-primary" disabled={!ready}>
        보내기
      </button>
    </form>
  );
}
