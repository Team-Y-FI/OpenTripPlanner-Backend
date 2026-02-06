from pydantic import BaseModel, field_validator
from typing import List, Optional

class FixedEvent(BaseModel):
    date: str
    title: str
    start_time: str
    end_time: str
    place_name: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

class PlanGenerateRequest(BaseModel):
    region: str
    start_date: str
    end_date: str
    first_day_start_time: str
    last_day_end_time: str
    categories: List[str] = []
    # 'transport' (대중교통+도보) 또는 'car' (승용차) 중 선택
    transport_mode: str = "transport"
    fixed_events: Optional[List[FixedEvent]] = []

    @field_validator('transport_mode', mode='before')
    @classmethod
    def normalize_transport_mode(cls, v):
        # 프론트엔드에서 'walkAndPublic' -> 백엔드 'transport'로 변환
        if v == 'walkAndPublic':
            return 'transport'
        return v

class ReplaceSpotsRequest(BaseModel):
    plan_id: str
    day: str  # "day1", "day2" 등
    spot_names: List[str]  # 대체하고 싶은 장소 이름들
    region: Optional[str] = None  # 지역 (선택적, plan에서 가져올 수도 있음)
    categories: Optional[List[str]] = None  # 원하는 카테고리 (선택적)

class AlternativeSpot(BaseModel):
    name: str
    category: str
    category2: Optional[str] = None
    lat: float
    lng: float
    reason: Optional[str] = None  # 추천 이유

class ReplaceSpotsResponse(BaseModel):
    alternatives: List[AlternativeSpot]