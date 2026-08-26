/** 말로 답하는 칸. 대화 화면이 입력줄 위에 끼워 넣는다.
 *
 * 이 파일은 **잇는 일만** 한다 — 녹음기(`useVoiceRecorder`)와 그리는 쪽(`VoiceBarView`)을
 * 붙이고, 학습자가 확정한 문장을 부모에게 넘긴다. 규칙은 두 이웃 파일에 있다.
 *   - `machine.ts`     — 어느 단계에서 무엇이 먹는가 (시험이 덮고 있다)
 *   - `VoiceBarView.tsx` — 단계마다 무엇을 그리는가
 *
 * **이 컴포넌트는 `/chat` 을 부르지 않는다.** 보내는 일과 스트리밍은 대화 화면이
 * 소유한다. 그래야 말로 하든 타자로 치든 대화 흐름을 다루는 코드가 한 벌로 남는다.
 */
import type { SttWordOut } from "../api/types";

import { VoiceBarView } from "./VoiceBarView";
import { useVoiceRecorder } from "./useVoiceRecorder";

export interface VoiceBarProps {
  /**
   * 학습자가 확인·수정을 끝냈다. **전사가 나오자마자 부르지 않는다** — 확인 칸이 있는
   * 이유가 STT 가 학습자의 오류를 지워 버리기 때문이고, 그 오류가 이 앱의 재료다.
   *
   * @param message 학습자가 **확정한** 문장. 이게 `/chat/stream` 의 `message` 가 된다.
   * @param transcript STT 가 원래 들은 문장. 고치기 전 원본 그대로.
   * @param words `/stt` 가 준 words 배열. 손대지 말고 그대로 넘긴다.
   */
  onConfirm: (message: string, transcript: string, words: SttWordOut[]) => void;
  /** 대화 화면이 답을 기다리는 중. 마이크와 보내기를 잠가 한 턴에 두 번 보내지 않게 한다. */
  disabled?: boolean;
}

export function VoiceBar({ onConfirm, disabled = false }: VoiceBarProps) {
  const voice = useVoiceRecorder();

  function handleConfirm(): void {
    const input = voice.confirm();
    // null 이면 넘길 게 없다(빈 칸이거나 이미 넘겼다). 두 번 눌러도 두 번 가지 않는다.
    if (input) onConfirm(input.message, input.transcript, input.transcript_words);
  }

  return (
    <VoiceBarView
      state={voice.state}
      disabled={disabled}
      onStart={voice.start}
      onStop={voice.stop}
      onCancel={voice.cancel}
      onEdit={voice.edit}
      onConfirm={handleConfirm}
    />
  );
}
