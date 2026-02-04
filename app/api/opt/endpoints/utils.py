from fastapi import APIRouter
from pydantic import BaseModel
from app.services.geocoding_service import GeocodingService

router = APIRouter()

class ReverseGeocodeRequest(BaseModel):
    lat: float
    lng: float

@router.post("/reverse-geocode")
async def reverse_geocode(body: ReverseGeocodeRequest):
    svc = GeocodingService()
    return await svc.reverse_geocode(body.lat, body.lng)
