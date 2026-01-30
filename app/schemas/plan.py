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
    fixed_events: Optional[List[FixedEvent]] = []
