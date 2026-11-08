"""
API/kpx_collector.py
전력거래소(KPX) 데이터 수집 모듈

[수집 대상]
1. 한국전력거래소 지역별 시간별 태양광 발전량 (공공데이터포털 #15103243)
   - 엔드포인트: /PvAmountByLocHr/getPvAmountByLocHr
   - 파라미터: tradeYmd=YYYYMMDD (1회 호출로 전국 17개 시도 24시간 반환)
   - 주요 필드: tradeYmd, tradeNo(시), regionNm(지역명), amgo(발전량 MWh)

2. 한국전력거래소 계통한계가격(SMP) 및 수요예측 (공공데이터포털 #15131225)
   - 엔드포인트: /SmpWithForecastDemand/getSmpWithForecastDemand
   - 파라미터: date=YYYYMMDD
"""

import sys
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from .config import APIConfig, NATIONWIDE_REGIONS
from .utils import RobustSession, setup_logger


# ---------------------------------------------------------------------------
# 지역명 별칭 → 시도코드 역매핑
# ---------------------------------------------------------------------------
_ALIAS_TO_CODE: Dict[str, str] = {
    "서울": "11", "서울특별시": "11", "서울시": "11",
    "부산": "26", "부산광역시": "26", "부산시": "26",
    "대구": "27", "대구광역시": "27", "대구시": "27",
    "인천": "28", "인천광역시": "28", "인천시": "28",
    "광주": "29", "광주광역시": "29", "광주시": "29",
    "대전": "30", "대전광역시": "30", "대전시": "30",
    "울산": "31", "울산광역시": "31", "울산시": "31",
    "세종": "36", "세종특별자치시": "36", "세종시": "36",
    "경기": "41", "경기도": "41",
    "강원": "42", "강원도": "42", "강원특별자치도": "42",
    "충북": "43", "충청북도": "43",
    "충남": "44", "충청남도": "44",
    "전북": "45", "전라북도": "45", "전북특별자치도": "45",
    "전남": "46", "전라남도": "46",
    "경북": "47", "경상북도": "47",
    "경남": "48", "경상남도": "48",
    "제주": "49", "제주도": "49", "제주특별자치도": "49",
}


def _resolve_region(raw_name: str):
    """지역명 문자열 → (region_code, region_name) 반환"""
    s = str(raw_name).strip()
    if s in _ALIAS_TO_CODE:
        code = _ALIAS_TO_CODE[s]
        return code, NATIONWIDE_REGIONS.get(code, {}).get("name", s)
    for alias, code in _ALIAS_TO_CODE.items():
        if alias in s:
            return code, NATIONWIDE_REGIONS.get(code, {}).get("name", alias)
    for code, meta in NATIONWIDE_REGIONS.items():
        if meta["name"] in s:
            return code, meta["name"]
    return "00", s


# ---------------------------------------------------------------------------
# 1. KPX 태양광 발전량 수집기
# ---------------------------------------------------------------------------

class KPXSolarCollector:
    """
    전국 17개 시도 KPX 시간별 태양광 발전량 수집기
    - tradeYmd 1일치 호출 → 전국 24시간×17개 시도 408건 반환
    - 실제 응답 키: tradeYmd / tradeNo / regionNm / amgo
    """

    def __init__(self, api_key: Optional[str] = None):
        self.config  = APIConfig()
        self.api_key = api_key or self.config.DATA_GO_KR_API_KEY
        self.session = RobustSession(retries=3, backoff_factor=0.5)
        self.logger  = setup_logger("KPXSolarCollector")

    # ------------------------------------------------------------------
    def fetch_day(self, trade_ymd: str) -> List[Dict[str, Any]]:
        """단일 날짜(YYYYMMDD) 조회 → raw item 리스트 반환"""
        params = {
            "serviceKey": self.api_key,
            "pageNo":     "1",
            "numOfRows":  "1000",
            "dataType":   "JSON",
            "tradeYmd":   trade_ymd.replace("-", "").strip(),
        }
        try:
            res      = self.session.get_json(self.config.KPX_PV_URL, params=params)
            body     = res.get("response", {}).get("body", {}) or res.get("body", {})
            items    = body.get("items", {})
            raw_item = items.get("item", []) if isinstance(items, dict) else items
            item_list = raw_item if isinstance(raw_item, list) else [raw_item]
            return [it for it in item_list if isinstance(it, dict) and it]
        except PermissionError:
            raise
        except Exception as exc:
            self.logger.error(f"KPX 발전량 수집 실패 ({trade_ymd}): {exc}")
            return []

    # ------------------------------------------------------------------
    def collect(
        self,
        start_date: str = "2022-01-01",
        end_date:   Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Returns
        -------
        pd.DataFrame
            columns: timestamp, date, hour, region_code, region_name,
                     plant_id, pv_generation_mwh, pv_generation_kwh
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        self.logger.info(f"[KPXSolar] 수집 시작: {start_date} ~ {end_date} (일별 호출)")
        all_records: List[Dict] = []
        cur = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
        end = datetime.strptime(end_date.replace("-", ""),   "%Y%m%d")
        total_days = (end - cur).days + 1
        day_count = 0

        try:
            while cur <= end:
                day_count += 1
                d_str = cur.strftime("%Y%m%d")
                if day_count % 30 == 1 or day_count == total_days:
                    pct = (day_count / total_days) * 100.0
                    self.logger.info(f"  -> [KPX 발전량 진행] {cur.strftime('%Y-%m-%d')} ({day_count}/{total_days}일, {pct:.1f}%)")

                items = self.fetch_day(d_str)
                all_records.extend(items)
                cur += timedelta(days=1)
        except PermissionError as pe:
            self.logger.warning(f"  ⚠️ {pe} — 지금까지 수집된 {len(all_records):,}건으로 저장합니다.")

        if not all_records:
            self.logger.warning("수집된 KPX 태양광 데이터가 없습니다.")
            return pd.DataFrame(columns=[
                "timestamp", "date", "hour", "region_code", "region_name",
                "plant_id", "pv_generation_mwh", "pv_generation_kwh",
            ])

        df = pd.DataFrame(all_records)
        result = self._standardize_dataframe(df)
        self.logger.info(f"[KPXSolar] 최종 수집: {len(result):,}건")
        return result

    def _standardize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """API 응답 또는 로컬 CSV 데이터의 컬럼/타임스탬프 표준화 및 태양광 필터링"""
        if df.empty:
            return pd.DataFrame()

        # 1. 연료원/발전원 구분이 있는 경우 (예: '태양광 및 풍력' 통합 CSV) 태양광만 즉시 필터링
        fuel_col = next((c for c in df.columns if any(k in str(c) for k in ["연료원", "발전원", "에너지원", "구분", "fuel"])), None)
        if fuel_col:
            df = df[df[fuel_col].astype(str).str.contains("태양광|solar|pv", case=False, na=False)].copy()
            if df.empty:
                self.logger.warning("  ⚠️ 연료원 필터링 결과 태양광 데이터가 없습니다.")
                return pd.DataFrame()

        df = df.rename(columns={
            "tradeYmd":  "trade_date",
            "tradeNo":   "trade_hour",
            "regionNm":  "region_name_raw",
            "amgo":      "pv_generation_mwh",
        })

        for src, dst in [("date", "trade_date"), ("거래일", "trade_date"), ("일자", "trade_date"),
                         ("hour", "trade_hour"), ("거래시간", "trade_hour"), ("시간", "trade_hour"),
                         ("region", "region_name_raw"), ("지역", "region_name_raw"),
                         ("amount", "pv_generation_mwh"), ("발전량", "pv_generation_mwh"), ("거래량", "pv_generation_mwh")]:
            if dst not in df.columns:
                for c in df.columns:
                    if src in str(c).lower():
                        df = df.rename(columns={c: dst})
                        break

        df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "").str.replace("/", "").str.strip()
        df["trade_hour"] = pd.to_numeric(df["trade_hour"], errors="coerce").fillna(1).astype(int)

        date_str = df["trade_date"].astype(str).str[:8]
        hour_str = (df["trade_hour"] - 1).clip(lower=0, upper=23).astype(str).str.zfill(2)
        df["timestamp"] = pd.to_datetime(
            date_str + " " + hour_str + ":00:00", format="%Y%m%d %H:%M:%S", errors="coerce"
        )
        df = df.dropna(subset=["timestamp"])
        df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        df["hour"] = df["timestamp"].dt.hour

        df[["region_code", "region_name"]] = (
            df["region_name_raw"]
            .apply(lambda x: pd.Series(_resolve_region(x)))
        )
        df["plant_id"] = "KPX_PV_" + df["region_code"]
        df["pv_generation_mwh"] = pd.to_numeric(
            df.get("pv_generation_mwh", 0), errors="coerce"
        ).fillna(0.0).clip(lower=0.0)
        df["pv_generation_kwh"] = df["pv_generation_mwh"] * 1000.0

        select = ["timestamp", "date", "hour", "region_code", "region_name",
                  "plant_id", "pv_generation_mwh", "pv_generation_kwh"]
        result = (
            df[select]
            .drop_duplicates(subset=["timestamp", "region_code"])
            .sort_values(["timestamp", "region_code"])
            .reset_index(drop=True)
        )
        return result

    def load_from_csv(self, file_path_or_dir: Any = None, min_date: str = "20220101") -> pd.DataFrame:
        """
        다운로드한 태양광 발전량(또는 태양광 및 풍력 통합) CSV 파일들을 표준 DataFrame으로 일괄 변환.
        - 단일 파일, 복수 파일 리스트, 폴더 경로, 또는 자동 탐색 지원
        - 풍력 데이터 자동 필터링 (연료원=='태양광' 또는 태양광발전량 컬럼 자동 감지)
        - min_date (기본 '20220101') 이후의 최신 데이터만 필터링하여 통합
        """
        import os
        import glob

        files = []
        if isinstance(file_path_or_dir, (list, tuple)):
            input_items = list(file_path_or_dir)
        elif isinstance(file_path_or_dir, str) and "," in file_path_or_dir:
            input_items = [x.strip() for x in file_path_or_dir.split(",") if x.strip()]
        elif file_path_or_dir:
            input_items = [file_path_or_dir]
        else:
            # 자동 탐색: 프로젝트 루트 및 data/ 디렉토리의 태양광 CSV 파일
            cand = (
                glob.glob("*태양광*.csv") + glob.glob("*solar*.csv") + glob.glob("*SOLAR*.csv") + glob.glob("*풍력*.csv")
            )
            input_items = [
                c for c in cand
                if "data/raw" not in c.replace("\\", "/") and "허가정보" not in c
            ]

        for item in input_items:
            p = str(item).strip()
            if os.path.isfile(p):
                files.append(p)
            elif os.path.isdir(p):
                cand = glob.glob(os.path.join(p, "*태양광*.csv")) + glob.glob(os.path.join(p, "*solar*.csv"))
                cand = [c for c in cand if "data/raw" not in c.replace("\\", "/") and "허가정보" not in c]
                files.extend(cand)
            else:
                self.logger.warning(f"지정한 파일 또는 경로를 찾을 수 없습니다: {p}")

        files = sorted(list(dict.fromkeys(files)))
        if not files:
            self.logger.error("처리할 태양광 발전량 CSV 파일을 찾을 수 없습니다.")
            return pd.DataFrame()

        self.logger.info(f"[KPXSolar] 감지된 태양광 CSV {len(files)}개 파일 로드 시작...")
        dfs = []
        clean_min_d = min_date.replace("-", "").strip()

        for fpath in files:
            raw_df = None
            for enc in ["cp949", "euc-kr", "utf-8-sig", "utf-8"]:
                try:
                    raw_df = pd.read_csv(fpath, encoding=enc, low_memory=False)
                    break
                except Exception:
                    continue

            if raw_df is None or raw_df.empty:
                continue

            raw_df.columns = [str(c).strip() for c in raw_df.columns]

            # 1. 연료원 필터링 (풍력/태양광 혼합 파일 처리)
            fuel_col = next((c for c in raw_df.columns if any(k in c for k in ["연료원", "발전원", "에너지원", "구분"])), None)
            if fuel_col:
                raw_df = raw_df[raw_df[fuel_col].astype(str).str.contains("태양광", na=False)]

            # 2. 태양광 발전량 컬럼 매핑
            solar_col = next((c for c in raw_df.columns if "태양광" in c and any(k in c.lower() for k in ["mwh", "kwh", "발전", "거래", "량"])), None)
            if not solar_col:
                solar_col = next((c for c in raw_df.columns if any(k in c for k in ["전력거래량", "발전량", "거래량", "amgo"])), None)

            date_col = next((c for c in raw_df.columns if any(k in c for k in ["일자", "거래일", "date", "ymd"])), None)
            hour_col = next((c for c in raw_df.columns if any(k in c for k in ["시간", "거래시간", "hour", "hh", "no"])), None)
            reg_col  = next((c for c in raw_df.columns if any(k in c for k in ["지역명", "지역", "region", "area"])), None)

            if not (date_col and hour_col and reg_col and solar_col):
                self.logger.warning(f"  ⚠️ 필수 컬럼 식별 실패로 건너뜁니다: {os.path.basename(fpath)}")
                continue

            sub = pd.DataFrame({
                "trade_date": raw_df[date_col].astype(str).str.replace("-", "").str.replace("/", "").str.strip().str[:8],
                "trade_hour": pd.to_numeric(raw_df[hour_col], errors="coerce").fillna(1).astype(int),
                "region_raw": raw_df[reg_col].astype(str).str.strip(),
                "pv_generation_mwh": pd.to_numeric(
                    raw_df[solar_col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
                ).fillna(0.0).clip(lower=0.0)
            })

            # 요청하신 min_date (2022-01-01) 이후 데이터만 필터링
            if clean_min_d:
                sub = sub[sub["trade_date"] >= clean_min_d]

            if sub.empty:
                continue

            # 빠른 지역 매핑 (딕셔너리 캐싱)
            unique_regions = sub["region_raw"].unique()
            reg_map = {r: _resolve_region(r) for r in unique_regions}
            sub["region_code"] = sub["region_raw"].map(lambda r: reg_map[r][0])
            sub["region_name"] = sub["region_raw"].map(lambda r: reg_map[r][1])

            # 벡터 타임스탬프 생성
            date_str = sub["trade_date"]
            hour_str = (sub["trade_hour"] - 1).clip(lower=0, upper=23).astype(str).str.zfill(2)
            sub["timestamp"] = pd.to_datetime(date_str + " " + hour_str + ":00:00", format="%Y%m%d %H:%M:%S", errors="coerce")
            sub = sub.dropna(subset=["timestamp"])
            sub["date"] = sub["timestamp"].dt.strftime("%Y-%m-%d")
            sub["hour"] = sub["timestamp"].dt.hour
            sub["plant_id"] = "KPX_PV_" + sub["region_code"]
            sub["pv_generation_kwh"] = sub["pv_generation_mwh"] * 1000.0

            select_cols = ["timestamp", "date", "hour", "region_code", "region_name",
                           "plant_id", "pv_generation_mwh", "pv_generation_kwh"]
            dfs.append(sub[select_cols])
            self.logger.info(f"  → 파일 로드 완료: {os.path.basename(fpath)} ({len(sub):,}행, {sub['date'].min()} ~ {sub['date'].max()})")

        if not dfs:
            self.logger.warning("변환된 유효 태양광 발전량 데이터가 없습니다.")
            return pd.DataFrame()

        total = (
            pd.concat(dfs, ignore_index=True)
            .drop_duplicates(subset=["timestamp", "region_code"])
            .sort_values(["timestamp", "region_code"])
            .reset_index(drop=True)
        )
        self.logger.info(
            f"[KPXSolar] 통합 변환 완료: 총 {len(total):,}건 "
            f"({total['date'].min()} ~ {total['date'].max()}, {total['region_code'].nunique()}개 시도)"
        )
        return total


# ---------------------------------------------------------------------------
# 2. KPX 계통한계가격(SMP) 수집기
# ---------------------------------------------------------------------------

class KPXSMPCollector:
    """
    KPX 계통한계가격(SMP) 수집기 (공공데이터포털 #15131225)
    - date 1일치 호출 → 육지 24건 + 제주 24건 = 48건 반환
    """

    def __init__(self, api_key: Optional[str] = None):
        self.config  = APIConfig()
        self.api_key = api_key or self.config.DATA_GO_KR_API_KEY
        self.session = RobustSession(retries=3, backoff_factor=0.5)
        self.logger  = setup_logger("KPXSMPCollector")

    # ------------------------------------------------------------------
    def fetch_day(self, date_str: str) -> List[Dict[str, Any]]:
        params = {
            "serviceKey": self.api_key,
            "pageNo":     "1",
            "numOfRows":  "100",
            "dataType":   "JSON",
            "date":       date_str.replace("-", "").strip(),
        }
        try:
            res      = self.session.get_json(self.config.KPX_SMP_URL, params=params)
            body     = res.get("response", {}).get("body", {}) or res.get("body", {})
            items    = body.get("items", {})
            raw_item = items.get("item", []) if isinstance(items, dict) else items
            item_list = raw_item if isinstance(raw_item, list) else [raw_item]
            return [it for it in item_list if isinstance(it, dict) and it]
        except PermissionError:
            raise
        except Exception as exc:
            self.logger.error(f"KPX SMP 수집 실패 ({date_str}): {exc}")
            return []

    # ------------------------------------------------------------------
    def collect(
        self,
        start_date: str = "2022-01-01",
        end_date:   Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Returns
        -------
        pd.DataFrame
            columns: timestamp, date, hour, smp_zone, smp_krw_kwh
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        self.logger.info(f"[KPXSMP] 수집 시작: {start_date} ~ {end_date} (일별 호출)")
        all_records: List[Dict] = []
        cur = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
        end = datetime.strptime(end_date.replace("-", ""),   "%Y%m%d")
        total_days = (end - cur).days + 1
        day_count = 0

        try:
            while cur <= end:
                day_count += 1
                d_str = cur.strftime("%Y%m%d")
                if day_count % 30 == 1 or day_count == total_days:
                    pct = (day_count / total_days) * 100.0
                    self.logger.info(f"  -> [KPX SMP 진행] {cur.strftime('%Y-%m-%d')} ({day_count}/{total_days}일, {pct:.1f}%)")

                items = self.fetch_day(d_str)
                all_records.extend(items)
                cur += timedelta(days=1)
        except PermissionError as pe:
            self.logger.warning(f"  ⚠️ {pe} — 지금까지 수집된 {len(all_records):,}건으로 저장합니다.")

        if not all_records:
            self.logger.warning("수집된 KPX SMP 데이터가 없습니다.")
            return pd.DataFrame(columns=["timestamp", "date", "hour", "smp_zone", "smp_krw_kwh"])

        df = pd.DataFrame(all_records)

        # 컬럼 표준화
        col_map: Dict[str, str] = {}
        for c in df.columns:
            cl = str(c).lower().replace("_", "").replace(" ", "")
            if any(k in cl for k in ["date", "ymd", "일자", "날짜"]):
                col_map[c] = "trade_date"
            elif any(k in cl for k in ["hour", "hh", "시간", "시"]):
                col_map[c] = "trade_hour"
            elif any(k in cl for k in ["landsmp", "육지smp", "육지"]):
                col_map[c] = "smp_land"
            elif any(k in cl for k in ["jejusmp", "제주smp", "제주"]):
                col_map[c] = "smp_jeju"
            elif "smp" in cl:
                col_map[c] = "smp_general"
            elif any(k in cl for k in ["area", "arecd", "지역"]):
                col_map[c] = "area_cd"
        df = df.rename(columns=col_map)

        df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "").str.strip()
        df["trade_hour"] = pd.to_numeric(df["trade_hour"], errors="coerce").fillna(1).astype(int)

        def _make_ts(row):
            d = str(row["trade_date"])
            h = int(row["trade_hour"]) - 1
            if len(d) < 8:
                return pd.NaT
            return pd.to_datetime(f"{d[:8]} {h:02d}:00:00", format="%Y%m%d %H:%M:%S", errors="coerce")

        df["timestamp"] = df.apply(_make_ts, axis=1)
        df = df.dropna(subset=["timestamp"])
        df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        df["hour"] = df["timestamp"].dt.hour

        # long-format 변환: 육지/제주 각각 행으로 분리
        rows = []
        for _, row in df.iterrows():
            ts = row["timestamp"]
            dt = row["date"]
            hr = row["hour"]
            land_val = row.get("smp_land") or row.get("smp_general")
            jeju_val = row.get("smp_jeju")
            if land_val is not None:
                rows.append({"timestamp": ts, "date": dt, "hour": hr,
                             "smp_zone": "land",
                             "smp_krw_kwh": float(str(land_val).replace(",", ""))})
            if jeju_val is not None:
                rows.append({"timestamp": ts, "date": dt, "hour": hr,
                             "smp_zone": "jeju",
                             "smp_krw_kwh": float(str(jeju_val).replace(",", ""))})
            if land_val is None and jeju_val is None and "smp_general" in row:
                rows.append({"timestamp": ts, "date": dt, "hour": hr,
                             "smp_zone": "land",
                             "smp_krw_kwh": float(str(row["smp_general"]).replace(",", ""))})

        result = (
            pd.DataFrame(rows)
            .drop_duplicates(subset=["timestamp", "smp_zone"])
            .sort_values(["timestamp", "smp_zone"])
            .reset_index(drop=True)
        )
        self.logger.info(f"[KPXSMP] 최종 수집: {len(result):,}건")
        return result

    def load_from_csv(self, file_path_or_dir: Any) -> pd.DataFrame:
        """
        전력거래소 EPSIS 또는 공공데이터포털에서 다운로드한 SMP CSV/Excel 파일을 표준 DataFrame으로 변환
        EPSIS 가로형 표 (기간, 01시, 02시, ... 24시) 자동 Unpivot 지원
        - 단일 파일, 복수 파일 리스트, 폴더 경로, 콤마 구분 파일 경로 모두 지원
        """
        import os
        import glob
        import re

        files = []
        if isinstance(file_path_or_dir, (list, tuple)):
            input_items = list(file_path_or_dir)
        elif isinstance(file_path_or_dir, str) and "," in file_path_or_dir:
            input_items = [x.strip() for x in file_path_or_dir.split(",") if x.strip()]
        else:
            input_items = [file_path_or_dir]

        for item in input_items:
            p = str(item).strip()
            if os.path.isfile(p):
                files.append(p)
            elif os.path.isdir(p):
                cand = glob.glob(os.path.join(p, "*smp*.csv")) + glob.glob(os.path.join(p, "*SMP*.csv"))
                if not cand:
                    cand = [os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".csv")]
                files.extend(cand)
            else:
                self.logger.warning(f"지정한 파일 또는 경로를 찾을 수 없습니다: {p}")

        files = sorted(list(set(files)))
        if not files:
            self.logger.error("처리할 SMP CSV 파일을 찾을 수 없습니다.")
            return pd.DataFrame()

        dfs = []
        for fpath in files:
            self.logger.info(f"[KPXSMP] 로컬 파일 로드 중: {fpath}")
            raw_df = None
            for enc in ["utf-8-sig", "cp949", "euc-kr", "utf-8"]:
                try:
                    # 기본 헤더(0행) 시도
                    temp_df = pd.read_csv(fpath, encoding=enc, low_memory=False)
                    has_hours = any(re.search(r"\b0?[1-9]시\b|\b1[0-9]시\b|\b2[0-4]시\b", str(c)) for c in temp_df.columns)
                    if has_hours:
                        raw_df = temp_df
                        break
                    # 파일 첫머리에 제목/메타 행이 있는 경우 헤더 위치 탐색
                    preview = pd.read_csv(fpath, encoding=enc, header=None, nrows=10)
                    h_idx = None
                    for idx, row in preview.iterrows():
                        row_txt = " ".join(row.astype(str))
                        if any(k in row_txt for k in ["기간", "일자", "date"]) and any("시" in str(x) for x in row):
                            h_idx = idx
                            break
                    if h_idx is not None:
                        raw_df = pd.read_csv(fpath, encoding=enc, header=h_idx, low_memory=False)
                        break
                    raw_df = temp_df
                    break
                except Exception:
                    continue

            if raw_df is None or raw_df.empty:
                continue

            # 파일명이나 내용에서 육지/제주 구분 감지
            fname_lower = os.path.basename(fpath).lower()
            default_zone = "jeju" if ("jeju" in fname_lower or "제주" in fname_lower) else "land"

            # 1. EPSIS 가로형 (Wide-format: 기간/일자, 01시, 02시 ... 24시) 감지
            hour_cols = [c for c in raw_df.columns if re.search(r"\b0?[1-9]시\b|\b1[0-9]시\b|\b2[0-4]시\b", str(c))]
            date_col = next((c for c in raw_df.columns if any(k in str(c) for k in ["기간", "일자", "날짜", "date"])), None)

            if hour_cols and date_col:
                self.logger.info(f"  → EPSIS 가로형(01시~24시) 표 구조 감지 ({len(hour_cols)}개 시간대 컬럼, 구역: {default_zone})")
                melted = raw_df.melt(
                    id_vars=[date_col],
                    value_vars=hour_cols,
                    var_name="hour_raw",
                    value_name="smp_val"
                )
                melted["trade_date"] = melted[date_col].astype(str).str.replace("/", "").str.replace("-", "").str.strip()
                melted["trade_hour"] = melted["hour_raw"].str.extract(r"(\d+)").astype(int)

                date_str = melted["trade_date"].astype(str).str[:8]
                hour_str = (melted["trade_hour"] - 1).clip(lower=0, upper=23).astype(str).str.zfill(2)
                melted["timestamp"] = pd.to_datetime(
                    date_str + " " + hour_str + ":00:00", format="%Y%m%d %H:%M:%S", errors="coerce"
                )
                melted = melted.dropna(subset=["timestamp"])
                melted["date"] = melted["timestamp"].dt.strftime("%Y-%m-%d")
                melted["hour"] = melted["timestamp"].dt.hour
                melted["smp_zone"] = default_zone
                melted["smp_krw_kwh"] = pd.to_numeric(
                    melted["smp_val"].astype(str).str.replace(",", ""), errors="coerce"
                )

                sub_df = melted[["timestamp", "date", "hour", "smp_zone", "smp_krw_kwh"]].dropna()
                dfs.append(sub_df)
            else:
                # 2. 일반 세로형 표준 처리
                std_df = self._standardize_dataframe(raw_df)
                if not std_df.empty:
                    dfs.append(std_df)

        if not dfs:
            return pd.DataFrame()

        total = (
            pd.concat(dfs, ignore_index=True)
            .drop_duplicates(subset=["timestamp", "smp_zone"])
            .sort_values(["timestamp", "smp_zone"])
            .reset_index(drop=True)
        )
        self.logger.info(f"[KPXSMP] 로컬 파일 통합 완료: 총 {len(total):,}건 (존: {total['smp_zone'].unique().tolist()})")
        return total
