from flask import Flask, render_template, request, jsonify
from engines import MatchEngine, DriverTrustEngine
from sentiment_engine import SentimentEngine

import os
import re
import random
import requests
import folium

import mysql.connector
from mysql.connector import Error

from datetime import datetime

app = Flask(__name__)

match_engine = MatchEngine()
trust_engine = DriverTrustEngine()
sentiment_engine = SentimentEngine()

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "123456")
MYSQL_DB = os.getenv("MYSQL_DB", "project")
ORDERS_TABLE = os.getenv("ORDERS_TABLE", "delivery_orders_2026")

CLEAN_REMARK_SQL = "REPLACE(REPLACE(REPLACE(REPLACE(TRIM(`비고`), '\\r', ''), '\\n', ''), '\\t', ''), ' ', '')"

_ORDER_ID_COL_CACHE = None
_ORDER_ID_COL_CANDIDATES = [
    "order_id", "ORDER_ID",
    "주문번호", "주문ID", "주문_id",
]

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "17cac59d2ab47302628e667cc96e6877")
KAKAO_HEADERS = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}

fallback_cache = {}


def get_db_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
    )


def detect_order_id_column(conn) -> str:
    global _ORDER_ID_COL_CACHE
    if _ORDER_ID_COL_CACHE:
        return _ORDER_ID_COL_CACHE

    cur = conn.cursor()
    cur.execute(f"SHOW COLUMNS FROM `{ORDERS_TABLE}`;")
    cols = cur.fetchall()
    cur.close()

    field_names = [c[0] for c in cols]
    lower_map = {name.lower(): name for name in field_names}

    for cand in _ORDER_ID_COL_CANDIDATES:
        if cand.lower() in lower_map:
            _ORDER_ID_COL_CACHE = lower_map[cand.lower()]
            return _ORDER_ID_COL_CACHE

    for c in cols:
        if (c[3] or "").upper() == "PRI":
            _ORDER_ID_COL_CACHE = c[0]
            return _ORDER_ID_COL_CACHE

    raise RuntimeError(f"주문번호 컬럼을 찾지 못했습니다. 테이블({ORDERS_TABLE}) 컬럼명을 확인하세요.")


def normalize_address(addr):
    if addr is None:
        return None
    s = str(addr).strip()
    if not s:
        return None
    s = re.sub(r"\d+-?\d*$", "", s)  # 번지 제거
    s = s.replace("인근", "").strip()
    return s


def kakao_address_search(query):
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    res = requests.get(url, headers=KAKAO_HEADERS, params={"query": query}, timeout=10).json()
    if res.get("documents"):
        d = res["documents"][0]
        return float(d["y"]), float(d["x"])
    return None


def kakao_keyword_search(query):
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    res = requests.get(url, headers=KAKAO_HEADERS, params={"query": query}, timeout=10).json()
    if res.get("documents"):
        d = res["documents"][0]
        return float(d["y"]), float(d["x"])
    return None


def address_variants(address):
    parts = address.split()
    variants = [address]
    if len(parts) >= 3:
        variants.append(" ".join(parts[:3]))
    if len(parts) >= 2:
        variants.append(" ".join(parts[:2]))
    if len(parts) >= 1:
        variants.append(parts[0])
    return list(dict.fromkeys(variants))


def address_to_coord_auto(address):
    address = normalize_address(address)
    if not address:
        return None

    if address in fallback_cache:
        return fallback_cache[address]

    for query in address_variants(address):
        coord = kakao_address_search(query)
        if coord:
            fallback_cache[address] = coord
            return coord
        coord = kakao_keyword_search(query)
        if coord:
            fallback_cache[address] = coord
            return coord

    return None


def get_real_route(origin, destination):
    lon1, lat1 = origin[1], origin[0]
    lon2, lat2 = destination[1], destination[0]
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}"
        "?overview=full&geometries=geojson"
    )
    data = requests.get(url, timeout=15).json()
    if data.get("code") != "Ok":
        return None, None, None

    route = data["routes"][0]
    coords = [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]
    distance_km = round(route["distance"] / 1000, 1)
    duration_min = int(route["duration"] / 60)
    return coords, distance_km, duration_min


def random_weather():
    return random.choice(["☀️ 맑음", "⛅ 흐림", "☔ 비", "❄️ 눈", "🌫️ 안개"])


def popup_card(title, rows):
    html = f"""
    <div style="width:340px;font-size:14px;line-height:1.6;word-break: keep-all;white-space: normal;">
      <div style="font-weight:bold;font-size:16px;margin-bottom:8px;">{title}</div>
      <table style="width:100%; border-collapse:collapse;">
    """
    for k, v in rows.items():
        html += f"""
        <tr>
          <td style="padding:4px 6px; font-weight:bold; width:35%; vertical-align:top;">{k}</td>
          <td style="padding:4px 6px;">{v if v is not None else ''}</td>
        </tr>
        """
    html += "</table></div>"
    return html


def ensure_static_maps_dir():
    path = os.path.join(app.root_path, "static", "maps")
    os.makedirs(path, exist_ok=True)
    return path


def fetch_order_row(order_id: str) -> dict:
    """주문번호로 1건 조회"""
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        order_col = detect_order_id_column(conn)
        cur.execute(
            f"SELECT * FROM `{ORDERS_TABLE}` WHERE `{order_col}` = %s LIMIT 1",
            (order_id,)
        )
        row = cur.fetchone()
        return row
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def generate_dispatch_map_html(row: dict) -> str:
    """
    주문 row로 folium 지도 HTML 생성 -> static/maps/*.html 저장 후
    브라우저에서 접근 가능한 URL(/static/maps/xxx.html) 반환
    """
    start_addr = row.get("출발지주소")
    end_addr = row.get("도착지주소")

    start = address_to_coord_auto(start_addr)
    end = address_to_coord_auto(end_addr)
    if not start or not end:
        raise RuntimeError("좌표 변환 실패: 출발지/도착지 주소를 확인하세요.")

    route, dist_km, time_min = get_real_route(start, end)
    if route is None:
        raise RuntimeError("도로 경로 생성 실패(OSRM 응답 오류).")

    weather = random_weather()

    m = folium.Map(location=route[0], zoom_start=7, tiles="OpenStreetMap")

    folium.Marker(
        start,
        popup=folium.Popup(
            popup_card("🚩 출발지 정보", {
                "주소": start_addr,
                "연락처": row.get("출발지연락처"),
                "출발시간": row.get("출발시간"),
            }),
            max_width=420
        ),
        icon=folium.Icon(color="green", icon="play")
    ).add_to(m)

    folium.Marker(
        end,
        popup=folium.Popup(
            popup_card("🏁 도착지 정보", {
                "주소": end_addr,
                "연락처": row.get("도착지연락처"),
                "특이사항": row.get("특이사항"),
            }),
            max_width=420
        ),
        icon=folium.Icon(color="red", icon="flag")
    ).add_to(m)

    poly = folium.PolyLine(route, color="blue", weight=6, opacity=0.9).add_to(m)
    m.fit_bounds(poly.get_bounds())

    amount = row.get("탁송금액")
    try:
        amount_txt = f"{int(float(amount)):,} 원" if amount is not None else ""
    except Exception:
        amount_txt = str(amount)

    folium.Marker(
        route[len(route)//2],
        popup=folium.Popup(
            popup_card("🚚 탁송 운행 정보", {
                "차종": row.get("차종"),
                "출발시간": row.get("출발시간"),
                "기상": weather,
                "거리": f"{dist_km} km",
                "예상시간": f"{time_min} 분",
                "금액": amount_txt,
                "특이사항": row.get("특이사항"),
            }),
            max_width=440
        ),
        icon=folium.Icon(color="blue", icon="info-sign")
    ).add_to(m)

    # 저장
    maps_dir = ensure_static_maps_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"dispatch_{stamp}.html"
    filepath = os.path.join(maps_dir, filename)
    m.save(filepath)

    return f"/static/maps/{filename}"


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/ranks")
def ranks_page():
    return render_template("ranks.html")


@app.get("/orders")
def orders_page():
    return render_template("orders.html")


@app.post("/api/match")
def api_match():
    data = request.get_json(force=True, silent=True) or {}
    order_id = (data.get("order_id") or "").strip()

    if not order_id:
        return jsonify({"ok": False, "error": "order_id(주문번호)를 입력하세요. 예: 26_1"}), 400

    result, err = match_engine.run_matching(order_id)
    if err:
        return jsonify({"ok": False, "error": err}), 404

    return jsonify({"ok": True, "data": result})


@app.get("/api/ranks")
def api_ranks():
    df, err = trust_engine.update_driver_ranks()
    if err:
        return jsonify({"ok": False, "error": err}), 404

    cols = [
        c for c in [
            "이름", "연락처", "탁송보험", "사고건수",
            "대리점평점", "고객평점", "완료횟수",
            "신뢰도점수", "신뢰도등급"
        ]
        if c in df.columns
    ]
    top = df[cols].head(30).to_dict(orient="records")
    return jsonify({"ok": True, "data": top})


@app.post("/api/auto_assign")
def api_auto_assign():
    result, err = trust_engine.match_best_driver()
    if err:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify({"ok": True, "data": result})


@app.post("/api/sentiment")
def api_sentiment():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"ok": False, "error": "text(리뷰 문장)를 입력하세요."}), 400

    result, err = sentiment_engine.predict(text)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    return jsonify({"ok": True, "data": result})


@app.get("/api/orders")
def api_orders():
    status = (request.args.get("status") or "").strip()
    limit = int(request.args.get("limit", "50"))
    offset = int(request.args.get("offset", "0"))

    allowed = {"", "대기", "예약", "운행중", "완료"}
    if status not in allowed:
        return jsonify({"ok": False, "error": "status는 대기/예약/운행중/완료 중 하나여야 합니다."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        sql = f"SELECT * FROM `{ORDERS_TABLE}`"
        params = []

        if status:
            sql += f" WHERE {CLEAN_REMARK_SQL} = %s"
            params.append(status)

        order_col = detect_order_id_column(conn)
        sql += f" ORDER BY `{order_col}` ASC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(sql, params)
        rows = cur.fetchall()
        return jsonify({"ok": True, "meta": {"order_id_col": order_col}, "data": rows})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


@app.get("/api/orders/summary")
def api_orders_summary():
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        sql = f"""
            SELECT
                CASE
                    WHEN {CLEAN_REMARK_SQL} = '대기' THEN '대기'
                    WHEN {CLEAN_REMARK_SQL} = '예약' THEN '예약'
                    WHEN {CLEAN_REMARK_SQL} = '운행중' THEN '운행중'
                    WHEN {CLEAN_REMARK_SQL} = '완료' THEN '완료'
                    ELSE '기타'
                END AS status,
                COUNT(*) AS cnt
            FROM `{ORDERS_TABLE}`
            GROUP BY status
            ORDER BY cnt DESC;
        """
        cur.execute(sql)
        rows = cur.fetchall()
        return jsonify({"ok": True, "data": rows})

    except Error as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


@app.post("/api/orders/transition")
def api_orders_transition():
    data = request.get_json(force=True, silent=True) or {}
    order_id = (data.get("order_id") or "").strip()
    action = (data.get("action") or "").strip()

    if not order_id:
        return jsonify({"ok": False, "error": "order_id가 필요합니다. 예: 26_10"}), 400

    transitions = {
        "accept": ("대기", "예약"),
        "start": ("예약", "운행중"),
        "done": ("운행중", "완료"),
    }
    if action not in transitions:
        return jsonify({"ok": False, "error": "action은 accept/start/done 중 하나여야 합니다."}), 400

    from_status, to_status = transitions[action]

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        order_col = detect_order_id_column(conn)

        sql = f"""
            UPDATE `{ORDERS_TABLE}`
            SET `비고` = %s
            WHERE `{order_col}` = %s
              AND {CLEAN_REMARK_SQL} = %s
        """
        cur.execute(sql, (to_status, order_id, from_status))
        conn.commit()

        if cur.rowcount == 0:
            return jsonify({
                "ok": False,
                "error": f"상태 변경 실패: 주문번호({order_id})의 현재 상태가 '{from_status}'가 아니거나 데이터가 없습니다."
            }), 409

        return jsonify({"ok": True, "data": {"order_id": order_id, "from": from_status, "to": to_status}})

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


@app.post("/api/orders/map")
def api_orders_map():
    """
    body: { order_id: "26_10" }
    return: { ok:true, data:{ url:"/static/maps/dispatch_yyyymmdd_hhmmss.html" } }
    """
    data = request.get_json(force=True, silent=True) or {}
    order_id = (data.get("order_id") or "").strip()
    if not order_id:
        return jsonify({"ok": False, "error": "order_id가 필요합니다."}), 400

    try:
        row = fetch_order_row(order_id)
        if not row:
            return jsonify({"ok": False, "error": f"주문을 찾을 수 없습니다: {order_id}"}), 404

        url = generate_dispatch_map_html(row)
        return jsonify({"ok": True, "data": {"url": url}})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    
@app.get("/damage")
def damage_page():
    return render_template("damage.html")


if __name__ == "__main__":
    app.run(debug=True)
