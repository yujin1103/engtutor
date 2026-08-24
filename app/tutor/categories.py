"""시나리오 분류.

시나리오가 3개일 때는 목록 하나로 충분했다. 30개가 넘으면 학습자가
"뭘 골라야 하지"에서 멈춘다. 그래서 **범주를 먼저 고르고 그 안으로 들어가는**
구조로 바꾼다.

분류는 문법이 아니라 **상황**으로 나눈다. 왕초보가 앱을 여는 이유는
"현재완료를 연습하고 싶어서"가 아니라 "다음 주에 카페에서 주문해야 해서"다.

순서는 화면에 나오는 순서이자 난이도 순이기도 하다 — 카페 주문이
면접보다 먼저 온다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    emoji: str
    blurb: str


CATEGORIES: tuple[Category, ...] = (
    Category("food", "카페·식당", "☕", "주문하고, 물어보고, 계산하기"),
    Category("getting_around", "길·이동", "🚇", "길 묻기, 택시, 지하철, 공항"),
    Category("people", "사람 만나기", "👋", "인사, 소개, 가벼운 잡담"),
    Category("shopping", "쇼핑", "🛍️", "고르고, 입어보고, 바꾸기"),
    Category("stay", "숙소·여행", "🏨", "체크인, 요청, 문제 해결"),
    Category("trouble", "곤란할 때", "🆘", "아플 때, 잃어버렸을 때, 항의할 때"),
)

BY_ID: dict[str, Category] = {c.id: c for c in CATEGORIES}
ORDER: tuple[str, ...] = tuple(c.id for c in CATEGORIES)


def label_of(category_id: str) -> str:
    """화면 표시용 이름. 모르는 값이면 그대로 돌려준다(시나리오가 먼저 추가된 경우)."""
    category = BY_ID.get(category_id)
    return f"{category.emoji} {category.label}" if category else category_id


def sort_key(category_id: str) -> int:
    """정의된 순서대로. 목록에 없으면 맨 뒤."""
    return ORDER.index(category_id) if category_id in ORDER else len(ORDER)
