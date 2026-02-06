import pandas as pd
import geopandas as gpd
from r5py import TransportNetwork, TravelTimeMatrix, TransportMode
from datetime import datetime
import pickle
import os

# =========================================================
# 설정 (route_service.py와 경로가 같아야 함)
# =========================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")

PLACE_FILE = os.path.join(DATA_DIR, "place_전체_통합_진짜최종.xlsx")
OSM_FILE = os.path.join(DATA_DIR, "seoul_osm_v.pbf")
GTFS_FILES = [os.path.join(DATA_DIR, "seoul_area_gtfs.zip")]
OUTPUT_PKL = os.path.join(DATA_DIR, "seoul_travel_time_matrix.pkl")
TN_CACHE_PATH = "./data/seoul_tn_cached.pkl"

def build_full_matrix():
    print("1. 데이터 로딩 중...")
    if not os.path.exists(PLACE_FILE):
        print(f"오류: 엑셀 파일이 없습니다 ({PLACE_FILE})")
        return

    df = pd.read_excel(PLACE_FILE)
    
    # [중요] ID 부여 (route_service.py와 동일한 기준이어야 함 -> 인덱스 사용)
    df['id'] = df.index 
    
    # 좌표 없는 데이터 제거
    df = df.dropna(subset=['lat', 'lng'])
    
    # GeoDataFrame 변환 (R5PY 입력용)
    gdf = gpd.GeoDataFrame(
        df, 
        geometry=gpd.points_from_xy(df.lng, df.lat),
        crs="EPSG:4326"
    )
    
    print(f"총 {len(gdf)}개 장소 로드됨. 예상 경로 수: {len(gdf)**2}개")

    # 2. R5PY 엔진 초기화
    print("2. 교통 네트워크(R5PY) 초기화 중...")
    if os.path.exists(TN_CACHE_PATH):
        tn = TransportNetwork.__new__(TransportNetwork)
        tn._transport_network = TransportNetwork._load_pickled_transport_network(tn, TN_CACHE_PATH)
        transport_network = tn
        print("교통 네트워크 캐시 로드")
    
    # 기준 시간 설정 (교통이 적당히 막히는 평일 오후 2시 추천)
    base_time = datetime(2026, 5, 20, 14, 0, 0) 
    
    # ---------------------------------------------------------
    # (A) 대중교통 매트릭스 (Transit + Walk)
    # ---------------------------------------------------------
    print("🚀 [1/3] 대중교통(Transit) 매트릭스 계산 중... (시간 소요됨)")
    matrix_transit = TravelTimeMatrix(
        tn, 
        origins=gdf, 
        destinations=gdf, 
        departure=base_time, 
        transport_modes=[TransportMode.WALK, TransportMode.TRANSIT]
    )
    
    # DataFrame -> Dictionary 변환 (검색 속도 최적화: O(1))
    transit_dict = {}
    for row in matrix_transit.itertuples():
        if not pd.isna(row.travel_time):
            transit_dict[(int(row.from_id), int(row.to_id))] = int(row.travel_time)
    print(f"   -> 대중교통 경로 {len(transit_dict)}개 저장 완료")

    # ---------------------------------------------------------
    # (B) 자동차 매트릭스 (Car)
    # ---------------------------------------------------------
    print("🚗 [2/3] 자동차(Car) 매트릭스 계산 중...")
    matrix_car = TravelTimeMatrix(
        tn, 
        origins=gdf, 
        destinations=gdf, 
        departure=base_time, 
        transport_modes=[TransportMode.CAR]
    )
    
    car_dict = {}
    for row in matrix_car.itertuples():
        if not pd.isna(row.travel_time):
            # 순수 주행 시간만 저장 (주차 시간은 서비스 로직에서 추가)
            car_dict[(int(row.from_id), int(row.to_id))] = int(row.travel_time)
    print(f"   -> 자동차 경로 {len(car_dict)}개 저장 완료")

    # ---------------------------------------------------------
    # (C) 도보 매트릭스 (Walk)
    # ---------------------------------------------------------
    print("🚶 [3/3] 도보(Walk) 매트릭스 계산 중...")
    matrix_walk = TravelTimeMatrix(
        tn, 
        origins=gdf, 
        destinations=gdf, 
        departure=base_time, 
        transport_modes=[TransportMode.WALK]
    )
    
    walk_dict = {}
    for row in matrix_walk.itertuples():
        if not pd.isna(row.travel_time):
            walk_dict[(int(row.from_id), int(row.to_id))] = int(row.travel_time)
    print(f"   -> 도보 경로 {len(walk_dict)}개 저장 완료")

    # 4. 파일 저장
    print("💾 파일 저장 중...")
    final_data = {
        "transit": transit_dict,
        "car": car_dict,
        "walk": walk_dict,
        # ID 매핑 정보도 같이 저장해두면 나중에 검증할 때 좋습니다
        "places_map": df.set_index('id')[['name', 'lat', 'lng']].to_dict('index') 
    }
    
    with open(OUTPUT_PKL, "wb") as f:
        pickle.dump(final_data, f)
        
    print(f"✅ 모든 작업 완료! 파일 생성됨: {OUTPUT_PKL}")

if __name__ == "__main__":
    # 이 스크립트를 직접 실행할 때만 작동
    build_full_matrix()