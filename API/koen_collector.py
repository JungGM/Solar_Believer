"""
API/koen_collector.py
한국남동발전(KOEN) 개별 태양광 발전소(호기)별 시간대별 발전실적 수집기 및 CSV 로더
- 공공데이터포털 Open API (#15119505): https://apis.data.go.kr/B551893/solar-power-by-hour/list
- 파일데이터 CSV (#15167614): 한국남동발전㈜_시간대별_태양광_실적_및_메타데이터_20260831.csv
- 전국 23개 개별 태양광 발전기(호기) ID 매핑, 위경도/기상청 격자(nx, ny)/관측소(stn_id) 및 20MW VPP 포트폴리오 매핑
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .config import APIConfig, NATIONWIDE_REGIONS
from .utils import RobustSession, setup_logger


# 한국남동발전 23개 개별 발전기(발전구분 + 호기 -> 공식 ippt 코드 및 설비/위치 메타데이터)
KOEN_PLANT_MASTER: Dict[Tuple[str, int], Dict[str, Any]] = {
    ("경상대태양광", 1): {
        "ippt": "C005", "region_code": "48", "capacity_kw": 1000.0,
        "latitude": 35.1542, "longitude": 128.0981, "nx": 80, "ny": 75, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("고흥만 수상태양광", 1): {
        "ippt": "K001", "region_code": "46", "capacity_kw": 63000.0,
        "latitude": 34.6485, "longitude": 127.2483, "nx": 67, "ny": 62, "vpp_group": "VPP_GOHEUNG_63MW",
    },
    ("광양항세방태양광", 1): {
        "ippt": "85G1", "region_code": "46", "capacity_kw": 3250.0,
        "latitude": 34.9082, "longitude": 127.6978, "nx": 73, "ny": 70, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("구미태양광", 1): {
        "ippt": "85S5", "region_code": "47", "capacity_kw": 1033.3,
        "latitude": 36.1035, "longitude": 128.3842, "nx": 84, "ny": 96, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("두산엔진MG태양광", 1): {
        "ippt": "85M1", "region_code": "48", "capacity_kw": 75.0,
        "latitude": 35.2054, "longitude": 128.6412, "nx": 90, "ny": 77, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광", 1): {
        "ippt": "85S1", "region_code": "48", "capacity_kw": 100.0,
        "latitude": 34.9056, "longitude": 128.1167, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광", 2): {
        "ippt": "85S2", "region_code": "48", "capacity_kw": 1100.0,
        "latitude": 34.9060, "longitude": 128.1172, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광", 3): {
        "ippt": "85S3", "region_code": "48", "capacity_kw": 400.0,
        "latitude": 34.9064, "longitude": 128.1178, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광", 4): {
        "ippt": "85S4", "region_code": "48", "capacity_kw": 2650.0,
        "latitude": 34.9068, "longitude": 128.1184, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광#5", 1): {
        "ippt": "855A", "region_code": "48", "capacity_kw": 4500.0,
        "latitude": 34.9072, "longitude": 128.1190, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광#5", 2): {
        "ippt": "855B", "region_code": "48", "capacity_kw": 6500.0,
        "latitude": 34.9076, "longitude": 128.1196, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("삼천포태양광#6", 1): {
        "ippt": "85S6", "region_code": "48", "capacity_kw": 1050.0,
        "latitude": 34.9085, "longitude": 128.1210, "nx": 81, "ny": 69, "vpp_group": "VPP_SOUTH_20MW",
    },
    ("여수태양광", 1): {
        "ippt": "997I", "region_code": "46", "capacity_kw": 1800.0,
        "latitude": 34.8390, "longitude": 127.6745, "nx": 73, "ny": 68, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영동태양광", 1): {
        "ippt": "84S1", "region_code": "42", "capacity_kw": 1100.0,
        "latitude": 37.7125, "longitude": 129.0085, "nx": 94, "ny": 129, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영흥태양광", 1): {
        "ippt": "9973", "region_code": "28", "capacity_kw": 1000.0,
        "latitude": 37.2408, "longitude": 126.4363, "nx": 51, "ny": 120, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영흥태양광", 2): {
        "ippt": "997J", "region_code": "28", "capacity_kw": 1000.0,
        "latitude": 37.2415, "longitude": 126.4375, "nx": 51, "ny": 120, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영흥태양광 #3", 1): {
        "ippt": "987A", "region_code": "28", "capacity_kw": 3500.0,
        "latitude": 37.2435, "longitude": 126.4401, "nx": 51, "ny": 120, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영흥태양광 #3", 2): {
        "ippt": "987B", "region_code": "28", "capacity_kw": 1500.0,
        "latitude": 37.2440, "longitude": 126.4410, "nx": 51, "ny": 120, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영흥태양광 #3", 3): {
        "ippt": "987C", "region_code": "28", "capacity_kw": 1800.0,
        "latitude": 37.2445, "longitude": 126.4420, "nx": 51, "ny": 120, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("영흥태양광#5", 1): {
        "ippt": "9875", "region_code": "28", "capacity_kw": 4500.0,
        "latitude": 37.2460, "longitude": 126.4450, "nx": 51, "ny": 120, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("예천태양광", 1): {
        "ippt": "9978", "region_code": "47", "capacity_kw": 2100.0,
        "latitude": 36.5862, "longitude": 128.4528, "nx": 85, "ny": 106, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("탑선태양광", 1): {
        "ippt": "C003", "region_code": "46", "capacity_kw": 3000.0,
        "latitude": 34.5812, "longitude": 126.4895, "nx": 54, "ny": 61, "vpp_group": "VPP_CENTRAL_20MW",
    },
    ("탑선태양광", 3): {
        "ippt": "C004", "region_code": "46", "capacity_kw": 9000.0,
        "latitude": 34.5825, "longitude": 126.4910, "nx": 54, "ny": 61, "vpp_group": "VPP_SOUTH_20MW",
    },
}

IPPT_TO_MASTER: Dict[str, Dict[str, Any]] = {
    v["ippt"]: {"plant_name": k[0], "hogi": k[1], **v}
    for k, v in KOEN_PLANT_MASTER.items()
}


class KOENSolarCollector:
    """
    한국남동발전(KOEN) 개별 발전소(호기)별 시간대별 태양광 발전실적 수집기
    - 23개 개별 발전기(호기) 단위의 시간별 발전량(kWh, MWh) 수집 및 표준화
    - 20MW 목표 VPP 포트폴리오 구성용 메타데이터 생성 지원
    """

    def __init__(self, api_key: Optional[str] = None):
        self.config = APIConfig()
        self.api_key = api_key or self.config.DATA_GO_KR_API_KEY
        self.session = RobustSession(retries=3, backoff_factor=0.5, rate_limit_delay=0.35)
        self.logger = setup_logger("KOENSolarCollector")

    def get_plants_metadata(self) -> pd.DataFrame:
        """23개 개별 태양광 발전기 메타데이터 및 VPP 포트폴리오 배정 테이블 반환"""
        rows = []
        for (plant_name, hogi), meta in KOEN_PLANT_MASTER.items():
            ippt = meta["ippt"]
            reg_code = meta["region_code"]
            reg_info = NATIONWIDE_REGIONS.get(reg_code, NATIONWIDE_REGIONS["48"])
            rows.append({
                "plant_id": f"KOEN_{ippt}",
                "ippt": ippt,
                "plant_name": f"{plant_name} #{hogi}",
                "facility_name": plant_name,
                "hogi": hogi,
                "capacity_kw": meta["capacity_kw"],
                "latitude": meta["latitude"],
                "longitude": meta["longitude"],
                "region_code": reg_code,
                "region_name": reg_info["name"],
                "smp_zone": reg_info["smp_zone"],
                "asos_stn": reg_info["asos_stn"],
                "nx": meta["nx"],
                "ny": meta["ny"],
                "vpp_group": meta["vpp_group"],
                "generation_source": "koen_solar",
            })
        return pd.DataFrame(rows)

    def fetch_range(self, start_ymd: str, end_ymd: str, page_size: int = 100) -> List[Dict[str, Any]]:
        """startD ~ endD 구간의 모든 페이지를 순회하며 수집 (최대 size=100 제약 준수)"""
        s_clean = start_ymd.replace("-", "").strip()
        e_clean = end_ymd.replace("-", "").strip()
        all_items: List[Dict[str, Any]] = []
        page = 1

        while True:
            params = {
                "serviceKey": self.api_key,
                "page": str(page),
                "size": str(min(page_size, 100)),
                "startD": s_clean,
                "endD": e_clean,
            }
            data = self.session.get_json(self.config.KOEN_PV_URL, params=params)
            if not data:
                break

            root = data.get("reponse") or data.get("response") or {}
            header = root.get("header") or {}
            res_code = str(header.get("resultCode", ""))
            if res_code and res_code != "00":
                self.logger.warning(f"[KOENSolar] API 응답 코드 {res_code}: {header.get('resultMsg')}")
                break

            body = root.get("body") or {}
            content = body.get("content") or []
            if isinstance(content, dict):
                content = [content]
            if not content:
                break

            all_items.extend(content)
            is_last = body.get("last", True)
            total_pages = int(body.get("totalPages", 1) or 1)
            if is_last or page >= total_pages:
                break
            page += 1

        return all_items

    def collect(self, start_date: str = "2022-01-01", end_date: Optional[str] = None) -> pd.DataFrame:
        """Open API를 통해 기간 내 모든 개별 발전기 시간대별 발전량 수집"""
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        self.logger.info(f"[KOENSolar] 개별 발전기 API 수집 시작: {start_date} ~ {end_date}")
        cur = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
        end = datetime.strptime(end_date.replace("-", ""), "%Y%m%d")
        all_raw: List[Dict[str, Any]] = []

        try:
            while cur <= end:
                chunk_end = min(cur + timedelta(days=29), end)
                s_str = cur.strftime("%Y%m%d")
                e_str = chunk_end.strftime("%Y%m%d")
                self.logger.info(f"  -> [KOEN 개별발전기 조회] {cur.strftime('%Y-%m-%d')} ~ {chunk_end.strftime('%Y-%m-%d')}")
                items = self.fetch_range(s_str, e_str, page_size=100)
                all_raw.extend(items)
                cur = chunk_end + timedelta(days=1)
        except PermissionError as pe:
            self.logger.warning(f"  ⚠️ {pe} — 지금까지 수집된 {len(all_raw):,}건(일별 행)으로 변환합니다.")

        if not all_raw:
            self.logger.warning("[KOENSolar] API에서 수집된 데이터가 없습니다.")
            return pd.DataFrame()

        raw_df = pd.DataFrame(all_raw)
        return self._unpivot_api_dataframe(raw_df)

    def _unpivot_api_dataframe(self, raw_df: pd.DataFrame) -> pd.DataFrame:
        """API 응답(qhorGen01~qhorGen24)을 시간별 Long-format DataFrame으로 변환"""
        if raw_df.empty:
            return pd.DataFrame()

        hour_cols = [f"qhorGen{h:02d}" for h in range(1, 25) if f"qhorGen{h:02d}" in raw_df.columns]
        id_cols = [c for c in ["dgenYmd", "ippt", "hogi", "ipptNam"] if c in raw_df.columns]
        melted = raw_df.melt(
            id_vars=id_cols,
            value_vars=hour_cols,
            var_name="hour_col",
            value_name="generation_kwh",
        )
        melted["trade_date"] = melted["dgenYmd"].astype(str).str.replace("-", "").str.replace("/", "").str.strip().str[:8]
        melted["trade_hour"] = melted["hour_col"].str.extract(r"(\d+)").astype(int)
        melted["ippt"] = melted["ippt"].astype(str).str.strip()
        melted["hogi"] = pd.to_numeric(melted["hogi"], errors="coerce").fillna(1).astype(int)

        return self._finalize_long_df(melted)

    def load_from_csv(self, file_path: Optional[str] = None, min_date: str = "20220101") -> pd.DataFrame:
        """한국남동발전 시간대별 태양광 실적 및 메타데이터 CSV 로드 및 시간별 표준화"""
        target = file_path or self.config.DEFAULT_KOEN_CSV_PATH
        if not os.path.exists(target):
            import glob
            cands = glob.glob("*남동발전*태양광*.csv") + glob.glob("data/*남동발전*태양광*.csv")
            if cands:
                target = cands[0]
            else:
                self.logger.error(f"남동발전 CSV 파일을 찾을 수 없습니다: {target}")
                return pd.DataFrame()

        self.logger.info(f"[KOENSolar] 로컬 개별 발전기 CSV 로드 중: {target}")
        raw_df = None
        for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
            try:
                raw_df = pd.read_csv(target, encoding=enc, low_memory=False)
                break
            except Exception:
                continue

        if raw_df is None or raw_df.empty:
            return pd.DataFrame()

        raw_df.columns = [str(c).strip() for c in raw_df.columns]
        raw_df["trade_date"] = raw_df["발전일"].astype(str).str.replace("-", "").str.replace("/", "").str.strip().str[:8]
        clean_min = min_date.replace("-", "").strip()
        if clean_min:
            raw_df = raw_df[raw_df["trade_date"] >= clean_min].copy()

        raw_df["facility_name"] = raw_df["발전구분"].astype(str).str.strip()
        raw_df["hogi"] = pd.to_numeric(raw_df["호기"], errors="coerce").fillna(1).astype(int)

        # (발전구분, 호기) -> ippt 매핑
        def _map_ippt(row):
            key = (row["facility_name"], int(row["hogi"]))
            if key in KOEN_PLANT_MASTER:
                return KOEN_PLANT_MASTER[key]["ippt"]
            return f"UNK_{row['facility_name']}_{row['hogi']}"

        raw_df["ippt"] = raw_df.apply(_map_ippt, axis=1)
        raw_df["ipptNam"] = raw_df["facility_name"]

        hour_cols = [f"{h}시발전량" for h in range(1, 25) if f"{h}시발전량" in raw_df.columns]
        melted = raw_df.melt(
            id_vars=["trade_date", "ippt", "hogi", "ipptNam"],
            value_vars=hour_cols,
            var_name="hour_col",
            value_name="generation_kwh",
        )
        melted["trade_hour"] = melted["hour_col"].str.extract(r"(\d+)").astype(int)
        result = self._finalize_long_df(melted)
        self.logger.info(
            f"[KOENSolar] 로컬 CSV 변환 완료: {len(result):,}행 "
            f"({result['date'].min()} ~ {result['date'].max()}, 발전기 {result['plant_id'].nunique()}개)"
        )
        return result

    def _finalize_long_df(self, melted: pd.DataFrame) -> pd.DataFrame:
        """공통 Long-format 데이터프레임 타임스탬프·메타데이터 결합 및 정제"""
        date_str = melted["trade_date"].astype(str).str[:8]
        hour_str = (melted["trade_hour"] - 1).clip(lower=0, upper=23).astype(str).str.zfill(2)
        melted["timestamp"] = pd.to_datetime(
            date_str + " " + hour_str + ":00:00", format="%Y%m%d %H:%M:%S", errors="coerce"
        )
        melted = melted.dropna(subset=["timestamp"])
        melted["date"] = melted["timestamp"].dt.strftime("%Y-%m-%d")
        melted["hour"] = melted["timestamp"].dt.hour

        melted["plant_id"] = "KOEN_" + melted["ippt"].astype(str)
        melted["plant_name"] = melted["ipptNam"].astype(str) + " #" + melted["hogi"].astype(str)

        # 메타데이터(지역코드, 설비용량, VPP 그룹 등) 매핑
        reg_codes = {ippt: info["region_code"] for ippt, info in IPPT_TO_MASTER.items()}
        cap_kws = {ippt: info["capacity_kw"] for ippt, info in IPPT_TO_MASTER.items()}
        vpp_grps = {ippt: info["vpp_group"] for ippt, info in IPPT_TO_MASTER.items()}

        melted["region_code"] = melted["ippt"].map(lambda x: reg_codes.get(x, "48"))
        melted["region_name"] = melted["region_code"].map(lambda c: NATIONWIDE_REGIONS.get(c, {"name": "경남"})["name"])
        melted["capacity_kw"] = melted["ippt"].map(lambda x: cap_kws.get(x, 1000.0))
        melted["vpp_group"] = melted["ippt"].map(lambda x: vpp_grps.get(x, "VPP_SOUTH_20MW"))

        melted["pv_generation_kwh"] = (
            pd.to_numeric(
                melted["generation_kwh"].astype(str).str.replace(",", "").str.strip(), errors="coerce"
            )
            .clip(lower=0.0)
            .round(3)
        )
        melted = melted.dropna(subset=["pv_generation_kwh"])
        melted["pv_generation_mwh"] = (melted["pv_generation_kwh"] / 1000.0).round(6)

        select_cols = [
            "timestamp", "date", "hour", "plant_id", "region_code",
            "capacity_kw", "pv_generation_kwh", "pv_generation_mwh",
        ]
        return (
            melted[select_cols]
            .drop_duplicates(subset=["timestamp", "plant_id"])
            .sort_values(["timestamp", "plant_id"])
            .reset_index(drop=True)
        )
