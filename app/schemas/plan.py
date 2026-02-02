from pydantic import BaseModel
from typing import List, Optional

class FixedEvent(BaseModel):
    date: str
    title: str
    start: str
    end: str

class PlanGenerateRequest(BaseModel):
    region: str
    start_date: str
    end_date: str
    first_day_start_time: str
    last_day_end_time: str
    # 'transport' (대중교통+도보) 또는 'car' (승용차) 중 선택
    transport_mode: str = "transport" 
    fixed_events: Optional[List[FixedEvent]] = []