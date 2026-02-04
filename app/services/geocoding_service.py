import httpx
from app.core.config import settings
from app.core.exceptions import AppError

class GeocodingService:
    BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self):
        self.api_key = settings.GOOGLE_API_KEY

    async def reverse_geocode(self, lat: float, lng: float) -> dict:
        if not self.api_key:
            raise AppError("config_error", "GOOGLE_API_KEY not configured", 500)

        params = {"latlng": f"{lat},{lng}", "key": self.api_key, "language": "ko"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.BASE_URL, params=params)

        if resp.status_code != 200:
            raise AppError("upstream_error", "Geocoding failed", 502)

        data = resp.json()
        status = data.get("status")

        if status == "ZERO_RESULTS":
            return {"address": None, "road_address": None, "region": None}
        if status != "OK":
            msg = data.get("error_message") or "Geocoding failed"
            raise AppError("upstream_error", msg, 502)

        results = data.get("results") or []
        if not results:
            return {"address": None, "road_address": None, "region": None}

        address = results[0].get("formatted_address")
        road_address = _pick_road_address(results)
        region = _extract_region(results[0].get("address_components") or [])
        return {"address": address, "road_address": road_address, "region": region}

def _pick_road_address(results: list[dict]) -> str | None:
    for r in results:
        types = r.get("types") or []
        if "street_address" in types or "route" in types or "premise" in types:
            return r.get("formatted_address")
    return None

def _extract_region(components: list[dict]) -> str | None:
    order = [
        "administrative_area_level_1",
        "administrative_area_level_2",
        "administrative_area_level_3",
        "locality",
        "sublocality_level_1",
    ]
    values: list[str] = []
    for t in order:
        comp = next((c for c in components if t in (c.get("types") or [])), None)
        if comp:
            val = comp.get("long_name")
            if val and val not in values:
                values.append(val)
    return " ".join(values) if values else None
