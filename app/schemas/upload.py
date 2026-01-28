from pydantic import BaseModel

class PlaceIn(BaseModel):
    name: str
    address: str | None = None
    category: str
    lat: float
    lng: float
