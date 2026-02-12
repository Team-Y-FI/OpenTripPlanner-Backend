from fastapi import APIRouter
from pydantic import BaseModel
from app.services.geocoding_service import GeocodingService

router = APIRouter()

class ReverseGeocodeRequest(BaseModel):
    lat: float
    lng: float

class GeocodeRequest(BaseModel):
    query: str

@router.post("/reverse-geocode")
async def reverse_geocode(body: ReverseGeocodeRequest):
    svc = GeocodingService()
    return await svc.reverse_geocode(body.lat, body.lng)

@router.post("/geocode")
async def geocode(body: GeocodeRequest):
    svc = GeocodingService()
    return await svc.geocode(body.query)
