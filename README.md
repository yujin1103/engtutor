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

## 구조

```
app/
├── main.py            FastAPI: /chat, /scenarios, /healthz
├── config.py          .env 로딩 (pydantic-settings)
├── session_store.py   인메모리 세션 (2단계에서 SQLite로 교체)
├── llm/               백엔드 추상화
│   ├── base.py            chat_json(system, messages, schema) 계약
│   ├── ollama_client.py   format 파라미터로 JSON 스키마 강제
│   ├── anthropic_client.py structured outputs로 동일 스키마 강제
│   └── factory.py         LLM_BACKEND로 분기
└── tutor/
    ├── schemas.py     TurnResponse / Correction (단일 출처)
    ├── prompts/       tutor_system.md + guardrails.md
    ├── scenarios/     YAML 3종
    ├── loader.py      시나리오·프롬프트 로딩
    └── service.py     프롬프트 조립 → LLM → 검증 → 1회 재시도
ui/chat_app.py         Streamlit 채팅 UI
tests/                 스키마·시나리오·프롬프트 스모크 테스트
```

### 설계 메모

- **LLM 클라이언트는 스키마를 인자로 받는다.** 턴 응답 전용 메서드로 만들면 2단계 리포트와
  3단계 단어 배치 생성에서 또 만들게 된다. 스키마는 pydantic 모델에서 파생시켜 단일 출처를 지킨다.
- **qwen3의 `<think>` 블록을 1단계부터 처리한다.** `think=false`로 막고, 그래도 새어 나오면
  정규식으로 걷어낸다. 이걸 안 해두면 JSON 파싱 실패로 시간을 날린다.
- **재시도는 같은 요청 반복이 아니다.** 온도를 낮추고 스키마 위반 사실을 알려주며 1회만 재요청한다.
- **`reply`에는 교정을 절대 섞지 않는다.** 대화 흐름 유지가 목적이고, 교정은 별도 필드로만 나간다.

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
- [ ] **2단계 — 저장과 리포트**: SQLite 세션·턴·교정, 세션 종료 리포트, 레벨 선택
- [ ] **3단계 — 콘텐츠 파이프라인**: NGSL 배치 생성, 검수용 Streamlit 앱, 리포트-단어 DB 연동
- [ ] **4단계 — 보안·마무리**: 인젝션 테스트 슈트, 아키텍처 다이어그램, 보안 결과 표
