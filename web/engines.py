import os
import pandas as pd


class MatchEngine:
    def __init__(self, orders_file="delivery_orders_2026.xlsx", drivers_file="drivers_2026.xlsx"):
        self.orders_file = orders_file
        self.drivers_file = drivers_file

    def get_data(self):
        if not os.path.exists(self.orders_file) or not os.path.exists(self.drivers_file):
            return None, None

        df_orders = pd.read_excel(self.orders_file)
        df_drivers = pd.read_excel(self.drivers_file)
        return df_orders, df_drivers

    # ✅ 주문번호는 문자열(예: 26_1) 지원
    def run_matching(self, order_id: str):
        df_orders, df_drivers = self.get_data()

        if df_orders is None or df_drivers is None:
            return None, "데이터 파일이 존재하지 않습니다."

        if "인덱스" not in df_orders.columns:
            return None, "주문 엑셀에 '인덱스' 컬럼이 없습니다."

        order_id = str(order_id).strip()
        order = df_orders[df_orders["인덱스"].astype(str).str.strip() == order_id]
        if order.empty:
            return None, "해당 주문을 찾을 수 없습니다."

        target_order = order.iloc[0]
        target_car = str(target_order.get("차종", "")).strip()

        # ✅ 기본 자격: 탁송보험 "가입 여부" 판단
        # 현재 파일은 '가입/미가입'이 아니라 '보험사명(DB손해/KB손해/현대해상)'이 들어있음
        if "탁송보험" not in df_drivers.columns:
            return None, "기사 엑셀에 '탁송보험' 컬럼이 없습니다."

        insured = (
            df_drivers["탁송보험"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        # 보험사명이 있으면 가입자로 판단
        qualified = df_drivers[insured != ""]

        # 차종에 따른 면허 필터링
        heavy_cars = ['봉고', '포터', '마이티', '카운티', '버스']
        is_heavy = any(car in target_car for car in heavy_cars)

        if is_heavy:
            if "면허종류" not in df_drivers.columns:
                return None, "기사 엑셀에 '면허종류' 컬럼이 없습니다."

            # ✅ 면허 표기 다양성(공백 차이) 대응: "1종 대형" / "1종대형"
            license_s = (
                qualified["면허종류"]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.replace(" ", "", regex=False)
            )

            qualified = qualified[license_s.isin(["1종대형"]) | license_s.str.contains("대형")]

        if qualified.empty:
            return None, "조건(보험/면허)을 만족하는 기사가 없습니다."

        # 단순 추천: 첫 번째 기사
        best = qualified.iloc[0]

        result = {
            "주문번호": order_id,
            "대상차종": target_car,
            "배정기사": str(best.get("이름", "")).strip(),
            "기사연락처": str(best.get("연락처", "")).strip(),
            "면허종류": str(best.get("면허종류", "")).strip(),
            "탁송보험": str(best.get("탁송보험", "")).strip(),  # 보험사명 표시
        }
        return result, None


class DriverTrustEngine:
    def __init__(self, drivers_file="drivers_2026.xlsx"):
        self.drivers_file = drivers_file

    def calculate_reliability(self, row):
        try:
            accidents = int(row.get('사고건수', 0) or 0)
            agency_score = float(row.get('대리점평점', 80) or 80)
            customer_score = float(row.get('고객평점', 80) or 80)
            total_trips = int(row.get('완료횟수', 0) or 0)
        except (ValueError, TypeError):
            return 0.0

        base_score = (agency_score * 0.35) + (customer_score * 0.65)
        penalty = accidents * 30
        bonus = 5 if total_trips >= 100 else 0

        final_score = base_score - penalty + bonus
        return max(0.0, min(100.0, round(final_score, 2)))

    def get_grade(self, score: float) -> str:
        if score >= 90: return "S (최우수)"
        if score >= 80: return "A (우수)"
        if score >= 60: return "B (보통)"
        return "C (관리대상)"

    def update_driver_ranks(self):
        if not os.path.exists(self.drivers_file):
            return None, "기사 데이터 파일이 없습니다."

        df = pd.read_excel(self.drivers_file)

        df['신뢰도점수'] = df.apply(self.calculate_reliability, axis=1)
        df['신뢰도등급'] = df['신뢰도점수'].apply(self.get_grade)

        df = df.sort_values(by='신뢰도점수', ascending=False)

        # 필요 없으면 주석 처리 가능
        df.to_excel(self.drivers_file, index=False)

        return df, None

    def match_best_driver(self):
        df, err = self.update_driver_ranks()
        if err:
            return None, err

        if "탁송보험" not in df.columns:
            return None, "기사 엑셀에 '탁송보험' 컬럼이 없습니다."

        insured = df["탁송보험"].fillna("").astype(str).str.strip()

        # ✅ 보험사명이 있으면 가입자
        eligible = df[insured != ""]
        if eligible.empty:
            return None, "현재 배차 가능한 보험 가입 기사가 없습니다."

        best = eligible.iloc[0]
        result = {
            "기사명": str(best.get("이름", "")).strip(),
            "신뢰도점수": float(best.get("신뢰도점수", 0)),
            "등급": str(best.get("신뢰도등급", "")).strip(),
            "사고이력": f"{best.get('사고건수', 0)}건",
            "연락처": str(best.get("연락처", "")).strip(),
            "탁송보험": str(best.get("탁송보험", "")).strip(),  # 보험사명 표시
        }
        return result, None
