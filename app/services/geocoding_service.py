import httpx
from app.core.config import settings
from app.core.exceptions import AppError

class GeocodingService:
    GOOGLE_BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    KAKAO_BASE_URL = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
    KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
    KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

    def __init__(self):
        self.google_api_key = settings.GOOGLE_API_KEY
        self.kakao_rest_api_key = getattr(settings, "KAKAO_REST_API_KEY", None)

    async def reverse_geocode(self, lat: float, lng: float) -> dict:
        """
        좌표 → 주소

        - 카카오 REST API 키가 설정된 경우: 카카오 우선 사용
        - 그렇지 않고 Google API 키가 있는 경우: Google 사용
        - 둘 다 없으면 설정 오류를 명확히 반환

        ※ 이전 버전처럼 예외를 삼키고 빈 주소를 돌려주면
        프론트에서는 "주소를 찾을 수 없습니다"만 보이고,
        실제 어떤 오류인지 알 수 없어서 디버깅이 어려움.
        """
        if self.kakao_rest_api_key:
            return await self._reverse_geocode_kakao(lat, lng)

        if self.google_api_key:
            return await self._reverse_geocode_google(lat, lng)

        # 설정이 전혀 안 되어 있는 경우는 명확한 에러로 반환
        raise AppError("config_error", "Geocoding API key not configured", 500)

    async def _reverse_geocode_kakao(self, lat: float, lng: float) -> dict:
        """카카오 역지오코딩 API 사용"""
        params = {"x": str(lng), "y": str(lat)}
        headers = {"Authorization": f"KakaoAK {self.kakao_rest_api_key}"}
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.KAKAO_BASE_URL, params=params, headers=headers)
        
        if resp.status_code != 200:
            raise AppError("upstream_error", "Kakao geocoding failed", 502)
        
        data = resp.json()
        documents = data.get("documents") or []
        
        if not documents:
            return {"address": None, "road_address": None, "region": None}
        
        doc = documents[0]
        road_address = doc.get("road_address")
        address = doc.get("address")
        
        # 도로명 주소 우선, 없으면 지번 주소
        road_addr_str = None
        if road_address:
            road_addr_str = road_address.get("address_name")
        
        addr_str = None
        if address:
            addr_str = address.get("address_name")
        
        # 지역 정보 추출
        region = None
        if address:
            region_parts = []
            if address.get("region_1depth_name"):
                region_parts.append(address.get("region_1depth_name"))
            if address.get("region_2depth_name"):
                region_parts.append(address.get("region_2depth_name"))
            if address.get("region_3depth_name"):
                region_parts.append(address.get("region_3depth_name"))
            region = " ".join(region_parts) if region_parts else None
        
        return {
            "address": addr_str,
            "road_address": road_addr_str,
            "region": region
        }

    async def _reverse_geocode_google(self, lat: float, lng: float) -> dict:
        """Google Maps 역지오코딩 API 사용"""
        params = {"latlng": f"{lat},{lng}", "key": self.google_api_key, "language": "ko"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.GOOGLE_BASE_URL, params=params)

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

    async def geocode(self, query: str) -> dict:
        q = (query or "").strip()
        if not q:
            raise AppError("bad_request", "주소 또는 장소명을 입력해주세요.", 400)

        if self.kakao_rest_api_key:
            result = await self._geocode_kakao(q)
            if result.get("lat") is not None and result.get("lng") is not None:
                return result
            if self.google_api_key:
                return await self._geocode_google(q)
            return result

        if self.google_api_key:
            return await self._geocode_google(q)

        raise AppError("config_error", "Geocoding API key not configured", 500)

    async def _geocode_kakao(self, query: str) -> dict:
        result = await self._geocode_kakao_address(query)
        if result.get("lat") is not None and result.get("lng") is not None:
            return result
        return await self._geocode_kakao_keyword(query)

    async def _geocode_kakao_address(self, query: str) -> dict:
        params = {"query": query}
        headers = {"Authorization": f"KakaoAK {self.kakao_rest_api_key}"}

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.KAKAO_ADDRESS_URL, params=params, headers=headers)

        if resp.status_code != 200:
            raise AppError("upstream_error", "Kakao geocoding failed", 502)

        data = resp.json()
        documents = data.get("documents") or []
        if not documents:
            return {"lat": None, "lng": None, "address": None, "road_address": None, "region": None}

        doc = documents[0] or {}
        address = doc.get("address") or {}
        road_address = doc.get("road_address") or {}

        lat = float(doc.get("y")) if doc.get("y") else None
        lng = float(doc.get("x")) if doc.get("x") else None

        addr_str = address.get("address_name")
        road_addr_str = road_address.get("address_name")
        region = _extract_region_kakao(address)

        return {
            "lat": lat,
            "lng": lng,
            "address": addr_str,
            "road_address": road_addr_str,
            "region": region,
        }

    async def _geocode_kakao_keyword(self, query: str) -> dict:
        params = {"query": query}
        headers = {"Authorization": f"KakaoAK {self.kakao_rest_api_key}"}

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.KAKAO_KEYWORD_URL, params=params, headers=headers)

        if resp.status_code != 200:
            raise AppError("upstream_error", "Kakao keyword geocoding failed", 502)

        data = resp.json()
        documents = data.get("documents") or []
        if not documents:
            return {"lat": None, "lng": None, "address": None, "road_address": None, "region": None}

        doc = documents[0] or {}
        lat = float(doc.get("y")) if doc.get("y") else None
        lng = float(doc.get("x")) if doc.get("x") else None

        addr_str = doc.get("address_name")
        road_addr_str = doc.get("road_address_name")

        return {
            "lat": lat,
            "lng": lng,
            "address": addr_str,
            "road_address": road_addr_str,
            "region": None,
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.GOOGLE_BASE_URL, params=params)

        if resp.status_code != 200:
            raise AppError("upstream_error", "Geocoding failed", 502)

        data = resp.json()
        status = data.get("status")

        if status == "ZERO_RESULTS":
            return {"lat": None, "lng": None, "address": None, "road_address": None, "region": None}
        if status != "OK":
            msg = data.get("error_message") or "Geocoding failed"
            raise AppError("upstream_error", msg, 502)

        results = data.get("results") or []
        if not results:
            return {"lat": None, "lng": None, "address": None, "road_address": None, "region": None}

        loc = (results[0].get("geometry") or {}).get("location") or {}
        lat = loc.get("lat")
        lng = loc.get("lng")
        address = results[0].get("formatted_address")
        road_address = _pick_road_address(results)
        region = _extract_region(results[0].get("address_components") or [])
        return {"lat": lat, "lng": lng, "address": address, "road_address": road_address, "region": region}

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

def _extract_region_kakao(address: dict | None) -> str | None:
    if not address:
        return None
    parts: list[str] = []
    for key in ["region_1depth_name", "region_2depth_name", "region_3depth_name"]:
        val = address.get(key)
        if val and val not in parts:
            parts.append(val)
    return " ".join(parts) if parts else None
