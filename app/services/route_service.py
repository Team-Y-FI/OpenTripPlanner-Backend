import os, pickle, re, time, math, json, zipfile, joblib, copy
import multiprocessing
import pandas as pd
import geopandas as gpd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai

from app.schemas.plan import PlanGenerateRequest
from sqlalchemy import create_engine
from app.core.config import settings

# ============================================================
# 1. 환경 설정 및 상수
# ============================================================
available_cores = multiprocessing.cpu_count() * 0.8
TARGET_THREADS = available_cores
os.environ["R5PY_NUM_THREADS"] = str(TARGET_THREADS)

# [Java 환경 설정] r5py(대중교통 라우팅 라이브러리) 구동을 위한 JVM 설정
os.environ["JAVA_HOME"] = r"C:\Program Files\Java\jdk-21.0.10"
os.environ["JAVA_OPTS"] = (
    f"-Xmx12G " # 힙 메모리 12GB 할당 (메모리 부족 방지)
    f"-XX:+UseG1GC " # G1 가비지 컬렉터 사용 (대용량 메모리 환경에서 성능 안정성 확보)
    f"-Djava.util.concurrent.ForkJoinPool.common.parallelism={TARGET_THREADS}"
)

from r5py import TransportNetwork, TravelTimeMatrix, DetailedItineraries, TransportMode

# [경로 설정] 프로젝트 루트 디렉토리 및 주요 하위 디렉토리(데이터, 모델) 경로 정의
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "model")

# [모델 및 데이터 파일 경로] 
TRAFFIC_MODEL_FILE = os.path.join(MODEL_DIR, "traffic_congestion_model_latlon.pkl")     # 이동 시간 예측용 (교통량 기반)
POPULATION_MODEL_FILE = os.path.join(MODEL_DIR, "congestion_model_latlon.pkl")          # 체류/대기 시간 예측용 (인구/혼잡도 기반)

PLACE_FILE = os.path.join(DATA_DIR, "place_전체_통합_진짜최종.xlsx") # 장소 마스터 데이터
OSM_FILE = os.path.join(DATA_DIR, "seoul_osm_v.pbf")                 # OpenStreetMap 서울 지도 데이터
GTFS_FILES = [os.path.join(DATA_DIR, "seoul_area_gtfs.zip")]         # 서울 대중교통 노선 데이터 (GTFS)
TN_CACHE_PATH = os.path.join(DATA_DIR, "seoul_tn_cached.pkl")        # 교통 네트워크 캐시
META_CACHE_PATH = os.path.join(DATA_DIR, "metadata_cache_v2.pkl")    # 메타데이터 캐시
RESULT_JSON_PATH = "result.json"  # 중간 결과 저장용
RESULT_FINAL_PATH = "result_timeline.json"  # 최종 타임라인 결과 저장용

# [휴일 데이터] 2026년도 한국 법정공휴일 리스트 (휴일 여부에 따른 혼잡도/영업시간 차이 반영 목적)
KOREAN_HOLIDAYS_2026 = [
    '20260101', '20260216', '20260217', '20260218', '20260301', '20260302',
    '20260505', '20260524', '20260525', '20260606', '20260608', '20260815',
    '20260817', '20260924', '20260925', '20260926', '20261003', '20261005',
    '20261009', '20261225'
]

# [위치 데이터] 서울시 25개 구청 기준 중심 좌표 (지역구 단위 거리 계산 및 필터링 시 활용)
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

# [최적화 관련 제약 조건 상수]
FALLBACK_MOVE_MIN = 30         # 경로 탐색 실패 시 기본 적용할 이동 시간(분)
MAX_TRANSFERS = 2              # 최대 환승 횟수 허용치
MAX_TRAVEL_TIME_MIN = 80      # 장소 간 최대 허용 이동 시간(분)
LUNCH_WINDOW = ("11:00", "14:00")  # 점심 식사 가능 시간대
DINNER_WINDOW = ("17:00", "20:00") # 저녁 식사 가능 시간대

# [장소 유형별 기본 체류 시간] 단위: 분
stay_time_map = {
    "관광지": 90, "카페": 50, "음식점": 60,
    "박물관": 120, "공원": 60, "시장": 80, "숙박": 0
}

# ============================================================
# 2. 타임라인 및 경로 최적화 (DFS 기반)
# ============================================================
class SimpleRouteSolver:
    def __init__(self, nodes, time_matrix, windows, start_min, max_horizon):
        self.nodes = nodes
        self.matrix = time_matrix
        self.windows = windows
        self.start_min = start_min
        self.max_horizon = max_horizon
        self.n = len(nodes)

        self.best_path = []
        self.best_cost = float('inf')
        self.best_score = -1
        self.best_arrival_times = {}
        
        # [NEW] 우선순위 비교를 위한 카운트 변수들
        self.best_fixed_cnt = -1
        self.best_selected_cnt = -1

    def solve(self):
        # 1. 초기화
        self.best_path = []
        self.best_cost = float('inf')
        self.best_score = -1
        self.best_arrival_times = {}
        self.best_fixed_cnt = -1
        self.best_selected_cnt = -1

        # 2. 모든 노드를 시작점으로 시도
        for i in range(self.n):
            node = self.nodes[i]
            
            # (시작점 필터링)
            if node["type"] not in ["spot", "selected", "fixed", "lunch", "dinner"]:
                continue
            
            win_start, win_end = self.windows[i]
            if self.start_min > win_end:
                continue
                
            visited = [False] * self.n
            visited[i] = True

            actual_start = max(self.start_min, win_start)
            departure_time = actual_start + node.get("stay", 0)

            score_map = {"fixed": 15000, "selected": 8000, "lunch": 5000, "dinner": 5000}
            initial_score = score_map.get(node["type"], 1000)

            is_start_lunch = (node["type"] == "lunch")
            is_start_dinner = (node["type"] == "dinner")
            
            # [NEW] 시작점의 타입에 따라 초기 카운트 설정
            initial_fixed_cnt = 1 if node["type"] == "fixed" else 0
            initial_selected_cnt = 1 if node["type"] == "selected" else 0

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
                fixed_cnt=initial_fixed_cnt,       # 고정일정 개수 전달
                selected_cnt=initial_selected_cnt  # [NEW] 선택장소 개수 전달
            )
        
        return self.best_path, self.best_arrival_times

    def _dfs(self, curr_idx, curr_time, visited, path, total_cost, current_score, arrival_times, has_lunch, has_dinner, fixed_cnt, selected_cnt):
        # [핵심 수정] 최적 경로 갱신 로직 (우선순위: 고정 > 선택 > 점수 > 비용)
        update_best = False
        
        # 1. 고정 일정 개수가 더 많으면 무조건 갱신
        if fixed_cnt > self.best_fixed_cnt:
            update_best = True
        
        # 2. 고정 일정 개수가 같다면 -> 선택 장소 개수 비교
        elif fixed_cnt == self.best_fixed_cnt:
            if selected_cnt > self.best_selected_cnt:
                update_best = True
            
            # 3. 선택 장소 개수도 같다면 -> 점수 비교
            elif selected_cnt == self.best_selected_cnt:
                if current_score > self.best_score:
                    update_best = True
                
                # 4. 점수도 같다면 -> 이동 시간(비용) 비교
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

                # (필터링 로직들)
                if node_type == "lunch" and has_lunch: continue
                if node_type == "dinner" and has_dinner: continue
                if any(self.nodes[p_idx]["name"] == node["name"] for p_idx in path): continue

                # (고정 일정/일반 일정 시간 체크 로직은 기존과 동일하므로 생략하지 않고 그대로 둠)
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
                node_score = {"fixed": 15000, "selected": 8000, "lunch": 5000, "dinner": 5000}.get(node_type, 1000)

                if len(path) > 1 and wait_time > 30: penalty_cost += (wait_time - 30) * 10
                penalty_cost += travel_time * 2
                if travel_time > 40: penalty_cost += (travel_time - 40) * 10

                visited[next_idx] = True
                path.append(next_idx)
                arrival_times[next_idx] = arrival
                
                # [NEW] 다음 단계로 카운트 증가시켜 전달
                next_fixed = fixed_cnt + (1 if node_type == "fixed" else 0)
                next_selected = selected_cnt + (1 if node_type == "selected" else 0)
                
                self._dfs(
                    next_idx, leave_time, visited, path,
                    total_cost + travel_time + actual_wait_for_penalty + penalty_cost,
                    current_score + node_score - penalty_cost,
                    arrival_times, 
                    has_lunch or (node_type == "lunch"), 
                    has_dinner or (node_type == "dinner"),
                    next_fixed,    # [NEW]
                    next_selected  # [NEW]
                )
                path.pop()
                visited[next_idx] = False
                del arrival_times[next_idx]

# ============================================================
# Plan 생성을 위한 class
# ============================================================
class RouteOptimizerService:
    """서울 여행 경로 최적화 서비스 (완전체)"""
    
    def __init__(self):
        self.is_initialized = False
        self.traffic_model = None    # 이동/교통대기 모델
        self.population_model = None # 체류/입장대기 모델
        self.transport_network = None
        self.df_places = None
        self.stop_coords = {}
        self.stop_id_to_name = {}
        self.route_id_to_name = {}
        self.stop_route_map = {}
        self.detailed_path_cache = {}
        self.api_key = None
        self.init_duration = 0

    def initialize_resources(self):
        if self.is_initialized: return
        start_t = time.time()
        print("리소스 초기화 시작...")
        
        load_dotenv()
        self.api_key = os.getenv("API_KEY_P")

        # 1. 모델 로드 (2개 분리)
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

        # 2. 데이터 및 네트워크 로드
        self.df_places = None
        data_loaded = False

        # [DB 로드 시도]
        if settings.PLACES_DATABASE_URL:
            try:
                db_url = settings.PLACES_DATABASE_URL.replace("+asyncpg", "+psycopg2")
                print(f"PostgreSQL 연결 시도... (Driver: psycopg2)")
                engine = create_engine(db_url)
                
                # 필요한 컬럼만 선택하여 로드 (테이블명 'places' 가정)
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
        
        # [엑셀 폴백]
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

        if os.path.exists(TN_CACHE_PATH):
            try:
                tn = TransportNetwork.__new__(TransportNetwork)
                tn._transport_network = TransportNetwork._load_pickled_transport_network(tn, TN_CACHE_PATH)
                self.transport_network = tn
                print("교통 네트워크 캐시 로드")
            except: self._build_transport_network()
        else: self._build_transport_network()

        self._load_metadata()
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
                self.stop_route_map = meta.get('stop_route_map', {}) # 로드
                self.stop_coords = meta.get('coords', {})
            return

        print("메타데이터 캐시 생성 중... (GTFS 파싱)")
        with zipfile.ZipFile(GTFS_FILES[0]) as z:
            # 1. Stops 로드
            with z.open('stops.txt') as f:
                stops_df = pd.read_csv(f, dtype={'stop_id': str}, usecols=['stop_id', 'stop_name', 'stop_lat', 'stop_lon'])
            self.stop_id_to_name = {str(r['stop_id']).strip(): str(r['stop_name']).strip() for _, r in stops_df.iterrows()}
            self.stop_coords = {str(r['stop_id']).strip(): {'lat': r['stop_lat'], 'lng': r['stop_lon']} for _, r in stops_df.iterrows()}
            
            # 2. Routes 로드
            with z.open('routes.txt') as f:
                routes_df = pd.read_csv(f)
            self.route_id_to_name = dict(zip(routes_df['route_id'].astype(str), routes_df['route_short_name'].astype(str)))

            # 3. Stop_Route_Map 생성
            with z.open('trips.txt') as f:
                trips_df = pd.read_csv(f, usecols=['trip_id', 'route_id'], dtype=str)
            
            with z.open('stop_times.txt') as f:
                stop_times_df = pd.read_csv(f, usecols=['trip_id', 'stop_id'], dtype=str)
            
            merged = stop_times_df.merge(trips_df, on='trip_id', how='left')

            stop_route_group = merged.groupby('stop_id')['route_id'].apply(set).to_dict()
            self.stop_route_map = {k.strip(): v for k, v in stop_route_group.items()}

        # 캐시 저장
        with open(META_CACHE_PATH, 'wb') as f:
            pickle.dump({
                'stops': self.stop_id_to_name, 
                'routes': self.route_id_to_name, 
                'stop_route_map': self.stop_route_map, # 저장
                'coords': self.stop_coords
            }, f)
        print("메타데이터 생성 완료")

    # ========== 예측 및 가중치 계산 ==========
    
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
        """교통량 모델 사용: 도로/이동 혼잡도"""
        return self._predict_congestion(self.traffic_model, lat, lng, dt)

    def _get_population_level(self, lat, lng, dt):
        """인구 모델 사용: 장소/대기 혼잡도"""
        return self._predict_congestion(self.population_model, lat, lng, dt)

    def _get_wait_weight(self, level):
        """대기 시간 가중치"""
        if level == 2: return 1.5
        elif level == 1: return 1.3
        else: return 1.0

    def _get_stay_weight(self, level):
        """체류 시간 가중치"""
        if level == 2: return 1.25
        elif level == 1: return 1.1
        else: return 1.0

    def _get_travel_time_weight(self, level, mode="transport"):
        """이동 시간 가중치"""
        if mode == "car":
            if level == 2: return 1.8
            elif level == 1: return 1.6
        elif mode == "bus":
            if level == 2: return 1.6
            elif level == 1: return 1.4
        return 1.1

    def _haversine(self, lat1, lng1, lat2, lng2):
        if lat1 is None or lat2 is None or lng1 is None or lng2 is None: return 0
        R = 6371
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1) # lng 사용
        a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def _travel_minutes(self, p1, p2, mode="transport"):
        if p1 is None or p2 is None or p1.get('lat') is None or p2.get('lat') is None: return 0
        dist = self._haversine(p1['lat'], p1['lng'], p2['lat'], p2['lng'])

        if mode == "car":
            # Assume 30km/h (0.5km/min) + 15 min parking/walking
            drive_time = int(dist / 0.5)
            return drive_time + 15
            
        return int(dist / 30 * 60)

    # ========== 경로 계산 (R5PY) ==========

    def _get_r5py_matrix(self, nodes, departure_time, transport_mode="transport"):
        valid_nodes = [n for n in nodes if n.get('lat') is not None]
        if len(valid_nodes) < 2: return {}
        gdf = gpd.GeoDataFrame(valid_nodes, geometry=gpd.points_from_xy([n['lng'] for n in valid_nodes], [n['lat'] for n in valid_nodes]), crs='EPSG:4326')
        modes = [TransportMode.CAR] if transport_mode == "car" else [TransportMode.WALK, TransportMode.TRANSIT]
        try:
            matrix = TravelTimeMatrix(self.transport_network, origins=gdf, destinations=gdf, departure=departure_time, transport_modes=modes)
            r5_travel_times = {}
            for row in matrix.itertuples():
                if not pd.isna(row.travel_time):
                    r5_travel_times[(int(row.from_id), int(row.to_id))] = int(row.travel_time)
            return r5_travel_times
        except: return {}
    
    def _make_cache_key(self, start_node, end_node, departure_time, transport_mode):
        s_lat = start_node.get('lat')
        s_lng = start_node.get('lng')
        e_lat = end_node.get('lat')
        e_lng = end_node.get('lng')

        safe_s_lat = s_lat if s_lat is not None else 0.0
        safe_s_lng = s_lng if s_lng is not None else 0.0
        safe_e_lat = e_lat if e_lat is not None else 0.0
        safe_e_lng = e_lng if e_lng is not None else 0.0

        return (
            start_node.get('name'), 
            round(safe_s_lat, 6), 
            round(safe_s_lng, 6),
            end_node.get('name'), 
            round(safe_e_lat, 6), 
            round(safe_e_lng, 6), 
            departure_time.hour,
            transport_mode
        )
    
    def _get_all_detailed_paths(self, trip_legs, departure_time, transport_mode="transport", cached_times=None):
        if not trip_legs: return {}
        path_map = {}

        if transport_mode == "car":
            for s, e in trip_legs:
                if s['id'] == e['id']: continue
                est_min = 0

                if cached_times and (s['id'], e['id']) in cached_times:
                    est_min = cached_times[(s['id'], e['id'])]
                else:
                    est_min = self._travel_minutes(s, e, "car")

                path_text = f"승용차 이동 : {est_min}분"

                entry = {
                    "fastest": [path_text], 
                    "min_transfer": [path_text]
                }
                path_map[(s['id'], e['id'])] = entry
            
            return path_map

        origins, dests = [], []
        
        for s, e in trip_legs:
            if s['id'] == e['id']: continue

            is_gap_filler_move = (s.get("type") == "gap_filler") or (e.get("type") == "gap_filler")
            dist_km = self._haversine(s['lat'], s['lng'], e['lat'], e['lng'])

            if is_gap_filler_move and dist_km < 1.5:
                walk_min = int(dist_km / 4 * 60) + 2
                walk_msg = f"도보 : {walk_min}분"
                path_map[(s['id'], e['id'])] = {
                    "fastest": [walk_msg], 
                    "min_transfer": [walk_msg]
                }
                continue

            ckey = self._make_cache_key(s, e, departure_time, transport_mode)
            if ckey in self.detailed_path_cache:
                path_map[(s['id'], e['id'])] = self.detailed_path_cache[ckey]
                continue

            if s.get('lat') is None or e.get('lat') is None:
                path_map[(s['id'], e['id'])] = {"fastest": [f"이동 : {FALLBACK_MOVE_MIN}분"], "min_transfer": [f"이동 : {FALLBACK_MOVE_MIN}분"]}
                continue
            origins.append(s); dests.append(e)

        if not origins: return path_map
        
        ogdf = gpd.GeoDataFrame(origins, geometry=gpd.points_from_xy([n['lng'] for n in origins], [n['lat'] for n in origins]), crs='EPSG:4326')
        ogdf['id'] = [n['id'] for n in origins]
        dgdf = gpd.GeoDataFrame(dests, geometry=gpd.points_from_xy([n['lng'] for n in dests], [n['lat'] for n in dests]), crs='EPSG:4326')
        dgdf['id'] = [n['id'] for n in dests]
        
        modes = [TransportMode.WALK, TransportMode.TRANSIT]
        max_rides = MAX_TRANSFERS

        try:
            computer = DetailedItineraries(
                self.transport_network,
                origins=ogdf,
                destinations=dgdf,
                departure=departure_time,
                transport_modes=modes,
                max_public_transport_rides=max_rides,
                max_time=timedelta(minutes=MAX_TRAVEL_TIME_MIN),
                snap_to_network=3000
            )
        except: return path_map

        if computer is None or computer.empty: return path_map
        mode_col = 'transport_mode' if 'transport_mode' in computer.columns else 'mode'

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
            for _, leg in df.iterrows():
                raw_mode = str(leg[mode_col]).upper()
                ride_time = max(1, get_minutes_ceil(leg.get('travel_time') or leg.get('duration')))
                wait_time = get_minutes_ceil(leg.get('wait_time') or leg.get('wait'))
                
                if wait_time == 0:
                    w_val = leg.get('wait_time') or leg.get('wait')
                    if w_val and pd.to_timedelta(w_val).total_seconds() > 0:
                        wait_time = 1

                if wait_time > 0 and 'WALK' not in raw_mode: segs.append(f"대기 : {wait_time}분")
                
                # [수정] 대중교통 모드인데 CAR가 나오면 이상하므로 체크
                if 'CAR' in raw_mode: 
                    segs.append(f"승용차 이동 : {ride_time}분")
                elif 'WALK' in raw_mode:
                    segs.append(f"도보 : {ride_time}분")
                else:
                    f_id = clean_id(leg.get('from_stop_id') or leg.get('start_stop_id'))
                    t_id = clean_id(leg.get('to_stop_id') or leg.get('end_stop_id'))
                    f_name = self.stop_id_to_name.get(f_id, "정류장")
                    t_name = self.stop_id_to_name.get(t_id, "정류장")
                    mode_nm = "지하철" if any(x in raw_mode for x in ['SUBWAY', 'RAIL', 'METRO']) else "버스"
                    
                    display_route_name = ""
                    if mode_nm == "버스":
                        routes_at_start = self.stop_route_map.get(f_id, set())
                        routes_at_end = self.stop_route_map.get(t_id, set())
                        common_routes = routes_at_start.intersection(routes_at_end)
                        if common_routes:
                            route_names = []
                            for rid in common_routes:
                                r_name = self.route_id_to_name.get(rid)
                                if r_name: route_names.append(str(r_name))
                            unique_routes = sorted(list(set(route_names)))
                            display_route_name = ", ".join(unique_routes)
                        else:
                            route_key = clean_id(leg.get('route_id'))
                            display_route_name = self.route_id_to_name.get(route_key, "대중교통")
                    else:
                        route_key = clean_id(leg.get('route_id'))
                        display_route_name = self.route_id_to_name.get(route_key, "대중교통")

                    segs.append(f"[{mode_nm}][{display_route_name}] : {f_name} → {t_name} : {ride_time}분")
            return segs

        for (f_id, t_id), group in computer.groupby(['from_id', 'to_id']):
            s_node = next((n for n in origins if n['id'] == int(f_id)), None)
            e_node = next((n for n in dests if n['id'] == int(t_id)), None)
            if not s_node or not e_node: continue
            
            safe_key = self._make_cache_key(s_node, e_node, departure_time, transport_mode)
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
            
            entry = {"fastest": parse_segments(fastest['route']), "min_transfer": parse_segments(winner['route']) if winner else [f"도보 : {FALLBACK_MOVE_MIN}분"]}
            path_map[(int(f_id), int(t_id))] = entry
            self.detailed_path_cache[safe_key] = entry
        
        return path_map

    # ========== 노드 빌더 (고정 일정 포함) ==========

    def _build_fixed_nodes(self, fixed_events, day_start_dt):
        """고정 일정 노드 생성 (초기 생성 및 재계산 모드 완벽 대응)"""
        nodes = []
        for event in fixed_events:
            # 1. 데이터 추출 (Dict/Object 호환)
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

            # ==========================================
            # [재계산 모드] 프론트에서 기존 데이터(window, orig_time_str)를 보냈을 때
            # ==========================================
            if not s_str and existing_window and orig_time_str:
                nodes.append({
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

            # ==========================================
            # [초기 생성 모드] 시작/종료 시간이 있는 경우
            # ==========================================
            if not s_str or not e_str: 
                continue

            try:
                # 2. 시간 파싱 및 분 단위 변환
                if "T" in s_str: s_str = s_str.split("T")[1]
                if "T" in e_str: e_str = e_str.split("T")[1]
                
                dt_start = datetime.strptime(s_str[:5], "%H:%M")
                dt_end = datetime.strptime(e_str[:5], "%H:%M")
                
                start_abs_min = dt_start.hour * 60 + dt_start.minute
                end_abs_min = dt_end.hour * 60 + dt_end.minute
                stay_duration = max(0, end_abs_min - start_abs_min)

                if existing_window:
                    final_window = tuple(existing_window)
                else:
                    final_window = (max(0, start_abs_min - 20), start_abs_min + 5)

                nodes.append({
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
        """전체 노드 통합 빌드 (재계산 시 기존 타입 및 윈도우 보존)"""
        nodes = []
        
        # 1. 관광지 (Spot) 및 일반 장소
        for p in places:
            # type이 없으면 기본 'spot'으로 설정
            p_type = p.get("type") or "spot"
            p_window = p.get("window")
            p_window = tuple(p_window) if p_window else (0, 1440)

            stay = p.get('stay') or stay_time_map.get(p.get("category"), 60)
            
            nodes.append({
                "name": p["name"],
                "category": p.get("category", "관광지"),
                "category2": p.get("category2", ""),
                "lat": p.get("lat"), 
                "lng": p.get("lng"),
                "stay": stay,
                "type": p_type,
                "window": p_window,
                "addr": p.get("address") or p.get("addr", "")
            })
        
        # 2. 식당 처리 (재계산 모드 대응)
        for r in restaurants:
            r_type = r.get("type")
            r_window = tuple(r.get("window")) if r.get("window") else None

            if r_type in ["lunch", "dinner"]:
                nodes.append({
                    "name": r["name"],
                    "category": "음식점",
                    "category2": r.get("category2", "음식점"),
                    "lat": r.get("lat"), 
                    "lng": r.get("lng"),
                    "stay": r.get("stay", 70),
                    "type": r_type,
                    "window": r_window,
                    "addr": r.get("address") or r.get("addr", "")
                })
            # [수정] 초기 생성 시 (여러 후보 중 선택해야 할 때)
            else:
                for meal_type in ["lunch", "dinner"]:
                    nodes.append({
                        "name": r["name"],
                        "category": "음식점",
                        "category2": r.get("category2", "음식점"),
                        "lat": r.get("lat"), 
                        "lng": r.get("lng"),
                        "stay": r.get("stay", 70),
                        "type": meal_type,
                        "window": None, 
                        "addr": r.get("address") or r.get("addr", "")
                    })
            
        # 3. 선택된 장소 (Selected)
        if selected_places:
            for sp in selected_places:
                sp_window = sp.get("window")
                if sp_window: sp_window = tuple(sp_window)
                else: sp_window = (0, 1440)

                nodes.append({
                    "name": sp["name"],
                    "category": sp.get("category", "선택장소"),
                    "category2": sp.get("category2", "선택장소"),
                    "lat": sp.get("lat"), 
                    "lng": sp.get("lng"),
                    "stay": sp.get("stay") or stay_time_map.get(sp.get("category"), 60),
                    "type": sp.get("type", "selected"),
                    "window": sp_window,
                    "addr": sp.get("address") or sp.get("addr", "")
                })
        
        # 4. 고정 일정 (Fixed)
        nodes.extend(self._build_fixed_nodes(fixed_events, day_start_dt))
        
        return nodes

    # ========== 타임라인 생성 (라벨링 적용) ==========

    def _build_timeline_by_type(self, visited_nodes, path_map, timeline_base_dt, target_date_str, path_type, transport_mode="transport"):
        timeline = []
        if not visited_nodes: return []
        
        start_min = visited_nodes[0].get('arrival_min', 0)
        
        ICONS = {0: "🟢", 1: "🟡", 2: "🔴"} 
        LVL_TXT = {0: "원활", 1: "서행", 2: "정체"}
        POP_TXT = {0: "여유", 1: "보통", 2: "혼잡"}

        # ==========================================
        # [NEW] 1. 첫 번째 장소 처리 (0번 인덱스)
        # ==========================================
        first_node = visited_nodes[0]
        f_arrival_dt = timeline_base_dt + timedelta(minutes=first_node.get('arrival_min', start_min))
        
        # 1-1. 인구 혼잡도 및 체류 시간 라벨링 (첫 장소)
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

        # 1-2. 시간 문자열 생성
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

        # 1-3. 타임라인 추가
        timeline.append({
            "name": first_node['name'],
            "category": first_node["category"],
            "category2": first_node.get("category2", ""),
            "time": f_time_str,
            "transit_to_here": [], 
            "population_level": f_pop_label,
            "traffic_level": f_traffic_str # 계산된 교통 정보 반영
        })

        # ==========================================
        # 2. 나머지 장소 처리 (1번 ~ N번 인덱스)
        # ==========================================
        for i in range(1, len(visited_nodes)):
            prev, node = visited_nodes[i-1], visited_nodes[i]
            transit_info, cur_travel_m = [], 0
            
            # (이하 기존 로직과 동일하지만 cursor_dt는 위에서 갱신된 값을 이어받음)
            arrival_dt = timeline_base_dt + timedelta(minutes=node.get('arrival_min', start_min))
            dest_traffic_lvl = self._get_traffic_level(node.get('lat'), node.get('lng'), arrival_dt)

            # [1] 이동 경로 및 실시간 교통 지연 계산
            if i >= 1: # 항상 True
                path_opts = path_map.get((prev['id'], node['id']))
                if path_opts:
                    chosen = path_opts.get('fastest' if transport_mode == "car" else path_type, [])
                    for seg in chosen:
                        s_min = sum(int(m) for m in re.findall(r'(\d+)분', seg))
                        added, tag = 0, ""
                        
                        if "대기" in seg:
                            origin_lvl = self._get_traffic_level(prev.get('lat'), prev.get('lng'), cursor_dt)
                            added = math.ceil(s_min * self._get_wait_weight(origin_lvl)) - s_min
                            tag = f" [{ICONS.get(origin_lvl)}혼잡 (+{added}분)]" if added > 0 else f" [{ICONS.get(origin_lvl)}혼잡]"
                        elif any(m in seg for m in ["승용차", "버스"]):
                            added = math.ceil(s_min * self._get_travel_time_weight(dest_traffic_lvl, "car" if "승용차" in seg else "bus")) - s_min
                            if transport_mode == "car":
                                added += 12
                            t_txt = LVL_TXT.get(dest_traffic_lvl, "서행")
                            tag = f" [{ICONS.get(dest_traffic_lvl)}{t_txt} (+{added}분)]" if added > 0 else f" [{ICONS.get(dest_traffic_lvl)}{t_txt}]"
                            if transport_mode == "car":
                                tag += " [주차/도보 +12분]"

                        real_m = s_min + added
                        cur_travel_m += real_m
                        transit_info.append(re.sub(r'\d+분', f'{real_m}분', seg) + tag)
                else: 
                    cur_travel_m = self._travel_minutes(prev, node, transport_mode)
                    transit_info.append(f"이동 : {cur_travel_m}분 (정보없음)")
                
                arrival_dt = cursor_dt + timedelta(minutes=cur_travel_m)

            # [2] 스마트 도착 보정 (대기 로직)
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

            # [3] 인구 혼잡도 및 체류 시간 라벨링
            pop_lvl = self._get_population_level(node.get('lat'), node.get('lng'), arrival_dt)
            pop_txt = POP_TXT.get(pop_lvl, "정보없음")
            icon = ICONS.get(pop_lvl, "")
            
            if node["type"] in ["spot", "selected", "lunch", "dinner", "gap_filler"]:
                w_stay = math.ceil(node["stay"] * self._get_stay_weight(pop_lvl))
                add_s, final_stay = w_stay - node["stay"], w_stay
                
                cong_tag = f" [{icon}{pop_txt} (+{add_s}분)]" if add_s > 0 else f" [{icon}{pop_txt}]"
                pop_label = f"인구 {pop_txt}{icon}"
            else:
                final_stay, cong_tag = node["stay"], ""
                pop_label = "고정일정"

            # [4] 결과 조립
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
    
    # ========== 최적화 ==========

    def _optimize_day(self, places, restaurants, fixed_events, start_time_str, target_date_str, end_time_str=None, transport_mode="transport", selected_places=None):
        
        # 1. [설정] 날짜 및 시간 변환 (분 단위)
        base_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        day_start_dt = datetime.strptime(start_time_str, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        start_min = day_start_dt.hour * 60 + day_start_dt.minute
        
        max_horizon = 24 * 60
        if end_time_str:
            end_dt = datetime.strptime(end_time_str, "%H:%M")
            max_horizon = end_dt.hour * 60 + end_dt.minute

        # 2. [노드 빌드] 관광지, 식당, 고정일정 통합 (가상 시작점 없음)
        nodes = self._build_nodes(places, restaurants, fixed_events, day_start_dt, selected_places)
        for idx, node in enumerate(nodes): node["id"] = int(idx)
        n = len(nodes)

        # 3. [체류시간 보정] 재계산이 아닐 경우 여유 시간 부여
        solver_nodes = copy.deepcopy(nodes)
        is_recalculation = any(p.get('window') for p in places if isinstance(p, dict))
        
        if not is_recalculation:
            for node in solver_nodes:
                if node["type"] in ["spot", "lunch", "dinner"]:
                    node["stay"] = int(node["stay"] * 1.2)

        # 4. [이동 시간] 매트릭스 생성 (R5PY + Haversine 보정)
        r5_dep = datetime.combine(base_date, datetime.strptime("11:00", "%H:%M").time())
        r5_times = self._get_r5py_matrix(nodes, r5_dep, transport_mode)

        time_matrix = [[0]*n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j: continue
                
                # R5PY 실패 시 직선거리 기반 추정
                val = r5_times.get((i, j)) or self._travel_minutes(nodes[i], nodes[j])
                
                # 고정 일정 간 이동 최소 시간 보장
                if "fixed" in [nodes[i]["type"], nodes[j]["type"]]:
                    val = max(val, 30)
                
                # 차량 모드 페널티 및 여유 시간(20%) 추가
                if transport_mode == 'car': val += 15
                time_matrix[i][j] = int(val * 1.2)

        # 5. [타임 윈도우] 방문 가능 시간 설정
        l_s, l_e = 690, 840  # 점심 (11:30 ~ 14:00)
        d_s, d_e = 1050, 1200 # 저녁 (17:30 ~ 20:00)
        
        windows = []
        for node in nodes:
            # 재계산 시 고정된 윈도우 우선 적용
            if node.get("window"):
                windows.append(tuple(node["window"]))
            elif node["type"] == "lunch":
                windows.append((l_s, l_e - 10))
            elif node["type"] == "dinner":
                windows.append((d_s, d_e - 10))
            else:
                windows.append((0, max_horizon))

        # 6. [최적화 실행] DFS 탐색
        print(f"[{target_date_str}] 경로 최적화 시작 (노드 {n}개)...")
        start_dfs = time.time()
        
        solver = SimpleRouteSolver(solver_nodes, time_matrix, windows, start_min, max_horizon)
        best_path_indices, arrival_times = solver.solve()
        
        print(f"[{target_date_str}] 최적화 완료 : {round(time.time() - start_dfs, 2)}초")

        if not best_path_indices:
            print(f"유효한 경로를 찾지 못했습니다.")
            return {"fastest_version": [], "min_transfer_version": []}, []

        # 7. [결과 재구성] 방문 순서 정렬 및 도착 시간 주입
        visited_nodes = []
        for idx in best_path_indices:
            node = copy.deepcopy(nodes[idx])
            node['arrival_min'] = arrival_times.get(idx, 0)
            
            # 도착 시간이 오픈 시간보다 빠르면 대기 후 입장
            win_start = windows[idx][0]
            real_start = max(node['arrival_min'], win_start)
            node['departure_min'] = real_start + node.get('stay', 0)
            
            visited_nodes.append(node)

        # 8. [후처리] 틈새 카페(Gap Filler) 삽입
        # 카페 데이터 로드
        df_cafes = pd.DataFrame()
        if self.df_places is not None:
            df_cafes = self.df_places[self.df_places['category'] == '카페'].copy()

        final_nodes = []
        if visited_nodes:
            # 첫 번째 장소는 그대로 추가
            final_nodes.append(visited_nodes[0])
            curr_cursor = visited_nodes[0]['departure_min']

            # 두 번째 장소부터 사이사이 카페 검토
            for i in range(1, len(visited_nodes)):
                next_node = visited_nodes[i]
                travel_min = time_matrix[final_nodes[-1]['id']][next_node['id']]
                expected_arrival = curr_cursor + travel_min
                
                # 다음 장소가 식사/고정 일정이라 시간이 붕 뜨는지 확인
                target_start = windows[next_node['id']][0] if next_node["type"] in ["lunch", "dinner", "fixed"] else None
                
                gap = (target_start - expected_arrival) if target_start else 0
                inserted = False

                # 30분 이상 시간이 남고 카페 데이터가 있다면
                if gap >= 50 and not df_cafes.empty:
                    # 거리 계산 및 가까운 카페 찾기 (0.6km 이내)
                    last_lat, last_lng = final_nodes[-1]['lat'], final_nodes[-1]['lng']
                    
                    # 좌표
                    target_lat, target_lng = next_node['lat'], next_node['lng']
                    
                    # 다음 장소(운동)까지의 거리를 계산
                    df_cafes['dist_to_next'] = df_cafes.apply(
                        lambda r: self._haversine(target_lat, target_lng, r['lat'], r['lng']), 
                        axis=1
                    )

                    candidates = df_cafes[df_cafes['dist_to_next'] <= 0.6].sort_values('dist_to_next')
                    
                    if not candidates.empty:
                        cafe = candidates.iloc[0] # 목적지와 가장 가까운 카페 선택
                        
                        walk_min_to_next = int(cafe['dist_to_next'] / 4 * 60) + 10
                        
                        stay_time = min(gap - walk_min_to_next - 5, 60)
                        
                        if stay_time >= 25: # 최소 20분 이상 앉아있을 수 있다면 추가
                            cafe_node = {
                                "id": 9900 + i,
                                "name": cafe['name'],
                                "type": "gap_filler",
                                "category": "틈새 카페",
                                "category2": cafe.get('category2', '카페'),
                                "lat": cafe['lat'], "lng": cafe['lng'],
                                "stay": int(stay_time), 
                                "arrival_min": expected_arrival # 이전 장소에서 바로 옴
                            }
                            print(f"틈새 카페 추가: {cafe['name']} (목적지까지 {int(cafe['dist_to_next']*1000)}m, 체류 {int(stay_time)}분)")
                            final_nodes.append(cafe_node)
                            
                            curr_cursor = cafe_node['arrival_min'] + stay_time
                            inserted = True

                # 다음 장소 추가 (도착 시간 갱신)
                travel_to_next = self._travel_minutes(final_nodes[-1], next_node, transport_mode) if inserted else travel_min
                next_node['arrival_min'] = curr_cursor + travel_to_next
                final_nodes.append(next_node)
                
                # 커서 업데이트 (다음 장소 출발 시간)
                real_start_next = max(next_node['arrival_min'], windows[next_node['id']][0])
                curr_cursor = real_start_next + next_node['stay']

        # 9. [최종 반환] 타임라인 생성
        timeline_base = datetime.combine(base_date, datetime.min.time())
        trip_legs = [(final_nodes[i], final_nodes[i+1]) for i in range(len(final_nodes)-1)]
        path_map = self._get_all_detailed_paths(trip_legs, r5_dep, transport_mode, cached_times=r5_times)

        result = {
            "fastest_version": self._build_timeline_by_type(final_nodes, path_map, timeline_base, target_date_str, "fastest", transport_mode)
        }
        if transport_mode != "car":
            result["min_transfer_version"] = self._build_timeline_by_type(final_nodes, path_map, timeline_base, target_date_str, "min_transfer", transport_mode)
        
        return result, final_nodes
    
    # ========== Gemini AI 및 기타 유틸 (원본 복구) ==========

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
        
        CAT_MAP = {
                "attraction": "관광지",
                "culture": "문화시설",
                "shopping": "쇼핑",
                "cafe": "카페"
            }
        
        PURPOSE_MAP = {
            "date": "데이트",
            "solo": "혼자 시간",
            "friends": "친구들과",
            "family": "가족 나들이",
            "photo": "사진 찍기",
            "gourmet": "맛집 위주"
        }

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
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt
            )
            plan = self._extract_json(response.text)
            
            with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            
            return plan, 0
        except Exception as e:
            print(f"Gemini API 오류: {e}")
            return None, 0
        
    def reoptimize_day(self, places, restaurants, fixed_events, start_time_str, target_date_str, end_time_str, transport_mode, selected_places):
        if not self.is_initialized: 
            self.initialize_resources()

        """외부 모듈(PlanService 등)에서 특정 날짜의 경로 최적화만 단독으로 재실행할 때 사용"""
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

    def generate_plan(self, request: PlanGenerateRequest):

        total_start_time = time.time()

        if not self.is_initialized: self.initialize_resources()
        if self.df_places is None: return {'error': '장소 데이터를 불러올 수 없습니다'}

        self.detailed_path_cache = {}

        # 1. 반경 검색 및 데이터 필터링 설정
        cols = ["name", "category", "category2", "lat", "lng", "address"]
        center = SEOUL_GU_COORDS.get(request.region, {"lat": 37.57, "lng": 126.98})

        if hasattr(request, 'selected_places') and request.selected_places:
            sp = request.selected_places[0]
            center = {"lat": float(sp['lat']), "lng": float(sp['lng'])}
            print(f"중심점 변경: 선택 장소 기준 ({center['lat']}, {center['lng']})")

        elif request.fixed_events:
            for event in request.fixed_events:
                if isinstance(event, dict):
                    e_lat = event.get('lat')
                    e_lng = event.get('lng') or event.get('lon')
                else:
                    e_lat = getattr(event, 'lat', None)
                    e_lng = getattr(event, 'lng', None) or getattr(event, 'lon', None)
                
                if e_lat is not None and e_lng is not None:
                    center = {"lat": float(e_lat), "lng": float(e_lng)}
                    print(f"중심점 변경: 고정일정 기준 ({center['lat']}, {center['lng']})")
                    break
        
        REDIUS = 5  # km
        df = self.df_places.copy()
        df['dist'] = df.apply(lambda r: self._haversine(center['lat'], center['lng'], r['lat'], r['lng']), axis=1)
        
        start_dt = datetime.strptime(request.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(request.end_date, '%Y-%m-%d')
        total_days = (end_dt - start_dt).days + 1

        sample_limit_places = total_days * 8 * 4 
        sample_limit_res = total_days * 4 * 6 
        sample_limit_acc = total_days * 2 * 6 

        mask = (df['dist'] <= REDIUS) & (~df['category'].isin(['음식점', '숙박']))

        categories = request.categories
        CAT_MAP = { "attraction": "관광지", "culture": "문화시설", "shopping": "쇼핑", "cafe": "카페" }

        if not categories: target_keys = ["attraction", "culture", "shopping", "cafe"]
        else: target_keys = categories

        target_cats = [CAT_MAP.get(c, c) for c in target_keys]
        mask = mask & (df['category'].isin(target_cats))

        filtered_df = df[mask]
        places = []

        for cat, group in filtered_df.groupby('category'):
            if len(group) > sample_limit_places:
                sampled_group = group.sample(n=sample_limit_places)
            else:
                sampled_group = group
            places.extend(sampled_group[cols].to_dict('records'))

        print(f"'{request.region}' 관광지 후보 (샘플링됨): {len(places)}개")

        df_rest = df[(df['dist'] <= REDIUS) & (df['category'] == '음식점')]
        if len(df_rest) > sample_limit_res: df_rest = df_rest.sample(n=sample_limit_res)
        restaurants = df_rest[cols].to_dict('records')
        print(f"'{request.region}' 음식점 후보 (샘플링됨): {len(restaurants)}개")

        df_accom = df[(df['dist'] <= REDIUS) & (df['category'] == '숙박')]
        if len(df_accom) > sample_limit_acc: df_accom = df_accom.sample(n=sample_limit_acc)
        accommodations = df_accom[cols].to_dict('records')
        print(f"'{request.region}' 숙박시설 후보 (샘플링됨): {len(accommodations)}개")
        
        # 3. Gemini 호출
        start_gemini = time.time()
        plan, _ = self._get_gemini_recommendation(total_days, places, restaurants, accommodations, request)
        print(f"Gemini 생성 완료 : {round(time.time() - start_gemini, 2)}초")
        if not plan: return {'error': 'AI 추천 실패'}

        # [핵심] Gemini 결과에 주소(address, addr) 강제 주입
        ref_df = self.df_places.set_index("name")
        name_to_addr = ref_df["address"].to_dict()
        name_to_lat = ref_df["lat"].to_dict()
        name_to_lng = ref_df["lng"].to_dict()
        name_to_cat = ref_df["category"].to_dict()

        for day_key, day_plan in plan['plans'].items():
            if 'route' in day_plan:
                for item in day_plan['route']:
                    name = item['name']
                    # [핵심] DB에 있는 정확한 좌표로 덮어쓰기
                    if name in name_to_lat:
                        item['lat'] = name_to_lat[name]
                        item['lng'] = name_to_lng[name]
                        item['category'] = name_to_cat.get(name, item.get('category')) # 카테고리 보정
                    
                    item['addr'] = name_to_addr.get(name, "")
                    item['type'] = 'spot'
            
            if 'restaurants' in day_plan:
                for item in day_plan['restaurants']:
                    name = item['name']
                    # 식당 좌표도 덮어쓰기
                    if name in name_to_lat:
                        item['lat'] = name_to_lat[name]
                        item['lng'] = name_to_lng[name]
                    
                    item['addr'] = name_to_addr.get(name, "")
                    item['type'] = 'restaurant'

            if 'accommodations' in day_plan:
                for item in day_plan['accommodations']:
                    name = item['name']
                    # 식당 좌표도 덮어쓰기
                    if name in name_to_lat:
                        item['lat'] = name_to_lat[name]
                        item['lng'] = name_to_lng[name]
                    
                    item['addr'] = name_to_addr.get(name, "")
                    item['type'] = 'accommodation'

        # 4. 최적화 루프
        start_opt = time.time()
        print(f"최적화 시작 ({total_days}일, Mode: {request.transport_mode})")
        
        final_result = {k: plan['plans'][k] for k in plan['plans']}
        curr = start_dt
        day_keys = list(plan['plans'].keys())

        global_visited_selected = set()
        
        for i, day_key in enumerate(day_keys):
            # 시간 설정
            if i == 0: day_start_time = request.first_day_start_time 
            else: day_start_time = "10:00"

            if i == len(day_keys) - 1: day_end_time = request.last_day_end_time 
            else: day_end_time = "21:00"

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

            # 선택된 장소 필터링
            available_selected = []
            if request.selected_places:
                available_selected = [
                    sp for sp in request.selected_places 
                    if sp['name'] not in global_visited_selected
                ]

            # [핵심] 최적화 실행
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
                if node.get('type') == 'selected':
                    global_visited_selected.add(node['name'])
                    print(f"[방문 확정] {node['name']} (다음 날부터 제외)")
            
            final_result[day_key]['timelines'] = timelines
        
            # [수정 포인트] 엔진 메타데이터 보존하여 반환
            clean_route_list = []
            for node in updated_nodes:
                if node['type'] == 'depot': continue
                clean_route_list.append({
                    "name": node['name'],
                    "category": node['category'],
                    "category2": node.get('category2', ""),
                    "lat": node['lat'], 
                    "lng": node['lng'],
                    "addr": node.get("addr", ""),
                    "type": node['type'],
                    "stay": node['stay'],
                    "window": list(node['window']) if node.get('window') else None,
                    "orig_time_str": node.get('orig_time_str', "") # 프론트가 기억할 수 있게 넘겨줌
                })
            final_result[day_key]['route'] = clean_route_list
            
            curr += timedelta(days=1)

        print(f"최적화 완료 : {round(time.time() - start_opt, 2)}초")
        total_end_time = time.time()
        print(f"[Total] 전체 프로세스 완료: {total_end_time - total_start_time:.2f}초")

        with open(RESULT_FINAL_PATH, "w", encoding="utf-8") as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)

        return final_result
    
route_service = RouteOptimizerService()