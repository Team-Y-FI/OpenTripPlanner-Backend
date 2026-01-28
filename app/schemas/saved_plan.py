from pydantic import BaseModel

class SavePlanRequest(BaseModel):
    plan_id: str
    title: str | None = None
