/** 토익 Part 5 형 문법 문제. 대화·연습장·낱말 목록과 나란한 네 번째 갈래다.
 *
 * 연습장과 무엇이 다른가 — 연습장은 **뜻**을 묻는다(빈칸에 어느 낱말이 오나).
 * 여기는 **형태**를 묻는다. 보기 넷이 전부 같은 낱말의 다른 모양이라 뜻은 이미
 * 알려 준 것과 같고, 가르는 것은 "이 자리에 어느 모양이 오는가" 하나다. 그래서
 * 연습장 화면을 트랙만 바꿔 다시 쓸 수 없었다 — 답이 자유 입력이 아니고,
 * 품사 힌트가 여기서는 정답 그 자체라 띄우면 안 된다.
 *
 * **여기에 고르기 겹이 없는 이유.** 연습장은 장면(팩)을 먼저 고르지만
 * (`practice/DeckPicker`), 문법은 지금 규칙이 하나뿐이고 규칙 목록을 주는
 * 엔드포인트도 없다. 화면이 규칙 이름 표를 들고 있으면 규칙을 하나 더 만들 때
 * 한쪽을 빠뜨리게 되므로, 규칙 이름을 아예 안 보내고 서버 기본값을 받는다.
 * 무엇을 푸는지는 응답의 `rule_title` 이 본문에서 말한다. 규칙이 늘고 목록
 * 엔드포인트가 생기면 `DeckPicker` 에 해당하는 겹이 이 자리에 들어온다.
 *
 * 이 파일이 정하는 것은 화면의 이름 하나다. 푸는 일은 `grammar/GrammarRun` 이
 * 한다(연습장의 `PracticeScreen` → `PracticeRun` 과 같은 나눔이다).
 */
import { GrammarRun } from "./grammar/GrammarRun";

const TITLE = "문법 문제";

export interface GrammarScreenProps {
  onBack: () => void;
}

export function GrammarScreen({ onBack }: GrammarScreenProps) {
  return <GrammarRun title={TITLE} onBack={onBack} />;
}
