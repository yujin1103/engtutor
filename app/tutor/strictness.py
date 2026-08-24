"""교정 강도 3단계.

왕초보는 매 턴 빨간 줄을 받으면 그만둔다. 반대로 어느 정도 하는 사람은
사소한 것까지 짚어주길 원한다. 같은 앱이 둘 다 만족시키려면 강도를
사용자가 고를 수 있어야 한다.

이미 만들어둔 Correction.kind(mistake/polish) 위에 그대로 얹힌다 —
등급이 이미 나뉘어 있으니 강도는 '어느 등급까지 보여줄 것인가'의 문제가 된다.

경쟁 앱에 거의 없는 축이다. 대부분 교정 강도가 고정돼 있다.
"""

from __future__ import annotations

from typing import Literal

Strictness = Literal["gentle", "balanced", "strict"]

DEFAULT_STRICTNESS: Strictness = "balanced"

# 화면에 보여줄 순서 (느슨 -> 빡빡)
ORDER: tuple[Strictness, ...] = ("gentle", "balanced", "strict")

# UI 표시용
LABELS: dict[Strictness, str] = {
    "gentle": "유연",
    "balanced": "중간",
    "strict": "엄격",
}

CAPTIONS: dict[Strictness, str] = {
    "gentle": "말이 통하면 넘어가요. 대화에 집중하고 싶을 때.",
    "balanced": "진짜 틀린 것만 고치고, 다듬을 건 접어서 보여줘요.",
    "strict": "관사·전치사까지 꼼꼼히 짚어요. 시험 준비하듯 할 때.",
}

# 시스템 프롬프트에 삽입되는 조각.
# corrections 규칙 전체를 덮어쓰지 않고, 강도에 해당하는 부분만 좁힌다.
_PROMPTS: dict[Strictness, str] = {
    "gentle": """\
## 이번 세션의 교정 강도: 가장 느슨하게

이 학습자는 지금 **말이 끊기지 않는 것**이 가장 중요합니다.

- `corrections` 에 넣는 것은 **듣는 사람이 실제로 오해하거나 못 알아듣는 것 하나뿐**입니다.
  최대 1건. 통하기만 하면 넘어가세요.
- `kind` 는 **항상 `"mistake"`** 입니다. `"polish"` 는 이 세션에서 절대 만들지 마세요.
  더 자연스러운 표현을 아는 것보다 지금은 계속 말하는 게 중요합니다.
- 관사 하나, 복수형 s 하나, 전치사 하나가 빠진 정도는 **교정하지 않습니다.**
  `I go store yesterday` 는 시제를 고치되 관사는 건드리지 마세요.
- 의미가 통했다면 `[]` 를 돌려주고, 대신 `reply` 로 대화를 이어가세요.\
""",
    "balanced": """\
## 이번 세션의 교정 강도: 보통

- 실제로 틀렸거나 오해를 부를 것은 `"mistake"` 로, 통하지만 어색한 것은 `"polish"` 로.
- 합쳐서 **최대 2건**. 한 턴에 셋 이상 짚으면 학습자가 무엇부터 고칠지 모릅니다.
- 둘 다 있으면 `"mistake"` 를 먼저 넣으세요.
- 사소한 취향 차이는 넣지 마세요. 원어민이 그 상황에서 실제로 다르게 말할 때만 `"polish"` 입니다.\
""",
    "strict": """\
## 이번 세션의 교정 강도: 가장 꼼꼼하게

이 학습자는 정확도를 원합니다. 통하는 것에 만족하지 마세요.

- 관사(a/an/the), 전치사, 시제, 3인칭 단수 s, 복수형, 어순까지 짚습니다.
  느슨한 강도라면 넘어갔을 것도 여기서는 `"mistake"` 입니다.
- 문장이 문법적으로 완전하더라도 원어민이 그 상황에서 다르게 말한다면 `"polish"` 로 넣으세요.
- 합쳐서 **최대 2건**은 그대로입니다. 스키마 제약이고, 한 번에 더 주면 오히려 안 남습니다.
  짚을 게 많으면 **가장 자주 반복될 것부터** 고르세요.
- 그래도 `reply` 는 여전히 캐릭터를 유지하고 8단어 이내입니다. 강도는 교정에만 적용됩니다.\
""",
}


def prompt_for(strictness: Strictness) -> str:
    return _PROMPTS.get(strictness, _PROMPTS[DEFAULT_STRICTNESS])


def show_polish(strictness: Strictness) -> bool:
    """UI 가 polish 영역을 그릴지. gentle 에서는 애초에 생성되지 않지만,
    모델이 규칙을 흘려 polish 를 만들어도 화면에는 나가지 않게 이중으로 막는다."""
    return strictness != "gentle"
