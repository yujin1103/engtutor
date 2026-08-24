# 단어 목록 데이터

## 파일

| 파일 | 내용 |
|---|---|
| `ngsl_raw.json` | NGSL 원본 (빈도 3구간 × 표제어 → word family) |
| `ngsl.csv` | `prepare_ngsl.py` 로 변환한 배치 입력. `word,band` 형식, **빈도 순서** |
| `starter_words.txt` | NGSL이 아닌 스타터 60단어. 파이프라인을 바로 돌려보기 위한 것 |

## NGSL

New General Service List — 약 **2,801 표제어**로 영어 텍스트의 90% 이상을 커버하는 공개 목록.

- 원저: **Browne, C., Culligan, B., & Phillips, J.** (2013, 개정 2023) — **CC BY**
- 공식: <https://www.newgeneralservicelist.com/>
- 기계가독 변환본: <https://github.com/lpmi-13/machine_readable_wordlists> (CC0-1.0)

> ⚠️ **`newgeneralservicelist.org` 는 인용하지 마세요.** 원저자가 소유권을 잃은 도메인이고,
> 현재 제3자가 원문을 복제해 도박 트래픽 유도에 쓰고 있습니다. 공식은 **`.com`** 입니다.

CC BY이므로 결과물을 공개할 때 **출처를 표기**하세요.

### 갱신하려면

```powershell
# 원본 내려받기 (또는 공식 사이트 스프레드시트를 직접 변환)
curl -o content/data/ngsl_raw.json `
  https://raw.githubusercontent.com/lpmi-13/machine_readable_wordlists/master/General/NGSL/NGSL.json

# CSV 변환
docker compose exec api python content/prepare_ngsl.py

# 배치 생성
docker compose exec api python content/batch_generate.py --wordlist content/data/ngsl.csv
```

### 빈도 구간(band)을 왜 유지하는가

원본은 `1000` / `2000` / `3000` 세 구간으로 나뉘고, `1000` 구간이 가장 자주 쓰이는 단어다.
2,801개를 전부 같은 무게로 검수할 수는 없으므로 **빈도가 곧 검수 우선순위**가 된다.
`ngsl.csv` 는 이 순서를 그대로 유지하고, 배치도 앞에서부터 처리한다.

일부 구간만 돌리려면:

```powershell
docker compose exec api python content/prepare_ngsl.py --band 1000
```

## 하지 말 것

시판 단어책·이북 등 저작물에서 예문이나 해설을 수집하는 코드는 작성하지 않습니다
(CLAUDE.md 9절). 교차 확인이 필요하면 공개 자원만 씁니다 — Wiktionary, WordNet,
Tatoeba(CC BY).
