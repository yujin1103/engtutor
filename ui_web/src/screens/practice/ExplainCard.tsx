/** 답을 낸 뒤 보는 설명. **여기서 문장을 지어내지 않는다.**
 *
 * 이 화면의 규칙 하나가 나머지를 다 결정한다 — **아는 것보다 더 주장하지 않는다.**
 * 서버는 WordNet 과 `words` 행만 보고 카드를 만든다. WordNet 은 `banana` 가
 * 명사인 건 알아도 `Can I borrow your ____?` 에 어울리는지는 모른다. 그래서
 * 서버가 말할 수 있는 만큼만 담은 문장(`pos_text_ko`·`label_ko`·`note_ko`)을
 * 미리 만들어 보내고, 여기서는 **그대로 띄운다.** 화면이 `pos_ko` 배열을 받아
 * 제 나름의 문장을 지으면 그 경계가 사라지고, 사라진 자리에서 앱이 학습자에게
 * 거짓을 가르치게 된다.
 *
 * 이 카드가 말하는 것은 두 갈래다.
 *
 *   - **세어서 아는 것** — 표제어, 예문, 문형, 품사, 후보 낱말의 철자.
 *     `words` 행과 WordNet 에서 그대로 오거나 둘을 맞춰 본 결과다.
 *   - **누가 써 준 것** — 뜻(`meaning_ko`), 쓰임(`usage_note`), 헷갈리는 낱말.
 *     3,245개 중 4개만 사람이 봤고, 확인된 환각 13건이 전부 이쪽에 있었다.
 *
 * 둘째 갈래는 승인된 것과 **다른 상자**에 그린다. 그런데 "그리는 쪽이 조심한다"
 * 로는 안 됐다 — 처음 만들 때 후보 목록에서 `reviewed` 를 한 번도 안 읽어서
 * `straw` 의 뜻이 "줄기"(빨대인데) 로 승인된 `쓰임` 과 똑같은 상자에 떴다.
 * 그래서 지금은 `flow.splitWords`·`meaningOf`·`splitNotes` 가 값을 먼저 갈라
 * 주고, 확인 안 된 글자는 `unchecked_ko` 같은 **다른 칸 이름**으로만 온다.
 * 승인된 줄을 그리는 컴포넌트에는 넘길 수조차 없다 — 넘기면 컴파일이 막는다.
 */
import type { ClozeExplainOut } from "../../api/types";
import type { CheckedWord, UncheckedWord } from "./flow";
import {
  UNCHECKED_MEANING_KO,
  UNCHECKED_WORDS_KO,
  meaningOf,
  splitNotes,
  splitWords,
} from "./flow";

import styles from "./practice.module.css";

export function ExplainCard({ card }: { card: ClozeExplainOut }) {
  const meaning = meaningOf(card);
  const notes = splitNotes(card);
  const alts = card.alternatives ? splitWords(card.alternatives.words) : null;

  return (
    <div className={styles.explain}>
      <section className={styles.section}>
        <div className={styles.label}>이 낱말</div>
        <div className={styles.headword}>
          <span className={styles.headwordEn}>{card.answer}</span>
          {/* 확인된 뜻만 표제어 옆에 붙는다. 확인 전이면 아래 점선 상자로 내려간다 —
              카드에서 제일 크게 읽히는 자리라 더더욱 자리를 갈라야 한다. */}
          {meaning.checked && <span className={styles.headwordKo}>{meaning.meaning_ko}</span>}
        </div>

        {!meaning.checked && (
          <div className={styles.inset}>
            <p className={styles.insetNote}>{UNCHECKED_MEANING_KO}</p>
            <p className={styles.headwordKo}>{meaning.unchecked_ko}</p>
          </div>
        )}

        {card.pos_ko.length > 0 && (
          <div className={styles.tags}>
            {card.pos_ko.map((label) => (
              <span key={label} className={styles.tag}>
                {label}
              </span>
            ))}
          </div>
        )}

        {/* 사용자가 원한 학습 지점이 이 한 줄이다 — "이 낱말이 동사도 되나?"
            품사는 WordNet 에서 오므로 검수와 무관하게 확인된 사실이다. */}
        {card.pos_text_ko && <p className={styles.posText}>{card.pos_text_ko}</p>}
      </section>

      <section className={styles.section}>
        <div className={styles.label}>온전한 문장</div>
        {/* 예문과 해석은 가르지 않는다. 예문은 빈칸 문제가 이미 그대로 쓰고 있어
            새로 주장하는 게 없고, 해석은 바로 위 영어 문장과 나란히 놓여 학습자가
            스스로 맞춰 볼 수 있다. 해석이 깨끗해서가 아니다 — `straw` 의 해석은
            "저는 주스를 위한 스푸이를 원해요." 다. 다만 해석은 문제 카드에도 같이
            뜨는 값이라 여기만 가르면 앱이 두 말을 하게 되고, 문제 카드마다 상자를
            달면 왕초보가 문제를 읽기도 전에 경고부터 읽는다. 저 값은 배치 쪽에서
            걸러야 한다(`app/content/schemas.py` 의 해석 검사). */}
        <p className={styles.example}>{card.example}</p>
        {/* 해석은 792개에만 있다. 없으면 그냥 안 그린다. */}
        {card.example_ko && <p className={styles.exampleKo}>{card.example_ko}</p>}
      </section>

      {/* 문제를 낼 때는 답이 들어 있어서 가려 보냈던 것이다. 답을 본 뒤라
          여기서는 온전히 나오고, 오히려 여기서 봐야 문형이 배워진다. */}
      {card.pattern && (
        <section className={styles.section}>
          <div className={styles.label}>이렇게 써요</div>
          <p className={styles.pattern}>{card.pattern}</p>
        </section>
      )}

      {/* 사람이 검수한 설명. 이 자리에 오는 것만 확인된 것이다. `card.usage_note`
          를 그냥 그리지 않고 `notes.checked` 를 거치는 이유가 그거다 — 서버가
          승인 전 설명을 승인 칸에 실어 보내도 여기까지 오지 못한다. */}
      {notes.checked && (
        <section className={styles.section}>
          <div className={styles.label}>쓰임</div>
          {notes.checked.usage_note && <p className={styles.note}>{notes.checked.usage_note}</p>}
          {notes.checked.confused_with.length > 0 && (
            <p className={styles.confused}>
              헷갈리는 낱말: {notes.checked.confused_with.join(", ")}
            </p>
          )}
        </section>
      )}

      {notes.unchecked && (
        <section className={styles.unverified}>
          {/* 서버가 준 문장이다. 화면이 이 줄을 빠뜨려도 글이 스스로를 밝히도록
              서버가 상자 안에 함께 넣어 두었지만, 빠뜨리지 않는 게 먼저다. */}
          <p className={styles.unverifiedNote}>{notes.unchecked.note_ko}</p>
          {notes.unchecked.unchecked_note && (
            <p className={styles.note}>{notes.unchecked.unchecked_note}</p>
          )}
          {notes.unchecked.unchecked_confused.length > 0 && (
            <p className={styles.confused}>
              헷갈리는 낱말: {notes.unchecked.unchecked_confused.join(", ")}
            </p>
          )}
        </section>
      )}

      {card.alternatives && alts && (alts.checked.length > 0 || alts.unchecked.length > 0) && (
        <section className={styles.section}>
          {/* **이름표가 곧 근거다.** "이 자리에 올 수 있어요" 가 아니라
              "같은 장면에서 쓰는 명사예요" 까지가 우리가 아는 전부라,
              서버가 목록과 한 객체로 묶어 보낸 문장을 그대로 제목으로 쓴다.
              이름표는 두 무리에 다 걸린다 — 어느 쪽이든 같은 근거로 모은 것이고,
              확인 전인 건 낱말이 아니라 그 옆의 뜻이다. */}
          <div className={styles.label}>{card.alternatives.label_ko}</div>

          {alts.checked.length > 0 && (
            <div className={styles.alts}>
              {alts.checked.map((alt) => (
                <CheckedRow key={alt.word} alt={alt} />
              ))}
            </div>
          )}

          {alts.unchecked.length > 0 && (
            <div className={styles.inset}>
              <p className={styles.insetNote}>{UNCHECKED_WORDS_KO}</p>
              <div className={styles.alts}>
                {alts.unchecked.map((alt) => (
                  <UncheckedRow key={alt.word} alt={alt} />
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/** 사람이 확인한 후보 한 줄. 채운 바탕. */
function CheckedRow({ alt }: { alt: CheckedWord }) {
  return (
    <div className={styles.alt}>
      <span className={styles.altWord}>{alt.word}</span>
      <span className={styles.altMeaning}>{alt.meaning_ko}</span>
      <span className={styles.altPos}>{alt.pos_ko.join("·")}</span>
    </div>
  );
}

/** 아직 확인하지 않은 후보 한 줄. 점선 테두리에 빈 바탕 — 상자 밖으로 옮겨 붙여도
    승인된 줄과 안 헷갈리게 **줄 자체의 모양도** 다르게 둔다. 낱말과 품사는
    `words` 표와 WordNet 에서 오므로 확인된 것이고, 확인 전인 건 뜻뿐이다. */
function UncheckedRow({ alt }: { alt: UncheckedWord }) {
  return (
    <div className={`${styles.alt} ${styles.altUnchecked}`}>
      <span className={styles.altWord}>{alt.word}</span>
      <span className={styles.altMeaning}>{alt.unchecked_ko}</span>
      <span className={styles.altPos}>{alt.pos_ko.join("·")}</span>
    </div>
  );
}
