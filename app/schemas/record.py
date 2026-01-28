from pydantic import BaseModel
from datetime import datetime
from app.schemas.upload import PlaceIn

class SpotCreateItem(BaseModel):
    photo_id: str
    visited_at: datetime
    place: PlaceIn

class SpotCreateRequest(BaseModel):
    upload_id: str
    spots: list[SpotCreateItem]

class SpotUpdateRequest(BaseModel):
    memo: str | None = None
    tags: list[str] | None = None
