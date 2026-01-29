from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class ReverseGeocodeRequest(BaseModel):
    lat: float
    lng: float

@router.post("/reverse-geocode")
async def reverse_geocode(body: ReverseGeocodeRequest):
    return {"address": "TODO address", "road_address": None, "region": None}
