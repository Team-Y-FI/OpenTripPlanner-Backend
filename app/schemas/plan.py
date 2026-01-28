from pydantic import BaseModel, Field

class PlanGenerateRequest(BaseModel):
    region: str
    purposes: list[str] = Field(default_factory=list)
    date: str
    start_time: str
    duration_hours: float
    transport: str
    categories: list[str] = Field(default_factory=list)
    crowd_mode: str
    seed_spot_ids: list[str] | None = None
