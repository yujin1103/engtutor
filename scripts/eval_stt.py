"""음성 전사가 학습자의 오류를 **살려 두는가**를 잰다.

왜 이걸 재는가
--------------
STT 를 붙일지 말지가 여기 달려 있다. Whisper 는 언어모델 prior 때문에 학습자의
틀린 영어를 매끄럽게 고쳐서 적는다. `I want ice americano` 가 `an iced americano`
로 전사되면 교정할 것이 사라지고, 앱의 존재 이유가 조용히 없어진다. 잘못 들은
것보다 이게 나쁜 이유는 **학습자 눈에 맞는 문장이 떠 있어서** 되돌릴 이유를
못 느끼기 때문이다.

전사 정확도(WER)만 보면 이 실패가 안 보인다. WER 이 낮을수록 좋아 보이는데, 정작
이 앱에서 중요한 것은 **학습자가 말한 대로 적었는가**이지 표준 영어에 가까운가가
아니다. 그래서 지표를 따로 만든다.

무엇을 재는가
-------------
1. **오류 생존율** — `error_phrase`(오류가 들어 있는 연속된 조각)가 전사에 그대로
   남았는가. 이게 이 측정의 결론이다.
2. **문장 완전 일치율** — 전사가 읽은 문장과 토큰 단위로 같은가.
3. **WER** — 일반적인 전사 정확도. 1번과 갈라지는지 보려고 같이 잰다.
4. **확신했는데 달라진 자리** — 낱말 확률이 높은데 실제와 다른 곳. 앱에서 쓸
   `app/tutor/transcript.py` 의 판정을 여기서 미리 검증한다.

CPU 로 돌려도 된다
------------------
**전사 정확도는 장치와 무관하다.** GPU 는 속도만 바꾼다. 그래서 api 컨테이너에
CUDA 를 넣지 않고 CPU·int8 로 돈다. 짧은 문장 열 개면 몇 분이면 끝난다.
속도가 궁금하면 그건 따로 재야 한다 — 이 스크립트는 정확도만 본다.

준비
----
faster-whisper 는 이미지에 들어 있다(requirements.txt). 읽을 문장부터 확인한다.

    docker compose exec api python scripts/eval_stt.py --list

실행
----
    docker compose exec api python scripts/eval_stt.py --audio-dir .review/audio
    docker compose exec api python scripts/eval_stt.py --models base small --naive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tutor.transcript import (  # noqa: E402
    confident_edits,
    edits,
    parse_words,
    tokens,
)

PROBES = Path(__file__).parent / "data" / "correction_probes.yaml"
AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm")


def load_probes(path: Path) -> list[dict]:
    """오류가 표시된 문장만. error_phrase 가 없으면 생존 여부를 판정할 수 없다."""
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [r for r in rows if r.get("expect") == "mistake" and r.get("error_phrase")]


def contains_phrase(transcript: str, phrase: str) -> bool:
    """전사가 그 조각을 **연속으로** 담고 있는가.

    연속이어야 하는 이유는 어순 오류 때문이다. `how i can` 이 `how can i` 로 바뀌면
    낱말은 다 있지만 오류는 사라졌다. 집합으로 보면 그걸 놓친다.
    """
    hay, needle = tokens(transcript), tokens(phrase)
    if not needle or len(hay) < len(needle):
        return False
    return any(hay[i : i + len(needle)] == needle for i in range(len(hay) - len(needle) + 1))


def wer(reference: str, hypothesis: str) -> float:
    """낱말 오류율. 편집 거리를 기준 낱말 수로 나눈다."""
    a, b = tokens(reference), tokens(hypothesis)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1] / len(a)


def audio_for(directory: Path, index: int) -> Path | None:
    for suffix in AUDIO_SUFFIXES:
        candidate = directory / f"{index:02d}{suffix}"
        if candidate.exists():
            return candidate
    return None


def print_reading_list(probes: list[dict], directory: Path) -> None:
    print()
    print(f"아래 {len(probes)}문장을 **적힌 그대로** 읽어서 녹음하세요.")
    print()
    print("  [주의] 틀린 문장입니다. 고쳐 읽으면 측정이 통째로 무의미해집니다.")
    print("         'I want ice americano' 를 'an iced americano' 로 읽으면 안 됩니다.")
    print("  [주의] 평소 말하듯 읽으세요. 또박또박 원어민처럼 읽으면 실제보다")
    print("         좋은 숫자가 나와서, 정작 알고 싶은 것을 못 봅니다.")
    print(f"  [형식] wav/mp3/m4a 아무거나. {directory} 에 아래 이름으로 저장하세요.")
    print()
    for i, probe in enumerate(probes, 1):
        print(f"  {i:02d}.wav   {probe['say']}")
        print(f"           오류: {probe.get('why', '')} · 판정 조각: {probe['error_phrase']!r}")
    print()


def transcribe(
    model,
    path: Path,
    *,
    naive: bool,
    beam_size: int = 0,
    initial_prompt: str = "",
) -> tuple[str, list[dict]]:
    """전사한다. `naive` 면 기본값으로 — prior 억제 설정이 효과가 있는지 보려고."""
    # 무음이 들어오면 Whisper 는 "I'm sorry" 같은 말을 **끝없이 지어낸다**. 실제로
    # 무음 파일로 배관을 확인하다 겪었다. 학습자가 마이크만 누르고 말을 안 하면
    # 앱에서도 똑같이 벌어지므로, 여기서도 앱에서도 음성 구간 검출을 켜 둔다.
    options: dict = {"word_timestamps": True, "language": "en", "vad_filter": True}
    if not naive:
        # prior 가 학습자의 오류를 다듬는 것을 최대한 줄인다. initial_prompt 는
        # 일부러 주지 않는다 — 그게 바로 모델을 표준 영어 쪽으로 미는 손잡이다.
        options["temperature"] = 0.0
        options["condition_on_previous_text"] = False
    if beam_size:
        # 빔 서치는 **가장 그럴듯한 문장**을 고르는 장치다. 그럴듯함은 곧 문법성이라,
        # 빔이 넓을수록 학습자의 오류가 후보에서 밀려난다. faster-whisper 의 기본값이
        # beam_size=5 · best_of=5 라서 여태 잰 것은 전부 빔 5 였다. 1 이면 그리디 —
        # 매 자리에서 1등만 뽑고 **문장 전체의 그럴듯함은 보지 않는다.**
        options["beam_size"] = beam_size
        options["best_of"] = beam_size
    if initial_prompt:
        # 이 손잡이는 앞서 **일부러 비워 뒀다** — 표준 영어 표본을 주면 모델이 그쪽으로
        # 밀린다고 봤기 때문이다. 그렇다면 학습자 영어 표본을 주면 반대로 밀릴 수도
        # 있다. 같은 손잡이를 반대로 돌리는 것이라, 되는지는 재 봐야 안다.
        options["initial_prompt"] = initial_prompt
    segments, _ = model.transcribe(str(path), **options)

    text_parts: list[str] = []
    words: list[dict] = []
    for segment in segments:
        text_parts.append(segment.text)
        for word in getattr(segment, "words", None) or []:
            words.append({"word": word.word, "probability": getattr(word, "probability", None)})
    return " ".join(text_parts).strip(), words


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probes", type=Path, default=PROBES)
    parser.add_argument("--audio-dir", type=Path, default=Path(".review/audio"))
    parser.add_argument("--models", nargs="+", default=["base", "small", "large-v3"])
    parser.add_argument("--list", action="store_true", help="읽을 문장과 파일 이름만 출력")
    parser.add_argument("--naive", action="store_true", help="prior 억제 없이 기본값으로 전사")
    parser.add_argument(
        "--beam-size", type=int, default=0, help="1 이면 그리디. 0 이면 기본값(빔 5)"
    )
    parser.add_argument(
        "--initial-prompt", default="", help="문체 표본. 학습자 영어를 주면 어떻게 되는지 보려고"
    )
    # 모델은 크다(base 150MB · small 500MB · large-v3 3GB). 컨테이너 안에 받으면
    # 재생성할 때마다 다시 받는다. 바인드 마운트된 곳에 두어 살아남게 한다.
    parser.add_argument("--cache-dir", type=Path, default=Path(".review/whisper"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    probes = load_probes(args.probes)
    if args.list:
        print_reading_list(probes, args.audio_dir)
        return 0

    missing = [i for i, _ in enumerate(probes, 1) if audio_for(args.audio_dir, i) is None]
    if missing:
        print(f"녹음이 없습니다: {', '.join(f'{i:02d}' for i in missing)}")
        print(f"`--list` 로 읽을 문장을 확인하고 {args.audio_dir} 에 저장하세요.")
        return 1

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper 가 없습니다. 이미지가 오래된 것입니다:")
        print("  docker compose build api && docker compose up -d api")
        return 1

    results: dict[str, list[dict]] = {}
    for name in args.models:
        # 정확도는 장치와 무관하다. GPU 는 속도만 바꾼다.
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        model = WhisperModel(
            name, device="cpu", compute_type="int8", download_root=str(args.cache_dir)
        )
        marks = []
        if args.naive:
            marks.append("기본값 — prior 억제 없음")
        if args.beam_size:
            marks.append("그리디 beam=1" if args.beam_size == 1 else f"beam={args.beam_size}")
        if args.initial_prompt:
            marks.append("학습자 문체 프롬프트")
        note = f"  ({' · '.join(marks)})" if marks else ""
        print()
        print("=" * 72)
        print(f"{name}{note}")
        print("=" * 72)

        rows: list[dict] = []
        for i, probe in enumerate(probes, 1):
            path = audio_for(args.audio_dir, i)
            assert path is not None
            text, words = transcribe(
                model,
                path,
                naive=args.naive,
                beam_size=args.beam_size,
                initial_prompt=args.initial_prompt,
            )
            survived = contains_phrase(text, probe["error_phrase"])
            exact = tokens(text) == tokens(probe["say"])
            # edits(들은 것, 실제로 말한 것) — 학습자가 고친 것과 같은 방향으로 본다.
            changes = edits(text, probe["say"])
            rows.append(
                {
                    "say": probe["say"],
                    "error_phrase": probe["error_phrase"],
                    "transcript": text,
                    "survived": survived,
                    "exact": exact,
                    "wer": round(wer(probe["say"], text), 3),
                    "confident_changes": [
                        {"heard": c.heard, "actual": c.confirmed}
                        for c in confident_edits(parse_words(words), changes)
                    ],
                    "words": words,
                }
            )
            print(f"  [{'O' if survived else 'X'}] {probe['say']}")
            if not exact:
                print(f"      들림: {text}")
        results[name] = rows

    print()
    print("=" * 72)
    print(f"{'모델':<16}{'오류 생존':>14}{'완전 일치':>12}{'평균 WER':>12}{'확신 오류':>12}")
    print("-" * 72)
    for name, rows in results.items():
        n = len(rows)
        alive = sum(r["survived"] for r in rows)
        exact = sum(r["exact"] for r in rows)
        avg_wer = sum(r["wer"] for r in rows) / n
        confident = sum(len(r["confident_changes"]) for r in rows)
        print(
            f"{name:<16}{f'{alive}/{n} ({alive / n * 100:.0f}%)':>14}"
            f"{f'{exact}/{n}':>12}{avg_wer:>12.3f}{confident:>12}"
        )

    print()
    print("읽는 법")
    print("  오류 생존 = 학습자가 말한 오류가 전사에 남은 비율. 이 앱에서는 이게 높아야 한다.")
    print("  평균 WER  = 일반적인 전사 정확도. 낮을수록 '잘 알아듣는' 모델이다.")
    print()
    print("  둘이 반대로 움직이면 그게 결론이다 — 잘 알아듣는 모델일수록 오류를 덮는다는")
    print("  뜻이라, 이 앱에는 작고 덜 똑똑한 모델이 맞을 수 있다.")
    print("  확신 오류 = 확률이 높은데 실제와 다른 낱말. 흐리게 표시하는 것으로는 못 잡는 자리다.")

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print()
        print(f"원자료: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
