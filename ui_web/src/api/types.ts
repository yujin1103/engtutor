/** 서버가 실제로 주고받는 모양.
 *
 * 손으로 지어내지 않았다. 돌고 있는 api 컨테이너의 `/openapi.json` 을 받아
 * `components.schemas` 를 그대로 옮긴 것이다. 다시 확인하려면:
 *
 *   docker compose exec -T api python -c "import httpx,json; \
 *     print(json.dumps(httpx.get('http://localhost:8000/openapi.json').json(),ensure_ascii=False))"
 *
 * 서버 스키마가 바뀌면 이 파일이 먼저 틀린다. 앱을 고치기 전에 위 명령으로
 * 다시 맞춰라 — 추측해서 고치면 런타임에서만 터진다.
 */

// ─────────────────────────────────────────────── 값이 정해져 있는 것들

/** CEFR 레벨. 서버 enum 그대로. */
export type Level = "A1" | "A2" | "B1";

/** 교정 강도. `/strictness` 가 라벨과 설명을 따로 내려준다 — 화면 문구를 여기 적지 않는다. */
export type StrictnessKey = "gentle" | "balanced" | "strict";

/** 이 턴을 타자로 쳤는지 말로 했는지. 음성이면 전사 원본을 함께 보낸다. */
export type InputMode = "text" | "voice";

/**
 * mistake = 진짜 틀렸거나 듣는 사람이 헷갈릴 것.
 * polish  = 영어로 맞지만 원어민이면 다르게 말할 것.
 * 유연(gentle) 모드에서는 서버가 polish 를 걷어내고 보낸다.
 */
export type CorrectionKind = "mistake" | "polish";

// ─────────────────────────────────────────────── 고르기 화면

export interface ScenarioOut {
  id: string;
  title: string;
  /** CategoryOut.id 와 맞물린다. */
  category: string;
  level: string;
  /** 어떤 상황인지 (한국어). */
  situation: string;
  /** 학습자가 해내야 할 것 (한국어). */
  goal: string;
  /** AI 가 먼저 던지는 영어 한 줄. 대화는 여기서 시작한다. */
  opening_line: string;
  /** 위 첫 대사의 한국어 번역. */
  opening_line_ko: string;
  /** 학습자가 지금 그대로 말하면 되는 영어 한 줄. */
  opening_say_en: string;
  /** 한 걸음 더 긴 표현. */
  opening_say_more: string;
  /** 무슨 상황인지 + say_en 이 무슨 뜻인지 (한국어). */
  opening_hint_ko: string;
}

export interface CategoryOut {
  id: string;
  label: string;
  emoji: string;
  blurb: string;
  /** 이 분류에 든 시나리오 개수. 서버가 세어서 준다. */
  count: number;
}

export interface StrictnessOut {
  key: StrictnessKey;
  label: string;
  caption: string;
}

// ─────────────────────────────────────────────── 대화

export interface Correction {
  /** 학습자가 실제로 쓴 표현. 그대로 인용된다. */
  original: string;
  kind: CorrectionKind;
  better: string;
  /** 한국어 설명 (해요체). */
  note: string;
}

export interface TurnResponse {
  /** 배역을 유지한 영어 답. **여기에 교정을 섞지 않는다** — 서버도 안 섞고 화면도 안 섞는다. */
  reply: string;
  /** `reply` 의 한국어 번역. */
  reply_ko: string;
  /** 직전 발화에 대한 교정. 문제없으면 빈 배열. */
  corrections: Correction[];
  /** 지금 그대로 말하면 되는 영어 한 줄. */
  say_en: string;
  /** 한 걸음 더 긴 표현. 비어 있지 않다. */
  say_more: string;
  /** 방금 무슨 일이 있었고 say_en 이 무슨 뜻인지 (한국어). */
  hint_ko: string;
}

export interface ChatRequest {
  scenario_id: string;
  /**
   * 학습자가 **확정한** 문장. 음성이라도 전사 그대로가 아니라 고친 뒤의 것이다.
   * 서버 제약: 1~1000자.
   */
  message: string;
  /** 첫 턴에는 없다. `session` 사건으로 받은 값을 다음 요청부터 넣는다. */
  session_id?: string | null;
  level?: Level | null;
  strictness?: StrictnessKey;
  input_mode?: InputMode;
  /** 음성일 때만. STT 가 원래 들은 문장 (최대 2000자). */
  transcript?: string | null;
  /** 음성일 때만. `/stt` 가 준 words 배열을 **그대로** 되돌려 보낸다. */
  transcript_words?: SttWordOut[] | null;
}

export interface ChatResponse {
  session_id: string;
  turn: TurnResponse;
}

// ─────────────────────────────────────────────── 음성 입력

export interface SttWordOut {
  word: string;
  /** STT 가 준 확률. **화면에 그리라고 주는 값이 아니다** — 기록용이다. */
  probability?: number | null;
}

export interface SttResponse {
  /** 전사 결과. **빈 문자열은 오류가 아니다** — 말을 안 한 경우다. */
  text: string;
  words: SttWordOut[];
  /** 서버가 전사에 쓴 시간(ms). */
  duration_ms: number;
  /** 오디오 길이(초). 지연이 길이 탓인지 구분하려고 함께 준다. */
  audio_seconds: number;
  model: string;
}

// ─────────────────────────────────────────────── 리포트

export interface LearnedExpression {
  english: string;
  note_ko: string;
}

export interface ReportInsight {
  summary_ko: string;
  /** 반복된 실수 패턴. 없으면 빈 배열. */
  patterns_ko: string[];
  learned: LearnedExpression[];
}

export interface WordTip {
  word: string;
  meaning_ko: string;
  pattern?: string | null;
  example: string;
  usage_note: string;
  confused_with: string[];
}

export interface SessionReport {
  session_id: string;
  scenario_title: string;
  level: string;
  turn_count: number;
  mistake_count: number;
  polish_count: number;
  mistakes: Correction[];
  insight: ReportInsight;
  /** 검수된 단어 설명. 매칭된 게 없으면 빈 배열. */
  word_tips: WordTip[];
}

// ─────────────────────────────────────────────── 단어 연습장 (cloze)

/**
 * 판정 일곱 가지. `right_pos`·`wrong_pos` 는 예전에 `wrong_word` 하나로 뭉쳐
 * 있던 것을 가른 것이다 — **품사를 비교할 수 있을 때만** 둘 중 하나로 온다.
 * 기능어로 답했거나 사전이 모르는 낱말이면 지금도 `wrong_word` 다.
 */
export type Verdict =
  | "correct"
  | "wrong_form"
  | "right_pos"
  | "wrong_pos"
  | "wrong_word"
  | "not_a_word"
  | "empty";

/**
 * 빈칸의 품사 힌트.
 *
 * **`text_ko` 를 그대로 쓴다. 화면에서 조립하지 않는다.** `source` 가 그 이유다 —
 * `slot` 은 관사·조동사로 자리를 좁힌 것이라 "여기엔 명사가 들어가요" 라고 말해도
 * 되지만, `word` 는 정답 낱말이 가질 수 있는 품사 전부라 "이 낱말은 명사로도
 * 동사로도 써요" 까지만 참이다. 화면이 `labels_ko` 로 문장을 지어내면 그 경계가
 * 사라지고, 사라진 자리에서 앱이 학습자에게 거짓을 가르치게 된다.
 */
export interface PosHintOut {
  /** WordNet 품사 코드 (n·v·a·r). 색이나 아이콘을 고를 때만 쓴다. */
  pos: string[];
  labels_ko: string[];
  /** 화면에 그대로 띄울 한 문장. */
  text_ko: string;
  source: "slot" | "word";
}

/**
 * 철자 단서 한 걸음. **정답을 통째로 드러내는 단계는 없다** — 마지막까지 봐도
 * 최소 한 글자는 밑줄로 남는다.
 *
 * 영어를 아예 모르는 학습자를 위한 것이다. 지금 빈칸이 주는 단서(낱말 뜻·문장
 * 해석·문형·품사)는 넷 다 한국어라, 알파벳을 못 읽는 사람은 뜻을 다 알고도 첫
 * 글자를 못 적는다. 그 사람에게 빈칸은 문제가 아니라 벽이다.
 *
 * 서버가 단계를 다 실어 보내고 **언제 보여줄지는 화면이 정한다.** 한 걸음마다
 * 서버에 다시 물으면 답을 적는 도중에 왕복이 생긴다.
 */
export interface SpellHintOut {
  step: number;
  /** "글자 수" · "첫 글자" · "앞 절반" */
  label_ko: string;
  /** 화면에 그대로 띄울 한 문장. */
  text_ko: string;
  /** 아직 안 드러난 글자를 밑줄로 둔 모양. `s _ _ _` */
  shape: string;
}

export interface ClozeOut {
  /** 채점할 때 어느 항목인지 가리키는 열쇠. **정답 표면형이 아니다.** */
  word: string;
  level: string;
  meaning_ko: string;
  /** `____` 가 한 번 들어 있는 영어 문장. */
  sentence: string;
  pattern?: string | null;
  reviewed: boolean;
  /**
   * 문장의 한국어 해석. **가리지 않고 그대로 보여준다** — 시험이 아니라
   * 연습장이고, 뜻을 알아야 `pen`·`a pen`·`your pen` 처럼 구로도 답할 수 있다.
   * 3,245개 중 792개만 채워져 있어서 대부분 없다. 없으면 **그냥 안 그린다** —
   * "해석이 아직 없어요" 같은 말은 학습자에게 아무 쓸모가 없다.
   */
  example_ko?: string | null;
  pos_hint?: PosHintOut | null;
  topic?: string | null;
  /**
   * 철자 단서. 답이 한 글자면 빈 배열이다 — 글자 수가 곧 정답이라 줄 것이 없다.
   * 예전 응답에는 없던 칸이라 optional 이다.
   */
  spell_hints?: SpellHintOut[];
}

/** 장면 묶음 하나. 다른 회화 앱의 '유닛'에 해당한다. */
export interface TopicOut {
  topic: string;
  /** 학습자가 읽을 이름("카페"). 서버가 준다 — 화면이 표를 따로 들고 있지 않는다. */
  label_ko: string;
  total: number;
  reviewed: number;
}

export interface ClozeQuery {
  /**
   * CEFR 레벨. **빈 문자열이면 레벨로 가르지 않는다** — 장면 팩을 통째로 낼 때
   * 그렇게 쓴다(app/main.py `list_cloze` 참고). 빼면 서버 기본값 A1 이 걸린다.
   */
  level?: string;
  count?: number;
  offset?: number;
  /** 기능어 빈칸을 뺀다. 연습장에서는 늘 켠다 — `and` 를 채우는 건 낱말 공부가 아니다. */
  speech?: boolean;
  reviewed_only?: boolean;
  topic?: string;
  /**
   * 어느 어휘 트랙에서 낼지. 빼면 서버 기본값인 생활 회화(`general`)다 —
   * 그 기본값이 안전장치라서 화면이 잊어도 왕초보에게 `reimbursement` 가 안 나온다.
   */
  track?: string;
}

// ─────────────────────────────────────────────── 낱말 목록 (읽기용)

/**
 * 읽기용 낱말 하나. 빈칸(`ClozeOut`)과 달리 **가리는 것이 없다** —
 * 이 낱말을 외우러 온 사람에게 보여주는 것이라 지울 것이 없다.
 *
 * CEFR 레벨이 없는 것은 빠뜨린 게 아니다. 토익 어휘는 난이도가 아니라 **빈도**로
 * 줄 세운 목록이고, 같은 화면에 A1/B1 딱지가 붙으면 학습자가 그걸 순서로 읽는다.
 */
export interface WordCardOut {
  word: string;
  /** 빈도 순위. 1이 가장 자주 쓰인다. 트랙 안에서만 뜻이 있는 값이다. */
  rank: number | null;
  meaning_ko: string;
  example: string;
  /** 예문 그 문장의 해석. 2,252개 중 2,128개만 채워져 있어 없을 수 있다. */
  example_ko: string | null;
  pattern: string | null;
  reviewed: boolean;
}

export interface WordPageOut {
  total: number;
  /**
   * 다음 장을 받을 자리. **서버가 정해 준다** — 안전 판정에 걸린 행이 중간에서
   * 빠지므로 `offset + items.length` 로 계산하면 그만큼씩 앞으로 밀린다.
   * 끝에 닿으면 `null`.
   */
  next_offset: number | null;
  items: WordCardOut[];
}

export interface WordQuery {
  track?: string;
  offset?: number;
  count?: number;
  /**
   * 단어장. 표제어를 쉼표로 이어 보내면 그것들만 빈도 순으로 돌려준다
   * (`offset`·`count` 는 무시된다). 서버 상한은 200개.
   */
  words?: string;
}

export interface ClozeAnswerRequest {
  word: string;
  /** 학습자가 말했거나 적은 답. 낱말 하나가 아니라 구·절이어도 된다. */
  said: string;
  /** 설명 카드를 함께 받을지. 기본 true. */
  explain?: boolean;
}

/** 같은 품사의 다른 낱말 하나. */
export interface AlternativeOut {
  word: string;
  meaning_ko: string;
  pos_ko: string[];
  reviewed: boolean;
}

/**
 * 같은 품사의 다른 낱말들.
 *
 * **`label_ko` 를 반드시 함께 그린다.** 근거를 떼면 "이 자리에 올 수 있어요" 가
 * 되는데 그건 우리가 모르는 사실이다 — WordNet 은 `banana` 가 명사인 건 알아도
 * `Can I borrow your ____?` 에 어울리는지는 모른다. 서버가 목록과 이름표를 한
 * 객체로 묶어 보내는 것도 화면에서 둘을 떼어 놓지 못하게 하려는 것이다.
 */
export interface AlternativesOut {
  basis: "topic" | "rank" | "level";
  label_ko: string;
  words: AlternativeOut[];
}

/**
 * 아직 사람이 확인하지 않은 설명. 승인된 항목이면 **이 상자 자체가 없다.**
 *
 * 확인된 환각 13건이 전부 `usage_note` 와 `confused_with` 에 있었다. 그래서
 * 서버가 승인된 것과 **자리를 갈라** 보낸다. 화면도 자리를 갈라 그린다 —
 * 승인된 설명과 같은 모양으로 그리면 구조로 막아 둔 것이 화면에서 무너진다.
 */
export interface UnverifiedOut {
  usage_note?: string | null;
  confused_with: string[];
  /** "아직 사람이 확인하지 않은 설명이에요. 참고만 하세요." — 그대로 띄운다. */
  note_ko: string;
}

/** 답을 낸 뒤 보여 줄 설명. 전부 `words` 행과 WordNet 에서만 온다. */
export interface ClozeExplainOut {
  word: string;
  /** 빈칸에 들어가는 표면형. 문제 문장의 `____` 자리에 이걸 끼우면 완성된 문장이다. */
  answer: string;
  meaning_ko: string;
  /** 빈칸을 뚫기 전의 온전한 예문. */
  example: string;
  example_ko?: string | null;
  /** 여기서는 가리지 않은 문형이 온다 — 답을 본 뒤라 가릴 이유가 없다. */
  pattern?: string | null;
  topic?: string | null;
  topic_ko?: string | null;
  pos: string[];
  pos_ko: string[];
  /** "이 낱말은 명사로도 동사로도 써요." 그대로 띄운다. */
  pos_text_ko?: string | null;
  /** 사람이 검수한 항목인지. false 면 설명이 `unverified` 상자로 들어와 있다. */
  reviewed: boolean;
  usage_note?: string | null;
  confused_with: string[];
  unverified?: UnverifiedOut | null;
  alternatives?: AlternativesOut | null;
  hint?: PosHintOut | null;
}

export interface ClozeAnswerOut {
  verdict: Verdict;
  ok: boolean;
  said: string;
  /** 정답 표면형. **문제를 받을 때는 안 오고 답을 낸 뒤에만 온다.** */
  answer: string;
  /** 판정 이유가 담긴 한국어 한 문장. 다시 쓰지 않고 그대로 보여준다. */
  message_ko: string;
  /**
   * 실제로 판정한 낱말. 구·절로 답하면(`a pen`) 그 안의 머리 낱말이라 `said` 와
   * 다르다. 서버가 `message_ko` 안에 "('a pen' 에서 'pen' 하나만 보고
   * 판정했어요.)" 로 이미 밝히므로 화면이 또 적을 필요는 없다.
   */
  head?: string | null;
  said_pos: string[];
  explain?: ClozeExplainOut | null;
}

// ─────────────────────────────────────────────── 문법 문제 (토익 Part 5 형)

/**
 * 보기 하나. **칸이 `word` 뿐인 것이 핵심이다.**
 *
 * 이 낱말이 어느 모양인지(동사원형·-ing 형·명사…)는 여기 안 온다. 서버 쪽
 * 주석 그대로 "그게 곧 정답" 이라서다 — `sending` 옆에 '동명사' 라고 적혀
 * 있으면 "to 뒤에는 동사원형" 을 아는 학습자가 문장을 안 읽고도 고른다.
 * 모양 이름은 채점한 뒤 `GrammarAnswerOut.why_ko` 로 처음 나온다.
 */
export interface GrammarChoiceOut {
  word: string;
}

/**
 * 문제 하나. **정답 칸이 없다** — 채점은 서버가 한다.
 *
 * `choices` 의 **차례를 화면에서 섞지 않는다.** 서버가 문제마다 고정된
 * 자리바꿈으로 굳혀 보내는 값이라, 화면이 다시 섞으면 같은 문제를 다시 열
 * 때마다 답의 자리가 옮겨 다닌다("아까는 ②였는데").
 */
export interface GrammarOut {
  /** 채점할 때 어느 문제인지 가리키는 열쇠. 정답과 아무 관계 없는 자리 번호다. */
  id: string;
  rule: string;
  /** 이 문제가 묻는 규칙의 이름("to 다음에는 동사원형"). 화면에 그대로 띄운다. */
  rule_title: string;
  /** `____` 가 한 번 들어 있는 영어 문장. */
  sentence: string;
  /** 낱말 자리를 '~' 로 비워 둔 한국어 뜻. 이 문제가 묻는 것은 뜻이 아니라 **형태**다. */
  sentence_ko: string;
  choices: GrammarChoiceOut[];
}

export interface GrammarQuery {
  /**
   * 어느 규칙을 풀지. **화면이 이 값을 지어내지 않는다** — 규칙 목록을 주는
   * 엔드포인트가 아직 없어서, 빼고 서버 기본값을 받은 뒤 응답의 `rule_title` 로
   * 무엇을 푸는지 학습자에게 알린다. 모르는 이름을 보내면 404 가 아니라
   * **빈 배열**이 온다(규칙이 아직 없는 것과 문제가 떨어진 것을 같게 다룬다).
   */
  rule?: string;
  count?: number;
  offset?: number;
}

export interface GrammarAnswerRequest {
  /**
   * `GET /grammar` 가 준 문제 id. 문제를 통째로 돌려보내지 않는 것이 요점이다 —
   * 서버는 자기가 가진 것으로만 채점한다.
   */
  id: string;
  /** 학습자가 고른 보기의 낱말. */
  chosen: string;
}

export interface GrammarAnswerOut {
  ok: boolean;
  /** 정답 낱말. **답을 낸 뒤에만 온다.** */
  answer: string;
  /** 서버가 실제로 채점한 값. 보낸 값을 다듬은 것이라 화면이 누른 글자와 다를 수 있다. */
  chosen: string;
  /** 판정과 규칙 설명이 한 문장에 담겨 온다. 다시 쓰지 않고 그대로 보여준다. */
  message_ko: string;
  /**
   * 보기 넷이 각각 어느 모양인지. `"send — 동사원형  ← 정답"` 처럼 정답 표시까지
   * 문장 안에 들어 있다 — 화면이 이 줄을 다시 짓거나 표시를 덧붙이지 않는다.
   */
  why_ko: string[];
}
