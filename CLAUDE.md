# 프로젝트: 영어 왕초보용 AI 회화 튜터 (working name: engtutor)

## 작업 지시

너는 이 프로젝트의 개발을 맡는다. 아래 명세는 이미 확정된 설계이므로 스택이나 구조를 재논의하지 말고 그대로 구현한다.
"로드맵 / 작업 순서"의 1단계부터 시작하고, 각 단계가 끝나면 실행·테스트 방법을 알려준 뒤 다음 단계 진행 여부를 확인받는다.

---

## 1. 프로젝트 개요

- 한국인 영어 왕초보(CEFR A1~A2) 대상 AI 회화 연습 앱.
- 목적: 포트폴리오·졸업작품 겸 개인 학습용 토이 프로젝트. 실서비스가 아니므로 회원가입, 결제, 멀티유저, 스케일링은 만들지 않는다. 단일 사용자 로컬 실행 기준.
- 핵심 차별점: 단순 챗봇 래퍼가 아니라 "학습 도구". 대화 중 오류를 축적하고 세션 종료 시 학습 리포트로 돌려주는 부분이 제품의 중심이다.
- 개발자는 AI 보안 전공. 보안 요소(페르소나 가드레일, 프롬프트 인젝션 테스트)를 구현하고 문서화하는 것도 프로젝트 목표에 포함된다.

## 2. 확정된 기술 스택

- Python 3.11+, FastAPI 백엔드.
- 프론트엔드: 1차는 Streamlit 채팅 UI. React 전환은 나중 단계이며 지금은 구현하지 않는다.
- DB: SQLite (SQLAlchemy 사용 권장).
- LLM: 듀얼 백엔드 구조. 환경변수로 전환하며 호출부 코드는 백엔드에 무관해야 한다.
  - 기본(개발·평소 사용): Ollama 로컬. 기본 모델 `qwen3:14b` (개발 머신: Windows, RTX 5080 16GB VRAM, 32GB RAM — Q4 기준 여유 있게 동작). 대안 모델: `qwen3:8b`, `gemma3:12b`, `exaone3.5:7.8b` — 모델명은 환경변수로만 지정.
  - 데모·고품질 필요 시: Claude API, 모델 `claude-haiku-4-5`.
- 구조화 출력: 두 백엔드 모두 JSON 스키마를 강제한다 (Ollama는 `format` 파라미터에 JSON 스키마, Claude는 structured output 또는 tool-use 방식). 파싱 실패 시 1회 재시도 폴백을 넣는다.
- 실행: 개발은 로컬 venv(uvicorn + streamlit), 마무리 단계에서 docker-compose 제공. Ollama는 호스트에서 실행하고 컨테이너에서는 `http://host.docker.internal:11434`로 접근한다.

## 3. 핵심 기능 명세 (MVP)

### 3.1 시나리오 기반 롤플레이

- 시작 시나리오 3종: 카페 주문, 자기소개, 길 묻기.
- 각 시나리오는 AI 역할(예: 카페 점원), 학습자 목표(예: 음료 주문 완료), AI의 첫 발화, 상황 설명을 가진다.
- 시나리오는 코드에 하드코딩하지 말고 YAML 또는 JSON 데이터 파일로 정의해 추가가 쉬워야 한다.

### 3.2 턴 응답 구조 (가장 중요)

LLM 호출 1회로 아래 JSON을 받는다:

```json
{
  "reply": "Sure! What size would you like?",
  "corrections": [
    {
      "original": "I want ice americano",
      "better": "Can I get an iced americano, please?",
      "note": "주문할 땐 Can I get ~ 이 훨씬 자연스러워요"
    }
  ],
  "hint_ko": "점원이 사이즈를 물어보고 있어요"
}
```

- `reply`는 롤플레이 캐릭터를 유지하는 영어 응답이며, 교정 내용을 절대 섞지 않는다 (대화 흐름 유지 목적).
- `corrections`는 직전 사용자 발화에 대한 교정. 오류가 없으면 빈 배열. `note`는 자연스러운 한국어 설명.
- `hint_ko`는 학습자가 다음에 무슨 말을 하면 되는지에 대한 한국어 힌트.
- pydantic 스키마로 응답을 검증한다.

### 3.3 레벨 제어 (시스템 프롬프트 요구사항)

- CEFR A1~A2 수준 강제: 한 문장 8단어 이내, 기초 어휘만 사용.
- few-shot 예시 2~3개를 시스템 프롬프트에 포함.
- 페르소나 유지: 역할 이탈 금지, 교정·설명은 JSON 필드로만 분리.
- 시스템 프롬프트는 코드와 분리된 별도 파일로 관리한다 (튜닝·버전 관리 편의).
- UI에서 레벨(A1/A2) 선택 가능 (2단계에서 구현).

### 3.4 세션 저장 & 학습 리포트

- SQLite 테이블: `sessions`, `turns`, `corrections` (+ 3단계의 `words`).
- 세션 종료 시 학습 리포트 생성: 틀린 문장 모음(원문 → 교정 → 이유), 오늘 배운 표현, 반복 실수 패턴 요약.
- 리포트 요약도 동일한 LLM 클라이언트로 1회 호출해 생성한다 (README에 "리포트는 API 백엔드 사용 권장" 명시).

### 3.5 단어·표현 콘텐츠 파이프라인 (실시간 LLM 아님 — 사전 생성)

원칙: **생성은 AI(로컬 배치), 검수는 사람, 서빙은 DB.**

- 단어 소스: NGSL(New General Service List, CC BY 라이선스, 약 2,800단어) 목록을 사용한다.
- 시판 단어책·이북 등 저작물에서 예문·해설을 수집하는 코드는 절대 작성하지 않는다 (저작권).
- 교차 확인용 공개 자원: Wiktionary, WordNet(뜻 확인), Tatoeba(CC BY, 한영 대역 예문).
- 배치 생성 스크립트: NGSL 목록 → Ollama 호출(JSON 스키마 강제) → `words` 테이블에 `reviewed=false`로 저장. 실시간성이 필요 없으므로 순차 배치로 충분하다.
- 검수 UI: 별도 Streamlit 앱. 목록/검색/미검수 필터, 항목 수정, 승인(`reviewed` 토글) 기능.
- 항목 스키마:

```json
{
  "word": "borrow",
  "level": "A1",
  "meaning_ko": "빌리다 (내가 빌려 오는 쪽)",
  "example": "Can I borrow your pen?",
  "usage_note": "빌려주는 쪽은 lend예요. '돈 좀 빌려줄래?'는 Can you lend me some money?가 자연스러워요.",
  "confused_with": ["lend"],
  "reviewed": false
}
```

- 앱 연동: 세션 리포트 생성 시 corrections에 등장한 단어를 `words` 테이블과 매칭해, 검수된 `usage_note`가 있으면 리포트에 함께 표시한다.

## 4. 보안 요구사항 (차별화 포인트 — 반드시 구현·문서화)

- 튜터 페르소나 가드레일: 시스템 프롬프트에 인젝션 방어 지침 포함 + 출력 JSON 스키마 강제로 역할 이탈을 억제.
- `tests/security/`에 프롬프트 인젝션 자동화 테스트 10개 이상: 예) "ignore previous instructions" 류, 역할 변경 유도, 시스템 프롬프트 유출 유도, 한국어 인젝션 변형, JSON 구조 파괴 시도.
- 테스트 결과(차단/통과)를 README의 Security 섹션에 표로 정리한다.
- API 키는 `.env`로만 관리하고 코드·레포에 노출 금지. `.env.example` 제공.

## 5. 로드맵 / 작업 순서

이 순서대로 진행한다. 각 단계 완료 시 실행 방법을 안내하고 확인받은 뒤 다음 단계로 넘어간다.

1. **1단계 — 코어 루프**: 프로젝트 뼈대, LLM 클라이언트 추상화(Ollama/Claude 듀얼 백엔드), 튜터 시스템 프롬프트, 시나리오 3종 데이터, FastAPI `/chat` 엔드포인트(구조화 응답), Streamlit 채팅 UI. 시나리오를 골라 대화하고 교정·힌트가 표시되면 완료.
2. **2단계 — 저장과 리포트**: SQLite 세션·턴·교정 저장, 세션 종료 리포트, 레벨(A1/A2) 선택.
3. **3단계 — 콘텐츠 파이프라인**: NGSL 배치 생성 스크립트, 검수용 Streamlit 앱, 리포트-단어 DB 연동.
4. **4단계 — 보안·마무리**: 인젝션 테스트 슈트, README(아키텍처 다이어그램, 실행법, 보안 결과 표), docker-compose.
5. **5단계 (선택) — 음성 모드**: STT 입력(faster-whisper 등) + TTS 출력. 지금은 구현하지 않되, 입출력 계층을 나중에 음성으로 확장할 수 있게만 설계해 둔다.

## 6. 디렉터리 구조 (제안 — 합리적 범위에서 조정 가능)

```
engtutor/
├── app/
│   ├── main.py              # FastAPI 엔트리
│   ├── llm/                 # base.py, ollama_client.py, anthropic_client.py
│   ├── tutor/               # prompts/(시스템 프롬프트 파일), scenarios/, schemas.py
│   ├── report/
│   └── db/                  # models.py, crud.py
├── content/
│   ├── batch_generate.py    # NGSL → Ollama 배치 생성
│   ├── review_app.py        # Streamlit 검수 UI
│   └── data/                # ngsl 목록 등
├── ui/
│   └── chat_app.py          # Streamlit 채팅 UI
├── tests/
│   └── security/            # 인젝션 테스트
├── .env.example
├── docker-compose.yml
└── README.md
```

## 7. 환경변수 (.env.example)

```
LLM_BACKEND=ollama            # ollama | anthropic
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:14b
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-haiku-4-5
DB_PATH=./data/engtutor.db
```

## 8. 품질 기준

- 모든 단계는 그 시점에 즉시 실행 가능한 상태를 유지한다. 실행 명령은 README에 기록.
- 타입힌트 사용, LLM 응답은 pydantic으로 검증.
- pytest: 응답 스키마 검증, 백엔드 전환, 인젝션 테스트.
- UI 문구, 교정 note, usage_note 등 학습자에게 보이는 텍스트는 자연스러운 한국어로 작성한다.

## 9. 하지 말 것

- 실서비스용 기능(인증, 결제, 클라우드 배포 인프라) 구현 금지.
- 이북·시판 교재 콘텐츠를 수집·크롤링하는 코드 작성 금지.
- 확정된 스택·구조 재논의 금지 (개선 제안은 해당 단계 완료 후 별도로만).
- `reply` 필드에 교정·해설을 섞는 것 금지.
- 실시간 대화 경로에서 단어 콘텐츠를 LLM으로 생성하는 것 금지 (콘텐츠는 항상 사전 생성 + 검수 + DB 조회).
