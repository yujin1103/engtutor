# engtutor — 영어 왕초보용 AI 회화 튜터

한국인 영어 왕초보(CEFR A1~A2)를 위한 시나리오 롤플레이 회화 연습 앱.
단순 챗봇이 아니라 **대화 중 오류를 모아 학습 리포트로 돌려주는 학습 도구**를 목표로 한다.

전체 설계와 단계별 범위는 [CLAUDE.md](./CLAUDE.md)를 참고.

---

## 실행 (Docker Desktop)

전제: Docker Desktop이 WSL2 백엔드로 실행 중이고, GPU를 쓰려면 컨테이너 GPU 통과가 되어야 한다.

```powershell
# GPU가 컨테이너까지 보이는지 확인
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

### 1) 환경 파일

```powershell
copy .env.example .env
```

Claude API를 쓸 때만 `.env`의 `ANTHROPIC_API_KEY`를 채운다. 로컬 Ollama만 쓸 거면 비워둬도 된다.

### 2) 로컬 GPU 모드 (기본)

```powershell
docker compose up -d
docker compose exec ollama ollama pull qwen3:14b   # 약 9GB, 최초 1회
```

- 채팅 UI: http://localhost:8501
- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/healthz

### 3) Claude API 모드 (데모용)

`.env`에서 `LLM_BACKEND=anthropic`으로 바꾸고, 9GB 모델 없이 두 서비스만 띄운다.

```powershell
docker compose up -d api ui
```

`api`에 `depends_on`을 걸지 않았기 때문에 서비스명을 지정하면 `ollama`가 따라 올라오지 않는다.

### 4) 종료

```powershell
docker compose down          # 컨테이너만 정리 (모델 볼륨은 유지)
docker compose down -v       # 모델 볼륨까지 삭제 (9GB 재다운로드 필요)
```

---

## 개발 워크플로

소스는 bind mount라 **코드를 고치면 재빌드 없이 반영된다.**

- `app/**` 수정 → uvicorn `--reload`가 자동 재시작
- `ui/chat_app.py` 수정 → Streamlit이 자동 재실행
- `app/tutor/prompts/*.md` 수정 → 다음 요청부터 즉시 반영 (재시작 불필요)
- `requirements.txt` 수정 → `docker compose build` 필요

> NTFS → WSL2 경계에서는 inotify 이벤트가 컨테이너로 넘어오지 않는다.
> 그래서 `WATCHFILES_FORCE_POLLING=true`와 Streamlit `fileWatcherType=poll`을 켜 두었다.

### 테스트

```powershell
docker compose run --rm api pytest -q
```

---

## 학습 리포트

대화를 마치면 사이드바의 **📘 대화 끝내고 리포트 보기** 를 누른다. API로는:

```
POST /sessions/{session_id}/report
```

리포트는 다섯 부분으로 나오는데, 출처가 다르다.

| 항목 | 출처 |
|---|---|
| 총평 (`summary_ko`) | LLM |
| 반복 실수 패턴 (`patterns_ko`) | LLM — `mistake` 등급만 근거로 삼는다 |
| 오늘 배운 표현 (`learned`) | LLM |
| 틀린 문장 모음 (`mistakes`) | **DB 그대로** — LLM이 지어낼 여지가 없다 |
| 오늘 나온 단어 (`word_tips`) | **DB 그대로** — 검수 완료된 항목만 |

### 교정의 두 등급

`Correction.kind`로 나눈다. 왕초보에게 "틀렸다"는 신호를 남발하면 위축되기 때문이다.

| kind | 뜻 | UI | 리포트 |
|---|---|---|---|
| `mistake` | 실제로 틀렸거나 듣는 사람이 오해할 것 | ✏️ 고쳐볼까요 (펼침) | `mistake_count`, 패턴 근거 |
| `polish` | 맞는 영어인데 원어민은 다르게 말할 것 | ✨ 더 자연스러워요 (접힘) | `polish_count`, 패턴에서 제외 |

예: `I want ice americano` → `mistake` (ice/iced 오류) · `Large` → `polish` (통하지만 please가 부드러움)

LLM 호출은 **세션당 1회**뿐이다. 교정 기록은 `corrections` 테이블 값을 그대로 싣는다.

> **리포트는 API 백엔드 사용을 권장한다.** 총평과 패턴 요약은 대화 전체를 읽고 추상화하는
> 작업이라 로컬 7~14B 모델에서는 품질 편차가 크다. 리포트를 뽑기 전에 `.env`의
> `LLM_BACKEND=anthropic`으로 바꾸고 `docker compose restart api` 하면 같은 세션을 그대로
> 이어서 쓸 수 있다. 대화 자체는 로컬 모델로 충분하다.

리포트를 생성하면 세션은 종료 처리되어(`ended_at` 기록) 더 이상 대화를 이어갈 수 없다.

---

## 단어 콘텐츠 파이프라인

원칙: **생성은 AI(로컬 배치), 검수는 사람, 서빙은 DB.**
실시간 대화 경로에서는 단어 콘텐츠를 절대 LLM으로 만들지 않는다 — 항상 DB 조회다.

```
단어 목록 ──▶ batch_generate.py ──▶ words 테이블 (reviewed=false)
                  (Ollama, JSON 스키마 강제)          │
                                                      ▼
                                          review_app.py (사람이 수정·승인)
                                                      │  reviewed=true
                                                      ▼
                                    리포트에 '오늘 나온 단어'로 노출
```

### 1) 배치 생성

```powershell
# 스타터 60단어 (기본)
docker compose exec api python content/batch_generate.py

# 품질만 먼저 눈으로 확인 (DB에 쓰지 않음)
docker compose exec api python content/batch_generate.py --dry-run --limit 4

# 실제 NGSL로
docker compose exec api python content/batch_generate.py --wordlist content/data/ngsl.csv
```

이미 생성된 단어는 자동으로 건너뛴다(`--redo`로 강제). **사람이 승인한 항목은 배치가 덮어쓰지 않는다** —
검수 결과를 배치가 날리면 검수가 무의미해지기 때문이다.

실측(qwen3:14b, `--concurrency 4`): **60단어 93.7초 (0.64단어/초)**. NGSL 2,800단어면 약 73분.

### 2) 검수

```powershell
docker compose --profile review up -d review   # http://localhost:8502
```

미검수 필터·검색·항목 수정·승인 토글. 필요할 때만 띄우면 되므로 profile 뒤에 두었다.

### 3) 리포트 연동

리포트를 만들 때 교정에 등장한 단어를 `words` 테이블과 매칭해 **`reviewed=true`인 항목만** 붙인다.
미검수 항목은 절대 새어 나가지 않는다(테스트로 고정).

### 단어 목록

`content/data/starter_words.txt`는 **NGSL이 아니라** 파이프라인을 바로 돌려보기 위해 직접 고른
60단어다(borrow/lend, say/tell/speak/talk 처럼 한국인이 실제로 헷갈리는 짝 위주).
실제 NGSL(약 2,800단어, CC BY)로 돌리는 법은 `content/data/README.md` 참고.

> 시판 단어책·이북 등 저작물에서 예문·해설을 수집하는 코드는 작성하지 않는다.
> 교차 확인이 필요하면 공개 자원만 쓴다 — Wiktionary, WordNet, Tatoeba(CC BY).

---

## 구조

```
app/
├── main.py            FastAPI: /chat, /scenarios, /healthz, /sessions/{id}/report
├── config.py          .env 로딩 (pydantic-settings)
├── session_store.py   SqliteSessionStore + InMemorySessionStore (같은 Protocol)
├── db/                models.py(sessions/turns/corrections) · crud.py · database.py
├── report/            schemas.py · service.py · prompts/report_system.md
├── content/           schemas.py · generator.py · prompts/word_system.md
├── llm/               백엔드 추상화
│   ├── base.py            chat_json(system, messages, schema) 계약
│   ├── ollama_client.py   format 파라미터로 JSON 스키마 강제
│   ├── anthropic_client.py structured outputs로 동일 스키마 강제
│   └── factory.py         LLM_BACKEND로 분기
└── tutor/
    ├── schemas.py     TurnResponse / Correction (단일 출처)
    ├── prompts/       tutor_system.md + guardrails.md
    ├── scenarios/     YAML 3종
    ├── korean.py      한국어 표기 정규화 (모델이 못 고치는 것)
    ├── loader.py      시나리오·프롬프트 로딩
    └── service.py     프롬프트 조립 → LLM → 검증 → 1회 재시도
ui/chat_app.py         Streamlit 채팅 UI
content/               batch_generate.py · review_app.py · data/
tests/                 스키마·시나리오·프롬프트·DB·리포트 스모크 테스트
```

### 설계 메모

- **LLM 클라이언트는 스키마를 인자로 받는다.** 턴 응답 전용 메서드로 만들면 2단계 리포트와
  3단계 단어 배치 생성에서 또 만들게 된다. 스키마는 pydantic 모델에서 파생시켜 단일 출처를 지킨다.
- **qwen3의 `<think>` 블록을 1단계부터 처리한다.** `think=false`로 막고, 그래도 새어 나오면
  정규식으로 걷어낸다. 이걸 안 해두면 JSON 파싱 실패로 시간을 날린다.
- **재시도는 같은 요청 반복이 아니다.** 온도를 낮추고 스키마 위반 사실을 알려주며 1회만 재요청한다.
- **`reply`에는 교정을 절대 섞지 않는다.** 대화 흐름 유지가 목적이고, 교정은 별도 필드로만 나간다.
- **한국어 표기 정규화는 코드에서 한다** (`app/tutor/korean.py`). 로컬 모델이 ㄷ불규칙 활용을
  자주 틀린다("묻을 때" → "물을 때"). 프롬프트에 반례까지 넣어 두 번 시도했지만 안 고쳐졌다.
  프롬프트로 못 고치는 종류는 pydantic 검증 단계에서 결정적으로 처리한다.

---

## 시나리오 추가하기

`app/tutor/scenarios/`에 YAML 파일 하나를 추가하면 끝이다. 파일명(확장자 제외)과 `id`는 같아야 한다.

```yaml
id: restaurant_order
title: 식당에서 주문하기
level: A1
ai_role: a waiter at a casual restaurant
situation: 학습자는 자리에 앉았고, 메뉴판을 막 받았어요.
goal: 음식 하나를 주문하고 물도 요청하기
opening_line: "Are you ready to order?"
opening_hint_ko: 메뉴 이름을 말해보세요. "I'll have the ~ ." 가 자연스러워요.
```

---

## 진행 상황

- [x] **1단계 — 코어 루프**: LLM 추상화, 시스템 프롬프트, 시나리오 3종, `/chat`, Streamlit UI
- [x] **2단계 — 저장과 리포트**: SQLite 세션·턴·교정, 세션 종료 리포트, 레벨 선택
- [x] **3단계 — 콘텐츠 파이프라인**: NGSL 배치 생성, 검수용 Streamlit 앱, 리포트-단어 DB 연동
- [ ] **4단계 — 보안·마무리**: 인젝션 테스트 슈트, 아키텍처 다이어그램, 보안 결과 표
