"""NGSL 원본(JSON) -> 배치 생성용 CSV.

원본 출처
---------
New General Service List (NGSL) — Browne, C., Culligan, B., & Phillips, J. (2013, rev. 2023)
공식: https://www.newgeneralservicelist.com/   (CC BY-SA 4.0)
기계가독 변환본: https://github.com/lpmi-13/machine_readable_wordlists  (CC0-1.0)

⚠️ 예전에 쓰이던 newgeneralservicelist.**org** 는 원저자가 소유권을 잃은 도메인이다.
   현재 다른 곳에서 운영하며 원문을 복제해 트래픽을 끌고 있으니 인용하지 말 것.

원본 구조
---------
{"1000": {headword: [word family...]}, "2000": {...}, "3000": {...}}
세 개의 빈도 구간(band)으로 나뉜다. 1000 구간이 가장 자주 쓰이는 단어다.

**band 를 유지하는 이유**: 2,800개를 전부 같은 무게로 검수할 수는 없다.
빈도가 곧 학습자가 마주칠 확률이므로, band 가 검수 우선순위가 된다.

실행:
    docker compose exec api python content/prepare_ngsl.py
    docker compose exec api python content/prepare_ngsl.py --band 1000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_SOURCE = DATA_DIR / "ngsl_raw.json"
DEFAULT_OUT = DATA_DIR / "ngsl.csv"

# 파일에 등장하는 순서 = 빈도 순서
BAND_ORDER = ("1000", "2000", "3000")


def convert(source: Path, out: Path, *, bands: tuple[str, ...] = BAND_ORDER) -> int:
    raw = json.loads(source.read_text(encoding="utf-8"))

    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for band in bands:
        if band not in raw:
            print(f"경고: band {band} 가 원본에 없습니다", file=sys.stderr)
            continue
        for headword in raw[band]:
            word = headword.strip().lower()
            # 표제어만 쓴다. 굴절형(word family)은 사전 항목으로 만들 필요가 없다.
            if not word or word in seen:
                continue
            if not word.isascii() or not word.replace("'", "").replace("-", "").isalpha():
                continue
            seen.add(word)
            rows.append((word, band))

    lines = [
        "# NGSL headwords — 빈도 구간(band) 순서로 정렬됨",
        "# 출처: Browne, Culligan & Phillips (NGSL, CC BY-SA 4.0) / 기계가독 변환본 CC0",
        "# 형식: word,band  (배치 스크립트는 첫 컬럼만 읽는다)",
        *[f"{w},{b}" for w, b in rows],
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts: dict[str, int] = {}
    for _, band in rows:
        counts[band] = counts.get(band, 0) + 1
    print(f"{out} 생성 — 총 {len(rows)}단어")
    for band in bands:
        if band in counts:
            print(f"  band {band}: {counts[band]}")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--band",
        action="append",
        choices=BAND_ORDER,
        help="특정 구간만 변환 (반복 지정 가능). 생략하면 전부",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"원본이 없습니다: {args.source}", file=sys.stderr)
        print("content/data/README.md 의 안내를 따라 내려받으세요.", file=sys.stderr)
        return 1

    bands = tuple(args.band) if args.band else BAND_ORDER
    convert(args.source, args.out, bands=bands)
    return 0


if __name__ == "__main__":
    sys.exit(main())
