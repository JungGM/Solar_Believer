"""
API/kma_collector.py
기상청(KMA) 데이터 수집 모듈

[수집 대상]
1. 기상청 API허브 지상관측(ASOS) 시간자료 (apihub.kma.go.kr)
   - kma_sfctm3.php : 기온(TA), 풍속(WS), 습도(HM), 일사량(SI), 전운량(CA_TOT)
2. 기상청 단기예보 REST API (공공데이터포털 #15084084)
   - getVilageFcst : 기온(TMP), 습도(REH), 풍속(WSD), 하늘상태(SKY), 강수확률(POP)

[수집 범위]
- 전국 17개 시도 대표 관측소 (config.py NATIONWIDE_REGIONS 참조)
- 2022-01-01 ~ 현재 (Hourly, 30일 청크 자동 분할)
"""

from typing import Optional, List, Dict, Any, Union
from datetime import datetime
import pandas as pd
import numpy as np

from .config import APIConfig, NATIONWIDE_REGIONS
from .utils import RobustSession, DateRangeChunker, setup_logger


# ---------------------------------------------------------------------------
# 1. 기상청 API허브 지상관측(ASOS) 시간자료 수집기
# ---------------------------------------------------------------------------

class KMAObsCollector:
    """
    기상청 API허브 kma_sfctm3.php 기반 전국 지상관측 시간자료 수집기
    - stn=0 : 전국 모든 관측소를 1회 호출로 일괄 수집
    - 17개 시도 대표 관측소만 필터링하여 반환
    - 필드 인덱스 (기상청 공식 헤더 기준):
        [0] TM(관측시각KST), [1] STN(지점번호),
        [3] WS(풍속 m/s), [11] TA(기온 ℃), [13] HM(상대습도 %),
        [25] CA_TOT(전운량 1/10), [34] SI(일사량 MJ/m²)
    """

    BASE_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php"

    def __init__(self, apihub_key: Optional[str] = None):
        self.config = APIConfig()
        self.apihub_key = apihub_key or self.config.KMA_APIHUB_KEY
        self.session = RobustSession(retries=3, backoff_factor=1.0)
        self.logger = setup_logger("KMAObsCollector")

        # 지점번호 → (지역코드, 지역명) 역매핑
        self._stn_map: Dict[int, tuple] = {
            v["asos_stn"]: (k, v["name"])
            for k, v in NATIONWIDE_REGIONS.items()
        }

    # ------------------------------------------------------------------
    def _fetch_chunk(self, start_dt8h: str, end_dt8h: str) -> List[Dict[str, Any]]:
        """
        단일 기간 조회 (tm1/tm2 포맷: YYYYMMDDHHMI)
        start_dt8h / end_dt8h 예: "202301010000", "202301312300"
        """
        if not self.apihub_key:
            self.logger.warning("KMA_APIHUB_KEY 미설정 — 기상청 관측 수집 건너뜀")
            return []

        params = {
            "tm1":     start_dt8h,
            "tm2":     end_dt8h,
            "stn":     "0",       # 0 = 전국 전체
            "help":    "0",
            "authKey": self.apihub_key,
        }

        try:
            self.logger.info(f"[KMAObs] 전국 일괄 조회: {start_dt8h[:8]} ~ {end_dt8h[:8]}")
            resp = self.session.session.get(self.BASE_URL, params=params, timeout=30)
            if resp.status_code != 200 or not resp.text:
                self.logger.warning(f"응답 이상: HTTP {resp.status_code}")
                return []

            records: List[Dict[str, Any]] = []
            for raw_line in resp.text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 26:          # 최소 CA_TOT(25) 이상 필요
                    continue
                try:
                    stn_id = int(parts[1])
                except ValueError:
                    continue
                if stn_id not in self._stn_map:
                    continue

                reg_code, reg_name = self._stn_map[stn_id]

                def _fval(idx: int) -> Optional[float]:
                    if idx >= len(parts):
                        return None
                    try:
                        v = float(parts[idx])
                        return None if v <= -90.0 else v
                    except ValueError:
                        return None

                records.append({
                    "timestamp_raw":      parts[0],          # YYYYMMDDHHMI
                    "stn_id":             stn_id,
                    "region_code":        reg_code,
                    "region_name":        reg_name,
                    "wind_speed_ms":      _fval(3),   # WS
                    "temp_c":             _fval(11),  # TA
                    "humidity_pct":       _fval(13),  # HM
                    "cloud_cover_10":     _fval(25),  # CA_TOT
                    "solar_radiation_mj": _fval(34) if len(parts) > 34 else None,  # SI
                })

            self.logger.info(f"  → {len(records):,}건 파싱 완료")
            return records

        except Exception as exc:
            self.logger.error(f"기상청 관측 수집 실패 ({start_dt8h}~{end_dt8h}): {exc}")
            return []

    # ------------------------------------------------------------------
    def collect(
        self,
        start_date: str = "2022-01-01",
        end_date:   Optional[str] = None,
        chunk_days: int = 30,
    ) -> pd.DataFrame:
        """
        2022-01-01 ~ 현재 전국 17개 시도 기상 관측 수집 후 DataFrame 반환

        Returns
        -------
        pd.DataFrame
            columns: timestamp, stn_id, region_code, region_name,
                     temp_c, humidity_pct, wind_speed_ms,
                     solar_radiation_mj, cloud_cover_10
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        self.logger.info(f"[KMAObs] 수집 시작: {start_date} ~ {end_date}")
        chunks = DateRangeChunker.split(start_date, end_date, chunk_days=chunk_days)
        all_records: List[Dict] = []

        for s, e in chunks:
            tm1 = s + "0000"
            tm2 = e + "2300"
            all_records.extend(self._fetch_chunk(tm1, tm2))

        if not all_records:
            self.logger.warning("수집된 기상 관측 데이터가 없습니다.")
            return pd.DataFrame(columns=[
                "timestamp", "stn_id", "region_code", "region_name",
                "temp_c", "humidity_pct", "wind_speed_ms",
                "solar_radiation_mj", "cloud_cover_10",
            ])

        df = pd.DataFrame(all_records)
        df["timestamp"] = pd.to_datetime(
            df["timestamp_raw"].astype(str), format="%Y%m%d%H%M", errors="coerce"
        )
        df = df.dropna(subset=["timestamp"])
        df = df.drop(columns=["timestamp_raw"])
        df = df.drop_duplicates(subset=["timestamp", "region_code"])
        df = df.sort_values(["timestamp", "region_code"]).reset_index(drop=True)

        self.logger.info(f"[KMAObs] 최종 수집: {len(df):,}건 (지역 {df['region_code'].nunique()}개)")
        return df


# ---------------------------------------------------------------------------
# 2. 기상청 단기예보 수집기
# ---------------------------------------------------------------------------

class KMAForecastCollector:
    """
    기상청 동네예보(단기예보) REST API 수집기 (공공데이터포털 #15084084)
    - 엔드포인트: /VilageFcstInfoService_2.0/getVilageFcst
    - 파라미터: base_date, base_time, nx, ny (기상청 격자좌표)
    - 반환 변수: TMP(기온), REH(습도), WSD(풍속), SKY(하늘상태), POP(강수확률)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.config = APIConfig()
        self.api_key = api_key or self.config.DATA_GO_KR_API_KEY
        self.session = RobustSession(retries=3, backoff_factor=0.5)
        self.logger = setup_logger("KMAForecastCollector")

    # ------------------------------------------------------------------
    def fetch_forecast(
        self,
        base_date:   str,
        base_time:   str = "0500",
        region_code: str = "11",
        num_of_rows: int = 1000,
    ) -> pd.DataFrame:
        """
        단일 기준시각 단기예보 조회

        Parameters
        ----------
        base_date   : YYYY-MM-DD or YYYYMMDD
        base_time   : 발표시각 (0200 / 0500 / 0800 / 1100 / 1400 / 1700 / 2000 / 2300)
        region_code : 17개 시도 코드 (config.py NATIONWIDE_REGIONS key)
        """
        meta   = NATIONWIDE_REGIONS.get(region_code, NATIONWIDE_REGIONS["11"])
        b_date = base_date.replace("-", "")

        params = {
            "serviceKey": self.api_key,
            "pageNo":     1,
            "numOfRows":  num_of_rows,
            "dataType":   "JSON",
            "base_date":  b_date,
            "base_time":  base_time,
            "nx":         meta["nx"],
            "ny":         meta["ny"],
        }

        try:
            res   = self.session.get_json(self.config.KMA_VILAGE_FCST_URL, params=params)
            items = (
                res.get("response", {})
                   .get("body", {})
                   .get("items", {})
                   .get("item", [])
            )
            if not items:
                return pd.DataFrame()

            df = pd.DataFrame(items)
            df["timestamp"] = pd.to_datetime(
                df["fcstDate"].astype(str) + " " + df["fcstTime"].astype(str).str.zfill(4),
                format="%Y%m%d %H%M",
                errors="coerce",
            )
            df["region_code"] = region_code
            df["region_name"] = meta["name"]
            df["base_date"]   = b_date
            df["base_time"]   = base_time

            keep = ["timestamp", "region_code", "region_name",
                    "base_date", "base_time", "category", "fcstValue"]
            return df[[c for c in keep if c in df.columns]]

        except Exception as exc:
            self.logger.error(f"단기예보 수집 실패 (region:{region_code}, {base_date} {base_time}): {exc}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    def collect(
        self,
        start_date:      str = "2022-01-01",
        end_date:        Optional[str] = None,
        base_times:      List[str] = None,
        target_regions:  Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        전국 17개 시도 단기예보 일괄 수집

        Returns
        -------
        pd.DataFrame — raw pivot 없이 long-format (category / fcstValue 컬럼 유지)
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if base_times is None:
            base_times = ["0500"]   # 하루 1회 오전 5시 예보
        if target_regions is None:
            target_regions = list(NATIONWIDE_REGIONS.keys())

        date_range = pd.date_range(start=start_date, end=end_date, freq="D")
        all_dfs: List[pd.DataFrame] = []

        for dt in date_range:
            b_date = dt.strftime("%Y%m%d")
            for b_time in base_times:
                for reg in target_regions:
                    df = self.fetch_forecast(b_date, b_time, region_code=reg)
                    if not df.empty:
                        all_dfs.append(df)

        if not all_dfs:
            self.logger.warning("수집된 단기예보 데이터가 없습니다.")
            return pd.DataFrame()

        result = pd.concat(all_dfs, ignore_index=True)
        result = result.sort_values(["timestamp", "region_code", "category"]).reset_index(drop=True)
        self.logger.info(f"[KMAForecast] 최종 수집: {len(result):,}건")
        return result
