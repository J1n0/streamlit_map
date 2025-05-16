import streamlit as st
import pandas as pd
import requests
import certifi
import json
import folium
import osmnx as ox
import networkx as nx
import polyline
import os
from dotenv import load_dotenv
from streamlit_folium import st_folium
from math import radians, sin, cos, sqrt, atan2
from folium import Popup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


# TLS 수정: 낮은 보안 설정 강제 적용
class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)

# TLSAdapter 사용한 세션 객체 생성
session = requests.Session()
session.mount("https://", TLSAdapter())

# 페이지 기본 설정
st.set_page_config(page_title="네비게이션", layout="wide")
st.title("베리어프리 내비게이션 앱")

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# 주소 → 위도/경도 변환 함수
@st.cache_data
def geocode_address_google(query):
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": query, "key": GOOGLE_API_KEY}
    try:
        res = requests.get(url, params=params)
        data = res.json()
        if data.get("status") == "OK":
            location = data["results"][0]["geometry"]["location"]
            return location["lat"], location["lng"]
    except:
        pass
    return None, None

# 테스트 지역 체크박스
use_test_area = st.sidebar.checkbox("🧪 테스트 지역으로 보기 (계단 많은 지역)")

# haversine 거리 계산
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# 공공 화장실 데이터 불러오기
@st.cache_data
def load_toilet_data():
    #return pd.read_excel("C:\\Users\\wlema\\wle\\data\\public_toilets_without_seoul.xlsx").dropna(subset=["WGS84위도", "WGS84경도"])
    return pd.read_excel("data\\public_toilets_without_seoul.xlsx").dropna(subset=["WGS84위도", "WGS84경도"])

toilet_df = load_toilet_data()

# 병원 데이터 불러오기
@st.cache_data
def load_hospital_data():
    #return pd.read_excel("C:\\Users\\wlema\\wle\\data\\filtered_by_coordinates.xlsx").dropna(subset=["좌표(Y)", "좌표(X)"])
    return pd.read_excel("data\\filtered_by_coordinates.xlsx").dropna(subset=["좌표(Y)", "좌표(X)"])

hospital_df = load_hospital_data()

# 목적지 초기화
if "destination" not in st.session_state:
    st.session_state["destination"] = None

# 기본 위치 지정
loc_lat, loc_lon = geocode_address_google("성남 폴리텍대학")

# 테스트 지역 선택 시 위치 덮어쓰기
if use_test_area:
    loc_lat, loc_lon = 35.0978, 129.0103  # 감천문화마을 입구
    st.session_state["location"] = (loc_lat, loc_lon)
    st.session_state["destination"] = (35.100602, 129.018518)  # 감천문화마을 반대편

# 입력창 UI
st.markdown(f"**현재 위치**: 위도 {loc_lat}, 경도 {loc_lon}")
loc_name = st.text_input("출발지")
dest_name = st.text_input("목적지")
search = st.button("경로 검색")

# 반경 슬라이더
radius_km = st.slider("검색 반경 (km)", 0.5, 5.0, 1.0)

# 목적지 설정 함수
def set_destination(lat, lon):
    st.session_state["destination"] = (lat, lon)
    st.session_state["from_sidebar"] = True  # 경로 탐색 트리거용 플래그


# OSM 기반 경로 탐색 함수 (계단 회피 포함)
@st.cache_data(show_spinner="OSM 도보 그래프 불러오는 중...")
def get_route_osm(start_lat, start_lon, end_lat, end_lon, dist=3000, avoid_stairs=True):
    G = ox.graph_from_point((start_lat, start_lon), dist=dist, network_type='walk', simplify=True)
    
    # ✅ 계단 회피 옵션이 켜진 경우 계단 edge 제거
    if avoid_stairs:
        edges_to_remove = [
            (u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
            if d.get("highway") == "steps"
        ]
        G.remove_edges_from(edges_to_remove)

    try:
        orig_node = ox.nearest_nodes(G, start_lon, start_lat)
        dest_node = ox.nearest_nodes(G, end_lon, end_lat)
        route = nx.shortest_path(G, orig_node, dest_node, weight="length")
        return [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in route], "OK"
    except Exception as e:
        return None, f"경로 탐색 실패: {e}"

# 테스트가 아닌 경우, 사용자가 입력한 출발지/목적지 적용
if not use_test_area:
    if loc_name:
        loc_lat, loc_lon = geocode_address_google(loc_name)
        if loc_lat and loc_lon and search:
            st.session_state["location"] = (loc_lat, loc_lon)
    else:
        loc_lat, loc_lon = loc_lat, loc_lon  # 기본 위치 유지

    if dest_name:
        dest_lat, dest_lon = geocode_address_google(dest_name)
        if dest_lat and dest_lon and search:
            st.session_state["destination"] = (dest_lat, dest_lon)


# 지도 생성
map_object = folium.Map(location=[loc_lat, loc_lon], zoom_start=15)
folium.Marker([loc_lat, loc_lon], tooltip="현재 위치", icon=folium.Icon(color="blue")).add_to(map_object)


# 기능 선택 버튼
options = st.multiselect(
    "표시할 정보를 선택하세요",
    ["공공 화장실", "병원", "계단 위치"],
    default=[]
)


#버튼 누르면 지도변환
if "공공 화장실" in options:
    filtered = toilet_df.copy()
    filtered["거리"] = filtered.apply(lambda row: haversine(loc_lat, loc_lon, row["WGS84위도"], row["WGS84경도"]), axis=1)
    filtered = filtered[filtered["거리"] <= radius_km].sort_values("거리").reset_index(drop=True)

    for _, row in filtered.iterrows():
        popup_html = f"""
        <b>{row['화장실명']}</b><br>
        {row['소재지도로명주소']}<br>
        {row['개방시간상세']}
        """
        popup = Popup(popup_html, max_width=400)
        folium.Marker(
            location=[row["WGS84위도"], row["WGS84경도"]],
            popup=popup,
            tooltip=row['화장실명'],
            icon=folium.Icon(color="green")
        ).add_to(map_object)

    # 👉 사이드바 선택 인터페이스
    with st.sidebar.expander("🚻 가까운 공공 화장실 리스트", expanded=True):
        selected_toilet = st.selectbox("화장실 선택", filtered["화장실명"].tolist())
        selected_row = filtered[filtered["화장실명"] == selected_toilet].iloc[0]
        st.write(f"📍 {selected_row['소재지도로명주소']}")
        st.write(f"⏰ {selected_row['개방시간상세']}")
        st.write(f"📏 거리: {selected_row['거리']:.2f} km")

        if st.button("🚶 길찾기 (화장실)", key="go_toilet"):
            set_destination(selected_row["WGS84위도"], selected_row["WGS84경도"])
            st.rerun()


#병원
if "병원" in options:
    hospital_df["거리"] = hospital_df.apply(lambda row: haversine(loc_lat, loc_lon, row["좌표(Y)"], row["좌표(X)"]), axis=1)
    hospital_df = hospital_df[hospital_df["거리"] <= radius_km]

    for idx, row in hospital_df.iterrows():
        popup_html = f"""
        <b>{row['요양기관명']}</b><br>
        {row['주소']}<br>
        ☎ {row['전화번호']}
        """
        popup = Popup(popup_html, max_width=500)

        folium.Marker(
            [row["좌표(Y)"], row["좌표(X)"]],
            tooltip=row["요양기관명"],
            popup=popup,
            icon=folium.Icon(color="red")
        ).add_to(map_object)

        
#계단 마커
if "계단 위치" in options:
    G_full = ox.graph_from_point((loc_lat, loc_lon), dist=3000, network_type='walk', simplify=True)
    stair_count = 0
    for u, v, k, d in G_full.edges(keys=True, data=True):
        if d.get("highway") == "steps":
            y = (G_full.nodes[u]["y"] + G_full.nodes[v]["y"]) / 2
            x = (G_full.nodes[u]["x"] + G_full.nodes[v]["x"]) / 2
            folium.Marker(
                location=[y, x],
                tooltip=f"계단: {round(d.get('length', 0), 1)}m",
                icon=folium.Icon(color="black", icon="arrow-down", prefix="fa")
            ).add_to(map_object)
            stair_count += 1
    st.info(f"총 {stair_count}개의 계단 마커를 표시했습니다.")
       


# 경로 탐색 결과
# 목적지 설정 시 두 경로 저장할 공간 초기화
if "routes" not in st.session_state:
    st.session_state["routes"] = {"stairs": None, "no_stairs": None}

# 경로 계산 조건
if st.session_state["destination"] and (search or use_test_area or st.session_state.get("from_sidebar", False)):
    dest_lat, dest_lon = st.session_state["destination"]
    folium.Marker([dest_lat, dest_lon], tooltip="도착지", icon=folium.Icon(color="orange")).add_to(map_object)

    # 계단 허용 경로
    stairs_route, stairs_status = get_route_osm(loc_lat, loc_lon, dest_lat, dest_lon, dist=3000, avoid_stairs=False)
    # 계단 회피 경로
    no_stairs_route, no_stairs_status = get_route_osm(loc_lat, loc_lon, dest_lat, dest_lon, dist=3000, avoid_stairs=True)

    # 결과 저장
    st.session_state["routes"] = {
        "stairs": stairs_route if stairs_status == "OK" else None,
        "no_stairs": no_stairs_route if no_stairs_status == "OK" else None,
    }

    # 플래그 초기화
    if "from_sidebar" in st.session_state:
        del st.session_state["from_sidebar"]

# 🔘 라디오 버튼으로 경로 선택
route_type = st.radio("🔀 지도에 표시할 경로 선택", ["계단 허용", "계단 없는 경로"], horizontal=True)

# 선택한 경로 가져오기
selected_route = st.session_state["routes"].get("stairs" if route_type == "계단 허용" else "no_stairs")

if selected_route:
    color = "purple" if route_type == "계단 허용" else "blue"
    folium.PolyLine(selected_route, color=color, weight=5).add_to(map_object)

    # 거리 계산
    total_distance = sum(
        haversine(selected_route[i][0], selected_route[i][1], selected_route[i + 1][0], selected_route[i + 1][1])
        for i in range(len(selected_route) - 1)
    )
    estimated_time = total_distance / 2.5 * 60  # 노인 속도 기준

    st.success(f"{route_type} 경로 표시 중  \n🛣 거리: {total_distance:.2f} km, ⏱ 예상 시간: {estimated_time:.0f}분")


# from_sidebar 플래그 초기화 (한 번만 탐색되도록)
if "from_sidebar" in st.session_state:
    del st.session_state["from_sidebar"]

        
        
# 지도 표시
st_folium(map_object, width="100%", height=550, returned_objects=[])



st.subheader("병원 리스트 (거리순 정렬)")

types = list(hospital_df["종별코드명"].dropna().unique())
tabs = st.tabs(types)

for tab_name, tab in zip(types, tabs):
    with tab:
        # 해당 종별 병원 필터링
        filtered_df = hospital_df[hospital_df["종별코드명"] == tab_name].copy()

        # 현재 위치와 거리 계산
        filtered_df["거리(km)"] = filtered_df.apply(
            lambda row: haversine(loc_lat, loc_lon, row["좌표(Y)"], row["좌표(X)"]),
            axis=1
        )

        # 거리순 정렬
        filtered_df = filtered_df.sort_values("거리(km)")

        st.markdown(f"**총 {len(filtered_df)}개 병원 (가까운 순)**")

        for i, (_, row) in enumerate(filtered_df.iterrows()):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(
                    f"**{row['요양기관명']}**  \n"
                    f"{row['주소']}  \n"
                    f"거리: {row['거리(km)']:.2f} km"
                )
            with col2:
                if st.button("길찾기 →", key=f"{tab_name}_hospital_{i}"):
                    set_destination(row["좌표(Y)"], row["좌표(X)"])
                    st.rerun()