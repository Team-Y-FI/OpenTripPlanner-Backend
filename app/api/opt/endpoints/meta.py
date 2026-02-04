from fastapi import APIRouter
from app.schemas.meta import MetaOptions
from app.core.enums import PURPOSES, CATEGORIES, TRANSPORTS, CROWD_MODES, PLACE_CATEGORIES

router = APIRouter()

@router.get("/options", response_model=MetaOptions)
async def options():
    return {
        "purposes": [{"value": o.value, "label": o.label} for o in PURPOSES],
        "categories": [{"value": o.value, "label": o.label} for o in CATEGORIES],
        "place_categories": [{"value": o.value, "label": o.label} for o in PLACE_CATEGORIES],
        "transport": [{"value": o.value, "label": o.label, "default": o.default} for o in TRANSPORTS],
        "crowd_mode": [{"value": o.value, "label": o.label, "default": o.default} for o in CROWD_MODES],
    }
