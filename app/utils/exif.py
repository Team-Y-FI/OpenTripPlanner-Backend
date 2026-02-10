import os
from datetime import datetime
from io import BytesIO
from typing import Optional

import exifread


def _convert_to_degrees(value) -> Optional[float]:
    """Convert EXIF GPS coordinates to decimal degrees."""
    if not value:
        return None

    try:
        d = float(value.values[0].num) / float(value.values[0].den)
        m = float(value.values[1].num) / float(value.values[1].den)
        s = float(value.values[2].num) / float(value.values[2].den)
        return d + (m / 60.0) + (s / 3600.0)
    except (AttributeError, IndexError, ZeroDivisionError, ValueError):
        return None


def _parse_datetime(date_str: str, time_str: Optional[str] = None) -> Optional[datetime]:
    """Convert EXIF date/time strings to a datetime object."""
    try:
        if time_str:
            dt_str = f"{date_str} {time_str}"
            return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
        return datetime.strptime(date_str, "%Y:%m:%d")
    except (ValueError, AttributeError):
        return None


def _extract_exif_from_tags(tags) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    lat = None
    lat_ref = tags.get("GPS GPSLatitudeRef")
    lat_val = tags.get("GPS GPSLatitude")
    if lat_val and lat_ref:
        lat = _convert_to_degrees(lat_val)
        if lat is not None and str(lat_ref) == "S":
            lat = -lat

    lng = None
    lng_ref = tags.get("GPS GPSLongitudeRef")
    lng_val = tags.get("GPS GPSLongitude")
    if lng_val and lng_ref:
        lng = _convert_to_degrees(lng_val)
        if lng is not None and str(lng_ref) == "W":
            lng = -lng

    taken_at = None
    date_time_original = tags.get("EXIF DateTimeOriginal")
    if date_time_original:
        dt_str = str(date_time_original)
        if " " in dt_str:
            date_part, time_part = dt_str.split(" ", 1)
            taken_at = _parse_datetime(date_part, time_part)
        else:
            taken_at = _parse_datetime(dt_str)

    if not taken_at:
        date_time = tags.get("Image DateTime")
        if date_time:
            dt_str = str(date_time)
            if " " in dt_str:
                date_part, time_part = dt_str.split(" ", 1)
                taken_at = _parse_datetime(date_part, time_part)
            else:
                taken_at = _parse_datetime(dt_str)

    return lat, lng, taken_at


def _extract_exif_from_stream(stream, source: str) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    try:
        tags = exifread.process_file(stream, details=False)
        return _extract_exif_from_tags(tags)
    except Exception as e:
        print(f"EXIF extraction failed ({source}): {e}")
        return None, None, None


def extract_exif_lat_lng_taken_at(file_path: str) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """Extract GPS lat/lng and taken_at from an image file path."""
    if not os.path.exists(file_path):
        return None, None, None
    with open(file_path, "rb") as f:
        return _extract_exif_from_stream(f, file_path)


def extract_exif_lat_lng_taken_at_bytes(content: bytes) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """Extract GPS lat/lng and taken_at directly from in-memory image bytes."""
    if not content:
        return None, None, None
    return _extract_exif_from_stream(BytesIO(content), "bytes")
