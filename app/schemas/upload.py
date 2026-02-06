from pydantic import BaseModel

class PlaceIn(BaseModel):
    name: str
    address: str | None = None
    category: str | None = None
    lat: float | None = None
    lng: float | None = None
