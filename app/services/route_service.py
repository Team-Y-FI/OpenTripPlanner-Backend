import os
import pickle
import re
import random
import time
import math
import json
import zipfile
import joblib
import copy
import multiprocessing
from datetime import datetime, timedelta

import pandas as pd
import geopandas as gpd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from google import genai

# R5PY & App Config
from r5py import TransportNetwork, TravelTimeMatrix, DetailedItineraries, TransportMode
from app.schemas.plan import PlanGenerateRequest
from app.core.config import settings

# =================================================
# 1. 환경 설정 및 상수 정의
# =================================================

# [시스템 리소스 설정]
available_cores = multiprocessing.cpu_count() * 0.8
TARGET_THREADS = int(available_cores)
os.environ["R5PY_NUM_THREADS"] = str(TARGET_THREADS)

# [Java JVM 설정] R5PY 구동용
# os.environ["JAVA_HOME"] = r"C:\Program Files\Java\jdk-21.0.10"
# os.environ["JAVA_OPTS"] = (
#     f"-Xmx12G "
#     f"-XX:+UseG1GC "
#     f"-Djava.util.concurrent.ForkJoinPool.common.parallelism={TARGET_THREADS}"
# )

# [파일 경로 설정]
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "model")

# 모델 및 데이터 파일
TRAFFIC_MODEL_FILE = os.path.join(MODEL_DIR, "traffic_congestion_model_latlon.pkl")  # 교통량 모델
POPULATION_MODEL_FILE = os.path.join(MODEL_DIR, "congestion_model_latlon.pkl")       # 인구 혼잡도 모델
PLACE_FILE = os.path.join(DATA_DIR, "place_전체_통합_진짜최종.xlsx")                   # 백업용 엑셀 데이터
OSM_FILE = os.path.join(DATA_DIR, "seoul_osm_v.pbf")                                 # 지도 데이터
GTFS_FILES = [os.path.join(DATA_DIR, "seoul_area_gtfs.zip")]                         # 대중교통 데이터
TN_CACHE_PATH = os.path.join(DATA_DIR, "seoul_tn_cached.pkl")                        # 네트워크 캐시
META_CACHE_PATH = os.path.join(DATA_DIR, "metadata_cache_v2.pkl")                    # 메타데이터 캐시
RESULT_JSON_PATH = "result.json"
RESULT_FINAL_PATH = "result_timeline.json"

# [날짜 및 휴일 데이터]
KOREAN_HOLIDAYS_2026 = [
    '20260101', '20260216', '20260217', '20260218', '20260301', '20260302',
    '20260505', '20260524', '20260525', '20260606', '20260608', '20260815',
    '20260817', '20260924', '20260925', '20260926', '20261003', '20261005',
    '20261009', '20261225'
]

# [서울시 구별 중심 좌표]
SEOUL_GU_COORDS = {
    "강남구": {"lat": 37.514575, "lng": 127.0495556}, "강동구": {"lat": 37.52736667, "lng": 127.1258639},
    "강북구": {"lat": 37.63695556, "lng": 127.0277194}, "강서구": {"lat": 37.54815556, "lng": 126.851675},
    "관악구": {"lat": 37.47538611, "lng": 126.9538444}, "광진구": {"lat": 37.53573889, "lng": 127.0845333},
    "구로구": {"lat": 37.49265, "lng": 126.8895972}, "금천구": {"lat": 37.44910833, "lng": 126.9041972},
    "노원구": {"lat": 37.65146111, "lng": 127.0583889}, "도봉구": {"lat": 37.66583333, "lng": 127.0495222},
    "동대문구": {"lat": 37.571625, "lng": 127.0421417}, "동작구": {"lat": 37.50965556, "lng": 126.941575},
    "마포구": {"lat": 37.56070556, "lng": 126.9105306}, "서대문구": {"lat": 37.57636667, "lng": 126.9388972},
    "서초구": {"lat": 37.48078611, "lng": 127.0348111}, "성동구": {"lat": 37.56061111, "lng": 127.039},
    "성북구": {"lat": 37.58638333, "lng": 127.0203333}, "송파구": {"lat": 37.51175556, "lng": 127.1079306},
    "양천구": {"lat": 37.51423056, "lng": 126.8687083}, "영등포구": {"lat": 37.52361111, "lng": 126.8983417},
    "용산구": {"lat": 37.53609444, "lng": 126.9675222}, "은평구": {"lat": 37.59996944, "lng": 126.9312417},
    "종로구": {"lat": 37.57037778, "lng": 126.9816417}, "중구": {"lat": 37.56100278, "lng": 126.9996417},
    "중랑구": {"lat": 37.60380556, "lng": 127.0947778}
}

# [제약 조건 및 파라미터]
FALLBACK_MOVE_MIN = 30         # 경로 탐색 실패 시 기본 이동 시간
MAX_TRANSFERS = 2              # 최대 환승 횟수
MAX_TRAVEL_TIME_MIN = 60       # 최대 허용 이동 시간
LUNCH_WINDOW = ("12:00", "13:30")
DINNER_WINDOW = ("18:00", "19:30")

# 장소별 기본 체류 시간 (분)
stay_time_map = {
    "관광지": 90, "카페": 50, "음식점": 60,
    "박물관": 120, "공원": 60, "시장": 80, "숙박": 0
}


# =================================================
# 2. 경로 최적화 알고리즘 (DFS Solver)
# =================================================
class SimpleRouteSolver:
    """DFS 기반의 타임라인 및 경로 최적화 솔버"""

    def __init__(self, nodes, time_matrix, windows, start_min, max_horizon):
        self.nodes = nodes
        self.matrix = time_matrix
        self.windows = windows
        self.start_min = start_min
        self.max_horizon = max_horizon
        self.n = len(nodes)

        # 최적 결과 저장
        self.best_path = []
        self.best_cost = float('inf')
        self.best_score = -1
        self.best_arrival_times = {}
        
        # 우선순위 비교 변수
        self.best_fixed_cnt = -1
        self.best_selected_cnt = -1

    def solve(self):
        """최적 경로 탐색 실행"""
        # 초기화
        self.best_path = []
        self.best_cost = float('inf')
        self.best_score = -1
        self.best_arrival_times = {}
        self.best_fixed_cnt = -1
        self.best_selected_cnt = -1

        # 모든 노드를 시작점으로 시도
        for i in range(self.n):
            node = self.nodes[i]
            
            # 시작 불가능한 타입 제외
            if node["type"] not in ["spot", "selected", "fixed", "lunch", "dinner"]:
                continue
            
            win_start, win_end = self.windows[i]
            if self.start_min > win_end:
                continue
                
            visited = [False] * self.n
            visited[i] = True

            actual_start = max(self.start_min, win_start)
            departure_time = actual_start + node.get("stay", 0)

            if node.get("is_selected"):
                initial_score = 8000 # Selected 점수
            else:
                score_map = {"fixed": 15000, "selected": 8000, "lunch": 5000, "dinner": 5000}
                initial_score = score_map.get(node["type"], 1000)

            is_start_lunch = (node["type"] == "lunch")
            is_start_dinner = (node["type"] == "dinner")
            
            # 초기 카운트 설정
            initial_fixed_cnt = 1 if node["type"] == "fixed" else 0
            initial_selected_cnt = 1 if (node["type"] == "selected" or node.get("is_selected")) else 0

            self._dfs(
                curr_idx=i, 
                curr_time=departure_time, 
                visited=visited, 
                path=[i], 
                total_cost=0, 
                current_score=initial_score, 
                arrival_times={i: actual_start}, 
                has_lunch=is_start_lunch, 
                has_dinner=is_start_dinner,
                fixed_cnt=initial_fixed_cnt,
                selected_cnt=initial_selected_cnt
            )
        
        return self.best_path, self.best_arrival_times

    def _dfs(self, curr_idx, curr_time, visited, path, total_cost, current_score, arrival_times, has_lunch, has_dinner, fixed_cnt, selected_cnt):
        # [최적 경로 갱신 로직] 우선순위: 고정 > 선택 > 점수 > 비용
        update_best = False
        
        if fixed_cnt > self.best_fixed_cnt:
            update_best = True
        elif fixed_cnt == self.best_fixed_cnt:
            if selected_cnt > self.best_selected_cnt:
                update_best = True
            elif selected_cnt == self.best_selected_cnt:
                if current_score > self.best_score:
                    update_best = True
                elif current_score == self.best_score:
                    if total_cost < self.best_cost:
                        update_best = True

        if update_best:
            self.best_fixed_cnt = fixed_cnt
            self.best_selected_cnt = selected_cnt
            self.best_score = current_score
            self.best_cost = total_cost
            self.best_path = list(path)
            self.best_arrival_times = arrival_times.copy()

        # 다음 노드 탐색
        for next_idx in range(self.n):
            if not visited[next_idx]:
                node = self.nodes[next_idx]
                node_type = node["type"]
                travel_time = self.matrix[curr_idx][next_idx]
                arrival = curr_time + travel_time
                win_start, win_end = self.windows[next_idx]

                # 필터링 로직
                if node_type == "lunch" and has_lunch: continue
                if node_type == "dinner" and has_dinner: continue
                if any(self.nodes[p_idx]["name"] == node["name"] for p_idx in path): continue

                # 고정 일정 / 일반 일정 시간 체크
                if node_type == "fixed":
                    if arrival > win_end: continue
                    start_activity = win_start
                    wait_time = win_start - arrival if arrival < win_start else 0
                    actual_wait_for_penalty = 0
                else:
                    if arrival > win_end: continue
                    wait_time = win_start - arrival if arrival < win_start else 0
                    actual_wait_for_penalty = 0 if len(path) == 1 else wait_time

                    if len(path) == 1 and arrival < win_start and node_type not in ["lunch", "dinner"]:
                        start_activity = win_start
                    else:
                        start_activity = arrival + wait_time

                    # 다른 고정 일정과 겹치는지 확인
                    stay_duration = self.nodes[next_idx]["stay"]
                    temp_leave_time = start_activity + stay_duration
                    overlap = False
                    for f_idx in range(self.n):
                        if f_idx == next_idx: continue 
                        if self.nodes[f_idx]["type"] == "fixed":
                            f_start = self.windows[f_idx][0]
                            f_end_act = f_start + self.nodes[f_idx]["stay"]
                            if not (temp_leave_time + 20 <= f_start or start_activity >= f_end_act):
                                overlap = True
                                break
                    if overlap: continue

                leave_time = start_activity + self.nodes[next_idx]["stay"]
                if leave_time > self.max_horizon: continue

                # 비용 및 점수 계산
                penalty_cost = 0
                if node.get("is_selected"):
                    node_score = 8000
                else:
                    node_score = {"fixed": 15000, "selected": 8000, "lunch": 5000, "dinner": 5000}.get(node_type, 1000)

                if len(path) > 1 and wait_time > 30: penalty_cost += (wait_time - 30) * 10
                penalty_cost += travel_time * 2
                if travel_time > 40: penalty_cost += (travel_time - 40) * 10

                visited[next_idx] = True
                path.append(next_idx)
                arrival_times[next_idx] = arrival
                
                # 재귀 호출 시 카운트 증가
                next_fixed = fixed_cnt + (1 if node_type == "fixed" else 0)
                # 타입이 lunch/dinner라도 is_selected가 True면 selected_cnt 증가
                is_sel = 1 if (node_type == "selected" or node.get("is_selected")) else 0
                next_selected = selected_cnt + is_sel
                
                self._dfs(
                    next_idx, leave_time, visited, path,
                    total_cost + travel_time + actual_wait_for_penalty + penalty_cost,
                    current_score + node_score - penalty_cost,
                    arrival_times, 
                    has_lunch or (node_type == "lunch"), 
                    has_dinner or (node_type == "dinner"),
                    next_fixed,
                    next_selected
                )
                
                # 백트래킹
                path.pop()
                visited[next_idx] = False
                del arrival_times[next_idx]


# =================================================
# 3. RouteOptimizerService (메인 서비스)
# =================================================
class RouteOptimizerService:
    """서울 여행 경로 최적화 및 타임라인 생성 서비스"""
    
    def __init__(self):
        self.is_initialized = False
        self.traffic_model = None    # 이동/교통대기 모델
        self.population_model = None # 체류/입장대기 모델
        self.transport_network = None
        self.df_places = None
        
        # 메타데이터 캐시
        self.stop_coords = {}
        self.stop_id_to_name = {}
        self.route_id_to_name = {}
        self.stop_route_map = {}
        self.place_id_to_name = {}
        
        self.detailed_path_cache = {}
        self.nearest_stop_map = {}
        self.api_key = None
        self.init_duration = 0
    
    
    # ========== 3-1. 초기화 및 리소스 로드 ==========
    def initialize_resources(self):
        if self.is_initialized: return
        start_t = time.time()
        print("리소스 초기화 시작...")
        print(f"루트 경로 : {BASE_DIR}")
        
        load_dotenv()
        self.api_key = os.getenv("API_KEY_P")

        # 모델 로드 (Traffic / Population)
        try:
            if os.path.exists(TRAFFIC_MODEL_FILE):
                self.traffic_model = joblib.load(TRAFFIC_MODEL_FILE)
                print("교통 모델(Traffic) 로드 성공")
            else:
                print(f"교통 모델 파일 없음: {TRAFFIC_MODEL_FILE}")
        except Exception as e: print(f"교통 모델 로드 실패: {e}")

        try:
            if os.path.exists(POPULATION_MODEL_FILE):
                self.population_model = joblib.load(POPULATION_MODEL_FILE)
                print("인구 모델(Population) 로드 성공")
            else:
                print(f"인구 모델 파일 없음: {POPULATION_MODEL_FILE}")
        except Exception as e: print(f"인구 모델 로드 실패: {e}")

        # 장소 데이터 로드 (DB 우선, 실패 시 엑셀)
        self.df_places = None
        data_loaded = False

        if settings.PLACES_DATABASE_URL:
            try:
                db_url = settings.PLACES_DATABASE_URL.replace("+asyncpg", "+psycopg2")
                print(f"PostgreSQL 연결 시도... (Driver: psycopg2)")
                engine = create_engine(db_url)
                query = """
                        SELECT name, category, category2, lat, lng, address 
                        FROM public.places
                        """
                self.df_places = pd.read_sql(query, engine)
                self.df_places = self.df_places.fillna("")
                print(f"DB 장소 데이터 로드 성공: {len(self.df_places)}개")
                data_loaded = True
            except Exception as e:
                print(f"DB 로드 실패: {e}")
        
        if not data_loaded and os.path.exists(PLACE_FILE):
            try:
                print(f"엑셀 파일 로드 시도: {PLACE_FILE}")
                self.df_places = pd.read_excel(PLACE_FILE)
                self.df_places = self.df_places.fillna("")
                print(f"엑셀 장소 데이터 로드 성공: {len(self.df_places)}개")
            except Exception as e:
                print(f"엑셀 로드 실패: {e}")
                self.df_places = None

        if self.df_places is None:
            print("경고: 장소 데이터를 로드하지 못했습니다.")

        # 교통 네트워크 및 메타데이터 로드
        if os.path.exists(TN_CACHE_PATH):
            try:
                tn = TransportNetwork.__new__(TransportNetwork)
                tn._transport_network = TransportNetwork._load_pickled_transport_network(tn, TN_CACHE_PATH)
                self.transport_network = tn
                print("교통 네트워크 캐시 로드")
            except: self._build_transport_network()
        else: self._build_transport_network()

        self._load_metadata()
        
        # 장소와 좌표ID 매핑
        if self.df_places is not None and not self.df_places.empty:
            try:
                self.place_id_to_name = {}
                count = 0
                
                # DataFrame을 순회하며 '좌표 ID'를 Key로 저장
                for _, row in self.df_places.iterrows():
                    # 헬퍼 함수를 사용해 통일된 좌표 ID 생성
                    c_id = self._generate_coord_id(row.get('lat'), row.get('lng'))
                    
                    if c_id:
                        self.place_id_to_name[c_id] = row['name']
                        count += 1
                        
                print(f"장소 이름 매핑 완료: {len(self.place_id_to_name)}개")
            except Exception as e:
                print(f"장소 이름 매핑 실패: {e}")
        
        # 모든 장소에 대해 가까운 정류장 미리 계산 (Pre-compute)
        # if self.df_places is not None and self.stop_coords:
        #     self._precompute_nearest_stops_for_all()
            
        self.init_duration = round(time.time() - start_t, 3)
        self.is_initialized = True
        print(f"초기화 완료 ({self.init_duration}초)")

    def _build_transport_network(self):
        print("TransportNetwork 빌드 중...")
        self.transport_network = TransportNetwork(OSM_FILE, GTFS_FILES)
        try:
            self.transport_network._save_pickled_transport_network(self.transport_network._transport_network, TN_CACHE_PATH)
        except: pass

    def _load_metadata(self):
        if os.path.exists(META_CACHE_PATH):
            with open(META_CACHE_PATH, 'rb') as f:
                meta = pickle.load(f)
                self.stop_id_to_name = meta.get('stops', {})
                self.route_id_to_name = meta.get('routes', {})
                self.stop_route_map = meta.get('stop_route_map', {})
                self.stop_coords = meta.get('coords', {})
            return

        print("메타데이터 캐시 생성 중... (GTFS 파싱)")
        with zipfile.ZipFile(GTFS_FILES[0]) as z:
            with z.open('stops.txt') as f:
                stops_df = pd.read_csv(f, dtype={'stop_id': str}, usecols=['stop_id', 'stop_name', 'stop_lat', 'stop_lon'])
            self.stop_id_to_name = {str(r['stop_id']).strip(): str(r['stop_name']).strip() for _, r in stops_df.iterrows()}
            self.stop_coords = {str(r['stop_id']).strip(): {'lat': r['stop_lat'], 'lng': r['stop_lon']} for _, r in stops_df.iterrows()}
            
            with z.open('routes.txt') as f:
                routes_df = pd.read_csv(f)
            self.route_id_to_name = dict(zip(routes_df['route_id'].astype(str), routes_df['route_short_name'].astype(str)))

            with z.open('trips.txt') as f:
                trips_df = pd.read_csv(f, usecols=['trip_id', 'route_id'], dtype=str)
            
            with z.open('stop_times.txt') as f:
                stop_times_df = pd.read_csv(f, usecols=['trip_id', 'stop_id'], dtype=str)
            
            merged = stop_times_df.merge(trips_df, on='trip_id', how='left')
            stop_route_group = merged.groupby('stop_id')['route_id'].apply(set).to_dict()
            self.stop_route_map = {k.strip(): v for k, v in stop_route_group.items()}

        with open(META_CACHE_PATH, 'wb') as f:
            pickle.dump({
                'stops': self.stop_id_to_name, 
                'routes': self.route_id_to_name, 
                'stop_route_map': self.stop_route_map, 
                'coords': self.stop_coords
            }, f)
        print("메타데이터 생성 완료")

    # ========== 3-2. 예측 모델 및 가중치 계산 ==========
    def _predict_congestion(self, model, lat, lng, dt):
        if model is None or lat is None or lng is None: return 0
        input_vector = pd.DataFrame([[
            dt.month, dt.day, dt.hour, dt.weekday(),
            1 if dt.strftime('%Y%m%d') in KOREAN_HOLIDAYS_2026 else 0,
            1 if dt.weekday() >= 5 else 0,
            lat, lng
        ]], columns=['month', 'day', 'hour', 'dayofweek', 'is_holiday', 'is_weekend', '위도', '경도'])
        return int(model.predict(input_vector)[0])

    def _get_traffic_level(self, lat, lng, dt):
        """교통량 모델: 도로/이동 혼잡도"""
        return self._predict_congestion(self.traffic_model, lat, lng, dt)

    def _get_population_level(self, lat, lng, dt):
        """인구 모델: 장소/대기 혼잡도"""
        return self._predict_congestion(self.population_model, lat, lng, dt)

    def _get_wait_weight(self, level):
        if level == 2: return 1.5
        elif level == 1: return 1.3
        else: return 1.0

    def _get_stay_weight(self, level):
        if level == 2: return 1.25
        elif level == 1: return 1.1
        else: return 1.0

    def _get_travel_time_weight(self, level, mode="transport"):
        if mode == "car":
            if level == 2: return 1.8
            elif level == 1: return 1.6
        elif mode == "bus":
            if level == 2: return 1.6
            elif level == 1: return 1.4
        return 1.1

    # ========== 3-3. 거리 및 이동 시간 계산 (Geometry & R5PY) ==========
    def _haversine(self, lat1, lng1, lat2, lng2):
        if lat1 is None or lat2 is None or lng1 is None or lng2 is None: return 0
        R = 6371
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    def _find_nearest_stop(self, lat, lng, max_dist_meters=800):
        """
        주어진 좌표에서 가장 가까운 GTFS 정류장(지하철/버스)을 찾음
        """
        best_dist = float('inf')
        best_stop = None
        
        # 전체 정류장을 순회하며 최소 거리 탐색 (성능을 위해 캐시 권장되나 로직 단순화)
        for stop_id, coords in self.stop_coords.items():
            s_lat = coords.get('lat')
            s_lng = coords.get('lng')
            
            if abs(lat - s_lat) > 0.01 or abs(lng - s_lng) > 0.01:
                continue
            
            dist = self._haversine(lat, lng, s_lat, s_lng)
            if dist < best_dist and dist <= max_dist_meters:
                best_dist = dist
                best_stop = {'id': stop_id, 'lat': s_lat, 'lng': s_lng, 'dist': dist}
                
        return best_stop
    
    # def _precompute_nearest_stops_for_all(self):
    #     """
    #     서버 시작 시 모든 장소(df_places)에 대해 _find_nearest_stop을 수행하여 캐싱
    #     """
    #     if self.df_places is None or self.df_places.empty:
    #         return

    #     print(f"[{len(self.df_places)}개 장소] 인근 정류장 좌표 미리 계산 중... (다소 시간 소요 가능)")
    #     count = 0
        
    #     # DataFrame을 순회하며 계산
    #     for idx, row in self.df_places.iterrows():
    #         # 장소 ID 식별 (DB 컬럼에 'id'가 있다면 그것을, 없다면 index 사용)
    #         lat = row.get('lat')
    #         lng = row.get('lng')

    #         if lat and lng:
    #             # 위에서 정의한 함수 재사용
    #             nearest = self._find_nearest_stop(lat, lng, max_dist_meters=800)
                
    #             if nearest:
    #                 # 결과 딕셔너리에 저장 (구조: { 장소ID : 정류장정보 })
    #                 coord_id = self._generate_coord_id(lat, lng)
    #                 self.nearest_stop_map[coord_id] = nearest
    #                 count += 1
        
    #     print(f"  -> {count}개 장소에 대한 정류장 매핑 완료.")

    def _travel_minutes(self, p1, p2, mode="transport"):
        if p1 is None or p2 is None or p1.get('lat') is None or p2.get('lat') is None: return 0
        dist = self._haversine(p1['lat'], p1['lng'], p2['lat'], p2['lng'])

        if mode == "car":
            drive_time = int((dist / 25) * 60)
            return drive_time + 10
        else:
            transit_time = int((dist / 15) * 60)
            return transit_time + 5
        

    def _get_r5py_matrix(self, nodes, departure_time, transport_mode="transport"):
        valid_nodes = [n for n in nodes if n.get('lat') is not None]
        if len(valid_nodes) < 2: return {}
        gdf = gpd.GeoDataFrame(valid_nodes, geometry=gpd.points_from_xy([n['lng'] for n in valid_nodes], [n['lat'] for n in valid_nodes]), crs='EPSG:4326')
        # 모드에 따라 CAR 또는 WALK/TRANSIT 선택
        modes = [TransportMode.CAR] if transport_mode == "car" else [TransportMode.WALK, TransportMode.TRANSIT]
        try:
            matrix = TravelTimeMatrix(
                self.transport_network,
                origins=gdf,
                destinations=gdf,
                departure=departure_time,
                transport_modes=modes
            )
            r5_travel_times = {}
            for row in matrix.itertuples():
                if not pd.isna(row.travel_time):
                    r5_travel_times[(int(row.from_id), int(row.to_id))] = int(row.travel_time)
            return r5_travel_times
        except: return {}
    
    # ========== 아이디 매핑 ==========
    def _make_cache_key(self, start_node, end_node, departure_time, transport_mode):
        s_lat = start_node.get('lat') if start_node.get('lat') is not None else 0.0
        s_lng = start_node.get('lng') if start_node.get('lng') is not None else 0.0
        e_lat = end_node.get('lat') if end_node.get('lat') is not None else 0.0
        e_lng = end_node.get('lng') if end_node.get('lng') is not None else 0.0

        return (
            start_node.get('name'), round(s_lat, 6), round(s_lng, 6),
            end_node.get('name'), round(e_lat, 6), round(e_lng, 6), 
            departure_time.hour, transport_mode
        )
    
    def _generate_coord_id(self, lat, lng):
        """좌표를 이용해 고유 ID 문자열 생성 (예: '37.566535_126.977969')"""
        if lat is None or lng is None: return "unknown_loc"
        return f"{float(lat):.6f}_{float(lng):.6f}"
    
    def _get_all_detailed_paths(self, trip_legs, departure_time, transport_mode="transport", cached_times=None):
        if not trip_legs: return {}
        path_map = {}

        # 차량 모드: R5PY 결과 우선, 실패 시 Fallback
        if transport_mode == "car":
            for s, e in trip_legs:
                if s['id'] == e['id']: continue
                
                est_min = 0
                path_text = ""
                # [1순위] R5PY 매트릭스에 값이 있으면 사용
                if cached_times and (s['id'], e['id']) in cached_times:
                    est_min = cached_times[(s['id'], e['id'])]
                    path_text = f"승용차 이동 : {est_min}분"
                # [2순위] 값이 없으면 직선 거리 공식 사용 (Fallback)
                else:
                    est_min = self._travel_minutes(s, e, "car")
                    path_text = f"승용차 이동 : {est_min}분"
                
                path_map[(s['id'], e['id'])] = {
                    "fastest": [path_text], 
                    "min_transfer": [path_text]
                }
            return path_map

        # 대중교통 모드: R5PY DetailedItineraries
        origins, dests = [], []
        for s, e in trip_legs:
            if s['id'] == e['id']: continue
            
            # 틈새 카페 등 가까운 거리는 도보 처리
            is_gap_filler_move = (s.get("type") == "gap_filler") or (e.get("type") == "gap_filler")
            dist_km = self._haversine(s['lat'], s['lng'], e['lat'], e['lng'])

            if is_gap_filler_move and dist_km < 1.5:
                walk_min = int(dist_km / 4 * 60) + 2
                walk_msg = f"도보 : {walk_min}분"
                path_map[(s['id'], e['id'])] = {"fastest": [walk_msg], "min_transfer": [walk_msg]}
                continue

            # 캐시 확인
            ckey = self._make_cache_key(s, e, departure_time, transport_mode)
            if ckey in self.detailed_path_cache:
                path_map[(s['id'], e['id'])] = self.detailed_path_cache[ckey]
                continue

            # 좌표 유효성 체크 (없으면 Fallback 값 미리 채움)
            if s.get('lat') is None or e.get('lat') is None:
                path_map[(s['id'], e['id'])] = {
                    "fastest": [f"이동 : {FALLBACK_MOVE_MIN}분"], 
                    "min_transfer": [f"이동 : {FALLBACK_MOVE_MIN}분"]
                }
                continue
            
            origins.append(s); dests.append(e)

        if not origins: return path_map
        
        # [공통 변수 및 내부 함수]
        modes = [TransportMode.WALK, TransportMode.TRANSIT]
        
        def clean_id(val):
            if pd.isna(val): return ""
            s = str(val).strip()
            if s.endswith(".0"): return s[:-2]
            return s

        def get_minutes_ceil(val_str):
            if not val_str: return 0
            try:
                seconds = pd.to_timedelta(val_str).total_seconds()
                return math.ceil(seconds / 60) if seconds > 0 else 0
            except: return 0

        def parse_segments(df):
            segs = []
            mode_col = 'transport_mode' if 'transport_mode' in df.columns else 'mode'
            for _, leg in df.iterrows():
                raw_mode = str(leg[mode_col]).upper()
                ride_time = max(1, get_minutes_ceil(leg.get('travel_time') or leg.get('duration')))
                wait_time = get_minutes_ceil(leg.get('wait_time') or leg.get('wait'))
                
                if wait_time == 0:
                    w_val = leg.get('wait_time') or leg.get('wait')
                    if w_val and pd.to_timedelta(w_val).total_seconds() > 0: wait_time = 1

                if wait_time > 0 and 'WALK' not in raw_mode: segs.append(f"대기 : {wait_time}분")
                
                if 'CAR' in raw_mode: segs.append(f"승용차 이동 : {ride_time}분")
                elif 'WALK' in raw_mode: segs.append(f"도보 : {ride_time}분")
                else:
                    f_id = clean_id(leg.get('from_stop_id') or leg.get('start_stop_id'))
                    t_id = clean_id(leg.get('to_stop_id') or leg.get('end_stop_id'))
                    f_name = self.stop_id_to_name.get(f_id, "정류장")
                    t_name = self.stop_id_to_name.get(t_id, "정류장")
                    mode_nm = "지하철" if any(x in raw_mode for x in ['SUBWAY', 'RAIL', 'METRO']) else "버스"
                    
                    display_route_name = ""
                    # 버스 노선명 매핑 시도
                    if mode_nm == "버스":
                        routes_at_start = self.stop_route_map.get(f_id, set())
                        routes_at_end = self.stop_route_map.get(t_id, set())
                        common_routes = routes_at_start.intersection(routes_at_end)
                        if common_routes:
                            route_names = [str(self.route_id_to_name.get(rid)) for rid in common_routes if self.route_id_to_name.get(rid)]
                            display_route_name = ", ".join(sorted(list(set(route_names))))
                        else:
                            route_key = clean_id(leg.get('route_id'))
                            display_route_name = self.route_id_to_name.get(route_key, "대중교통")
                    else:
                        route_key = clean_id(leg.get('route_id'))
                        display_route_name = self.route_id_to_name.get(route_key, "대중교통")

                    segs.append(f"[{mode_nm}][{display_route_name}] : {f_name} → {t_name} : {ride_time}분")
            
            return segs

        def process_computer_result(comp_df, target_map):
            if comp_df is None or comp_df.empty: return
            mode_col = 'transport_mode' if 'transport_mode' in comp_df.columns else 'mode'
            
            for (f_id, t_id), group in comp_df.groupby(['from_id', 'to_id']):
                options = []
                for _, opt in group.groupby("option"):
                    total_min = sum(get_minutes_ceil(leg.get('travel_time') or leg.get('duration')) for _, leg in opt.iterrows())
                    transfers = sum(1 for _, leg in opt.iterrows() if 'WALK' not in str(leg[mode_col]).upper())
                    options.append({"route": opt, "time": total_min, "transfers": transfers})

                if not options: continue
                fastest = min(options, key=lambda x: (x['time'], x['transfers']))
                
                transit = [o for o in options if o['transfers'] > 0]
                best_transit = min(transit, key=lambda x: (x['transfers'], x['time'])) if transit else None
                best_walk = min([o for o in options if o['transfers'] == 0], key=lambda x: x['time'], default=None)
                
                winner = best_transit if best_transit else best_walk
                if best_walk and best_transit and best_walk['time'] <= best_transit['time'] + 5: winner = best_walk
                
                entry = {
                    "fastest": parse_segments(fastest['route']), 
                    "min_transfer": parse_segments(winner['route']) if winner else [f"도보 : {FALLBACK_MOVE_MIN}분"]
                }
                target_map[(int(f_id), int(t_id))] = entry

        # [1차 시도] 원래 좌표로 R5PY 경로 계산
        ogdf = gpd.GeoDataFrame(origins, geometry=gpd.points_from_xy([n['lng'] for n in origins], [n['lat'] for n in origins]), crs='EPSG:4326')
        ogdf['id'] = [n['id'] for n in origins]
        dgdf = gpd.GeoDataFrame(dests, geometry=gpd.points_from_xy([n['lng'] for n in dests], [n['lat'] for n in dests]), crs='EPSG:4326')
        dgdf['id'] = [n['id'] for n in dests]
        
        try:
            computer = DetailedItineraries(
                self.transport_network,
                origins=ogdf, destinations=dgdf,
                departure=departure_time,
                transport_modes=modes,
                force_all_to_all=False,
                max_public_transport_rides=MAX_TRANSFERS,
                max_time=timedelta(minutes=MAX_TRAVEL_TIME_MIN),
                snap_to_network=False,
                departure_time_window=timedelta(minutes=10)
            )
            process_computer_result(computer, path_map)
        except: pass

        # [2차 시도] 실패한 구간 식별 및 좌표 보정 (Rescue)
        failed_pairs = []
        for s, e in trip_legs:
            if s['id'] != e['id'] and (s['id'], e['id']) not in path_map:
                failed_pairs.append((s, e))
        
        if failed_pairs:
            print(f"[Rescue] {len(failed_pairs)}개 구간 경로 실패 -> 인근 정류장 좌표로 재계산 시도")
            retry_origins, retry_dests = [], []
            
            for s, e in failed_pairs:
                s_pid = s.get('place_id')
                e_pid = e.get('place_id')
                
                # 캐시(Pre-computed)에서 먼저 찾기
                new_s_stop = self.nearest_stop_map.get(s_pid) if s_pid is not None else None
                new_e_stop = self.nearest_stop_map.get(e_pid) if e_pid is not None else None
                
                # [추가] 캐시에 없으면(선택장소 등) 실시간으로 가까운 정류장 찾기
                if not new_s_stop and s.get('lat'):
                    print(f"[Rescue] '{s['name']}' 정류장 실시간 탐색...") 
                    new_s_stop = self._find_nearest_stop(s['lat'], s['lng'])
                
                if not new_e_stop and e.get('lat'):
                    print(f"[Rescue] '{e['name']}' 정류장 실시간 탐색...")
                    new_e_stop = self._find_nearest_stop(e['lat'], e['lng'])
                
                rs = copy.deepcopy(s)
                re_node = copy.deepcopy(e)
                
                s_place_name = self.place_id_to_name.get(s_pid, s.get('name', f"장소({s['id']})"))
                e_place_name = self.place_id_to_name.get(e_pid, e.get('name', f"장소({e['id']})"))
                
                # 좌표 교체 로직
                if new_s_stop:
                    rs['lat'], rs['lng'] = new_s_stop['lat'], new_s_stop['lng']
                    stop_name = self.stop_id_to_name.get(new_s_stop['id'], f"정류장({new_s_stop['id']})")
                    print(f"(보정) 출발지: {s_place_name} -> {stop_name} (좌표 변경)")
                    
                if new_e_stop:
                    re_node['lat'], re_node['lng'] = new_e_stop['lat'], new_e_stop['lng']
                    stop_name = self.stop_id_to_name.get(new_e_stop['id'], f"정류장({new_e_stop['id']})")
                    print(f"(보정) 도착지: {e_place_name} -> {stop_name} (좌표 변경)")
                
                retry_origins.append(rs)
                retry_dests.append(re_node)
            
            if retry_origins:
                rogdf = gpd.GeoDataFrame(retry_origins, geometry=gpd.points_from_xy([n['lng'] for n in retry_origins], [n['lat'] for n in retry_origins]), crs='EPSG:4326')
                rogdf['id'] = [n['id'] for n in retry_origins]
                rdgdf = gpd.GeoDataFrame(retry_dests, geometry=gpd.points_from_xy([n['lng'] for n in retry_dests], [n['lat'] for n in retry_dests]), crs='EPSG:4326')
                rdgdf['id'] = [n['id'] for n in retry_dests]
                
                try:
                    rescue_computer = DetailedItineraries(
                        self.transport_network,
                        origins=rogdf, destinations=rdgdf,
                        departure=departure_time,
                        transport_modes=modes,
                        force_all_to_all=False,
                        max_public_transport_rides=MAX_TRANSFERS,
                        max_time=timedelta(minutes=MAX_TRAVEL_TIME_MIN),
                        snap_to_network=False,
                        departure_time_window=timedelta(minutes=10)
                    )
                    process_computer_result(rescue_computer, path_map)
                except Exception as e:
                    print(f"[Rescue Fail] 재계산 오류: {e}")

        # [3차 최후통첩] 여전히 실패한 구간은 직선 거리 비례 시간으로 Fallback
        for s, e in trip_legs:
            if s['id'] != e['id'] and (s['id'], e['id']) not in path_map:
                dist_min = self._travel_minutes(s, e, transport_mode)
                path_map[(s['id'], e['id'])] = {
                    "fastest": [f"이동 : {dist_min}분"], 
                    "min_transfer": [f"이동 : {dist_min}분"]
                }
        
        return path_map

    # ========== 3-4. 노드 구성 (장소, 고정일정 등) ==========
    def _build_fixed_nodes(self, fixed_events, day_start_dt):
        """고정 일정 노드 생성"""
        nodes = []
        for event in fixed_events:
            # 데이터 추출
            if isinstance(event, dict):
                s_str = event.get('start_time') or event.get('start')
                e_str = event.get('end_time') or event.get('end')
                title = event.get('title') or event.get('name', "고정일정")
                lat = event.get('lat') 
                lng = event.get('lng')
                address = event.get('address') or event.get('addr', "")
                existing_window = event.get('window')
                orig_time_str = event.get('orig_time_str')
                stay = event.get('stay')
            else:
                s_str = getattr(event, 'start_time', None) or getattr(event, 'start', None)
                e_str = getattr(event, 'end_time', None) or getattr(event, 'end', None)
                title = getattr(event, 'title', None) or getattr(event, 'name', "고정일정")
                lat = getattr(event, 'lat', None)
                lng = getattr(event, 'lng', None)
                address = getattr(event, 'address', None) or ""
                existing_window = getattr(event, 'window', None)
                orig_time_str = getattr(event, 'orig_time_str', None) 
                stay = getattr(event, 'stay', None)                   

            # [재계산 모드]
            if not s_str and existing_window and orig_time_str:
                nodes.append({
                    "place_id": self._generate_coord_id(lat, lng),
                    "name": title,
                    "category": "고정일정",
                    "category2": "고정일정",
                    "lat": float(lat) if lat else None, 
                    "lng": float(lng) if lng else None,
                    "addr": address,
                    "stay": stay or 60,
                    "type": "fixed",
                    "window": tuple(existing_window),
                    "orig_time_str": orig_time_str
                })
                continue

            # [초기 생성 모드]
            if not s_str or not e_str: continue

            try:
                if "T" in s_str: s_str = s_str.split("T")[1]
                if "T" in e_str: e_str = e_str.split("T")[1]
                
                dt_start = datetime.strptime(s_str[:5], "%H:%M")
                dt_end = datetime.strptime(e_str[:5], "%H:%M")
                
                start_abs_min = dt_start.hour * 60 + dt_start.minute
                end_abs_min = dt_end.hour * 60 + dt_end.minute
                stay_duration = max(0, end_abs_min - start_abs_min)

                if existing_window: final_window = tuple(existing_window)
                else: final_window = (max(0, start_abs_min - 20), start_abs_min + 5)

                nodes.append({
                    "place_id": self._generate_coord_id(lat, lng),
                    "name": title,
                    "category": "고정일정",
                    "category2": "고정일정",
                    "lat": float(lat) if lat else None, 
                    "lng": float(lng) if lng else None,
                    "addr": address,
                    "stay": stay_duration,
                    "type": "fixed",
                    "window": final_window,
                    "orig_time_str": f"{s_str[:5]} - {e_str[:5]}"
                })
            except Exception as e:
                print(f"[Warning] 고정 일정 생성 실패: {title} - {e}")
                continue
        return nodes

    def _build_nodes(self, places, restaurants, fixed_events, day_start_dt, selected_places=None):
        """전체 노드 통합 빌드"""
        nodes = []
        
        # 관광지
        for p in places:
            p_type = p.get("type") or "spot"
            p_window = tuple(p.get("window")) if p.get("window") else (0, 1440)
            stay = p.get('stay') or stay_time_map.get(p.get("category"), 60)
            
            nodes.append({
                "place_id": self._generate_coord_id(p.get("lat"), p.get("lng")),
                "name": p["name"],
                "category": p.get("category", "관광지"),
                "category2": p.get("category2", ""),
                "lat": p.get("lat"), "lng": p.get("lng"),
                "stay": stay,
                "type": p_type,
                "window": p_window,
                "addr": p.get("address") or p.get("addr", "")
            })
        
        # 식당
        for r in restaurants:
            r_type = r.get("type")
            r_window = tuple(r.get("window")) if r.get("window") else None

            if r_type in ["lunch", "dinner"]:
                nodes.append({
                    "place_id": self._generate_coord_id(r.get("lat"), r.get("lng")),
                    "name": r["name"],
                    "category": "음식점",
                    "category2": r.get("category2", "음식점"),
                    "lat": r.get("lat"), "lng": r.get("lng"),
                    "stay": r.get("stay", 70),
                    "type": r_type,
                    "window": r_window,
                    "addr": r.get("address") or r.get("addr", "")
                })
            else:
                for meal_type in ["lunch", "dinner"]:
                    nodes.append({
                        "place_id": self._generate_coord_id(r.get("lat"), r.get("lng")),
                        "name": r["name"],
                        "category": "음식점",
                        "category2": r.get("category2", "음식점"),
                        "lat": r.get("lat"), "lng": r.get("lng"),
                        "stay": r.get("stay", 70),
                        "type": meal_type,
                        "window": None, 
                        "addr": r.get("address") or r.get("addr", "")
                    })
            
        # 선택 장소
        if selected_places:
            for sp in selected_places:
                sp_window = tuple(sp.get("window")) if sp.get("window") else (0, 1440)
                category = sp.get("category", "선택장소")
                
                # [핵심 변경 사항] 카테고리가 '음식점'이면 Lunch/Dinner로 분기
                if category == "음식점":
                    # 점심 옵션 추가 (is_selected=True)
                    nodes.append({
                        "place_id": self._generate_coord_id(sp.get("lat"), sp.get("lng")),
                        "name": sp["name"],
                        "category": category,
                        "category2": "선택장소",
                        "lat": sp.get("lat"), "lng": sp.get("lng"),
                        "stay": sp.get("stay") or 70,  # 식사 시간 기본값
                        "type": "lunch",               # 타입은 lunch로 설정하여 시간 제약 적용
                        "window": None,                # lunch 타입은 solver에서 윈도우 자동 할당
                        "addr": sp.get("address") or sp.get("addr", ""),
                        "is_selected": True            # [중요] Solver가 이를 선택된 장소로 인식하게 함
                    })
                    # 저녁 옵션 추가 (is_selected=True)
                    nodes.append({
                        "place_id": self._generate_coord_id(sp.get("lat"), sp.get("lng")),
                        "name": sp["name"],
                        "category": category,
                        "category2": "선택장소",
                        "lat": sp.get("lat"), "lng": sp.get("lng"),
                        "stay": sp.get("stay") or 70,
                        "type": "dinner",              # 타입은 dinner
                        "window": None,
                        "addr": sp.get("address") or sp.get("addr", ""),
                        "is_selected": True
                    })
                # 음식점이 아니면 기존 로직대로 처리
                else:
                    nodes.append({
                        "place_id": self._generate_coord_id(sp.get("lat"), sp.get("lng")),
                        "name": sp["name"],
                        "category": category,
                        "category2": "선택장소",
                        "lat": sp.get("lat"), "lng": sp.get("lng"),
                        "stay": sp.get("stay") or stay_time_map.get(sp.get("category"), 60),
                        "type": sp.get("type", "selected"),
                        "window": sp_window,
                        "addr": sp.get("address") or sp.get("addr", ""),
                        "is_selected": True
                    })
        
        # 고정 일정
        nodes.extend(self._build_fixed_nodes(fixed_events, day_start_dt))
        
        return nodes

    # ========== 3-5. 타임라인 생성 ==========
    def _build_timeline_by_type(self, visited_nodes, path_map, timeline_base_dt, target_date_str, path_type, transport_mode="transport"):
        if not visited_nodes: return []
        timeline = []
        
        start_min = visited_nodes[0].get('arrival_min', 0)
        
        ICONS = {0: "🟢", 1: "🟡", 2: "🔴"} 
        LVL_TXT = {0: "원활", 1: "서행", 2: "정체"}
        POP_TXT = {0: "여유", 1: "보통", 2: "혼잡"}

        # 첫 번째 장소 처리
        first_node = visited_nodes[0]
        f_arrival_dt = timeline_base_dt + timedelta(minutes=first_node.get('arrival_min', start_min))
        
        f_pop_lvl = self._get_population_level(first_node.get('lat'), first_node.get('lng'), f_arrival_dt)
        f_pop_txt = POP_TXT.get(f_pop_lvl, "정보없음")
        f_icon = ICONS.get(f_pop_lvl, "")
        
        f_traffic_lvl = self._get_traffic_level(first_node.get('lat'), first_node.get('lng'), f_arrival_dt)
        f_traffic_str = f"교통 {LVL_TXT.get(f_traffic_lvl, '원활')}{ICONS.get(f_traffic_lvl, '')}"

        f_cong_tag = ""
        f_final_stay = first_node["stay"]
        
        if first_node["type"] in ["spot", "selected", "lunch", "dinner", "gap_filler"]:
            f_w_stay = math.ceil(first_node["stay"] * self._get_stay_weight(f_pop_lvl))
            f_add_s = f_w_stay - first_node["stay"]
            f_final_stay = f_w_stay
            
            if f_add_s > 0:
                f_cong_tag = f" [{f_icon}{f_pop_txt} (+{f_add_s}분)]"
            else:
                f_cong_tag = f" [{f_icon}{f_pop_txt}]"
            f_pop_label = f"인구 {f_pop_txt}{f_icon}"
        else:
            f_pop_label = "고정일정"

        if first_node["type"] == "fixed":
            f_time_str = first_node.get("orig_time_str", "")
            try:
                end_time_part = f_time_str.split(' - ')[1]
                cursor_dt = datetime.strptime(f"{target_date_str} {end_time_part}", "%Y-%m-%d %H:%M")
            except:
                cursor_dt = f_arrival_dt + timedelta(minutes=f_final_stay)
        else:
            f_end_dt = f_arrival_dt + timedelta(minutes=f_final_stay)
            f_time_str = f"{f_arrival_dt.strftime('%H:%M')} - {f_end_dt.strftime('%H:%M')}{f_cong_tag}"
            cursor_dt = f_end_dt

        timeline.append({
            "name": first_node['name'],
            "category": first_node["category"],
            "category2": first_node.get("category2", ""),
            "time": f_time_str,
            "transit_to_here": [], 
            "population_level": f_pop_label,
            "traffic_level": f_traffic_str
        })

        # 나머지 장소 처리
        for i in range(1, len(visited_nodes)):
            prev, node = visited_nodes[i-1], visited_nodes[i]
            transit_info, cur_travel_m = [], 0
            
            arrival_dt = timeline_base_dt + timedelta(minutes=node.get('arrival_min', start_min))
            dest_traffic_lvl = self._get_traffic_level(node.get('lat'), node.get('lng'), arrival_dt)

            path_opts = path_map.get((prev['id'], node['id']))
            if path_opts:
                chosen = path_opts.get('fastest' if transport_mode == "car" else path_type, [])
                for seg in chosen:
                    found_times = re.findall(r'(\d+)분', seg)
                    s_min = sum(int(m) for m in found_times)
                    
                    added = 0
                    tag = ""
                    
                    is_car = "승용차" in seg
                    is_bus = "버스" in seg
                    
                    # 대기: 시간만 더하고 텍스트(태그)는 출력 안 함
                    if "대기" in seg:
                        origin_lvl = self._get_traffic_level(prev.get('lat'), prev.get('lng'), cursor_dt)
                        added = math.ceil(s_min * self._get_wait_weight(origin_lvl)) - s_min
                        tag = "" # 태그 비움

                    # [버스/승용차] 로직 (기존 유지: 원활이면 +0분 숨김)
                    elif is_car or is_bus:
                        weight = self._get_travel_time_weight(dest_traffic_lvl, "car" if is_car else "bus")
                        added = math.ceil(s_min * weight) - s_min
                        
                        if is_car: added += 12

                        t_txt = LVL_TXT.get(dest_traffic_lvl, "서행")
                        
                        if dest_traffic_lvl == 0:
                            tag = f" [{ICONS.get(dest_traffic_lvl)}{t_txt}]"
                        elif added > 0:
                            tag = f" [{ICONS.get(dest_traffic_lvl)}{t_txt} (+{added}분)]"
                        else:
                            tag = f" [{ICONS.get(dest_traffic_lvl)}{t_txt}]"

                        if is_car: tag += " [주차/도보 +12분]"

                    real_m = s_min + added
                    cur_travel_m += real_m
                    
                    if found_times:
                        # "대기 : 5분" 형태로만 출력됨
                        new_seg = re.sub(r'(\d+)분', f'{real_m}분', seg) + tag
                        transit_info.append(new_seg)
                    else:
                        transit_info.append(seg + tag)

            else: 
                cur_travel_m = self._travel_minutes(prev, node, transport_mode)
                transit_info.append(f"이동 : {cur_travel_m}분 (정보없음)")
            
            arrival_dt = cursor_dt + timedelta(minutes=cur_travel_m)

            # 스마트 도착 보정
            target_dt = None
            if node["type"] in ["lunch", "dinner"]:
                win = LUNCH_WINDOW if node["type"] == "lunch" else DINNER_WINDOW
                target_dt = datetime.strptime(f"{target_date_str} {win[0]}", "%Y-%m-%d %H:%M")
            elif node["type"] == "fixed":
                try: target_dt = datetime.strptime(f"{target_date_str} {node['orig_time_str'].split(' - ')[0]}", "%Y-%m-%d %H:%M")
                except: pass

            wait_m, slack_m = 0, 0
            if target_dt and arrival_dt < target_dt:
                diff = int((target_dt - arrival_dt).total_seconds() / 60)
                if diff > 10: 
                    slack_m, wait_m = diff - 10, 10
                    cursor_dt += timedelta(minutes=slack_m)
                    arrival_dt = target_dt - timedelta(minutes=10)
                else:
                    wait_m = diff

            if slack_m > 0: transit_info.insert(0, f"출발 전 여유 : {slack_m}분")
            if wait_m > 0: transit_info.append(f"현장 대기 : {wait_m}분")
            arrival_dt += timedelta(minutes=wait_m)

            pop_lvl = self._get_population_level(node.get('lat'), node.get('lng'), arrival_dt)
            pop_txt = POP_TXT.get(pop_lvl, "정보없음")
            icon = ICONS.get(pop_lvl, "")
            
            if node["type"] in ["spot", "selected", "lunch", "dinner", "gap_filler"]:
                w_stay = math.ceil(node["stay"] * self._get_stay_weight(pop_lvl))
                add_s, final_stay = w_stay - node["stay"], w_stay
                
                if add_s > 0:
                    cong_tag = f" [{icon}{pop_txt} (+{add_s}분)]"
                else:
                    cong_tag = f" [{icon}{pop_txt}]"
                    
                pop_label = f"인구 {pop_txt}{icon}"
            else:
                final_stay, cong_tag = node["stay"], ""
                pop_label = "고정일정"

            if node["type"] == "fixed":
                time_str = node["orig_time_str"]
                try:
                    cursor_dt = datetime.strptime(f"{target_date_str} {node['orig_time_str'].split(' - ')[1]}", "%Y-%m-%d %H:%M")
                except:
                    cursor_dt = arrival_dt + timedelta(minutes=final_stay)
            else:
                end_dt = arrival_dt + timedelta(minutes=final_stay)
                time_str = f"{arrival_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}{cong_tag}"
                cursor_dt = end_dt

            timeline.append({
                "name": node['name'],
                "category": node["category"],
                "category2": node.get("category2", ""),
                "time": time_str,
                "transit_to_here": transit_info,
                "population_level": pop_label,
                "traffic_level": f"교통 {LVL_TXT.get(dest_traffic_lvl, '원활')}{ICONS.get(dest_traffic_lvl, '')}"
            })
            
        return timeline
    
    # ========== 3-6. 핵심 최적화 로직 (_optimize_day) ==========
    def _optimize_day(self, places, restaurants, fixed_events, start_time_str, target_date_str, end_time_str=None, transport_mode="transport", selected_places=None):
        
        # 날짜 및 시간 설정
        base_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        day_start_dt = datetime.strptime(start_time_str, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        start_min = day_start_dt.hour * 60 + day_start_dt.minute
        
        max_horizon = 24 * 60
        if end_time_str:
            end_dt = datetime.strptime(end_time_str, "%H:%M")
            max_horizon = end_dt.hour * 60 + end_dt.minute

        # 노드 빌드
        nodes = self._build_nodes(places, restaurants, fixed_events, day_start_dt, selected_places)
        for idx, node in enumerate(nodes): node["id"] = int(idx)
        n = len(nodes)

        # 체류시간 보정 (재계산이 아닐 경우)
        solver_nodes = copy.deepcopy(nodes)
        is_recalculation = any(p.get('window') for p in places if isinstance(p, dict))
        if not is_recalculation:
            for node in solver_nodes:
                if node["type"] in ["spot", "lunch", "dinner"]:
                    node["stay"] = int(node["stay"] * 1.2)

        # 이동 시간 매트릭스 (R5PY + Fallback)
        r5_dep = datetime.combine(base_date, datetime.strptime("11:00", "%H:%M").time())
        r5_times = self._get_r5py_matrix(nodes, r5_dep, transport_mode)

        time_matrix = [[0]*n for _ in range(n)]
        
        # [통계용] R5PY 실패 횟수 카운트
        fallback_count = 0
        
        for i in range(n):
            for j in range(n):
                if i == j: continue
                
                # [핵심 로직] R5PY 결과가 있으면 사용, 없으면 거리 비례 계산(Fallback)
                val = r5_times.get((i, j))
                
                if val is None:
                    # R5PY 실패 -> 직선 거리 기반 추정치 사용
                    val = self._travel_minutes(nodes[i], nodes[j], transport_mode)
                    
                    # [패널티] 계산된 경로가 아니므로 불안정성을 감안하여 10% 정도 시간을 더 잡음 (선호도 낮춤)
                    val = int(val * 1.1)
                    fallback_count += 1
                
                # 고정 일정 사이의 이동은 최소 30분 보장 (물리적 이동 고려)
                if "fixed" in [nodes[i]["type"], nodes[j]["type"]]: 
                    val = max(val, 30)
                    
                # 차량 모드일 경우 주차/교통체증 여유 시간 추가
                if transport_mode == 'car': 
                    val += 15
                    
                # 전체적으로 20% 여유를 두어 빡빡한 일정 방지
                time_matrix[i][j] = int(val * 1.2)
                
        if fallback_count > 0:
            print(f"[{target_date_str}] 매트릭스 보정: {fallback_count}개 구간 풀백")

        # 타임 윈도우 설정
        l_s, l_e = 720, 820
        d_s, d_e = 1080, 1180
        windows = []
        for node in nodes:
            if node.get("window"): windows.append(tuple(node["window"]))
            elif node["type"] == "lunch": windows.append((l_s, l_e - 10))
            elif node["type"] == "dinner": windows.append((d_s, d_e - 10))
            else: windows.append((0, max_horizon))

        # DFS 최적화 실행
        print(f"[{target_date_str}] 경로 최적화 시작 (노드 {n}개)...")
        start_dfs = time.time()
        
        solver = SimpleRouteSolver(solver_nodes, time_matrix, windows, start_min, max_horizon)
        best_path_indices, arrival_times = solver.solve()
        
        print(f"[{target_date_str}] 최적화 완료 : {round(time.time() - start_dfs, 2)}초")

        if not best_path_indices:
            print(f"유효한 경로를 찾지 못했습니다.")
            return {"fastest_version": [], "min_transfer_version": []}, []

        # 방문 노드 구성 및 도착 시간 주입
        visited_nodes = []
        for idx in best_path_indices:
            node = copy.deepcopy(nodes[idx])
            node['arrival_min'] = arrival_times.get(idx, 0)
            win_start = windows[idx][0]
            real_start = max(node['arrival_min'], win_start)
            node['departure_min'] = real_start + node.get('stay', 0)
            visited_nodes.append(node)

        # 후처리: 틈새 카페(Gap Filler) 삽입
        df_cafes = pd.DataFrame()
        if self.df_places is not None:
            df_cafes = self.df_places[self.df_places['category'] == '카페'].copy()

        final_nodes = []
        if visited_nodes:
            final_nodes.append(visited_nodes[0])
            curr_cursor = visited_nodes[0]['departure_min']

            for i in range(1, len(visited_nodes)):
                next_node = visited_nodes[i]
                travel_min = time_matrix[final_nodes[-1]['id']][next_node['id']]
                expected_arrival = curr_cursor + travel_min
                
                target_start = windows[next_node['id']][0] if next_node["type"] in ["lunch", "dinner", "fixed"] else None
                gap = (target_start - expected_arrival) if target_start else 0
                inserted = False

                if gap >= 50 and not df_cafes.empty:
                    last_lat, last_lng = final_nodes[-1]['lat'], final_nodes[-1]['lng']
                    target_lat, target_lng = next_node['lat'], next_node['lng']
                    
                    df_cafes['dist_to_next'] = df_cafes.apply(lambda r: self._haversine(target_lat, target_lng, r['lat'], r['lng']), axis=1)
                    candidates = df_cafes[df_cafes['dist_to_next'] <= 0.6].sort_values('dist_to_next')
                    
                    if not candidates.empty:
                        cafe = candidates.iloc[0]
                        walk_min_to_next = int(cafe['dist_to_next'] / 4 * 60) + 10
                        stay_time = min(gap - walk_min_to_next - 5, 60)
                        
                        if stay_time >= 25:
                            cafe_node = {
                                "id": 9900 + i, "name": cafe['name'], "type": "gap_filler",
                                "category": "틈새 카페", "category2": cafe.get('category2', '카페'),
                                "lat": cafe['lat'], "lng": cafe['lng'],
                                "stay": int(stay_time), "arrival_min": expected_arrival
                            }
                            print(f"틈새 카페 추가: {cafe['name']} (목적지까지 {int(cafe['dist_to_next']*1000)}m, 체류 {int(stay_time)}분)")
                            final_nodes.append(cafe_node)
                            curr_cursor = cafe_node['arrival_min'] + stay_time
                            inserted = True

                travel_to_next = self._travel_minutes(final_nodes[-1], next_node, transport_mode) if inserted else travel_min
                next_node['arrival_min'] = curr_cursor + travel_to_next
                final_nodes.append(next_node)
                
                real_start_next = max(next_node['arrival_min'], windows[next_node['id']][0])
                curr_cursor = real_start_next + next_node['stay']

        # 타임라인 생성 및 반환
        print(f"[{target_date_str}] 상세 경로 생성(Detailed Path) 시작...")
        start_path = time.time()
        
        timeline_base = datetime.combine(base_date, datetime.min.time())
        trip_legs = [(final_nodes[i], final_nodes[i+1]) for i in range(len(final_nodes)-1)]
        path_map = self._get_all_detailed_paths(trip_legs, r5_dep, transport_mode, cached_times=r5_times)
        
        print(f"[{target_date_str}] 상세 경로 생성 완료 : {round(time.time() - start_path, 2)}초")
        
        start_timeline = time.time()
        
        result = {"fastest_version": self._build_timeline_by_type(final_nodes, path_map, timeline_base, target_date_str, "fastest", transport_mode)}
        if transport_mode != "car":
            result["min_transfer_version"] = self._build_timeline_by_type(final_nodes, path_map, timeline_base, target_date_str, "min_transfer", transport_mode)
            
        print(f"[{target_date_str}] 타임라인 조립 완료 : {round(time.time() - start_timeline, 2)}초")
        
        return result, final_nodes

    # ========== 3-7. Gemini AI 연동 및 외부 API ==========
    def _extract_json(self, text):
        if not text: raise ValueError("Gemini 응답이 비어있습니다.")
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"): text = text[4:]
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == -1: raise ValueError("JSON 파싱 실패")
        return json.loads(text[start:end])

    def _get_gemini_recommendation(self, days, places, restaurants, accommodations, request: PlanGenerateRequest):
        if os.path.exists(RESULT_JSON_PATH):
            print("result.json 발견 → Gemini 호출 생략")
            try:
                with open(RESULT_JSON_PATH, "r", encoding="utf-8") as f:
                    return json.load(f), 0
            except: pass
        
        if not self.api_key:
            print("Google API Key 없음")
            return None, 0
        
        client = genai.Client(api_key=self.api_key)
        
        schema = """
        {
        "plans": {
            "day1": {
                "route": [{"name": "...", "category": "...", "category2": "...", "lat": 0.0, "lng": 0.0}],
                "restaurants": [{"name": "...", "category": "...", "category2": "...", "lat": 0.0, "lng": 0.0}],
                "accommodations": [{"name": "...", "category": "...", "category2": "...", "lat": 0.0, "lng": 0.0}]
                },
                "day2": { "...(days 수만큼 반복)..." }
            }
        }
        """
        
        CAT_MAP = {"attraction": "관광지", "culture": "문화시설", "shopping": "쇼핑", "cafe": "카페"}
        PURPOSE_MAP = {"date": "데이트", "solo": "혼자 시간", "friends": "친구들과", "family": "가족 나들이", "photo": "사진 찍기", "gourmet": "맛집 위주"}

        korean_categories = [CAT_MAP.get(cat, cat) for cat in request.categories]
        categories_str = ", ".join(korean_categories)

        korean_purposes = [PURPOSE_MAP.get(p, p) for p in request.purposes]
        purposes_str = ", ".join(korean_purposes)
        
        system_prompt = f"""
        너는 '서울 여행 장소 추천 전문가'이다. 반드시 제공된 데이터만을 사용하여 계획을 세운다.

        [입력 정보]
        여행 기간: 총 {days}일 (입력된 days 값에 맞춰 'day1'부터 'day{days}'까지 생성할 것)

        [관심 장소 카테고리]
        {categories_str}를(을) 적절한 비율로 추천해줘.

        [여행 테마 및 목적]
        {purposes_str} (이 테마에 어울리는 분위기의 장소와 식당을 우선적으로 배치해줘)

        [출력 형식]
        {schema}

        [절대 규칙]
        1. 모든 장소의 이름, 좌표(lat, lng), 카테고리는 입력된 데이터와 100% 일치해야 한다.
        2. 'route' 배열: 제공된 'places' 목록에서 8개를 선택
        3. 'restaurants' 배열: 제공된 'restaurants' 목록에서 4개를 선택
        4. 'accommodations' 배열: 제공된 'accommodations' 목록에서 1개를 선택 (마지막 날은 빈 배열)
        5. 출력: 순수 JSON만 출력
        """

        user_prompt = {
            "days": days,
            "start_location": {"lat": 37.5547, "lng": 126.9706},
            "places": places,
            "restaurants": restaurants,
            "accommodations": accommodations
        }
        
        try:
            print("Gemini가 초기 계획을 생성하고 있습니다...")
            prompt = system_prompt + "\n\n" + json.dumps(user_prompt, ensure_ascii=False)
            response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            plan = self._extract_json(response.text)
            
            # with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
            #     json.dump(plan, f, ensure_ascii=False, indent=2)
            
            return plan, 0
        except Exception as e:
            print(f"Gemini API 오류: {e}")
            return None, 0
        
    def reoptimize_day(self, places, restaurants, fixed_events, start_time_str, target_date_str, end_time_str, transport_mode, selected_places):
        """특정 날짜의 경로 최적화 재실행 (외부 호출용)"""
        if not self.is_initialized: 
            self.initialize_resources()

        return self._optimize_day(
            places=places,
            restaurants=restaurants,
            fixed_events=fixed_events,
            start_time_str=start_time_str,
            target_date_str=target_date_str,
            end_time_str=end_time_str,
            transport_mode=transport_mode,
            selected_places=selected_places
        )
    
    def get_alternative_spot(self, original_name: str, lat: float, lng: float, category: str):
        """대체 장소 추천"""
        if not self.is_initialized: self.initialize_resources()
        if self.df_places is None: return None
        
        # 후보군 필터링 (반경 0.8km 이내)
        candidates = self.df_places[(self.df_places['category'] == category) & (self.df_places['name'] != original_name)].copy()
        if candidates.empty: return None
        
        candidates['dist'] = candidates.apply(lambda r: self._haversine(lat, lng, r['lat'], r['lng']), axis=1)
        nearby_candidates = candidates[candidates['dist'] <= 1.0].sort_values('dist')

        if nearby_candidates.empty:
            nearby_candidates = candidates[candidates['dist'] <= 1.2].sort_values('dist')
            if nearby_candidates.empty: return None
            
        top_candidates = nearby_candidates.sample(n=6).to_dict('records')
        print(f"    [RouteService] Candidates for '{original_name}' (Radius 800m):")
        for c in top_candidates: print(f"      - {c['name']} ({int(c['dist']*1000)}m)")

        # Gemini 추천 요청
        if not self.api_key:
            best = top_candidates[0]
            return {
                "id": self._generate_coord_id(best['lat'], best['lng']),
                "name": best['name'],
                "category": best['category'],
                "lat": best['lat'], "lng": best['lng'],
                "reason": "원래 장소와 가장 가까운 대체 장소입니다."
            }

        try:
            client = genai.Client(api_key=self.api_key)
            candidate_list_text = "\n".join([f"- {c['name']} (거리: {int(c['dist']*1000)}m)" for c in top_candidates])
            
            prompt = f"""
            여행 계획 중 '{original_name}'({category})을(를) 대신할 장소를 찾고 있습니다.
            
            [후보 목록 (반경 800m 내)]
            {candidate_list_text}
            
            [요청사항]
            1. 위 후보 중 여행자에게 가장 매력적인 곳을 하나만 선택하세요.
            2. 선택한 장소의 이름과, 그곳을 추천하는 이유를 한국어 1문장으로 작성하세요.
            3. 응답은 반드시 다음 JSON 형식으로만 주세요:
            {{"name": "선택한장소이름", "reason": "추천이유"}}
            """
            
            response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            result = self._extract_json(response.text)
            target_name = result.get('name')
            reason = result.get('reason', "추천 장소입니다.")
            
            selected_spot = next((c for c in top_candidates if c['name'] == target_name), top_candidates[0])
            return {
                "id": self._generate_coord_id(selected_spot['lat'], selected_spot['lng']),
                "name": selected_spot['name'],
                "category": selected_spot['category'], "category2": selected_spot.get('category2', ''),
                "lat": selected_spot['lat'], "lng": selected_spot['lng'],
                "reason": reason
            }

        except Exception as e:
            print(f"Gemini 추천 실패: {e}")
            best = top_candidates[0]
            return {
                "id": self._generate_coord_id(best['lat'], best['lng']),
                "name": best['name'],
                "category": best['category'], "category2": best.get('category2', ''),
                "lat": best['lat'], "lng": best['lng'],
                "reason": "가장 가까운 거리의 대체 장소입니다."
            }

    # ========== 3-8. API 진입점 (generate_plan) ==========
    def generate_plan(self, request: PlanGenerateRequest):
        total_start_time = time.time()
        if not self.is_initialized: self.initialize_resources()
        if self.df_places is None: return {'error': '장소 데이터를 불러올 수 없습니다'}

        self.detailed_path_cache = {}

        # 중심 좌표 및 검색 반경 설정
        cols = ["name", "category", "category2", "lat", "lng", "address"]
        center = SEOUL_GU_COORDS.get(request.region, {"lat": 37.57, "lng": 126.98})

        if hasattr(request, 'selected_places') and request.selected_places:
            sp = request.selected_places[0]  # 여기서 sp 정의
            center = {"lat": float(sp['lat']), "lng": float(sp['lng'])} # sp 사용은 반드시 if문 안에서!
            print(f"중심점 변경: 선택 장소 기준 ({center['lat']}, {center['lng']})")
        
        # 고정 일정이 있으면 덮어쓰기 (elif 사용)
        elif request.fixed_events:
            for event in request.fixed_events:
                e_lat = event.get('lat') if isinstance(event, dict) else getattr(event, 'lat', None)
                e_lng = event.get('lng') if isinstance(event, dict) else (getattr(event, 'lng', None) or getattr(event, 'lon', None))
                if e_lat is not None and e_lng is not None:
                    center = {"lat": float(e_lat), "lng": float(e_lng)}
                    print(f"중심점 변경: 고정일정 기준 ({center['lat']}, {center['lng']})")
                    break
        
        REDIUS = 5  # km
        df = self.df_places.copy()
        df['dist'] = df.apply(lambda r: self._haversine(center['lat'], center['lng'], r['lat'], r['lng']), axis=1)
        
        # 날짜 계산 및 후보 장소 필터링
        start_dt = datetime.strptime(request.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(request.end_date, '%Y-%m-%d')
        total_days = (end_dt - start_dt).days + 1

        sample_limit_places = total_days * 8 * 4 
        sample_limit_res = total_days * 4 * 6 
        sample_limit_acc = total_days * 2 * 6 

        # 카테고리 필터링
        mask = (df['dist'] <= REDIUS) & (~df['category'].isin(['음식점', '숙박']))
        CAT_MAP = { "attraction": "관광지", "culture": "문화시설", "shopping": "쇼핑", "cafe": "카페" }
        target_keys = request.categories if request.categories else ["attraction", "culture", "shopping", "cafe"]
        target_cats = [CAT_MAP.get(c, c) for c in target_keys]
        mask = mask & (df['category'].isin(target_cats))

        filtered_df = df[mask]
        places = []
        for cat, group in filtered_df.groupby('category'):
            if len(group) > sample_limit_places: sampled_group = group.sample(n=sample_limit_places)
            else: sampled_group = group
            places.extend(sampled_group[cols].to_dict('records'))

        # 식당 및 숙박 샘플링
        df_rest = df[(df['dist'] <= REDIUS) & (df['category'] == '음식점')]
        if len(df_rest) > sample_limit_res: df_rest = df_rest.sample(n=sample_limit_res)
        restaurants = df_rest[cols].to_dict('records')

        df_accom = df[(df['dist'] <= REDIUS) & (df['category'] == '숙박')]
        if len(df_accom) > sample_limit_acc: df_accom = df_accom.sample(n=sample_limit_acc)
        accommodations = df_accom[cols].to_dict('records')

        print(f"후보 장소: {len(places)}개, 음식점: {len(restaurants)}개, 숙박: {len(accommodations)}개")
        
        # Gemini Plan 생성
        start_gemini = time.time()
        plan, _ = self._get_gemini_recommendation(total_days, places, restaurants, accommodations, request)
        print(f"Gemini 생성 완료 : {round(time.time() - start_gemini, 2)}초")
        if not plan: return {'error': 'AI 추천 실패'}
        
        # 이름 정규화 함수 (공백 제거 및 소문자화)
        def normalize(n): return re.sub(r'\s+', '', str(n)).lower()
        
        # 검증용 Lookup Table 생성 (이름 -> 데이터)
        norm_valid_places = {normalize(p['name']): p for p in places}
        norm_valid_restaurants = {normalize(r['name']): r for r in restaurants}
        norm_valid_accommodations = {normalize(a['name']): a for a in accommodations}
        
        # 전체 DB
        full_db_map = {normalize(row['name']): row.to_dict() for _, row in self.df_places.iterrows()}
        
        def validate_and_fix(item_list, valid_map, category_name):
            for i, item in enumerate(item_list):
                orig_name = item.get('name', '')
                norm_name = normalize(orig_name)
                
                # [Case 1] 후보군(Sample)에 정확히 있거나 유사한 이름인 경우
                if norm_name in valid_map:
                    target = valid_map[norm_name]
                    item.update({"name": target['name'], "lat": target['lat'], "lng": target['lng'], 
                                "category": target.get('category', item.get('category')),
                                "category2": target.get('category2', "")})
                    continue
                
                # [Case 2] 후보군엔 없지만 전체 DB에 실존하는 경우 (여기서 '경희궁' 등이 구제됨)
                if norm_name in full_db_map:
                    target = full_db_map[norm_name]
                    item.update({"name": target['name'], "lat": target['lat'], "lng": target['lng'],
                                "category": target.get('category', item.get('category')),
                                "category2": target.get('category2', "")})
                    print(f"[Info] 실존 장소 데이터 복구: {orig_name}")
                    continue
                
                # [Case 3] 진짜 없는 장소(환각)인 경우 -> 후보군 중 랜덤 교체
                if valid_map:
                    replacement = random.choice(list(valid_map.values()))
                    print(f"[Warn] 환각 장소 교체: {orig_name} -> {replacement['name']}")
                    item.update({"name": replacement['name'], "lat": replacement['lat'], "lng": replacement['lng'],
                                "category": replacement['category'], "category2": replacement.get('category2', "")})

        # 3. 각 항목 검증 실행
        for day_key, day_plan in plan['plans'].items():
            if 'route' in day_plan:
                validate_and_fix(day_plan['route'], norm_valid_places, "관광지")
            if 'restaurants' in day_plan:
                validate_and_fix(day_plan['restaurants'], norm_valid_restaurants, "음식점")
            if 'accommodations' in day_plan:
                validate_and_fix(day_plan['accommodations'], norm_valid_accommodations, "숙박")

        # 좌표 및 주소 보정
        ref_df = self.df_places.set_index("name")
        name_to_addr = ref_df["address"].to_dict()
        name_to_lat = ref_df["lat"].to_dict()
        name_to_lng = ref_df["lng"].to_dict()
        name_to_cat = ref_df["category"].to_dict()

        for day_key, day_plan in plan['plans'].items():
            if 'route' in day_plan:
                for item in day_plan['route']:
                    name = item['name']
                    if name in name_to_lat:
                        item['lat'], item['lng'] = name_to_lat[name], name_to_lng[name]
                        item['category'] = name_to_cat.get(name, item.get('category'))
                    item['addr'] = name_to_addr.get(name, "")
                    item['type'] = 'spot'
            
            if 'restaurants' in day_plan:
                for item in day_plan['restaurants']:
                    name = item['name']
                    if name in name_to_lat: item['lat'], item['lng'] = name_to_lat[name], name_to_lng[name]
                    item['addr'] = name_to_addr.get(name, "")
                    item['type'] = 'restaurant'

            if 'accommodations' in day_plan:
                for item in day_plan['accommodations']:
                    name = item['name']
                    if name in name_to_lat: item['lat'], item['lng'] = name_to_lat[name], name_to_lng[name]
                    item['addr'] = name_to_addr.get(name, "")
                    item['type'] = 'accommodation'

        # 일별 경로 최적화 실행
        start_opt = time.time()
        print(f"최적화 시작 ({total_days}일, Mode: {request.transport_mode})")
        
        final_result = {k: plan['plans'][k] for k in plan['plans']}
        curr = start_dt
        day_keys = list(plan['plans'].keys())
        global_visited_selected = set()
        
        for i, day_key in enumerate(day_keys):
            day_start_time = request.first_day_start_time if i == 0 else "10:00"
            day_end_time = request.last_day_end_time if i == len(day_keys) - 1 else "21:00"
            current_date_str = curr.strftime("%Y-%m-%d")

            # 고정 일정 필터링
            daily_fixed_events = []
            if request.fixed_events:
                for e in request.fixed_events:
                    e_date = e.get('date') if isinstance(e, dict) else getattr(e, 'date', None)
                    if e_date == current_date_str:
                        event_dict = {}
                        if isinstance(e, dict): event_dict = e.copy()
                        elif hasattr(e, 'model_dump'): event_dict = e.model_dump()
                        elif hasattr(e, 'dict'): event_dict = e.dict()
                        else:
                            try: event_dict = vars(e).copy()
                            except: continue
                        event_dict['lat'] = event_dict.get('lat')
                        event_dict['lng'] = event_dict.get('lng')
                        event_dict['addr'] = event_dict.get('address') or event_dict.get('addr', "")
                        daily_fixed_events.append(event_dict)

            # 선택 장소 필터링
            available_selected = []
            if request.selected_places:
                available_selected = [sp for sp in request.selected_places if sp['name'] not in global_visited_selected]

            # 최적화 호출
            timelines, updated_nodes = self._optimize_day(
                places=plan['plans'][day_key]['route'], 
                restaurants=plan['plans'][day_key]['restaurants'], 
                fixed_events=daily_fixed_events,             
                start_time_str=day_start_time,           
                target_date_str=current_date_str,          
                end_time_str=day_end_time,             
                transport_mode=request.transport_mode,
                selected_places=available_selected 
            )

            for node in updated_nodes:
                if node.get('type') == 'selected' or node.get('is_selected'):
                    global_visited_selected.add(node['name'])
                    print(f"[방문 확정] {node['name']} (다음 날부터 제외)")
            
            final_result[day_key]['timelines'] = timelines
        
            # 메타데이터 보존 후 결과 저장
            clean_route_list = []
            for node in updated_nodes:
                if node['type'] == 'depot': continue
                
                final_id = node.get('place_id')
                if not final_id:
                    final_id = self._generate_coord_id(node.get('lat'), node.get('lng'))
                
                clean_route_list.append({
                    "id": final_id,
                    "name": node['name'], "category": node['category'], "category2": node.get('category2', ""),
                    "lat": node['lat'], "lng": node['lng'], "addr": node.get("addr", ""),
                    "type": node['type'], "stay": node['stay'],
                    "window": list(node['window']) if node.get('window') else None,
                    "orig_time_str": node.get('orig_time_str', "")
                })
            final_result[day_key]['route'] = clean_route_list
            curr += timedelta(days=1)

        print(f"최적화 완료 : {round(time.time() - start_opt, 2)}초")
        print(f"[Total] 전체 프로세스 완료: {time.time() - total_start_time:.2f}초")

        with open(RESULT_FINAL_PATH, "w", encoding="utf-8") as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)

        return final_result

# 인스턴스 생성
route_service = RouteOptimizerService()