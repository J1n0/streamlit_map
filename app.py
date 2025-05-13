import streamlit as st
import pandas as pd
import requests
import certifi
import json
import folium
import osmnx as ox
import networkx as nx
import polyline
from streamlit_folium import st_folium
from math import radians, sin, cos, sqrt, atan2
from folium import Popup

#TLS 수정
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)
    
session = requests.Session()
session.mount("https://", TLSAdapter())

# 페이지 설정
st.set_page_config(page_title="네비게이션", layout="wide")
st.title("베리어프리 내비게이션 앱")


# API 키 설정
GOOGLE_API_KEY = "AIzaSyCdxL3cf8DA5hfMFL1rvjcGoit92Xmp4OE"
#Google 주소 → 좌표 변환
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
        else:
            st.error(f"Google API 오류: {data.get('status')}")
    except Exception as e:
        st.error(f"예외 발생: {e}")
    return None, None


# 거리 계산 함수
def haversine(lat1, lon1, lat2, lon2):
    R = 6371    #KM단위
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# 공공 화장실 데이터
@st.cache_data
def load_toilet_data():
    return pd.read_excel("data/public_toilets_without_seoul.xlsx").dropna(subset=["WGS84위도", "WGS84경도"])
toilet_df = load_toilet_data()


#병원 데이터
@st.cache_data
def load_hospital_data():
    return pd.read_excel("data/filtered_by_coordinates.xlsx").dropna(subset=["좌표(Y)", "좌표(X)"])
hospital_df = load_hospital_data()





#목적지 초기화
if "destination" not in st.session_state:
    st.session_state["destination"] = None


# 기본 좌표
lat, lon = geocode_address_google("성남 폴리텍대학")
st.markdown(f"**현재 위치**: 위도 {lat}, 경도 {lon}")
dest_name = st.text_input("목적지")
search = st.button("경로 검색")

radius_km = st.slider("검색 반경 (km)", 0.5, 5.0, 1.0)


#현위치 설정
def set_destination(lat, lon):
    st.session_state["destination"] = (lat, lon)



# OSM 계단 회피 경로 탐색
@st.cache_data(show_spinner="OSM 도보 그래프 불러오는 중...")
def get_route_osm(start_lat, start_lon, end_lat, end_lon, dist=3000, avoid_stairs=True):
    G = ox.graph_from_point((start_lat, start_lon), dist=dist, network_type='walk', simplify=True)
    try:
        orig_node = ox.nearest_nodes(G, start_lon, start_lat)
        dest_node = ox.nearest_nodes(G, end_lon, end_lat)
        route = nx.shortest_path(G, orig_node, dest_node, weight="length")
        return [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in route], "OK"
    except Exception as e:
        return None, f"경로 탐색 실패: {e}"

# 지도 생성
map_object = folium.Map(location=[lat, lon], zoom_start=15)
folium.Marker([lat, lon], tooltip="현재 위치", icon=folium.Icon(color="blue")).add_to(map_object)


# 기능 선택 버튼
options = st.multiselect(
    "표시할 정보를 선택하세요",
    ["공공 화장실", "병원", "계단 위치"],
    default=[]
)
# 경로 옵션 선택
route_type = st.radio("경로 옵션을 선택하세요", ["계단 허용", "계단 없는 경로"])
no_stair = (route_type == "계단 없는 경로")


#버튼 누르면 지도변환
if "공공 화장실" in options:
    filtered = toilet_df.copy()
    filtered["거리"] = filtered.apply(lambda row: haversine(lat, lon, row["WGS84위도"], row["WGS84경도"]), axis=1)
    filtered = filtered[filtered["거리"] <= radius_km]
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


#병원
if "병원" in options:
    hospital_df["거리"] = hospital_df.apply(lambda row: haversine(lat, lon, row["좌표(Y)"], row["좌표(X)"]), axis=1)
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
    G_full = ox.graph_from_point((lat, lon), dist=3000, network_type='walk', simplify=True)
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
       

# 목적지 검색
if dest_name:
    dest_lat, dest_lon = geocode_address_google(dest_name)
    if dest_lat and dest_lon:
        if search:
            st.session_state["destination"] = (dest_lat, dest_lon)
    elif search:
        st.warning("주소를 찾을 수 없습니다.")


# 경로 탐색 결과
if st.session_state["destination"]:
    dest_lat, dest_lon = st.session_state["destination"]
    folium.Marker([dest_lat, dest_lon], tooltip="도착지", icon=folium.Icon(color="orange")).add_to(map_object)

    route_coords, status = get_route_osm(lat, lon, dest_lat, dest_lon, dist=3000, avoid_stairs=no_stair)

    st.write("경로 탐색 상태:", status)
    if status == "OK" and route_coords:
        # 선 색상 구분
        color = "blue" if no_stair else "purple"
        folium.PolyLine(route_coords, color=color, weight=5).add_to(map_object)

        total_distance = sum(
            haversine(route_coords[i][0], route_coords[i][1], route_coords[i+1][0], route_coords[i+1][1])
            for i in range(len(route_coords) - 1)
        )
        estimated_time = total_distance / 4.5 * 60

        st.success(f"경로 탐색 완료: {route_type} \n🛣 거리: {total_distance:.2f} km, ⏱ 시간: {estimated_time:.0f}분")
    else:
        st.error(status)
        
        
# 지도 표시
st_folium(map_object, width=750, height=550, returned_objects=[])

st.subheader("병원 리스트 (거리순 정렬)")

types = list(hospital_df["종별코드명"].dropna().unique())
tabs = st.tabs(types)

for tab_name, tab in zip(types, tabs):
    with tab:
        # 해당 종별 병원 필터링
        filtered_df = hospital_df[hospital_df["종별코드명"] == tab_name].copy()

        # 현재 위치와 거리 계산
        filtered_df["거리(km)"] = filtered_df.apply(
            lambda row: haversine(lat, lon, row["좌표(Y)"], row["좌표(X)"]),
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
            
# # 무장애 관광지
# def get_accessible_attractions(lat, lon, radius_km=10):
#     url = "https://apis.data.go.kr/B551011/KorWithService1/locationBasedList1"
#     params = {
#         "MobileOS": "ETC",
#         "MobileApp": "NAVI",
#         "serviceKey": OPEN_TOURISM_API_KEY,
#         "numOfRows": 10,
#         "pageNo": 1,
#         "contentTypeId": 12,
#         "listYN": "Y",
#         "radius": radius_km * 1000,
#         "mapX": lon,
#         "mapY": lat,
#         "_type": "json"
#     }
#     headers = {"User-Agent": "Mozilla/5.0"}
#     res = session.get(url, params=params, headers=headers, verify=False)

#     items = []
#     try:
#         for item in res.json()['response']['body']['items']['item']:
#             items.append({
#                 "이름": item["title"],
#                 "주소": item.get("addr1", ""),
#                 "위도": float(item["mapy"]),
#                 "경도": float(item["mapx"])
#             })
#     except:
#         pass
#     return items


# elif option == "무장애 관광지":
#     if st.button("무장애 관광지 불러오기"):
#         attractions = get_accessible_attractions(lat, lon, radius_km)
#         if attractions:
#             for a in attractions:
#                 folium.Marker([a["위도"], a["경도"]], tooltip=a["이름"],
#                               popup=a["주소"], icon=folium.Icon(color="purple")).add_to(map_object)
#             st.dataframe(pd.DataFrame(attractions))
#         else:
#             st.warning("주변에 무장애 관광지가 없습니다.")


# elif option == "계단 없는 경로":
#     dest = st.text_input("목적지 주소", value="")
#     dest_lat, dest_lon = geocode_address(dest)

#     if dest_lat and dest_lon and st.button("계단 없는 경로 탐색"):
#         result, status = get_directions_with_elevation(lat, lon, dest_lat, dest_lon)

#         if status == "OK":
#             route = result["route"]
#             points = result["polyline"]
#             has_stairs = result["has_stairs"]
#             steps = route["legs"][0]["steps"]

#             st.subheader("🧭 경로 안내")
#             st.warning("⚠️ 계단 포함 가능성이 있는 경로입니다." if has_stairs else "✅ 계단 없는 경로로 예상됩니다.")

#             for idx, step in enumerate(steps):
#                 instruction = step["html_instructions"].replace("<b>", "").replace("</b>", "").replace('<div style="font-size:0.9em">', " ").replace("</div>", "")
#                 st.markdown(f"{idx+1}. {instruction}")

#             folium.Marker([dest_lat, dest_lon], tooltip="도착지", icon=folium.Icon(color="red")).add_to(map_object)
#             folium.PolyLine(points, color="yellow", weight=15).add_to(map_object)
#         else:
#             st.error(status)

#--------------------------------------------병원 api-----이제 안씀
#     hospitals = []
#     try:
#         res = session.get(url, params=params, timeout=5, verify=False)
#         res = session.get(url, params=params, timeout=5, verify=False)

#         # 응답이 JSON이 아닌 경우 (예: XML) 대비
#         if "application/json" in res.headers.get("Content-Type", ""):
#             data = res.json()
#         else:
#             st.error("⚠️ API가 JSON이 아닌 XML로 응답했습니다. 응답 내용 확인:")
#             st.code(res.text)
#             return []
#         res.raise_for_status()
#         data = res.json()
#         items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])

#         for item in items:
#             name = item.get("yadmNm", "이름없음")
#             addr = item.get("addr", "주소없음")
#             lat_ = float(item.get("YPos", 0))
#             lon_ = float(item.get("XPos", 0))
#             tel = item.get("telno", "전화번호 없음")
#             open_time = item.get("dutyTime1s", "")
#             close_time = item.get("dutyTime1c", "")

#             # 운영시간 문자열 구성
#             if open_time and close_time:
#                 open_time_fmt = f"{open_time[:2]}:{open_time[2:]}"
#                 close_time_fmt = f"{close_time[:2]}:{close_time[2:]}"
#                 hours = f"{open_time_fmt} ~ {close_time_fmt}"
#             else:
#                 hours = "운영시간 정보 없음"

#             hospitals.append({
#                 "이름": name,
#                 "주소": addr,
#                 "위도": lat_,
#                 "경도": lon_,
#                 "전화번호": tel,
#                 "운영시간": hours
#             })
#     except Exception as e:
#         st.error(f"병원 정보 조회 실패: {e}")
#     return hospitals
