/** 음성 입력의 바깥 문. 대화 화면은 이것만 알면 된다.
 *
 *   import { VoiceBar } from "../voice";
 *
 *   <VoiceBar
 *     disabled={streaming}
 *     onConfirm={(message, transcript, words) =>
 *       send({ message, input_mode: "voice", transcript, transcript_words: words })
 *     }
 *   />
 *
 * `onConfirm` 이 값을 객체가 아니라 **세 개로 따로** 넘기는 것은 대화 화면 쪽에
 * 먼저 적혀 있던 계약이라 그대로 맞춘 것이다. 처음에 대화 화면은 이 컴포넌트를
 * `import.meta.glob` 으로 런타임에 찾아 끼웠는데, 그러면 **타입 검사가 두 파일
 * 사이를 이어 주지 않아** 이름이나 순서를 한 글자만 바꿔도 컴파일은 통과하고
 * 폰에서만 깨진다. 지금은 `ChatScreen.tsx` 가 이 파일을 정적으로 import 해서
 * 그 경계가 `tsc` 안에 들어와 있다 — **글롭으로 되돌리지 마라.**
 *
 * 세 값의 뜻: `message` 는 학습자가 확정한 문장, `transcript` 는 STT 가 원래 들은 것,
 * `words` 는 `/stt` 가 준 낱말 배열 그대로. 셋을 따로 남기는 이유는 README 의
 * "음성 입력" 절에 있다 — 둘의 차이가 이 STT 를 믿어도 되는지 알려 주는 유일한 신호다.
 */
export { VoiceBar, type VoiceBarProps } from "./VoiceBar";
export type { VoiceInput, VoiceState } from "./machine";
