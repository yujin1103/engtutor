"""CEFR-J 어휘 목록을 내려받아 레벨 대조용 표로 만든다. LLM 을 부르지 않는다.

왜 필요한가
-----------
이 앱의 `level`(A1/A2/B1)은 LLM 이 붙인다. 그런데 프롬프트에 "레벨을 부풀리지
말라"고 적어 놓고도 2,801개 중 1,899개(68%)가 B1 으로 나왔다. **모델이 붙인 레벨을
모델로 검사하면 같은 편향이 두 번 들어간다.**

그래서 밖에서 만든 등급표를 가져와 대조한다. CEFR-J 어휘 목록은 빈도만으로 만든
것이 아니라 **교육용으로 설계된** 목록이라 이 용도에 맞는다.

출처와 조건
-----------
- CEFR-J Vocabulary Profile 1.5 — Tono Laboratory, Tokyo University of Foreign Studies
- 배포: https://github.com/openlanguageprofiles/olp-en-cefrj
- 조건: "can be used for research and commercial purposes with no charge, provided
  that you cite the dataset properly." (CC 계열이 아니라 별도 허가 문구다)

CLAUDE.md §3.5 가 금지하는 것은 시판 교재·이북의 예문·해설 수집이다. 여기서 쓰는 것은
공개된 등급표이고, **예문이나 해설은 한 줄도 가져오지 않는다** — 표제어와 레벨뿐이다.

실행:
    docker compose exec api python content/prepare_cefrj.py
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SOURCE = (
    "https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/"
    "master/cefrj-vocabulary-profile-1.5.csv"
)
OUT = Path(__file__).resolve().parent.parent / "app" / "content" / "data" / "cefrj_levels.csv"

# 우리가 쓰는 세 등급으로 접는다. B2 이상은 "왕초보용이 아니다"는 한 가지 뜻만 가진다.
FOLD = {"A1": "A1", "A2": "A2", "B1": "B1", "B2": "B2", "C1": "C2", "C2": "C2"}
ORDER = ["A1", "A2", "B1", "B2", "C2"]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("prepare_cefrj")


def normalize(headword: str) -> list[str]:
    """표제어 칸을 실제 낱말들로 편다.

    `a.m./A.M./am/AM` 처럼 한 칸에 표기 변형이 슬래시로 묶여 있다. 우리 표제어는
    소문자 한 낱말이라, 편 다음 낱말 모양인 것만 남긴다.
    """
    out: list[str] = []
    for part in headword.replace("|", "/").split("/"):
        word = part.strip().lower()
        if not word or not word.isascii():
            continue
        if not word.replace("'", "").replace("-", "").isalpha():
            continue
        if word not in out:
            out.append(word)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    logger.info("내려받는 중: %s", args.source)
    res = httpx.get(args.source, timeout=120, follow_redirects=True)
    res.raise_for_status()

    # 한 표제어가 품사마다 다른 레벨을 가진다(`book` 명사 A1 / 동사 A2).
    # 우리 표는 표제어 하나에 레벨 하나라, **가장 낮은(쉬운) 레벨**을 남긴다 —
    # 학습자가 그 단어를 처음 만나는 지점이 그쪽이기 때문이다.
    best: dict[str, str] = {}
    rows = 0
    for row in csv.DictReader(io.StringIO(res.text)):
        level = FOLD.get((row.get("CEFR") or "").strip().upper())
        if not level:
            continue
        rows += 1
        for word in normalize(row.get("headword") or ""):
            current = best.get(word)
            if current is None or ORDER.index(level) < ORDER.index(current):
                best[word] = level

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# CEFR-J Vocabulary Profile 1.5 — Tono Laboratory, TUFS\n")
        handle.write("# https://github.com/openlanguageprofiles/olp-en-cefrj\n")
        handle.write("# 표제어와 레벨만 옮긴 표. 뜻·예문은 가져오지 않는다.\n")
        handle.write("# 한 표제어가 품사마다 다른 레벨이면 가장 쉬운 쪽을 남겼다.\n")
        handle.write("word,cefr\n")
        for word in sorted(best):
            handle.write(f"{word},{best[word]}\n")

    spread = Counter(best.values())
    logger.info(
        "원본 %d행 -> 표제어 %d개 (%s)",
        rows,
        len(best),
        " · ".join(f"{lv} {spread[lv]}" for lv in ORDER if spread[lv]),
    )
    logger.info("저장: %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
