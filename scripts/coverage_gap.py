"""단어 표가 이 앱이 실제로 쓰는 영어를 얼마나 덮는지 잰다. LLM 을 부르지 않는다.

왜 재는가
---------
단어 표는 NGSL 2,801개다. NGSL 은 **글**의 빈도 목록이라, 이 앱이 실제로 하는
말(카페에서 아메리카노를 시키고, 지하철역을 묻고, 호텔에 수건을 부탁하는 말)과
같지 않다. "단어를 더 넣어야 하나"는 목록을 하나 더 사 오는 문제가 아니라
**이 앱이 쓰는 말 중 표에 없는 것이 무엇인가**를 세는 문제다.

무엇을 세는가
-------------
학습자가 이 앱에서 실제로 마주치는 영어만 센다.
- 시나리오 33개의 영어 칸(첫 발화·따라 말할 문장)
- 교정 측정용 발화(사람이 쓴 왕초보 문장)
- DB 에 쌓인 실제 대화와 교정
- `--play` 로 만든 자가 대화 기록(선택)

한국어 설명 칸은 세지 않는다. 학습자가 **영어로 마주치는 것**만이 단어 표가
덮어야 할 것이기 때문이다.

무엇을 덮개로 치는가
--------------------
표제어 그 자체, 굴절형(사전으로 원형을 되돌려 대조), 기능어. 굴절형 판정은
app/content/lexicon.py 를 쓴다 — 여기서 따로 규칙을 만들면 채점기와 갈라진다.

실행:
    docker compose exec api python scripts/coverage_gap.py
    docker compose exec api python scripts/coverage_gap.py --add-list .review/to_add.txt
    docker compose exec api python scripts/coverage_gap.py --play 4 --corpus .review/selfplay.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content import lexicon  # noqa: E402
from app.db.database import db_session  # noqa: E402
from app.db.models import CorrectionRow, TurnRow, WordRow  # noqa: E402
from app.tutor.loader import get_scenarios, load_prompt  # noqa: E402
from app.tutor.schemas import TurnResponse, turn_response_schema  # noqa: E402
from app.tutor.service import MAX_HISTORY_MESSAGES, TutorService  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
PROBES = ROOT / "scripts" / "data" / "correction_probes.yaml"

# 시나리오에서 학습자가 영어로 마주치는 칸. 한국어 칸은 세지 않는다.
ENGLISH_FIELDS = ("opening_line", "opening_say_en", "opening_say_more")

_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

# 축약형은 표제어와 이어지지 않는다 — don't 를 그대로 찾으면 표에 없고, 아포스트로피
# 앞만 떼면 don 이 된다. 그래서 덮개를 판정할 때만 풀어서 조각마다 확인한다.
# can't -> ca + n't 처럼 앞조각이 깨지는 것들이 있어 통째로 적어 둔다.
_WHOLE = {
    "can't": ("can", "not"),
    "cannot": ("can", "not"),
    "won't": ("will", "not"),
    "shan't": ("shall", "not"),
    "let's": ("let", "us"),
    "ain't": ("be", "not"),
}
_SUFFIX = {
    "n't": "not",
    "'m": "am",
    "'re": "are",
    "'ve": "have",
    "'ll": "will",
    "'d": "would",
    "'s": "is",
}


def expand(word: str) -> tuple[str, ...]:
    """축약형을 조각으로 푼다. 축약형이 아니면 자기 자신 하나."""
    w = word.replace("’", "'")
    if w in _WHOLE:
        return _WHOLE[w]
    for suffix, tail in _SUFFIX.items():
        if w.endswith(suffix) and len(w) > len(suffix):
            return (w[: -len(suffix)], tail)
    return (w,)


class Corpus:
    """토큰을 출처·빈도·대문자 여부와 함께 모은다."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.sources: defaultdict[str, set[str]] = defaultdict(set)
        # 문장 첫머리가 **아닌** 자리에서 대문자로 쓰인 횟수 / 그 자리에 나온 횟수.
        # 고유명사(Seoul, Jisu)를 어휘와 갈라 놓는 데 쓴다 — 지우지는 않고 표시만 한다.
        self.mid_upper: Counter[str] = Counter()
        self.mid_total: Counter[str] = Counter()

    def add(self, text: str, source: str) -> None:
        if not text:
            return
        for chunk in re.split(r"(?<=[.!?])\s+", text):
            for match in _WORD.finditer(chunk):
                raw = match.group(0)
                word = raw.lower().replace("’", "'")
                self.counts[word] += 1
                self.sources[word].add(source)
                if match.start() > 0:  # 문장 첫머리가 아니면 대문자가 뜻을 가진다
                    self.mid_total[word] += 1
                    if raw[0].isupper():
                        self.mid_upper[word] += 1

    def looks_proper(self, word: str) -> bool:
        """문장 중간에서 늘 대문자로 나오면 이름이다. 어휘가 아니라 배역이다."""
        seen = self.mid_total[word]
        return seen >= 1 and self.mid_upper[word] == seen


def gather_static(corpus: Corpus) -> None:
    for path in sorted((ROOT / "app" / "tutor" / "scenarios").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for field in ENGLISH_FIELDS:
            corpus.add(str(data.get(field) or ""), "시나리오")

    if PROBES.exists():
        for probe in yaml.safe_load(PROBES.read_text(encoding="utf-8")) or []:
            corpus.add(str(probe.get("say") or ""), "측정 발화")

    with db_session() as db:
        for (content,) in db.execute(select(TurnRow.content)):
            corpus.add(content, "대화 기록")
        for original, better in db.execute(select(CorrectionRow.original, CorrectionRow.better)):
            corpus.add(original, "교정")
            corpus.add(better, "교정")


# ------------------------------------------------------------------ 자가 대화
def build_system(scenario_id: str, level: str) -> str:
    service = TutorService.__new__(TutorService)
    service._system_template = load_prompt("tutor_system.md")
    service._guardrails = load_prompt("guardrails.md")
    return service.build_system(get_scenarios()[scenario_id], level, "balanced")


def ask(model: str, system: str, messages: list[dict], level: str, num_ctx: int) -> TurnResponse:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": False,
        "format": turn_response_schema(),
        "keep_alive": "30m",
        "think": False,
        "options": {"temperature": 0.7, "num_ctx": num_ctx, "num_predict": 1024},
    }
    with httpx.Client(timeout=600) as client:
        res = client.post(f"{OLLAMA}/api/chat", json=payload)
        res.raise_for_status()
        content = (res.json().get("message") or {}).get("content", "")
    return TurnResponse.model_validate(json.loads(content), context={"level": level})


def play_one(scenario_id: str, turns: int, model: str, level: str, num_ctx: int) -> list[dict]:
    """앱이 스스로와 대화한다. 학습자 쪽은 앱이 내놓은 `say_more` 를 그대로 말한다.

    왜 두 번째 모델을 쓰지 않는가: 재고 싶은 것은 **이 앱이 학습자에게 내놓는 영어**다.
    따라 말하라고 준 문장(say_en/say_more)이 곧 학습자가 쓸 말이므로, 그것을 되먹이면
    앱이 실제로 굴리는 어휘가 그대로 드러난다. 밖에서 다른 모델을 데려오면 이 앱이
    아니라 그 모델의 어휘를 재게 된다.

    앱과 다른 점이 하나 있다: **스키마 실패를 다시 시도하지 않는다.** 앱은 한 번
    고쳐 묻지만 여기서는 그 시나리오를 거기서 멈추고 기록에 남긴다. 어휘를 세는 데는
    충분하고, 몇 번 실패했는지가 같이 보이는 편이 낫다(A1 에서 `say_more` 가 10단어를
    넘겨 끊긴 적이 있다).
    """
    scenario = get_scenarios()[scenario_id]
    system = build_system(scenario_id, level)
    lines: list[dict] = [{"role": "opening", "text": scenario.opening_line}]
    said = (getattr(scenario, "opening_say_more", "") or getattr(scenario, "opening_say_en", "")).strip()
    history: list[dict] = []
    for _ in range(turns):
        if not said:
            break
        lines.append({"role": "learner", "text": said})
        # 앱과 같은 대화를 보낸다 — 첫 발화를 앞에 붙이고 최근 12개만 남긴다
        # (app/tutor/service.py 의 _messages). 여기서 전부 보내면 실제보다 긴 문맥을
        # 재게 되고, 4096 토큰 창이 실제로 언제 차는지도 알 수 없게 된다.
        messages = [
            {"role": "assistant", "content": scenario.opening_line},
            *history[-MAX_HISTORY_MESSAGES:],
            {"role": "user", "content": said},
        ]
        history.append({"role": "user", "content": said})
        try:
            turn = ask(model, system, messages, level, num_ctx)
        except Exception as exc:  # 한 시나리오가 죽어도 나머지는 계속 돈다
            lines.append({"role": "error", "text": f"{type(exc).__name__}: {exc}"[:200]})
            break
        history.append({"role": "assistant", "content": turn.reply})
        lines.append({"role": "tutor", "text": turn.reply})
        for correction in turn.corrections:
            lines.append({"role": "correction", "text": correction.better})
        said = (turn.say_more or turn.say_en or "").strip()
    return lines


def run_selfplay(turns: int, model: str, level: str, num_ctx: int, workers: int) -> dict:
    ids = sorted(get_scenarios())
    print(f"자가 대화: 시나리오 {len(ids)}개 x {turns}턴 · {model} · {level} · 동시 {workers}")

    def one(scenario_id: str) -> tuple[str, list[dict]]:
        return scenario_id, play_one(scenario_id, turns, model, level, num_ctx)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        played = dict(pool.map(one, ids))
    broken = [sid for sid, lines in played.items() if any(x["role"] == "error" for x in lines)]
    if broken:
        print(f"  ! 도중에 끊긴 시나리오 {len(broken)}개: {', '.join(broken)} (기록에 남겼습니다)")
    return played


def add_selfplay(corpus: Corpus, played: dict) -> None:
    for lines in played.values():
        for line in lines:
            if line["role"] == "error":
                continue
            corpus.add(line["text"], "자가 대화")


# -------------------------------------------------------------------- 덮개 판정
def covered(word: str, heads: set[str]) -> bool:
    """표가 이 토큰을 덮는가. 축약형은 조각이 **전부** 덮여야 덮인 것으로 친다."""

    def one(piece: str) -> bool:
        if not piece:
            return True
        if piece in heads or piece in lexicon.FUNCTION_WORDS:
            return True
        return bool(lexicon.lemmas(piece) & heads)

    return all(one(piece) for piece in expand(word))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--play", type=int, default=0, help="시나리오당 자가 대화 턴 수 (0이면 안 함)")
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    parser.add_argument("--level", default="A1", choices=["A1", "A2", "B1"])
    parser.add_argument("--num-ctx", type=int, default=int(os.getenv("OLLAMA_NUM_CTX", "4096")))
    parser.add_argument("--workers", type=int, default=4, help="OLLAMA_NUM_PARALLEL 과 맞춘다")
    parser.add_argument("--corpus", type=Path, default=None, help="자가 대화 기록을 읽거나 쓸 파일")
    parser.add_argument("--add-list", type=Path, default=None, help="추가 후보를 목록 파일로 저장")
    parser.add_argument("--show", type=int, default=60)
    args = parser.parse_args()

    corpus = Corpus()
    gather_static(corpus)

    played: dict = {}
    if args.play:
        played = run_selfplay(args.play, args.model, args.level, args.num_ctx, args.workers)
        if args.corpus:
            args.corpus.parent.mkdir(parents=True, exist_ok=True)
            args.corpus.write_text(json.dumps(played, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"자가 대화 기록을 {args.corpus} 에 남겼습니다.")
    elif args.corpus and args.corpus.exists():
        played = json.loads(args.corpus.read_text(encoding="utf-8"))
        print(f"자가 대화 기록을 {args.corpus} 에서 읽었습니다.")
    if played:
        add_selfplay(corpus, played)

    with db_session() as db:
        heads = {w.lower() for (w,) in db.execute(select(WordRow.word))}
        reviewed = {
            w.lower()
            for (w,) in db.execute(select(WordRow.word).where(WordRow.reviewed.is_(True)))
        }

    tokens = sum(corpus.counts.values())
    kinds = len(corpus.counts)
    gaps = {w: n for w, n in corpus.counts.items() if not covered(w, heads)}
    gap_tokens = sum(gaps.values())

    print(f"\n단어 표 {len(heads)}개 (검수 완료 {len(reviewed)}개)")
    print(f"앱이 쓰는 영어: 토큰 {tokens} · 종류 {kinds}")
    print(
        f"표가 덮지 못한 것: 종류 {len(gaps)} ({len(gaps) / max(kinds, 1):.1%}) · "
        f"토큰 {gap_tokens} ({gap_tokens / max(tokens, 1):.1%})"
    )

    names = {w: n for w, n in gaps.items() if corpus.looks_proper(w)}
    rest = {w: n for w, n in gaps.items() if w not in names}
    unknown = {w: n for w, n in rest.items() if lexicon.known(w) is False}
    real = {w: n for w, n in rest.items() if w not in unknown}

    def dump(title: str, data: dict[str, int], note: str) -> None:
        if not data:
            return
        print(f"\n{title} ({len(data)}개) — {note}")
        for word, count in sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))[: args.show]:
            where = "·".join(sorted(corpus.sources[word]))
            print(f"  {count:4d}  {word:20s} {where}")
        if len(data) > args.show:
            print(f"  … {len(data) - args.show}개 더 (--show 로 늘립니다)")

    if played:
        print("\n시나리오별 (자가 대화 기준) — 전체 평균만 보면 카페가 얼마나 비었는지 안 보인다")
        rows: list[tuple[float, str, int, int]] = []
        for scenario_id, lines in played.items():
            one = Corpus()
            for line in lines:
                if line["role"] != "error":
                    one.add(line["text"], "자가 대화")
            seen = sum(one.counts.values())
            missing = sum(n for w, n in one.counts.items() if not covered(w, heads))
            if seen:
                rows.append((missing / seen, scenario_id, missing, seen))
        rows.sort(reverse=True)
        for share, scenario_id, missing, seen in rows[:12]:
            mark = "  ⚠" if share >= 0.03 else "   "
            print(f"{mark} {scenario_id:22s} {share:5.1%}  ({missing}/{seen} 토큰)")
        if len(rows) > 12:
            covered_enough = sum(1 for share, *_ in rows if share < 0.03)
            print(f"    … 나머지 {len(rows) - 12}개 · 3% 미만인 시나리오 {covered_enough}개")

    dump("추가 후보", real, "사전에 있는 실제 단어인데 표에 없습니다")
    dump(
        "늘 대문자로 쓰인 말",
        names,
        "배역·지명이면 어휘가 아니라 빼고, 요일·국적이면 넣습니다",
    )
    dump("사전에 없는 말", unknown, "상표·외래어·오타입니다. 하나씩 눈으로 봐야 합니다")

    if args.add_list:
        args.add_list.parent.mkdir(parents=True, exist_ok=True)
        # 표제어는 원형으로 만든다 — towels 가 아니라 towel 이 표에 들어가야
        # 굴절형까지 한 항목이 덮는다. 원형이 여럿이면 가장 짧은 것을 쓴다.
        ordered: list[str] = []
        for word, _ in sorted(real.items(), key=lambda kv: (-kv[1], kv[0])):
            bases = sorted(lexicon.lemmas(word), key=len)
            head = bases[0] if bases else word
            if head not in ordered:
                ordered.append(head)
        args.add_list.write_text(
            "# scripts/coverage_gap.py 가 뽑은 추가 후보. 빈도 순.\n"
            "# 이름·고유명사와 사전에 없는 말은 빠져 있습니다 — 화면 출력에서 따로 보세요.\n"
            + "\n".join(ordered)
            + "\n",
            encoding="utf-8",
        )
        print(f"\n추가 후보 {len(ordered)}개를 {args.add_list} 에 적었습니다.")
        print(f"  docker compose exec api python content/batch_generate.py --wordlist {args.add_list}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
