import os, pickle, re, time, math, json, zipfile, joblib, copy
import multiprocessing
import pandas as pd
import geopandas as gpd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from r5py import TransportNetwork, TravelTimeMatrix, DetailedItineraries, TransportMode
from google import genai

from app.schemas.plan import PlanGenerateRequest

# ============================================================
# 1. 환경 설정 및 상수
# ============================================================
available_cores = multiprocessing.cpu_count()
JAVA_PARALLELISM = max(2, available_cores // 2)
os.environ["JAVA_HOME"] = r"C:\Program Files\Java\jdk-21.0.10"
os.environ["JAVA_OPTS"] = f"-Xmx8G -Djava.util.concurrent.ForkJoinPool.common.parallelism={JAVA_PARALLELISM}"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "model")

# [모델 경로]
TRAFFIC_MODEL_FILE = "./model/traffic_congestion_model_latlon.pkl"     # 이동 시간용 (교통량)
POPULATION_MODEL_FILE = "./model/congestion_model_latlon.pkl"          # 체류/대기용 (인구)

# [데이터 경로]
PLACE_FILE = "./data/place_전체_통합_진짜최종.xlsx"
OSM_FILE = "./data/seoul_osm_v.pbf"
GTFS_FILES = ["./data/seoul_area_gtfs.zip"]
TN_CACHE_PATH = "./data/seoul_tn_cached.pkl"
META_CACHE_PATH = "./data/metadata_cache_v2.pkl"
RESULT_JSON_PATH = "result.json"
RESULT_FINAL_PATH = "result_timeline.json"

KOREAN_HOLIDAYS_2026 = [
    '20260101', '20260216', '20260217', '20260218', '20260301', '20260302',
    '20260505', '20260524', '20260525', '20260606', '20260608', '20260815',
    '20260817', '20260924', '20260925', '20260926', '20261003', '20261005',
    '20261009', '20261225'
]

SEOUL_GU_COORDS = {
    "강남구": {"lat": 37.514575, "lon": 127.0495556}, "강동구": {"lat": 37.52736667, "lon": 127.1258639},
    "강북구": {"lat": 37.63695556, "lon": 127.0277194}, "강서구": {"lat": 37.54815556, "lon": 126.851675},
    "관악구": {"lat": 37.47538611, "lon": 126.9538444}, "광진구": {"lat": 37.53573889, "lon": 127.0845333},
    "구로구": {"lat": 37.49265, "lon": 126.8895972}, "금천구": {"lat": 37.44910833, "lon": 126.9041972},
    "노원구": {"lat": 37.65146111, "lon": 127.0583889}, "도봉구": {"lat": 37.66583333, "lon": 127.0495222},
    "동대문구": {"lat": 37.571625, "lon": 127.0421417}, "동작구": {"lat": 37.50965556, "lon": 126.941575},
    "마포구": {"lat": 37.56070556, "lon": 126.9105306}, "서대문구": {"lat": 37.57636667, "lon": 126.9388972},
    "서초구": {"lat": 37.48078611, "lon": 127.0348111}, "성동구": {"lat": 37.56061111, "lon": 127.039},
    "성북구": {"lat": 37.58638333, "lon": 127.0203333}, "송파구": {"lat": 37.51175556, "lon": 127.1079306},
    "양천구": {"lat": 37.51423056, "lon": 126.8687083}, "영등포구": {"lat": 37.52361111, "lon": 126.8983417},
    "용산구": {"lat": 37.53609444, "lon": 126.9675222}, "은평구": {"lat": 37.59996944, "lon": 126.9312417},
    "종로구": {"lat": 37.57037778, "lon": 126.9816417}, "중구": {"lat": 37.56100278, "lon": 126.9996417},
    "중랑구": {"lat": 37.60380556, "lon": 127.0947778}
}

FALLBACK_MOVE_MIN = 30
MAX_TRANSFERS = 2
MAX_TRAVEL_TIME_MIN = 90
LUNCH_WINDOW = ("11:00", "13:30")
DINNER_WINDOW = ("17:00", "19:30")

stay_time_map = {
    "관광지": 90, "카페": 50, "음식점": 70,
    "박물관": 120, "공원": 60, "시장": 80, "숙박": 0
}

# ============================================================
# 타임라인, 경로 최적화 구현
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
        self.best_score = -1 # 방문 개수가 아닌 '점수'로 비교
        self.best_arrival_times = {}
    
    def solve(self):
        visited = [False] * self.n
        visited[0] = True

        self._dfs(
            curr_idx=0,
            curr_time=self.start_min,
            visited=visited,
            path=[0],
            total_cost=0,
            current_score=0,
            arrival_times={0: self.start_min}
        )

        return self.best_path, self.best_arrival_times

    def _dfs(self, curr_idx, curr_time, visited, path, total_cost, current_score, arrival_times):
        
        # 신기록 달성 (점수가 더 높으면 무조건 교체)
        if current_score > self.best_score:
            self.best_score = current_score
            self.best_cost = total_cost
            self.best_path = list(path)
            self.best_arrival_times = arrival_times.copy()

        # 점수가 같으면? 시간이 덜 걸리는 쪽 선택
        elif current_score == self.best_score:
            if total_cost < self.best_cost:
                self.best_cost = total_cost
                self.best_path = list(path)
                self.best_arrival_times = arrival_times.copy()

        # 탐색 진행
        for next_idx in range(self.n):
            if not visited[next_idx]:
                
                # 1. 이동 시간 및 도착 예측
                travel_time = self.matrix[curr_idx][next_idx]
                arrival = curr_time + travel_time
                win_start, win_end = self.windows[next_idx]

                # (A) 하드 제약: 문 닫으면 못 감
                if arrival > win_end:
                    continue

                # (B) 대기 시간 계산
                wait_time = 0
                if arrival < win_start:
                    wait_time = win_start - arrival
                
                # '식당'이 첫 장소인데 대기가 길다면, 그 시간에 '관광지'를 다녀오는 게 이득이므로 비용을 그대로 둔다.
                node_type = self.nodes[next_idx]["type"]

                # 식당은 오픈런 대기 시간을 '비용'으로 인식시켜서, 가능하다면 앞에 다른 관광지를 끼워 넣도록 유도함
                if len(path) == 1: 
                    if node_type in ["lunch", "dinner"]:
                        pass
                    else:
                        wait_time = 0

                # (C) 식사 대기 페널티 (30분 넘으면 비용 증가, 그러나 방문은 허용)
                penalty_cost = 0

                # [점수 계산] 식사는 10점, 나머지는 1점
                node_score = 10 if node_type in ["lunch", "dinner"] else 1

                if node_type in ["lunch", "dinner"]:
                    if wait_time > 30:
                        penalty_cost = (wait_time - 30) * 10

                # 3. 활동 종료 시간
                if len(path) == 1 and arrival < win_start and node_type not in ["lunch", "dinner"]:
                    start_activity = win_start
                else:
                    start_activity = arrival + wait_time
                
                stay_duration = self.nodes[next_idx]["stay"]
                leave_time = start_activity + stay_duration

                # (D) 하루 종료 시간 초과 체크
                if leave_time > self.max_horizon:
                    continue

                # 4. 재귀 호출
                visited[next_idx] = True
                path.append(next_idx)
                arrival_times[next_idx] = arrival # 실제 도착 시간

                # 점수 누적해서 넘기기
                self._dfs(
                    next_idx, 
                    leave_time, 
                    visited, 
                    path, 
                    total_cost + travel_time + wait_time + penalty_cost, 
                    current_score + node_score, # 점수 추가
                    arrival_times
                )

                # 5. 상태 복구
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
        self.api_key = os.getenv("GOOGLE_API_KEY")

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
        try:
            if os.path.exists(PLACE_FILE):
                self.df_places = pd.read_excel(PLACE_FILE)
                print(f"장소 데이터: {len(self.df_places)}개")
        except: self.df_places = None

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
        if level == 2: return 2.0
        elif level == 1: return 1.5
        else: return 1.0

    def _get_stay_weight(self, level):
        """체류 시간 가중치"""
        if level == 2: return 1.4
        elif level == 1: return 1.2
        else: return 1.0

    def _get_travel_time_weight(self, level, mode="transport"):
        """이동 시간 가중치"""
        if mode == "car":
            if level == 2: return 2.0
            elif level == 1: return 1.7
        elif mode == "bus":
            if level == 2: return 1.8
            elif level == 1: return 1.5
        return 1.2

    def _haversine(self, lat1, lon1, lat2, lon2):
        if lat1 is None or lat2 is None or lon1 is None or lon2 is None: return 0
        R = 6371
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def _travel_minutes(self, p1, p2):
        if p1 is None or p2 is None or p1.get('lat') is None or p2.get('lat') is None: return 0
        dist = self._haversine(p1['lat'], p1['lng'], p2['lat'], p2['lng'])
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
    
    def _make_cache_key(self, start_node, end_node, departure_time):
        return (start_node.get('name'), round(start_node.get('lat',0),6), round(start_node.get('lng',0),6),
                end_node.get('name'), round(end_node.get('lat',0),6), round(end_node.get('lng',0),6), departure_time.hour)
    
    def _get_all_detailed_paths(self, trip_legs, departure_time, transport_mode="transport"):
        if not trip_legs: return {}
        path_map = {}
        origins, dests = [], []
        
        for s, e in trip_legs:
            if s['id'] == e['id']: continue
            ckey = self._make_cache_key(s, e, departure_time)
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
        
        modes = [TransportMode.CAR] if transport_mode == "car" else [TransportMode.WALK, TransportMode.TRANSIT]
        max_rides = 0 if transport_mode == "car" else MAX_TRANSFERS

        try:
            computer = DetailedItineraries(self.transport_network, origins=ogdf, destinations=dgdf, departure=departure_time, transport_modes=modes, max_public_transport_rides=max_rides, max_time=timedelta(minutes=MAX_TRAVEL_TIME_MIN))
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

                if wait_time > 0: segs.append(f"대기 : {wait_time}분")
                
                if 'CAR' in raw_mode: segs.append(f"승용차 이동 : {ride_time}분")
                elif 'WALK' in raw_mode: segs.append(f"도보 : {ride_time}분")
                else:
                    f_id = clean_id(leg.get('from_stop_id') or leg.get('start_stop_id'))
                    t_id = clean_id(leg.get('to_stop_id') or leg.get('end_stop_id'))
                    
                    f_name = self.stop_id_to_name.get(f_id, "정류장")
                    t_name = self.stop_id_to_name.get(t_id, "정류장")
                    
                    mode_nm = "지하철" if any(x in raw_mode for x in ['SUBWAY', 'RAIL', 'METRO']) else "버스"
                    
                    # 버스일 경우 모든 가능한 노선 찾기
                    display_route_name = ""
                    if mode_nm == "버스":
                        # 1. 출발 정류장에 서는 노선들
                        routes_at_start = self.stop_route_map.get(f_id, set())
                        # 2. 도착 정류장에 서는 노선들
                        routes_at_end = self.stop_route_map.get(t_id, set())
                        # 3. 교집합 (두 정류장을 모두 지나는 노선들)
                        common_routes = routes_at_start.intersection(routes_at_end)
                        
                        if common_routes:
                            # ID를 사람이 읽을 수 있는 번호(short_name)로 변환
                            route_names = []
                            for rid in common_routes:
                                r_name = self.route_id_to_name.get(rid)
                                if r_name:
                                    route_names.append(str(r_name))
                            # 정렬 및 중복 제거
                            unique_routes = sorted(list(set(route_names)))
                            display_route_name = ", ".join(unique_routes)
                        else:
                            # 데이터 매핑 실패 시 기존 방식(단일 노선) fallback
                            route_key = clean_id(leg.get('route_id'))
                            display_route_name = self.route_id_to_name.get(route_key, "대중교통")
                    else:
                        # 지하철은 호선이 중요하므로 단일 노선 유지 (또는 동일 로직 적용 가능)
                        route_key = clean_id(leg.get('route_id'))
                        display_route_name = self.route_id_to_name.get(route_key, "대중교통")

                    segs.append(f"[{mode_nm}][{display_route_name}] : {f_name} → {t_name} : {ride_time}분")
            return segs

        for (f_id, t_id), group in computer.groupby(['from_id', 'to_id']):
            s_node = next((n for n in origins if n['id'] == int(f_id)), None)
            e_node = next((n for n in dests if n['id'] == int(t_id)), None)
            if not s_node or not e_node: continue
            
            safe_key = self._make_cache_key(s_node, e_node, departure_time)
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
        """고정 일정 노드 생성"""
        nodes = []
        BUFFER = 15
        for event in fixed_events:
            event_start = datetime.strptime(event.start, "%H:%M") if hasattr(event, 'start') else day_start_dt
            event_end = datetime.strptime(event.end, "%H:%M") if hasattr(event, 'end') else day_start_dt
            
            orig_start_min = int((event_start - day_start_dt).total_seconds() / 60)
            orig_end_min = int((event_end - day_start_dt).total_seconds() / 60)

            raw_start_min = orig_start_min - BUFFER
            buffered_start_min = max(0, raw_start_min)
            
            final_stay = (orig_end_min - orig_start_min) + (orig_start_min - buffered_start_min) + BUFFER

            nodes.append({
                "name": event.title if hasattr(event, 'title') else "고정일정",
                "category": "고정일정",
                "lat": None,
                "lng": None,
                "stay": final_stay,
                "type": "fixed",
                "window": (buffered_start_min, buffered_start_min + 10),
                "orig_time_str": f"{event.start} - {event.end}" if hasattr(event, 'start') else "00:00 - 00:00"
            })
        return nodes

    def _build_nodes(self, places, restaurants, fixed_events, day_start_dt):
        nodes = []

        # 시작점 (Depot) - 첫 번째 장소 근처 혹은 서울 시청 등 (여기선 places의 첫번째 좌표 활용)
        first_loc = places[0] if places else {"lat": 37.5665, "lng": 126.9780}

        nodes.append({
                "name": "시작점",
                "category": "출발",
                "category2": "",
                "lat": first_loc.get("lat"),
                "lng": first_loc.get("lng"),
                "stay": 0,
                "type": "depot"
                })
        
        for p in places:
            nodes.append({
                "name": p["name"],
                "category": p.get("category", "관광지"),
                "category2": p.get("category2", ""),
                "lat": p.get("lat"), "lng": p.get("lng"),
                "stay": stay_time_map.get(p.get("category"), 60),
                "type": "spot"
                })
            
        if len(restaurants) > 0:
            r = restaurants[0]
            nodes.append({
                "name": r["name"],
                "category": "음식점",
                "category2": r.get("category2", "음식점"),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "stay": 70,
                "type": "lunch"
            })
        if len(restaurants) > 1:
            r = restaurants[1]
            nodes.append({
                "name": r["name"],
                "category": "음식점",
                "category2": r.get("category2", "음식점"),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "stay": 70,
                "type": "dinner"
            })
        
        # 고정 일정 빌더 호출
        nodes.extend(self._build_fixed_nodes(fixed_events, day_start_dt))
        return nodes

    # ========== 타임라인 생성 (라벨링 적용) ==========

    def _build_timeline_by_type(self, visited_nodes, path_map, timeline_base_dt, target_date_str, path_type, transport_mode="transport"):
        timeline = []
        if len(visited_nodes) < 2: return []
        
        # 초기값은 기준 날짜의 00:00
        cursor_dt = timeline_base_dt 
        
        ICONS = {0: "🟢", 1: "🟡", 2: "🔴"}

        # 노드 순회 (0번은 출발지이므로 1번부터 실제 방문지)
        for i in range(1, len(visited_nodes)):
            prev, node = visited_nodes[i-1], visited_nodes[i]
            transit_info = []
            
            current_leg_travel_time = 0 # 이번 이동에 걸린 실제 시간 (분)

            if i == 1:
                if 'arrival_min' in node:
                    arrival_dt = timeline_base_dt + timedelta(minutes=node['arrival_min'])
                else:
                    arrival_dt = cursor_dt
            else:
                # 일단 잠시 보류 (경로 계산 후 더할 것임)
                arrival_dt = cursor_dt 

            # [예측용] 도착지 혼잡도 조회 (i > 1일 경우 임시 arrival_dt로 조회)
            dest_traffic_lvl = self._get_traffic_level(node.get('lat'), node.get('lng'), arrival_dt)
            traffic_checked = False

            # 1. 상세 이동 경로 생성 및 '실제 소요 시간' 합산
            if i > 1:
                path_options = path_map.get((prev['id'], node['id']))
                if path_options:
                    chosen_path = path_options.get('fastest' if transport_mode == "car" else path_type, [])
                    
                    for segment in chosen_path:
                        # 원본 소요 시간 파싱
                        seg_mins = sum(int(m) for m in re.findall(r'(\d+)분', segment))
                        
                        added_min = 0
                        status_tag = ""
                        
                        # (A) 대기 시간 지연 계산
                        if "대기" in segment:
                            origin_traffic_lvl = self._get_traffic_level(prev.get('lat'), prev.get('lng'), cursor_dt)
                            if origin_traffic_lvl > 0:
                                weight = self._get_wait_weight(origin_traffic_lvl)
                                final_mins = math.ceil(seg_mins * weight)
                                added_min = final_mins - seg_mins
                                
                                icon = ICONS.get(origin_traffic_lvl, "")
                                status_tag = f" [{icon}]"

                        # (B) 이동 시간 지연 계산
                        elif "승용차" in segment or "버스" in segment:
                            traffic_checked = True
                            if dest_traffic_lvl > 0:
                                mode_key = "car" if "승용차" in segment else "bus"
                                weight = self._get_travel_time_weight(dest_traffic_lvl, mode_key)
                                final_mins = math.ceil(seg_mins * weight)
                                added_min = final_mins - seg_mins
                                
                                t_txt = {1: "서행", 2: "정체"}.get(dest_traffic_lvl, "")
                                icon = ICONS.get(dest_traffic_lvl, "")
                                status_tag = f" [{icon}{t_txt}]"

                        # (C) 텍스트 업데이트 및 시간 누적
                        real_mins = seg_mins + added_min
                        current_leg_travel_time += real_mins # <--- 실제 걸리는 시간 누적

                        if added_min > 0:
                            status_tag = status_tag.replace("]", f" (+{added_min}분)]")
                            segment = re.sub(r'\d+분', f'{real_mins}분', segment)
                        
                        transit_info.append(segment + status_tag)

                else:
                    # 경로 정보가 없을 경우 (직선 거리)
                    est_min = self._travel_minutes(prev, node)
                    current_leg_travel_time += est_min
                    transit_info.append(f"이동 : {est_min}분")
                
                # [중요] 누적된 이동 시간을 더해서 진짜 도착 시간 확정
                arrival_dt = cursor_dt + timedelta(minutes=current_leg_travel_time)

            # 2. 현장 대기 (식당 오픈 대기)
            if node["type"] in ["lunch", "dinner"]:
                t_win = LUNCH_WINDOW if node["type"] == "lunch" else DINNER_WINDOW
                win_start = datetime.strptime(f"{target_date_str} {t_win[0]}", "%Y-%m-%d %H:%M")
                
                # 계산된 arrival_dt가 오픈 시간보다 빠르면 대기
                if arrival_dt < win_start:
                    wait_min = int((win_start - arrival_dt).total_seconds() / 60)
                    if wait_min > 0:
                        transit_info.append(f"남는 시간 : {wait_min}분")
                        arrival_dt = win_start # 대기 후 입장

            # 3. 체류 시간 및 라벨링
            final_stay = node["stay"]
            pop_label = "정보없음"
            time_congestion_info = ""

            if node["type"] not in ["fixed", "depot"]:
                pop_lvl = self._get_population_level(node.get('lat'), node.get('lng'), arrival_dt)
                
                weighted_stay = math.ceil(node["stay"] * self._get_stay_weight(pop_lvl))
                add_stay = weighted_stay - node["stay"]
                final_stay = weighted_stay
                
                pop_txt = {0: "여유", 1: "보통", 2: "혼잡"}.get(pop_lvl, "정보없음")
                icon = ICONS.get(pop_lvl, "")
                pop_label = f"{icon}{pop_txt}"
                
                if add_stay > 0:
                    time_congestion_info = f" [{icon}{pop_txt} (+{add_stay}분)]"
            
            elif node["type"] == "fixed":
                pop_label = "📅고정일정"

            # 4. 타임라인 문자열 조립 및 커서 업데이트
            # Traffic Label
            traffic_label = "-"
            if i == 1: traffic_label = "-"
            elif traffic_checked:
                t_txt = {0: "원활", 1: "서행", 2: "정체"}.get(dest_traffic_lvl, "정보없음")
                t_icon = ICONS.get(dest_traffic_lvl, "")
                traffic_label = f"{t_icon}{t_txt}"
            else:
                traffic_label = "🟢도보/원활"

            if node["type"] == "fixed":
                t_parts = node.get("orig_time_str").split(" - ")
                cursor_dt = datetime.strptime(f"{target_date_str} {t_parts[1]}", "%Y-%m-%d %H:%M")
                time_str = node.get("orig_time_str")
            else:
                end_dt = arrival_dt + timedelta(minutes=final_stay)
                time_str = f"{arrival_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}{time_congestion_info}"
                
                # [중요] 다음 장소는 여기서 끝난 시간부터 출발
                cursor_dt = end_dt

            timeline.append({
                "name": node['name'],
                "category": node["category"],
                "category2": node.get("category2", ""),
                "time": time_str,
                "transit_to_here": transit_info,
                "population_level": pop_label,
                "traffic_level": traffic_label
            })
            
        return timeline
    
    # ========== OR-Tools 최적화 ==========

    def _optimize_day(self, places, restaurants, fixed_events, start_time_str, target_date_str, end_time_str=None, transport_mode="transport"):

        # 1. 날짜 및 시간 설정 (분 단위 변환)
        base_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        day_start_dt = datetime.strptime(start_time_str, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)

        # 하루 시작 시간(분) 예: 10:00 -> 600
        start_min = day_start_dt.hour * 60 + day_start_dt.minute

        # 하루 종료 시간(분)
        max_horizon = 24 * 60
        if end_time_str:
            end_dt = datetime.strptime(end_time_str, "%H:%M")
            max_horizon = end_dt.hour * 60 + end_dt.minute

        # 2. 노드 리스트 생성
        nodes = self._build_nodes(places, restaurants, fixed_events, day_start_dt)
        for idx, node in enumerate(nodes): node["id"] = int(idx)
        n = len(nodes)

        # 실제 타임라인에는 영향을 주지 않고, 계획 단계에서만 시간을 넉넉하게 잡음
        solver_nodes = copy.deepcopy(nodes)
        for node in solver_nodes:
            if node["type"] not in ["fixed", "depot"]:
                node["stay"] = int(node["stay"] * 1.1)

        # 3. 이동 시간 매트릭스 생성 (R5PY + Haversine)
        r5_dep = datetime.combine(base_date, datetime.strptime("11:00", "%H:%M").time())
        r5_times = self._get_r5py_matrix(nodes, r5_dep, transport_mode)

        time_matrix = [[0]*n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j: continue
                # R5PY 결과가 없으면 Haversine 거리 기반 추정
                val = r5_times.get((i, j)) or self._travel_minutes(nodes[i], nodes[j])
                
                # 고정 일정 사이 이동 시 최소 시간 보장 (옵션)
                if nodes[i]["type"] == "fixed" or nodes[j]["type"] == "fixed":
                    if not (nodes[i]["type"] == "depot" and nodes[j]["type"] == "fixed"):
                        val = max(val, 30)
                
                # 이동 시간 20% 여유(Safety Margin) 추가
                val = int(val * 1.2)
                
                time_matrix[i][j] = int(val)
        
        # 타임 윈도우(방문 가능 시간) 설정
        def to_min(t_str):
            dt = datetime.strptime(t_str, "%H:%M")
            return dt.hour * 60 + dt.minute

        l_s, l_e = to_min(LUNCH_WINDOW[0]), to_min(LUNCH_WINDOW[1])
        d_s, d_e = to_min(DINNER_WINDOW[0]), to_min(DINNER_WINDOW[1])

        # 식사 마감 시간 20분 앞당기기 (Safety Margin)
        SAFETY_MARGIN = 20

        windows = []
        for node in nodes:
            if node["type"] == "lunch":
                windows.append((l_s, l_e - SAFETY_MARGIN))
            elif node["type"] == "dinner":
                windows.append((d_s, d_e - SAFETY_MARGIN))
            elif node["type"] == "fixed":
                windows.append(node.get("window", (0, 1440)))
            else:
                windows.append((0, max_horizon))
        
        # 자체 솔버 실행
        print(f"[{target_date_str}] 경로 최적화 진행 중 (노드 {n}개)...")
        solver = SimpleRouteSolver(solver_nodes, time_matrix, windows, start_min, max_horizon)
        best_path_indices, arrival_times = solver.solve()

        # 해를 찾지 못한 경우
        if not best_path_indices:
            print(f"[{target_date_str}] 유효한 경로 X (보수적 조건 적용됨)")
            return {"fastest_version": [], "min_transfer_version": []}

        # 결과 노드 재구성 (방문 순서대로 정렬)
        visited_nodes = []
        for idx in best_path_indices:
            node = nodes[idx]
            # 도착 시간 주입 (SimpleRouteSolver가 계산한 값)
            node['arrival_min'] = arrival_times[idx]
            visited_nodes.append(node)

        # 타임라인 생성 및 상세 경로 정보 부착
        trip_legs = [(visited_nodes[i], visited_nodes[i+1]) for i in range(len(visited_nodes)-1)]
        path_map = self._get_all_detailed_paths(trip_legs, r5_dep, transport_mode)

        # 타임라인 텍스트 생성용 기준 시간
        timeline_base_dt = datetime.combine(base_date, datetime.min.time())

        res_key = "car_version" if transport_mode == "car" else "fastest_version"
        result = {res_key: self._build_timeline_by_type(visited_nodes, path_map, timeline_base_dt, target_date_str, "fastest", transport_mode)}

        if transport_mode != "car":
            result["min_transfer_version"] = self._build_timeline_by_type(visited_nodes, path_map, timeline_base_dt, target_date_str, "min_transfer", transport_mode)
        
        return result

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

    def _get_gemini_recommendation(self, days, places, restaurants, accommodations):
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
                }
            }
        }
        """
        
        system_prompt = f"""
        너는 '서울 여행 장소 추천 전문가'이다. 반드시 제공된 데이터만을 사용하여 계획을 세운다.
        출력 형식: {schema}
        [절대 규칙]
        1. 모든 장소의 이름, 좌표(lat, lng), 카테고리는 입력된 데이터와 100% 일치해야 한다.
        2. 'route' 배열: 제공된 'places' 목록에서 5개를 선택
        3. 'restaurants' 배열: 제공된 'restaurants' 목록에서 2개를 선택
        4. 'accommodations' 배열: 제공된 'accommodations' 목록에서 1개를 선택 (마지막 날은 빈 배열)
        5. 출력: 순수 JSON만 출력
        """

        user_prompt = {
            "days": days, "start_location": {"lat": 37.5547, "lng": 126.9706},
            "places": places, "restaurants": restaurants, "accommodations": accommodations
        }
        
        try:
            print("Gemini가 초기 계획을 생성하고 있습니다...")
            prompt = system_prompt + "\n\n" + json.dumps(user_prompt, ensure_ascii=False)
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt, config={"temperature": 0}
            )
            plan = self._extract_json(response.text)
            
            with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            
            return plan, 0
        except Exception as e:
            print(f"Gemini API 오류: {e}")
            return None, 0

    def generate_plan(self, request: PlanGenerateRequest):

        total_start_time = time.time()

        if not self.is_initialized: self.initialize_resources()
        if self.df_places is None: return {'error': '장소 데이터를 불러올 수 없습니다'}
        
        # 1. 반경 검색 및 데이터 필터링
        center = SEOUL_GU_COORDS.get(request.region, {"lat": 37.57, "lon": 126.98})
        REDIUS = 8
        df = self.df_places.copy()
        df['dist'] = df.apply(lambda r: self._haversine(center['lat'], center['lon'], r['lat'], r['lng']), axis=1)
        
        places = df[(df['dist']<=REDIUS) & (~df['category'].isin(['음식점','숙박']))].to_dict('records')
        print(f"'{request.region}' 중심 반경 {REDIUS}km 내 관광 장소 개수 {len(places)}개")

        restaurants = df[(df['dist']<=REDIUS) & (df['category']=='음식점')].to_dict('records')
        print(f"'{request.region}' 중심 반경 {REDIUS}km 내 음식점 개수 {len(restaurants)}개")

        accommodations = df[(df['dist']<=REDIUS) & (df['category']=='숙박')].to_dict('records')
        print(f"'{request.region}' 중심 반경 {REDIUS}km 내 숙박시설 개수 {len(accommodations)}개")
        
        # 2. 날짜 계산
        start_dt = datetime.strptime(request.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(request.end_date, '%Y-%m-%d')
        total_days = (end_dt - start_dt).days + 1
        
        # 3. Gemini 호출 (장소 추천)
        start_gemini = time.time()
        plan, _ = self._get_gemini_recommendation(total_days, places, restaurants, accommodations)
        print(f"Gemini 생성 완료 : {round(time.time() - start_gemini, 2)}초")
        if not plan: return {'error': 'AI 추천 실패'}

        # 4. 병렬 최적화 (경로 순서 및 시간 계산)
        start_opt = time.time()
        print(f"병렬 최적화 시작 ({total_days}일, Mode: {request.transport_mode})")
        
        tasks = []
        curr = start_dt
        day_keys = list(plan['plans'].keys())
        
        # 날짜별 설정값(Tasks) 생성
        for i, day_key in enumerate(day_keys):
            # (1) 시작 시간 설정
            # 첫날: 사용자 입력 시작 시간
            # 그 외: 아침 10:00
            if i == 0:
                day_start_time = request.first_day_start_time # 예: "14:00"
            else:
                day_start_time = "10:00"

            # (2) 종료 시간 설정
            # 마지막 날: 사용자 입력 종료 시간
            # 그 외: 저녁 21:00
            if i == len(day_keys) - 1:
                day_end_time = request.last_day_end_time # 예: "18:00"
            else:
                day_end_time = "21:00"

            # (3) 고정 일정 필터링
            current_date_str = curr.strftime("%Y-%m-%d")
            daily_fixed_events = [e for e in request.fixed_events if e.date == current_date_str]

            tasks.append((
                day_key,                # 0: day_key (e.g., "day1")
                current_date_str,       # 1: 날짜 문자열
                day_start_time,         # 2: 시작 시간
                day_end_time,           # 3: 종료 시간
                daily_fixed_events      # 4: 고정 일정 리스트
            ))
            
            curr += timedelta(days=1)
            
        # ThreadPoolExecutor로 병렬 실행
        with ThreadPoolExecutor(min(total_days, 4)) as ex:
            # lambda를 통해 _optimize_day 호출
            results = list(ex.map(lambda task: (task[0], self._optimize_day(
                places=plan['plans'][task[0]]['route'], 
                restaurants=plan['plans'][task[0]]['restaurants'], 
                fixed_events=task[4],             # daily_fixed_events
                start_time_str=task[2],           # day_start_time
                target_date_str=task[1],          # current_date_str
                end_time_str=task[3],             # day_end_time
                transport_mode=request.transport_mode
            )), tasks))
            
        # 5. 결과 조합
        final_result = {k: plan['plans'][k] for k in plan['plans']}
        for key, res in results:
            final_result[key]['timelines'] = res

        print(f"병렬 최적화 완료 : {round(time.time() - start_opt, 2)}초")

        total_end_time = time.time()
        print(f"[Total] 전체 프로세스 완료: {total_end_time - total_start_time:.2f}초")

        with open(RESULT_FINAL_PATH, "w", encoding="utf-8") as f:
                json.dump(final_result, f, ensure_ascii=False, indent=2)

        return final_result
route_service = RouteOptimizerService()