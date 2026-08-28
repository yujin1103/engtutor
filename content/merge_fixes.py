"""교정 YAML 에서 같은 낱말이 두 번 나오는 것을 하나로 합친다. LLM 을 부르지 않는다.

왜 필요한가
-----------
`apply_fixes.py` 는 항목마다 고친 값을 **DB 의 지금 값과 합쳐** 선별기에 건다.
같은 낱말이 두 번 있으면 앞 항목은 "새 설명 + 옛 문형" 이라는, 실제로는 어느
시점에도 존재하지 않는 상태로 검사받아 없는 지적을 낸다.

실제로 그렇게 쌓였다 — 검수를 묶음으로 돌리다 보면 앞 묶음에서 이미 고친 낱말이
다음 묶음에서 다시 올라온다. 한 번은 101개가 겹쳐 있었다.

합치는 규칙은 하나다: **뒤에 온 값이 이긴다.** 그게 마지막 판단이기 때문이다.
다만 `reason` 은 이어 붙인다 — 왜 고쳤는지가 이 파일의 값어치라서, 앞의 근거를
덮어쓰면 나중에 "이건 왜 이렇게 됐지" 를 답할 수 없다.

실행:
    docker compose exec api python content/merge_fixes.py
    docker compose exec api python content/merge_fixes.py --check    # 세기만 한다
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("merge_fixes")

DATA = Path(__file__).parent / "data"
FIXES = DATA / "manual_fixes.yaml"
GLOSS_FIXES = DATA / "gloss_fixes.yaml"

# 내보낼 때의 칸 차례. 읽는 사람이 늘 같은 순서로 보게 한다.
WORD_FIELDS = (
    "reason",
    "removed",
    "level",
    "meaning_ko",
    "pattern",
    "example",
    "usage_note",
    "confused_with",
)
GLOSS_FIELDS = ("example_ko", "reason")


def _merge(entries: list[dict]) -> tuple[OrderedDict, int]:
    """같은 표제어를 하나로 접는다. 돌려주는 값은 (합친 것, 접힌 수)."""
    out: OrderedDict[str, dict] = OrderedDict()
    folded = 0
    for entry in entries:
        word = str(entry["word"])
        if word not in out:
            out[word] = dict(entry)
            continue
        folded += 1
        prev = out[word]
        before, after = prev.get("reason", ""), entry.get("reason", "")
        prev.update({k: v for k, v in entry.items() if k != "reason"})
        if after and after not in before:
            prev["reason"] = (before + " / " + after).strip(" /")
    return out, folded


def _dump(path: Path, merged: OrderedDict, fields: tuple[str, ...]) -> None:
    """머리말 주석은 그대로 두고 항목만 다시 쓴다.

    JSON 표기로 적는 이유: `yaml.safe_dump` 는 한 줄짜리 글에도 문서 끝 표시(`...`)
    를 붙여 파일을 깨뜨린다. JSON 스칼라는 YAML 이 그대로 읽는다.
    """
    raw = path.read_text(encoding="utf-8")
    head = raw[: raw.index("\n- word:")]
    lines = [head.rstrip("\n"), ""]
    for word, entry in merged.items():
        lines.append(f"- word: {json.dumps(word, ensure_ascii=False)}")
        for key in fields:
            if key in entry:
                lines.append(f"  {key}: {json.dumps(entry[key], ensure_ascii=False)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run(path: Path, fields: tuple[str, ...], *, check: bool) -> int:
    entries = [e for e in (yaml.safe_load(path.read_text(encoding="utf-8")) or []) if isinstance(e, dict)]
    counted = Counter(str(e["word"]) for e in entries)
    dupes = sorted(w for w, n in counted.items() if n > 1)
    if not dupes:
        logger.info("%s: 겹치는 낱말 없음 (%d개)", path.name, len(entries))
        return 0
    logger.info("%s: 겹치는 낱말 %d개 — %s", path.name, len(dupes), ", ".join(dupes[:8]))
    if check:
        return len(dupes)
    merged, folded = _merge(entries)
    _dump(path, merged, fields)
    logger.info("%s: %d개를 접어 %d개가 됐습니다", path.name, folded, len(merged))
    return len(dupes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="세기만 하고 고치지 않는다")
    args = parser.parse_args()
    total = run(FIXES, WORD_FIELDS, check=args.check)
    total += run(GLOSS_FIXES, GLOSS_FIELDS, check=args.check)
    # `--check` 는 겹치는 것이 있으면 종료 코드로 알린다. 시험이 이미 같은 것을
    # 보지만, 사람이 손으로 돌려 볼 때도 조용히 지나가면 안 된다.
    return 1 if (args.check and total) else 0


if __name__ == "__main__":
    sys.exit(main())
