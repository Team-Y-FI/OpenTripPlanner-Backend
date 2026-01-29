import os
from datetime import datetime
from typing import Optional
import exifread


def _convert_to_degrees(value) -> Optional[float]:
    """EXIF GPS 좌표를 도(degree) 단위로 변환"""
    if not value:
        return None
    
    try:
        # exifread는 GPS 좌표를 [degrees, minutes, seconds] 형태의 리스트로 반환
        d = float(value.values[0].num) / float(value.values[0].den)
        m = float(value.values[1].num) / float(value.values[1].den)
        s = float(value.values[2].num) / float(value.values[2].den)
        
        return d + (m / 60.0) + (s / 3600.0)
    except (AttributeError, IndexError, ZeroDivisionError, ValueError):
        return None


def _parse_datetime(date_str: str, time_str: Optional[str] = None) -> Optional[datetime]:
    """EXIF 날짜/시간 문자열을 datetime 객체로 변환"""
    try:
        if time_str:
            dt_str = f"{date_str} {time_str}"
            return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
        else:
            return datetime.strptime(date_str, "%Y:%m:%d")
    except (ValueError, AttributeError):
        return None


def extract_exif_lat_lng_taken_at(file_path: str) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """
    이미지 파일에서 EXIF 위치 정보(GPS 위도/경도)와 촬영 시간을 추출
    
    Args:
        file_path: 이미지 파일의 절대 경로
        
    Returns:
        (위도, 경도, 촬영시간) 튜플. 정보가 없으면 None 반환
    """
    if not os.path.exists(file_path):
        return None, None, None
    
    try:
        with open(file_path, 'rb') as f:
            tags = exifread.process_file(f, details=False)
        
        # GPS 위도 추출
        lat = None
        lat_ref = tags.get('GPS GPSLatitudeRef')
        lat_val = tags.get('GPS GPSLatitude')
        if lat_val and lat_ref:
            lat = _convert_to_degrees(lat_val)
            if lat is not None and str(lat_ref) == 'S':
                lat = -lat
        
        # GPS 경도 추출
        lng = None
        lng_ref = tags.get('GPS GPSLongitudeRef')
        lng_val = tags.get('GPS GPSLongitude')
        if lng_val and lng_ref:
            lng = _convert_to_degrees(lng_val)
            if lng is not None and str(lng_ref) == 'W':
                lng = -lng
        
        # 촬영 시간 추출 (DateTimeOriginal 우선, 없으면 DateTime 사용)
        taken_at = None
        date_time_original = tags.get('EXIF DateTimeOriginal')
        if date_time_original:
            dt_str = str(date_time_original)
            if ' ' in dt_str:
                date_part, time_part = dt_str.split(' ', 1)
                taken_at = _parse_datetime(date_part, time_part)
            else:
                taken_at = _parse_datetime(dt_str)
        
        if not taken_at:
            date_time = tags.get('Image DateTime')
            if date_time:
                dt_str = str(date_time)
                if ' ' in dt_str:
                    date_part, time_part = dt_str.split(' ', 1)
                    taken_at = _parse_datetime(date_part, time_part)
                else:
                    taken_at = _parse_datetime(dt_str)
        
        return lat, lng, taken_at
        
    except Exception as e:
        # 파일 읽기 오류, EXIF 파싱 오류 등 모든 예외 처리
        print(f"EXIF 추출 실패 ({file_path}): {e}")
        return None, None, None
