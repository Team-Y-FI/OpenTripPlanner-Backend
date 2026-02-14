from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Option:
    value: str
    label: str
    default: bool = False

# Enums (API spec v0.1 / 2026-01-25)
TRANSPORTS = [
    Option("walk", "걷기", True),
    Option("bike", "자전거"),
    Option("car", "자동차"),
    Option("transit", "대중교통"),
]

CROWD_MODES = [
    Option("avoid", "붐비는곳피하기", True),
    Option("normal", "기본"),
    Option("popular", "인기장소위주"),
]

PURPOSES = [
    Option("date", "데이트"),
    Option("solo", "혼자시간"),
    Option("friends", "친구들과"),
    Option("family", "가족나들이"),
    Option("photo", "사진찍기"),
    Option("food_focus", "맛집위주"),
]

CATEGORIES = [
    Option("cafe", "카페"),
    Option("food", "맛집"),
    Option("exhibition", "전시/미술관"),
    Option("park", "공원/산책"),
    Option("nightview", "야경/전망"),
    Option("shopping", "쇼핑"),
]

# Manual place categories (Upload 수동입력)
PLACE_CATEGORIES = [
    Option("cafe", "카페"),
    Option("attraction", "관광지"),
    Option("culture", "문화시설"),
    Option("shopping", "쇼핑"),
    Option("restaurant", "음식점"),
]

def normalize_by_label_or_value(raw: str, options: list[Option]) -> str:
    """Accept either 'value' or 'label' and return the canonical 'value' (or label if values are Korean)."""
    if raw is None:
        return raw
    for opt in options:
        if raw == opt.value or raw == opt.label:
            return opt.value
    return raw
