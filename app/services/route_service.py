import os, pickle, re, time, math, json, zipfile, joblib
import multiprocessing
import pandas as pd
import geopandas as gpd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from ortools.constraint_solver import routing_enums_pb2, pywrapcp
from r5py import TransportNetwork, TravelTimeMatrix, DetailedItineraries, TransportMode
from google import genai

from app.schemas.plan import PlanGenerateRequest

# ============================================================
# 환경 및 Java 설정
# ============================================================
available_cores = multiprocessing.cpu_count()
JAVA_PARALLELISM = max(2, available_cores // 2)
os.environ["JAVA_HOME"] = r"C:\Program Files\Java\jdk-21.0.10"
os.environ["JAVA_OPTS"] = f"-Xmx8G -Djava.util.concurrent.ForkJoinPool.common.parallelism={JAVA_PARALLELISM}"

# ============================================================
# 전역 경로 및 상수
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "model")

# 파일 경로 (사용자 환경에 맞게 수정 필요 시 수정)
MODEL_FILE = "./model/congestion_model_latlon.pkl" 
PLACE_FILE = "./data/place_전체_통합_진짜최종.xlsx"
OSM_FILE = "./data/seoul_osm_v.pbf"
GTFS_FILES = ["./data/seoul_area_gtfs.zip"]
TN_CACHE_PATH = "./data/seoul_tn_cached.pkl"
META_CACHE_PATH = "./data/metadata_cache_v2.pkl"
RESULT_JSON_PATH = "result.json"

# 공휴일 정의
KOREAN_HOLIDAYS_2026 = [
    '20260101', '20260216', '20260217', '20260218', '20260301', '20260302',
    '20260505', '20260524', '20260525', '20260606', '20260608', '20260815',
    '20260817', '20260924', '20260925', '20260926', '20261003', '20261005',
    '20261009', '20261225'
]

# 서울 구별 중심 좌표
SEOUL_GU_COORDS = {
    "강남구": {"lat": 37.514575, "lon": 127.0495556},
    "강동구": {"lat": 37.52736667, "lon": 127.1258639},
    "강북구": {"lat": 37.63695556, "lon": 127.0277194},
    "강서구": {"lat": 37.54815556, "lon": 126.851675},
    "관악구": {"lat": 37.47538611, "lon": 126.9538444},
    "광진구": {"lat": 37.53573889, "lon": 127.0845333},
    "구로구": {"lat": 37.49265, "lon": 126.8895972},
    "금천구": {"lat": 37.44910833, "lon": 126.9041972},
    "노원구": {"lat": 37.65146111, "lon": 127.0583889},
    "도봉구": {"lat": 37.66583333, "lon": 127.0495222},
    "동대문구": {"lat": 37.571625, "lon": 127.0421417},
    "동작구": {"lat": 37.50965556, "lon": 126.941575},
    "마포구": {"lat": 37.56070556, "lon": 126.9105306},
    "서대문구": {"lat": 37.57636667, "lon": 126.9388972},
    "서초구": {"lat": 37.48078611, "lon": 127.0348111},
    "성동구": {"lat": 37.56061111, "lon": 127.039},
    "성북구": {"lat": 37.58638333, "lon": 127.0203333},
    "송파구": {"lat": 37.51175556, "lon": 127.1079306},
    "양천구": {"lat": 37.51423056, "lon": 126.8687083},
    "영등포구": {"lat": 37.52361111, "lon": 126.8983417},
    "용산구": {"lat": 37.53609444, "lon": 126.9675222},
    "은평구": {"lat": 37.59996944, "lon": 126.9312417},
    "종로구": {"lat": 37.57037778, "lon": 126.9816417},
    "중구": {"lat": 37.56100278, "lon": 126.9996417},
    "중랑구": {"lat": 37.60380556, "lon": 127.0947778},
}

# 파라미터
FALLBACK_MOVE_MIN = 30
MAX_TRANSFERS = 2
MAX_TRAVEL_TIME_MIN = 90
LUNCH_WINDOW = ("11:20", "13:20")
DINNER_WINDOW = ("17:40", "19:30")

stay_time_map = {
    "관광지": 90, "카페": 50, "음식점": 70,
    "박물관": 120, "공원": 60, "시장": 80, "숙박": 0
}

# ============================================================
# RouteOptimizerService 클래스
# ============================================================
class RouteOptimizerService:
    """서울 여행 경로 최적화 서비스 (싱글톤 패턴)"""
    
    def __init__(self):
        self.is_initialized = False
        self.congestion_model = None
        self.transport_network = None
        self.df_places = None
        self.stop_coords = {}
        self.stop_id_to_name = {}
        self.route_id_to_name = {}
        self.stop_route_map = {}
        self.detailed_path_cache = {}
        self.api_key = None
        self.init_duration = 0

    # ========== 초기화 ==========
    
    def initialize_resources(self):
        """리소스 초기화 (최초 1회만 실행)"""
        if self.is_initialized:
            return
        
        start_t = time.time()
        print("🔄 리소스 초기화 시작...")
        
        load_dotenv()
        self.api_key = os.getenv("GOOGLE_API_KEY")

        # 1) 혼잡도 모델
        print("🧠 혼잡도 예측 모델 로드 중...")
        try:
            if os.path.exists(MODEL_FILE):
                self.congestion_model = joblib.load(MODEL_FILE)
                print("✅ 혼잡도 모델 로드 성공")
        except Exception as e:
            print(f"⚠️ 혼잡도 모델 로드 실패: {e}")
            self.congestion_model = None

        # 2) 장소 데이터
        print("📂 장소 데이터 로드 중...")
        try:
            if os.path.exists(PLACE_FILE):
                self.df_places = pd.read_excel(PLACE_FILE)
                print(f"✅ 장소 데이터 로드: {len(self.df_places)}개")
        except Exception as e:
            print(f"⚠️ 장소 데이터 로드 실패: {e}")
            self.df_places = None

        # 3) 교통 네트워크
        print("🚇 교통 네트워크 로드 중...")
        if os.path.exists(TN_CACHE_PATH):
            try:
                tn = TransportNetwork.__new__(TransportNetwork)
                tn._transport_network = TransportNetwork._load_pickled_transport_network(tn, TN_CACHE_PATH)
                self.transport_network = tn
                print("✅ 교통 네트워크 캐시 로드 완료")
            except Exception as e:
                print(f"⚠️ 캐시 로드 실패, 재생성: {e}")
                self._build_transport_network()
        else:
            self._build_transport_network()

        # 4) 메타데이터
        print("⚡ 메타데이터 로드 중...")
        self._load_metadata()

        self.init_duration = round(time.time() - start_t, 3)
        self.is_initialized = True
        print(f"✅ 리소스 초기화 완료 ({self.init_duration}초)\n")

    def _build_transport_network(self):
        """교통 네트워크 새로 빌드 및 캐싱"""
        print("🚀 TransportNetwork 생성 중 (최초 1회)...")
        self.transport_network = TransportNetwork(OSM_FILE, GTFS_FILES)
        try:
            self.transport_network._save_pickled_transport_network(
                self.transport_network._transport_network,
                TN_CACHE_PATH
            )
            print("💾 교통 네트워크 캐시 저장 완료")
        except Exception as e:
            print(f"⚠️ 캐시 저장 실패: {e}")

    def _load_metadata(self):
        """GTFS 메타데이터 로드 (정류장, 노선 정보)"""
        if os.path.exists(META_CACHE_PATH):
            with open(META_CACHE_PATH, 'rb') as f:
                meta = pickle.load(f)
                self.stop_id_to_name = meta.get('stops', {})
                self.route_id_to_name = meta.get('routes', {})
                self.stop_route_map = meta.get('stop_route_map', {})
                self.stop_coords = meta.get('coords', {})
            print(f"✅ 메타데이터 캐시 로드: 정류장 {len(self.stop_id_to_name)}개")
            return

        print("🐢 메타데이터 생성 중 (좌표 포함)...")
        with zipfile.ZipFile(GTFS_FILES[0]) as z:
            # 정류장 정보
            with z.open('stops.txt') as f:
                stops_df = pd.read_csv(f, dtype={'stop_id': str},
                                       usecols=['stop_id', 'stop_name', 'stop_lat', 'stop_lon'])
            
            self.stop_id_to_name = {
                str(r['stop_id']).strip(): str(r['stop_name']).strip()
                for _, r in stops_df.iterrows()
            }
            
            self.stop_coords = {
                str(r['stop_id']).strip(): {'lat': r['stop_lat'], 'lng': r['stop_lon']}
                for _, r in stops_df.iterrows()
            }

            # 노선 정보
            with z.open('routes.txt') as f:
                routes_df = pd.read_csv(f)
            self.route_id_to_name = dict(zip(
                routes_df['route_id'].astype(str),
                routes_df['route_short_name'].astype(str)
            ))

            # 정류장-노선 매핑
            try:
                with z.open('trips.txt') as f:
                    trips = pd.read_csv(f, usecols=['route_id', 'trip_id'])
                with z.open('stop_times.txt') as f:
                    stop_times = pd.read_csv(f, usecols=['trip_id', 'stop_id'], dtype={'stop_id': str})
                
                merged = stop_times.merge(trips, on='trip_id')[['stop_id', 'route_id']].drop_duplicates()
                self.stop_route_map = merged.groupby('stop_id')['route_id'].apply(set).to_dict()
            except Exception as e:
                print(f"⚠️ 정류장-노선 매핑 실패: {e}")
                self.stop_route_map = {}

        # 캐시 저장
        with open(META_CACHE_PATH, 'wb') as f:
            pickle.dump({
                'stops': self.stop_id_to_name,
                'routes': self.route_id_to_name,
                'stop_route_map': self.stop_route_map,
                'coords': self.stop_coords
            }, f)
        print("💾 메타데이터 캐시 저장 완료")

    # ========== 혼잡도 & 유틸리티 ==========
    
    def _get_congestion_level(self, lat, lng, dt):
        """위치와 시간 기반 혼잡도 예측 (0:여유, 1:보통, 2:혼잡)"""
        if self.congestion_model is None or lat is None or lng is None:
            return 0
        
        # [수정] 모델 학습시 사용한 Feature 순서 및 전처리 방식 일치시킴
        input_vector = pd.DataFrame([[
            dt.month, dt.day, dt.hour, dt.weekday(),
            1 if dt.strftime('%Y%m%d') in KOREAN_HOLIDAYS_2026 else 0,
            1 if dt.weekday() >= 5 else 0,
            lat, lng
        ]], columns=['month', 'day', 'hour', 'dayofweek', 'is_holiday', 'is_weekend', '위도', '경도'])
        
        return int(self.congestion_model.predict(input_vector)[0])

    def _get_stay_weight(self, level):
        if level == 2: return 1.35
        elif level == 1: return 1.2
        else: return 1.0

    def _get_wait_weight(self, level):
        if level == 2: return 2.0
        elif level == 1: return 1.5
        else: return 1.0

    def _haversine(self, lat1, lon1, lat2, lon2):
        if lat1 is None or lat2 is None or lon1 is None or lon2 is None:
            return 0
        R = 6371
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def _travel_minutes(self, p1, p2):
        if p1 is None or p2 is None or p1.get('lat') is None or p2.get('lat') is None:
            return 0
        dist = self._haversine(p1['lat'], p1['lng'], p2['lat'], p2['lng'])
        return int(dist / 30 * 60)

    # ========== R5PY 경로 계산 ==========
    
    def _get_r5py_matrix(self, nodes, departure_time):
        """r5py 이동 시간 행렬 계산 (안전한 노드 필터링 적용)"""
        valid_nodes = [n for n in nodes if n.get('lat') is not None]
        if len(valid_nodes) < 2:
            return {}

        gdf = gpd.GeoDataFrame(
            valid_nodes,
            geometry=gpd.points_from_xy(
                [n['lng'] for n in valid_nodes],
                [n['lat'] for n in valid_nodes]
            ),
            crs='EPSG:4326'
        )

        try:
            matrix = TravelTimeMatrix(
                self.transport_network,
                origins=gdf,
                destinations=gdf,
                departure=departure_time,
                transport_modes=[TransportMode.WALK, TransportMode.TRANSIT]
            )
            
            r5_travel_times = {}
            for row in matrix.itertuples():
                if not pd.isna(row.travel_time):
                    r5_travel_times[(int(row.from_id), int(row.to_id))] = int(row.travel_time)
            
            return r5_travel_times
        except Exception as e:
            print(f"⚠️ r5py 행렬 계산 오류: {e}")
            return {}
    
    
    def _make_cache_key(self, start_node, end_node, departure_time):
        # 좌표 소수점 6자리(약 10cm 오차)까지 사용하여 키 생성
        s_lat = round(start_node['lat'], 6) if start_node.get('lat') else 0
        s_lng = round(start_node['lng'], 6) if start_node.get('lng') else 0
        e_lat = round(end_node['lat'], 6) if end_node.get('lat') else 0
        e_lng = round(end_node['lng'], 6) if end_node.get('lng') else 0
        
        # 이름도 포함하여 안전성 확보
        s_name = start_node.get('name', 'unknown')
        e_name = end_node.get('name', 'unknown')
        
        # 키: (출발이름, 출발위도, 출발경도, 도착이름, 도착위도, 도착경도, 시간)
        return (s_name, s_lat, s_lng, e_name, e_lat, e_lng, departure_time.hour)
    
    def _get_all_detailed_paths(self, trip_legs, departure_time):
        """
        상세 경로 계산 (수정됨: 시간 계산 시 올림(ceil) 처리로 0분 방지)
        """
        if not trip_legs: return {}
        path_map = {}
        origins_list, dests_list = [], []

        # ---------------------------------------------------------
        # 1. 요청할 목록 추리기
        # ---------------------------------------------------------
        for start_node, end_node in trip_legs:
            if start_node['id'] == end_node['id']: continue
            
            ckey = self._make_cache_key(start_node, end_node, departure_time)
            
            if ckey in self.detailed_path_cache:
                path_map[(start_node['id'], end_node['id'])] = self.detailed_path_cache[ckey]
                continue
            
            if start_node.get('lat') is None or end_node.get('lat') is None:
                fallback_msg = [f"이동(좌표없음) : {FALLBACK_MOVE_MIN}분"]
                entry = {"fastest": fallback_msg, "min_transfer": fallback_msg}
                path_map[(start_node['id'], end_node['id'])] = entry
                self.detailed_path_cache[ckey] = entry 
                continue

            origins_list.append(start_node)
            dests_list.append(end_node)

        if not origins_list: return path_map

        # ---------------------------------------------------------
        # 2. r5py 실행
        # ---------------------------------------------------------
        ogdf = gpd.GeoDataFrame(
            origins_list, 
            geometry=gpd.points_from_xy([n['lng'] for n in origins_list], [n['lat'] for n in origins_list]), 
            crs='EPSG:4326'
        )
        ogdf['id'] = [n['id'] for n in origins_list]
        
        dgdf = gpd.GeoDataFrame(
            dests_list, 
            geometry=gpd.points_from_xy([n['lng'] for n in dests_list], [n['lat'] for n in dests_list]), 
            crs='EPSG:4326'
        )
        dgdf['id'] = [n['id'] for n in dests_list]

        try:
            computer = DetailedItineraries(
                self.transport_network, 
                origins=ogdf, 
                destinations=dgdf, 
                departure=departure_time,
                transport_modes=[TransportMode.WALK, TransportMode.TRANSIT],
                max_public_transport_rides=MAX_TRANSFERS, 
                max_time=timedelta(minutes=MAX_TRAVEL_TIME_MIN)
            )
        except Exception as e:
            print(f"⚠️ R5py Error: {e}")
            return path_map

        if computer is None or computer.empty: return path_map

        # ---------------------------------------------------------
        # 3. 결과 파싱 (올림 처리 적용됨)
        # ---------------------------------------------------------
        mode_col = 'transport_mode' if 'transport_mode' in computer.columns else 'mode'

        def get_val(row, candidates, default=None):
            for c in candidates:
                if c in row.index and pd.notna(row[c]): return str(row[c]).strip()
            return default

        # [수정] 시간을 올림 처리하는 헬퍼 함수
        def get_minutes_ceil(val_str):
            if not val_str: return 0
            try:
                seconds = pd.to_timedelta(val_str).total_seconds()
                if seconds <= 0: return 0
                # 올림 처리 (예: 40초 -> 0.66분 -> 1분)
                return math.ceil(seconds / 60)
            except:
                return 0

        def parse_segments(df):
            segs = []
            for _, leg in df.iterrows():
                raw_mode = str(leg[mode_col]).upper()
                
                # [수정] 시간 계산 시 올림 적용 & 최소 1분 보장
                dur_val = get_val(leg, ['travel_time', 'duration'], 0)
                ride_time = get_minutes_ceil(dur_val)
                ride_time = max(1, ride_time) # 0분 방지 (최소 1분)
                
                wait_val = get_val(leg, ['wait_time', 'wait'], 0)
                wait_time = get_minutes_ceil(wait_val)
                # 대기 시간은 0일 수도 있으나, 값이 존재한다면 최소 1분으로 표기
                if wait_time == 0 and pd.to_timedelta(wait_val).total_seconds() > 0:
                    wait_time = 1

                f_id = str(get_val(leg, ['from_stop_id', 'start_stop_id'])).strip()
                t_id = str(get_val(leg, ['to_stop_id', 'end_stop_id'])).strip()

                if wait_time > 0:
                    segs.append(f"대기 : {wait_time}분 [STOP:{f_id}]")

                if 'WALK' in raw_mode:
                    segs.append(f"도보 : {ride_time}분")
                    continue

                f_name = self.stop_id_to_name.get(f_id, "정류장")
                t_name = self.stop_id_to_name.get(t_id, "정류장")
                
                route_id = str(get_val(leg, ['route_id']))
                route_name = self.route_id_to_name.get(route_id, "대중교통")
                
                mode_nm = "지하철" if any(x in raw_mode for x in ['SUBWAY', 'RAIL', 'METRO']) else "버스"
                
                segs.append(f"[{mode_nm}][{route_name}] : {f_name} → {t_name} : {ride_time}분")
            return segs

        for (f_id, t_id), group in computer.groupby(['from_id', 'to_id']):
            s_node = next((n for n in origins_list if n['id'] == int(f_id)), None)
            e_node = next((n for n in dests_list if n['id'] == int(t_id)), None)
            
            if not s_node or not e_node: continue

            safe_key = self._make_cache_key(s_node, e_node, departure_time)

            options_data = []
            for _, opt in group.groupby("option"):
                # [수정] 총 소요 시간 합산 시에도 올림 적용
                total_min = sum(
                    get_minutes_ceil(get_val(leg, ['travel_time', 'duration'], 0))
                    for _, leg in opt.iterrows()
                )
                transfers = sum(1 for _, leg in opt.iterrows() if 'WALK' not in str(leg[mode_col]).upper())
                options_data.append({"route": opt, "time": total_min, "transfers": transfers})

            if not options_data: continue

            fastest_opt = min(options_data, key=lambda x: (x['time'], x['transfers']))
            
            walk_opts = [o for o in options_data if o['transfers'] == 0]
            transit_opts = [o for o in options_data if o['transfers'] > 0]
            
            best_walk = min(walk_opts, key=lambda x: x['time']) if walk_opts else None
            
            if transit_opts:
                transit_opts.sort(key=lambda x: (x['transfers'], x['time']))
                best_transit = transit_opts[0]
            else:
                best_transit = None

            if best_walk and best_transit:
                if best_walk['time'] <= best_transit['time'] + 5: 
                    winner_opt = best_walk
                else:
                    winner_opt = best_transit
            elif best_transit:
                winner_opt = best_transit
            else:
                winner_opt = best_walk

            entry = {
                "fastest": parse_segments(fastest_opt['route']),
                "min_transfer": parse_segments(winner_opt['route']) if winner_opt else [f"도보 : {FALLBACK_MOVE_MIN}분"]
            }

            path_map[(int(f_id), int(t_id))] = entry
            self.detailed_path_cache[safe_key] = entry

        return path_map
    # ========== 노드 빌더 ==========
    
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
        """전체 노드 구성"""
        nodes = []
        first_place = places[0] if places else {"lat": 37.5665, "lng": 126.9780}
        
        # 시작점
        nodes.append({
            "name": "시작점", "category": "출발", "lat": first_place.get("lat"), "lng": first_place.get("lng"),
            "stay": 0, "type": "depot"
        })

        # 관광지
        for p in places:
            nodes.append({
                "name": p["name"], "category": p.get("category", "관광지"), "category2": p.get("category2", ""),
                "lat": p.get("lat"), "lng": p.get("lng"),
                "stay": stay_time_map.get(p.get("category"), 60), "type": "spot"
            })

        # 식당
        if len(restaurants) >= 2:
            nodes.append({
                "name": restaurants[0]["name"], "category": "음식점", "category2": restaurants[0].get("category2", "식당"),
                "lat": restaurants[0].get("lat"), "lng": restaurants[0].get("lng"),
                "stay": 70, "type": "lunch"
            })
            
            dinner_idx = 1 if restaurants[0]["name"] != restaurants[1]["name"] else 2
            if len(restaurants) > dinner_idx:
                nodes.append({
                    "name": restaurants[dinner_idx]["name"], "category": "음식점", "category2": restaurants[dinner_idx].get("category2", "식당"),
                    "lat": restaurants[dinner_idx].get("lat"), "lng": restaurants[dinner_idx].get("lng"),
                    "stay": 70, "type": "dinner"
                })

        # 고정 일정
        nodes.extend(self._build_fixed_nodes(fixed_events, day_start_dt))
        
        return nodes

    def _build_time_windows(self, nodes, day_start_dt):
        """시간 윈도우 생성"""
        windows = []
        def get_rel(t_str):
            return int((datetime.strptime(t_str, "%H:%M") - day_start_dt).total_seconds() / 60)
        
        l_s, l_e = get_rel(LUNCH_WINDOW[0]), get_rel(LUNCH_WINDOW[1])
        d_s, d_e = get_rel(DINNER_WINDOW[0]), get_rel(DINNER_WINDOW[1])

        for n in nodes:
            if n["type"] == "lunch": windows.append((l_s, l_e))
            elif n["type"] == "dinner": windows.append((d_s, d_e))
            elif n["type"] == "fixed": windows.append(n["window"])
            else: windows.append((0, 24 * 60))
        return windows

    # ========== 타임라인 빌더 ==========
    
    def _build_timeline_by_type(self, visited_nodes, path_map, display_start_dt, target_date_str, path_type):
        """
        타임라인 생성
        1. 첫 번째 장소: 이동 없이 사용자 설정 시간에 즉시 시작
        2. 식사 시간: 날짜 기준 윈도우 체크로 저녁 식사 시간 준수
        """
        timeline = []
        
        # visited_nodes[0]은 Depot(가상 시작점)이므로 최소 2개(Depot + 첫장소)는 있어야 함
        if len(visited_nodes) < 2:
            return []

        # 시간 커서 초기화 (사용자가 설정한 그 시간, 예: 10:00)
        cursor_dt = display_start_dt

        # i=1 (첫 번째 실제 방문지) 부터 시작
        for i in range(1, len(visited_nodes)):
            prev = visited_nodes[i-1]
            node = visited_nodes[i]
            
            transit_info = []
            travel_min = 0
            
            # ============================================================
            # 1. 이동 경로 및 시간 계산 (첫 번째 장소 예외 처리 포함)
            # ============================================================
            if i == 1:
                # (A) 첫 번째 장소: 이동 경로 없음, 시작 시간 고정
                transit_info = [] 
                travel_min = 0
                arrival_dt = display_start_dt
                
            else:
                # (B) 두 번째 장소부터: 정상 경로 계산
                path_options = path_map.get((prev['id'], node['id']))
                
                if path_options:
                    chosen_path = path_options.get(path_type, path_options.get('fastest', []))
                    
                    for segment in chosen_path:
                        seg_mins = sum(int(m) for m in re.findall(r'(\d+)분', segment))
                        
                        # 대기 시간 및 혼잡도 처리
                        if "대기" in segment:
                            target_lat, target_lng = None, None
                            stop_match = re.search(r'\[STOP:(.*?)\]', segment)
                            if stop_match:
                                s_id = stop_match.group(1).strip()
                                if s_id in self.stop_coords:
                                    target_lat = self.stop_coords[s_id]['lat']
                                    target_lng = self.stop_coords[s_id]['lng']
                            
                            if target_lat is None:
                                target_lat, target_lng = prev.get('lat'), prev.get('lng')

                            cong_level = self._get_congestion_level(target_lat, target_lng, cursor_dt)
                            weight = self._get_wait_weight(cong_level)
                            
                            weighted_wait = int(seg_mins * weight)
                            added_wait = weighted_wait - seg_mins
                            
                            icons = {0: "🟢", 1: "🟡", 2: "🔴"}
                            cong_icon = icons.get(cong_level, "")

                            clean_segment = re.sub(r'\s*\[STOP:.*?\]', '', segment)
                            clean_segment += f" {cong_icon}"
                            
                            if added_wait > 0:
                                seg_mins = weighted_wait
                                clean_segment += f"(+{added_wait}분)"
                            
                            segment = clean_segment
                        
                        transit_info.append(segment)
                        travel_min += seg_mins
                else:
                    # 폴백 로직
                    lat1, lng1 = prev.get('lat'), prev.get('lng')
                    lat2, lng2 = node.get('lat'), node.get('lng')
                    
                    if None not in [lat1, lng1, lat2, lng2]:
                        dist = self._haversine(lat1, lng1, lat2, lng2)
                        calc_min = int(dist * 15 * 1.3)
                        travel_min = max(5, calc_min)
                        transit_info.append(f"도보 : {travel_min}분 (경로없음)")
                    else:
                        travel_min = FALLBACK_MOVE_MIN
                        transit_info.append(f"이동 : {travel_min}분 (좌표미상)")

                # 도착 시간 계산 (이전 커서 + 이동 시간)
                arrival_dt = cursor_dt + timedelta(minutes=travel_min)

            # ============================================================
            # [수정] 2. 식사 시간 윈도우 체크 (날짜 기준 명확한 비교)
            # ============================================================
            if node["type"] in ["lunch", "dinner"]:
                # 현재 일정의 날짜(YYYY-MM-DD)와 식사 시작 시간(HH:MM)을 결합
                target_window = LUNCH_WINDOW if node["type"] == "lunch" else DINNER_WINDOW
                window_start_str = target_window[0] # "11:20" or "17:40"
                
                # datetime 객체로 변환 (예: 2026-02-07 17:40:00)
                window_start_dt = datetime.strptime(f"{target_date_str} {window_start_str}", "%Y-%m-%d %H:%M")
                
                # 20분 정도 일찍 도착하는 것은 허용
                earliest_start_dt = window_start_dt - timedelta(minutes=20)
                
                # 너무 일찍 도착했으면 식사 시간까지 대기
                if arrival_dt < earliest_start_dt:
                    wait_min = int((window_start_dt - arrival_dt).total_seconds() / 60)
                    transit_info.append(f"현장 대기 : {wait_min}분 (식사 시간 준수)")
                    arrival_dt = window_start_dt

            # ============================================================
            # 3. 체류 시간 및 종료 시간 계산
            # ============================================================
            final_stay_min = node["stay"]
            congestion_label = ""
            
            if node["type"] not in ["fixed", "depot"]:
                cong_level = self._get_congestion_level(node.get('lat'), node.get('lng'), arrival_dt)
                labels = {0: "🟢여유", 1: "🟡보통", 2: "🔴혼잡"}
                
                weight = self._get_stay_weight(cong_level)
                final_stay_min = int(node["stay"] * weight)
                
                # 혼잡도 라벨링 (+추가시간 표시)
                add_min = final_stay_min - node["stay"]
                base_label = labels.get(cong_level, "정보없음")
                congestion_label = f"{base_label}(+{add_min}분)" if add_min > 0 else base_label

            elif node["type"] == "fixed":
                congestion_label = "📅고정"

            # 4. 종료 시간 계산 및 커서 업데이트
            if node["type"] == "fixed":
                time_str = node.get("orig_time_str", "00:00 - 00:00")
                time_parts = time_str.split(" - ")
                cursor_dt = datetime.strptime(f"{target_date_str} {time_parts[1]}", "%Y-%m-%d %H:%M")
            else:
                end_dt = arrival_dt + timedelta(minutes=final_stay_min)
                time_str = f"{arrival_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
                cursor_dt = end_dt # 다음 장소는 여기서부터 시작

            # 5. 결과 저장
            timeline.append({
                "name": node['name'],
                "category": node["category"],
                "category2": node.get("category2", node["category"]),
                "time": time_str,
                "transit_to_here": transit_info,
                "congestion_level": congestion_label
            })
        
        return timeline

    # ========== OR-Tools 최적화 ==========
    
    def _optimize_day(self, places, restaurants, fixed_events, start_time_str, target_date_str, end_time_str=None):
        """단일 일자 경로 최적화"""
        day_start_dt = datetime.strptime(start_time_str, "%H:%M")
        
        r5_dep_dt = datetime.combine(datetime.strptime(target_date_str, "%Y-%m-%d"), datetime.strptime("11:00", "%H:%M").time())
        display_start_dt = datetime.combine(datetime.strptime(target_date_str, "%Y-%m-%d"), day_start_dt.time())

        max_horizon_minutes = 24 * 60
        if end_time_str:
            diff = int((datetime.strptime(end_time_str, "%H:%M") - day_start_dt).total_seconds() / 60)
            if diff > 0:
                max_horizon_minutes = diff

        # 노드 구성
        nodes = self._build_nodes(places, restaurants, fixed_events, day_start_dt)
        for idx, node in enumerate(nodes):
            node["id"] = int(idx)
        n = len(nodes)

        # r5py 이동 시간 행렬
        r5_travel_times = self._get_r5py_matrix(nodes, r5_dep_dt)
        
        # 시간 행렬 구성
        time_matrix = [[0]*n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j: continue
                val = r5_travel_times.get((i, j))
                if val is None:
                    # r5 실패 시 거리 기반 폴백 사용
                    val = self._travel_minutes(nodes[i], nodes[j])
                
                # 고정일정 이동시간 보정
                if nodes[i]["type"] == "fixed" or nodes[j]["type"] == "fixed":
                    if not (nodes[i]["type"] == "depot" and nodes[j]["type"] == "fixed"):
                        val = max(val, 30)
                
                time_matrix[i][j] = nodes[i]["stay"] + int(val)

        # OR-Tools 모델
        manager = pywrapcp.RoutingIndexManager(n, 1, 0)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_idx, to_idx):
            return time_matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

        transit_callback = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback)
        routing.AddDimension(transit_callback, 480, max_horizon_minutes, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")

        time_windows = self._build_time_windows(nodes, day_start_dt)
        solver = routing.solver()

        for i, node in enumerate(nodes):
            index = manager.NodeToIndex(i)
            if node["type"] == "depot":
                continue
            
            window = time_windows[i]
            if node["type"] == "fixed":
                time_dim.CumulVar(index).SetRange(max(0, window[0]), min(max_horizon_minutes, window[1]))
                continue

            overlap_start = max(0, window[0])
            overlap_end = min(max_horizon_minutes, window[1])
            
            if overlap_start > overlap_end:
                routing.AddDisjunction([index], 0)
                solver.Add(routing.VehicleVar(index) == -1)
            else:
                time_dim.CumulVar(index).SetRange(overlap_start, overlap_end)
                penalty = 1000000 if node["type"] in ["lunch", "dinner"] else 100000
                routing.AddDisjunction([index], penalty)

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        solution = routing.SolveWithParameters(search_params)
        
        if not solution:
            print("⚠️ OR-Tools 최적화 실패")
            return {"fastest_version": [], "min_transfer_version": []}

        # 방문 순서 추출
        index = routing.Start(0)
        visited_nodes = []
        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            nodes[node_idx]['arrival_min'] = solution.Value(time_dim.CumulVar(index))
            visited_nodes.append(nodes[node_idx])
            index = solution.Value(routing.NextVar(index))

        # 상세 경로 계산
        trip_legs = [(visited_nodes[i], visited_nodes[i+1]) for i in range(len(visited_nodes)-1)]
        
        print("🚀 상세 경로 계산 중...")
        start_path_time = time.time()
        path_map = self._get_all_detailed_paths(trip_legs, r5_dep_dt)
        print(f"⏱ 상세 경로 계산 완료: {round(time.time() - start_path_time, 2)}초")

        return {
            "fastest_version": self._build_timeline_by_type(visited_nodes, path_map, display_start_dt, target_date_str, "fastest"),
            "min_transfer_version": self._build_timeline_by_type(visited_nodes, path_map, display_start_dt, target_date_str, "min_transfer")
        }

    # ========== Gemini AI 추천 ==========
    
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
            print("📂 result.json 발견 → Gemini 호출 생략")
            try:
                with open(RESULT_JSON_PATH, "r", encoding="utf-8") as f:
                    return json.load(f), 0
            except: pass
        
        if not self.api_key:
            print("⚠️ Google API Key 없음")
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
            print("🤖 Gemini가 초기 계획을 생성하고 있습니다...")
            prompt = system_prompt + "\n\n" + json.dumps(user_prompt, ensure_ascii=False)
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt, config={"temperature": 0}
            )
            plan = self._extract_json(response.text)
            
            with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            
            return plan
        except Exception as e:
            print(f"⚠️ Gemini API 오류: {e}")
            return None, 0

    # ========== 메인 API: generate_plan ==========
    
    def generate_plan(self, request: PlanGenerateRequest):
        """여행 계획 생성 메인 API (좌표 매핑 로직 제거됨)"""
        
        # 1. 초기화 및 리소스 체크
        if not self.is_initialized:
            self.initialize_resources()
        
        if self.df_places is None:
            return {'error': '장소 데이터를 불러올 수 없습니다'}

        # 2. 기본 데이터 준비
        center = SEOUL_GU_COORDS.get(request.region, {"lat": 37.57, "lon": 126.98})
        
        # 3. 장소 데이터 필터링
        df = self.df_places.copy()
        df['distance_km'] = df.apply(
            lambda r: self._haversine(center['lat'], center['lon'], r['lat'], r['lng']), axis=1
        )
        
        RADIUS_KM = 8
        dist_mask = df['distance_km'] <= RADIUS_KM
        
        filtered_spot = df[dist_mask & (df['category'] != '음식점') & (df['category'] != '숙박')][
            ['name', 'lat', 'lng', 'category', 'category2']
        ]
        places = filtered_spot.to_dict(orient='records')
        
        avg_lat = filtered_spot['lat'].mean() if len(filtered_spot) > 0 else center['lat']
        avg_lng = filtered_spot['lng'].mean() if len(filtered_spot) > 0 else center['lon']
        
        df['dist_to_center'] = df.apply(
            lambda r: self._haversine(avg_lat, avg_lng, r['lat'], r['lng']), axis=1
        )
        filtered_restaurant = df[(df['dist_to_center'] <= 3) & (df['category'] == '음식점')][
            ['name', 'lat', 'lng', 'category', 'category2']
        ]
        restaurants = filtered_restaurant.to_dict(orient='records')
        
        filtered_accom = df[dist_mask & (df['category'] == '숙박')][
            ['name', 'lat', 'lng', 'category', 'category2']
        ]
        accommodations = filtered_accom.to_dict(orient='records')

        start_dt = datetime.strptime(request.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(request.end_date, '%Y-%m-%d')
        days = (end_dt - start_dt).days + 1
        print(f"📅 총 여행 일수: {days}일")

        # 4. Gemini AI 추천 호출
        start_gemini = time.time()
        
        # [수정된 부분] 반환값을 변수 2개로 나누어 받습니다 (Unpacking)
        # gemini_plan: 계획 데이터(Dict), _: 소요시간(사용 안함)
        gemini_plan, _ = self._get_gemini_recommendation(days, places, restaurants, accommodations)
        
        print(f"⏱ Gemini 응답 완료: {round(time.time() - start_gemini, 2)}초")
        
        if not gemini_plan or 'plans' not in gemini_plan:
            return {'error': 'AI 추천 실패'}

        # 5. 병렬 경로 최적화
        plans = gemini_plan['plans']
        day_keys = list(plans.keys())
        
        print(f"\n🚀 병렬 최적화 시작: {len(day_keys)}일치 일정 계산")
        start_total_opt = time.time()

        def process_day_wrapper(args):
            day_key, date_obj, is_first, is_last = args
            todays_start = request.first_day_start_time if is_first else "10:00"
            todays_end = request.last_day_end_time if is_last else "21:00"
            current_date_str = date_obj.strftime("%Y-%m-%d")
            
            day_fixed = [e for e in request.fixed_events if e.date == current_date_str]
            
            day_res = self._optimize_day(
                places=plans[day_key]["route"],
                restaurants=plans[day_key]["restaurants"],
                fixed_events=day_fixed,
                start_time_str=todays_start,
                target_date_str=current_date_str,
                end_time_str=todays_end
            )
            return day_key, day_res

        tasks = []
        curr = start_dt
        for i, day_key in enumerate(day_keys):
            tasks.append((day_key, curr, i==0, i==len(day_keys)-1))
            curr += timedelta(days=1)

        processed_results = {}
        max_workers = min(days, 4)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(process_day_wrapper, tasks))
            for day_key, day_res in results:
                processed_results[day_key] = day_res

        opt_duration = round(time.time() - start_total_opt, 2)
        print(f"⏱ 전체 최적화 완료: {opt_duration}초")

        # 6. 결과 취합
        final_result = {}
        for day_key in day_keys:
            final_result[day_key] = {
                'route': plans[day_key]['route'],
                'restaurants': plans[day_key]['restaurants'],
                'accommodations': plans[day_key].get('accommodations', []),
                'timelines': processed_results[day_key]
            }

        return final_result
# 싱글톤 인스턴스
route_service = RouteOptimizerService()
