"""단어 후보가 **실재하는 말인지** 사전으로 확인한다. 판정에 LLM 을 쓰지 않는다.

왜 필요한가
-----------
장면별 어휘(카페 메뉴, 증상, 옷)는 빈도 목록에 없어서 LLM 에게 후보를 받아야 한다.
그런데 LLM 은 없는 단어를 만들어 낸다 — NGSL 2,801개 전수 조사에서 `restaurate`·
`habor`·`oranje` 가 나왔다(docs/hallucinations.md). 그래서 **후보는 LLM, 판정은 사전**
이라는 경계를 여기서 긋는다.

어떻게 확인하는가
-----------------
1. **WordNet** — 있으면 끝. 품사까지 준다.
2. **Wiktionary** — WordNet 은 2006년 판이라 그 뒤 자리 잡은 일상어를 모른다
   (`americano`, `latte`, `smoothie`, `wifi`). 영어 표제부가 있고 뜻풀이가 달려
   있으면 실재로 본다. **뜻과 출처 주소를 함께 적어 둔다** — 나중에 사람이 확인할 수
   있어야 하기 때문이다.
3. 둘 다 모르면 **버린다.** "아마 맞겠지"로 통과시키면 검사기가 있으나 마나다.

Wiktionary 는 그대로 쓰면 너무 헐겁다
-------------------------------------
Wiktionary 에는 폐어·방언·오철자까지 다 있다. 실제로 우리가 환각으로 잡아냈던
`restaurate` 가 **표제어로 존재한다** — "To restore. {{lb|en|obsolete|or|nonstandard}}".
그래서 존재만 보면 지웠던 환각이 되돌아온다.

그래서 **꼬리표를 본다.** 위키텍스트의 영어 구역에서 뜻풀이 줄마다 붙은
`{{lb|en|obsolete}}` 류를 읽어, **꼬리표 없는 현대 뜻이 하나라도 있을 때만** 통과시킨다.
`tumbler` 는 낡은 뜻이 여럿이지만 '유리컵'이라는 멀쩡한 뜻이 있어 통과하고,
`restaurate` 는 모든 뜻이 폐어라 떨어진다.

CLAUDE.md §3.5 가 교차 확인용으로 지정한 공개 자원만 쓴다(Wiktionary, WordNet).
시판 교재·이북은 건드리지 않는다.

결과물
------
- `app/content/data/lexicon_extra.yaml` — WordNet 밖에서 확인된 말. 앱이 읽는다.
- `--out` 로 지정한 단어 목록 파일 — 배치 생성에 그대로 먹인다.

실행:
    docker compose exec api python scripts/verify_words.py --words .review/candidates.json
    docker compose exec api python scripts/verify_words.py --words a.txt --out content/data/topic_words.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content import lexicon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXTRA_PATH = ROOT / "app" / "content" / "data" / "lexicon_extra.yaml"
CACHE_PATH = ROOT / ".review" / "wiktionary_cache.json"

WIKTIONARY = "https://en.wiktionary.org/api/rest_v1/page/definition/{word}"
WIKITEXT = "https://en.wiktionary.org/w/api.php"
# 위키미디어는 사람이 읽을 수 있는 User-Agent 를 요구한다. 연락처 없는 기본값으로
# 두들기면 차단당해도 할 말이 없다.
#
# **ASCII 로만 쓴다.** 여기에 한글을 넣었다가 httpx 가 헤더를 인코딩하지 못해
# (`UnicodeEncodeError: ascii codec`) 요청이 아예 나가지 않았고, 재시도가 그걸
# 그대로 삼켜서 멀쩡한 낱말 11개가 "사전에 없음"으로 떨어졌다. 네트워크가 막힌 게
# 아니라 **우리가 못 보낸 것**이었다.
HEADERS = {"User-Agent": "engtutor-wordcheck/0.1 (personal study project; local build)"}

# 표제어로 받아들이는 모양. 공백은 받지 않는다 — 이 프로젝트의 표제어는 한 낱말이다.
# 하이픈과 아포스트로피는 낱말 안에 들어간다 (`check-in`, `o'clock`).
_HEADWORD = re.compile(r"^[a-z]+(?:['-][a-z]+)*$")
_TAG = re.compile(r"<[^>]+>")
# REST 응답에 위키 스타일 블록이 통째로 섞여 나올 때가 있다
# (`.mw-parser-output .defdate{font-size:smaller}`). 뜻풀이가 아니라 CSS 다.
_CSS_JUNK = re.compile(r"\.mw-parser-output[^\n]*?\}")


def clean_definition(text: str) -> str:
    return " ".join(_CSS_JUNK.sub(" ", _TAG.sub("", text)).split())

# Wiktionary 품사 이름 -> WordNet 태그. 매핑되지 않는 것(Interjection, Preposition)도
# 실재의 근거로는 충분하다. 다만 품사 대조에는 쓸 수 없어서 태그를 비워 둔다.
POS_MAP = {
    "noun": lexicon.POS_NOUN,
    "verb": lexicon.POS_VERB,
    "adjective": lexicon.POS_ADJ,
    "adverb": lexicon.POS_ADV,
}
# 이것만 있으면 실재어로 보지 않는다. 고유명사는 어휘가 아니라 이름이고,
# 약어·기호는 학습자에게 가르칠 표제어가 아니다.
POS_REJECT = {"proper noun", "symbol", "letter", "prefix", "suffix", "abbreviation"}

# 위키텍스트에서 뜻풀이 줄과 그 꼬리표를 읽는다.
# `#` 로 시작하는 줄이 뜻이고, `#*`·`#:` 는 인용과 예문이라 뜻이 아니다.
_ENGLISH_SECTION = re.compile(r"^==\s*English\s*==\s*$(.*?)(?=^==[^=]|\Z)", re.M | re.S)
_SENSE_LINE = re.compile(r"^#(?![#*:])\s*(.*)$", re.M)
_LABEL_TEMPLATE = re.compile(r"\{\{(?:lb|lbl|label|tlb|term-label)\|en\|([^}]*)\}\}", re.I)
_FORM_OF = re.compile(
    r"\{\{\s*(misspelling of|obsolete spelling of|obsolete form of|archaic spelling of|"
    r"archaic form of|eye dialect of|dated form of|superseded spelling of)\b",
    re.I,
)

# 이 꼬리표만 달린 뜻은 학습자에게 가르칠 뜻이 아니다. 낡았거나, 지역에만 있거나,
# 초보에게 가르치면 안 되는 말이다. `informal`·`slang` 은 남긴다 — 일상 회화가
# 그 층위이기 때문이고, 그건 뜻이 낡았다는 표시가 아니다.
STALE_LABELS = {
    "obsolete", "archaic", "dated", "rare", "nonstandard", "proscribed",
    "dialectal", "dialect", "regional", "historical", "poetic", "literary",
    "vulgar", "offensive", "derogatory", "slur", "ethnic slur", "obsolete term",
    "uncommon", "neologism", "rare or obsolete", "now rare", "now obsolete",
}


@dataclass
class Verdict:
    word: str
    ok: bool
    source: str  # wordnet | wiktionary | function-word | none
    pos: list[str] = field(default_factory=list)
    pos_raw: list[str] = field(default_factory=list)
    gloss: str = ""
    glosses: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def url(self) -> str:
        return f"https://en.wiktionary.org/wiki/{self.word}#English"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response | None:
    """공개 API 를 예의 있게 두드린다. 429 는 기다렸다 다시 묻는다.

    처음에 스레드 4개로 몰아쳤다가 `hoodie`·`app` 같은 멀쩡한 단어가 429 를 받고
    "사전에 없음"으로 떨어졌다. **막힌 것을 없는 것으로 기록하면** 검사기가
    조용히 거짓말을 하게 된다. 그래서 429 는 실패가 아니라 대기다.
    """
    for attempt in range(5):
        try:
            res = client.get(url, params=params, headers=HEADERS, timeout=30)
        except Exception:
            time.sleep(1 + attempt)
            continue
        if res.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        return res
    return None


def ask_wiktionary(word: str, client: httpx.Client) -> dict | None:
    """영어 표제부를 돌려준다. 없으면 None. 못 물어봤으면 예외를 던진다."""
    res = fetch(client, WIKTIONARY.format(word=quote(word, safe="")))
    if res is None:
        raise LookupError(f"Wiktionary 에 물어보지 못했습니다: {word}")
    if res.status_code == 404:
        return None
    if res.status_code != 200:
        raise LookupError(f"Wiktionary 응답 {res.status_code}: {word}")
    try:
        data = res.json()
    except Exception:
        return None
    sections = data.get("en")
    if not sections:
        return None
    out: dict = {"pos_raw": [], "gloss": "", "glosses": []}
    for section in sections:
        name = str(section.get("partOfSpeech") or "").strip()
        definitions = [
            clean_definition(str(d.get("definition") or ""))
            for d in section.get("definitions") or []
        ]
        definitions = [d for d in definitions if d]
        if not definitions:
            continue
        out["pos_raw"].append(name)
        if not out["gloss"]:
            out["gloss"] = definitions[0][:300]
        for definition in definitions[:2]:
            if len(out["glosses"]) < 3:
                out["glosses"].append(f"[{name}] {definition[:200]}")
    return out if out["pos_raw"] else None


_POS_HEADER = re.compile(r"^===+\s*([A-Za-z][A-Za-z ]*?)\s*===+\s*$", re.M)
_WIKI_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_WIKI_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
_WIKI_QUOTES = re.compile(r"'{2,}")

# 위키텍스트에서 품사 제목으로 나오는 것들. Etymology/Pronunciation 같은 다른 제목과
# 갈라내려면 목록이 필요하다.
_POS_HEADINGS = {
    "noun", "verb", "adjective", "adverb", "proper noun", "interjection",
    "preposition", "conjunction", "pronoun", "numeral", "determiner", "article",
    "particle", "phrase", "prefix", "suffix", "abbreviation", "symbol", "letter",
    "contraction", "postposition", "participle",
}


# 뜻이 통째로 템플릿 하나인 경우가 있다 — `{{alternative form of|en|non-smoking}}`.
# 그냥 걷어내면 뜻풀이가 빈 줄이 되어 **사람이 확인할 근거가 사라진다.**
# 실제로 `nonsmoking`·`round-trip` 이 뜻 없이 추가 사전에 들어갔다.
_POINTER = re.compile(
    r"\{\{\s*(alternative form of|alternative spelling of|altform|synonym of|syn of|"
    r"abbreviation of|initialism of|clipping of|short for|contraction of|plural of)"
    r"\s*\|\s*en\s*\|\s*([^|}]+)[^}]*\}\}",
    re.I,
)


def strip_wikitext(line: str) -> str:
    """뜻풀이 한 줄에서 위키 표기를 걷어낸다. 사람이 읽을 근거로만 쓴다."""
    text = _POINTER.sub(lambda m: f"{m.group(1).lower()} {m.group(2).strip()}", line)
    text = _WIKI_TEMPLATE.sub(" ", text)
    text = _WIKI_TEMPLATE.sub(" ", text)  # 중첩 템플릿 한 겹 더
    text = _WIKI_LINK.sub(r"\1", text)
    text = _WIKI_QUOTES.sub("", text)
    return " ".join(text.split())


def read_wikitext(word: str, client: httpx.Client) -> str | None:
    """영어 구역의 위키텍스트. 문서가 없으면 None, 못 물어봤으면 예외."""
    res = fetch(
        client,
        WIKITEXT,
        {"action": "parse", "page": word, "prop": "wikitext", "format": "json", "formatversion": 2},
    )
    if res is None:
        raise LookupError(f"Wiktionary 에 물어보지 못했습니다: {word}")
    if res.status_code != 200:
        raise LookupError(f"Wiktionary 응답 {res.status_code}: {word}")
    try:
        data = res.json()
    except Exception:
        return None
    if "error" in data:  # missingtitle 등 — 문서가 없다는 뜻
        return None
    text = data.get("parse", {}).get("wikitext", "")
    section = _ENGLISH_SECTION.search(text or "")
    return section.group(1) if section else None


def parse_english(section: str) -> dict:
    """영어 구역에서 품사·뜻·꼬리표를 읽는다. 요청을 한 번만 쓰기 위한 것이다.

    처음에는 REST 뜻풀이 API 와 위키텍스트 API 를 둘 다 불렀는데, 낱말당 두 번씩
    두들기다 429 로 막혔다. 위키텍스트 하나에 필요한 게 다 들어 있다.
    """
    pos_raw: list[str] = []
    for name in _POS_HEADER.findall(section):
        low = name.strip().lower()
        if low in _POS_HEADINGS and name not in pos_raw:
            pos_raw.append(name.strip())

    modern = total = 0
    labels: list[str] = []
    glosses: list[str] = []
    for line in _SENSE_LINE.findall(section):
        if not line.strip():
            continue
        total += 1
        found = {
            part.strip().lower()
            for group in _LABEL_TEMPLATE.findall(line)
            for part in group.split("|")
            if part.strip()
        }
        stale = sorted(found & STALE_LABELS)
        labels.extend(stale)
        if stale or _FORM_OF.search(line):
            continue
        modern += 1
        text = strip_wikitext(line).strip(" .;:,")
        # 템플릿만으로 된 뜻줄은 걷어내면 아무것도 안 남는다. 그럴 땐 다음 뜻을 쓴다 —
        # 근거로 보여줄 문장이 "." 이면 근거가 아니다.
        if len(text) > 2 and len(glosses) < 3:
            glosses.append(text[:200])
    return {
        "pos_raw": pos_raw,
        "modern": modern,
        "senses": total,
        "labels": sorted(set(labels)),
        "glosses": glosses,
        "gloss": glosses[0] if glosses else "",
    }


def modern_senses(word: str, client: httpx.Client) -> tuple[int, int, list[str]]:
    """(현대 뜻 수, 전체 뜻 수, 붙은 꼬리표들). 영어 구역이 없으면 (0, 0, []).

    꼬리표가 하나도 없는 뜻이 '현대 뜻'이다. 지역·전문 분야 꼬리표(`chemistry`,
    `US`)는 낡음의 표시가 아니므로 STALE_LABELS 에 있는 것만 센다.
    """
    res = fetch(
        client,
        WIKITEXT,
        {"action": "parse", "page": word, "prop": "wikitext", "format": "json", "formatversion": 2},
    )
    if res is None or res.status_code != 200:
        return (0, 0, [])
    try:
        text = res.json().get("parse", {}).get("wikitext", "")
    except Exception:
        return (0, 0, [])
    section = _ENGLISH_SECTION.search(text or "")
    if not section:
        return (0, 0, [])

    modern = total = 0
    seen: list[str] = []
    for line in _SENSE_LINE.findall(section.group(1)):
        if not line.strip():
            continue
        total += 1
        labels = {
            part.strip().lower()
            for group in _LABEL_TEMPLATE.findall(line)
            for part in group.split("|")
            if part.strip()
        }
        seen.extend(sorted(labels & STALE_LABELS))
        if labels & STALE_LABELS or _FORM_OF.search(line):
            continue
        modern += 1
    return (modern, total, sorted(set(seen)))


def verify(word: str, client: httpx.Client, cache: dict) -> Verdict:
    w = word.strip().lower()
    if not _HEADWORD.match(w):
        return Verdict(w, False, "none", reason="표제어 모양이 아닙니다 (한 낱말·영문 소문자·하이픈만)")
    if w in lexicon.FUNCTION_WORDS:
        return Verdict(w, True, "function-word")

    pos = lexicon.parts_of_speech(w)
    if pos:
        return Verdict(w, True, "wordnet", pos=sorted(pos))
    bases = lexicon.lemmas(w) - {w}
    if bases:
        # 굴절형이라 원형으로만 잡히는 경우. 실재하지만 표제어로는 부적절하다.
        # 원형이 안 나오는데 known 이 True 인 경우(하이픈 합성어)는 여기가 아니다 —
        # `to-go`·`carry-on` 을 굴절형이라고 부르면 멀쩡한 표제어가 사라진다.
        return Verdict(w, False, "wordnet", reason=f"굴절형입니다 (원형: {', '.join(sorted(bases))})")

    if w in cache:
        found = cache[w]
    else:
        section = read_wikitext(w, client)  # 429·네트워크 실패는 예외로 올라간다
        found = parse_english(section) if section else None
        if found and not found["pos_raw"]:
            found = None
        cache[w] = found
        time.sleep(0.5)  # 공개 API 다. 몰아치지 않는다.
    if not found:
        return Verdict(w, False, "none", reason="WordNet 에도 Wiktionary 에도 없습니다")

    raw = [str(p) for p in found.get("pos_raw") or []]
    lowered = [p.lower() for p in raw]
    if all(p in POS_REJECT for p in lowered):
        return Verdict(w, False, "wiktionary", pos_raw=raw, reason=f"품사가 {', '.join(raw)} 뿐입니다")

    # 꼬리표 검사. 뜻줄을 하나도 못 읽었으면(위키텍스트 모양이 특이한 경우)
    # 막지 않는다 — 못 읽은 것을 '낡았다'로 바꾸면 멀쩡한 말이 조용히 사라진다.
    modern, total, labels = found.get("modern", 0), found.get("senses", 0), found.get("labels") or []
    if total and not modern:
        return Verdict(
            w, False, "wiktionary", pos_raw=raw,
            reason=f"뜻 {total}개가 모두 {', '.join(labels) or '옛말'} 입니다",
        )

    tags = sorted({POS_MAP[p] for p in lowered if p in POS_MAP})
    return Verdict(
        w, True, "wiktionary", pos=tags, pos_raw=raw,
        gloss=str(found.get("gloss") or ""), glosses=list(found.get("glosses") or []),
    )


def load_candidates(path: Path) -> list[tuple[str, str]]:
    """(단어, 주제) 목록. txt 는 주제 없이, json 은 워크플로 결과 모양 그대로 읽는다."""
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        packs = data.get("packs", data) if isinstance(data, dict) else data
        out: list[tuple[str, str]] = []
        for pack in packs:
            topic = str(pack.get("topic") or "")
            for item in pack.get("words") or []:
                word = str(item.get("word") or "").strip().lower()
                if word:
                    out.append((word, topic))
        return out
    return [
        (line.strip().lower(), "")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def write_extra(verdicts: list[Verdict]) -> int:
    """Wiktionary 로만 확인된 말을 앱이 읽는 파일에 적는다. 기존 항목은 유지한다."""
    existing: dict = {}
    if EXTRA_PATH.exists():
        existing = yaml.safe_load(EXTRA_PATH.read_text(encoding="utf-8")) or {}

    added = 0
    for v in verdicts:
        if not (v.ok and v.source == "wiktionary"):
            continue
        if v.word in existing:
            continue
        existing[v.word] = {
            "pos": v.pos,
            "pos_raw": v.pos_raw,
            "gloss": v.gloss,
            "glosses": v.glosses,
            "source": v.url,
        }
        added += 1

    header = (
        "# WordNet 이 모르는데 실재하는 말. **조회해서 적은 것이지 지어낸 것이 아니다.**\n"
        "#\n"
        "# WordNet 은 2006년 판이라 그 뒤 자리 잡은 일상어를 모른다 — americano, latte,\n"
        "# smoothie, wifi. 이 앱의 첫 시나리오가 카페 주문인데 americano 를 '없는 단어'로\n"
        "# 판정하면 빈칸 채점이 학습자에게 '그런 단어가 없어요'라고 말한다.\n"
        "#\n"
        "# 항목마다 Wiktionary 뜻풀이와 주소를 남긴다. 손으로 늘리지 말고\n"
        "# `scripts/verify_words.py` 로 다시 만든다.\n"
        "#\n"
        "# pos 는 품사 대조에 쓰는 태그(n/v/a/r)이고, pos_raw 는 Wiktionary 가 적은 그대로다.\n"
    )
    EXTRA_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXTRA_PATH.write_text(
        header + yaml.safe_dump(existing, allow_unicode=True, sort_keys=True, width=100),
        encoding="utf-8",
    )
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--words", type=Path, default=None, help="후보 파일 (.txt 또는 워크플로 .json)")
    parser.add_argument(
        "--audit-db",
        action="store_true",
        help="이미 저장된 표제어 중 사전이 모르는 것만 확인한다 (표에 있는 말은 사전이 알아야 한다)",
    )
    parser.add_argument("--out", type=Path, default=None, help="확인된 표제어를 목록 파일로 저장")
    parser.add_argument("--workers", type=int, default=1, help="사전 조회는 한 줄로 돈다 (429 방지)")
    parser.add_argument("--skip-existing", action="store_true", help="이미 words 테이블에 있는 단어는 뺀다")
    parser.add_argument("--no-extra", action="store_true", help="lexicon_extra.yaml 을 건드리지 않는다")
    parser.add_argument("--show", type=int, default=40)
    args = parser.parse_args()

    if args.audit_db:
        # 표에 있는 말은 사전이 알아야 한다. 모르는 것이 남아 있으면 빈칸 채점이
        # 학습자에게 "그런 단어가 없어요"라고 말한다 — 실제로 `americano` 가 그랬다.
        from app.db.database import db_session
        from app.db.models import WordRow
        from sqlalchemy import select

        with db_session() as db:
            heads = sorted({w.lower() for (w,) in db.execute(select(WordRow.word))})
        candidates = [(w, "") for w in heads if lexicon.known(w) is not True]
        print(f"표제어 {len(heads)}개 중 사전이 모르는 것 {len(candidates)}개")
    elif args.words:
        candidates = load_candidates(args.words)
    else:
        parser.error("--words 또는 --audit-db 중 하나가 필요합니다")
    topic_of: dict[str, str] = {}
    for word, topic in candidates:
        topic_of.setdefault(word, topic)
    words = list(topic_of)
    print(f"후보 {len(candidates)}개 · 중복 제외 {len(words)}개")

    if args.skip_existing:
        from app.db.database import db_session
        from app.db.models import WordRow
        from sqlalchemy import select

        with db_session() as db:
            have = {w.lower() for (w,) in db.execute(select(WordRow.word))}
        # 굴절형으로만 겹치는 것도 뺀다 — `towels` 를 따로 넣을 이유가 없다.
        before = len(words)
        words = [w for w in words if w not in have and not (lexicon.lemmas(w) & have)]
        print(f"이미 표에 있는 것 {before - len(words)}개 제외 -> {len(words)}개")

    cache = _load_cache()
    client = httpx.Client(follow_redirects=True)
    verdicts: list[Verdict] = []
    asked = 0
    try:
        # 한 줄로 돈다. 공개 API 를 병렬로 두들기면 429 가 돌아오고, 그걸 '없는
        # 단어'로 기록하면 검사기가 거짓말을 한다. WordNet 은 어차피 캐시라 빠르다.
        for i, word in enumerate(words, start=1):
            try:
                verdicts.append(verify(word, client, cache))
            except LookupError as exc:
                verdicts.append(Verdict(word, False, "none", reason=f"확인 못 함: {exc}"))
            if word not in cache:
                continue
            asked += 1
            if asked % 25 == 0:
                print(f"  … {i}/{len(words)} (사전 조회 {asked}건)")
                _save_cache(cache)  # 중간에 끊겨도 물어본 것은 남는다
    finally:
        client.close()
        _save_cache(cache)

    ok = [v for v in verdicts if v.ok]
    bad = [v for v in verdicts if not v.ok]
    by_source: dict[str, int] = {}
    for v in ok:
        by_source[v.source] = by_source.get(v.source, 0) + 1
    print(f"\n확인됨 {len(ok)} · 버림 {len(bad)}")
    for source, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {source:14s} {n}")

    if bad:
        print(f"\n버린 것 {len(bad)}개")
        for v in bad[: args.show]:
            print(f"  {v.word:20s} {v.reason}")
        if len(bad) > args.show:
            print(f"  … {len(bad) - args.show}개 더")

    fresh = [v for v in ok if v.source == "wiktionary"]
    if fresh:
        print(f"\nWordNet 밖에서 확인된 말 {len(fresh)}개 (뜻과 주소를 남깁니다)")
        for v in fresh[: args.show]:
            print(f"  {v.word:20s} {', '.join(v.pos_raw)[:28]:30s} {v.gloss[:60]}")
        if len(fresh) > args.show:
            print(f"  … {len(fresh) - args.show}개 더")

    if not args.no_extra:
        added = write_extra(verdicts)
        print(f"\n{EXTRA_PATH.relative_to(ROOT)} 에 {added}개 추가")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# rank: none",
            "#",
            "# scripts/verify_words.py 가 사전으로 확인한 표제어입니다.",
            "# WordNet 에 있거나, Wiktionary 에 **현대 뜻**이 있는 것만 남았습니다.",
            "# 순서는 장면별 묶음이지 빈도가 아닙니다.",
            "#",
        ]
        grouped: dict[str, list[str]] = {}
        for v in ok:
            grouped.setdefault(topic_of.get(v.word, ""), []).append(v.word)
        for topic in sorted(grouped):
            lines.append("")
            if topic:
                lines.append(f"# topic: {topic}")
            lines.extend(sorted(grouped[topic]))
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"확인된 {len(ok)}개를 {args.out} 에 적었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
