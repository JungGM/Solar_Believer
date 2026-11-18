"""
API/api_collector.py
K-VPP Insight — 공공데이터 API 및 태양광 인프라 통합 자동 수집 오케스트레이터

[역할]
- 아래 수집기를 순서대로 호출하여 raw 데이터를 각각 CSV로 저장
  1. KMAObsCollector      → kma_obs_<start>_<end>.csv (기상청 실측 기상)
  2. KMAForecastCollector → kma_forecast_<start>_<end>.csv (기상청 단기예보, 선택)
  3. KPXSolarCollector    → kpx_solar_<start>_<end>.csv (KPX 태양광 발전량)
  4. KPXSMPCollector      → kpx_smp_<start>_<end>.csv (KPX 계통한계가격)
  5. SolarPlantCollector  → solar_plants_permit.csv (전국 태양광 발전소 허가정보)

[저장 위치]
  data/raw/   ← 각 API 및 소스별 원시 CSV (merge/분류 없음)

[실행 방법]
  python api.py --start 2022-01-01 --end 2024-12-31
  python api.py --start 2022-01-01            # end 생략 시 오늘까지
  python api.py --start 2023-01-01 --end 2023-01-02 --plant-csv ./전국태양광발전소전기사업허가정보표준데이터.csv
"""

from __future__ import annotations

import os
import sys
import logging
from datetime import datetime
from typing import Optional, List

import pandas as pd

from .config import APIConfig
from .utils import setup_logger
from .kma_collector   import KMAObsCollector, KMAForecastCollector
from .kpx_collector   import KPXSolarCollector, KPXSMPCollector
from .plant_collector import SolarPlantCollector


# ---------------------------------------------------------------------------
# 안전 출력 (Windows 콘솔 핸들 에러 방어)
# ---------------------------------------------------------------------------
def safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 단순 결측치 보간 마킹 유틸리티
# ---------------------------------------------------------------------------
def mark_interpolated(df: pd.DataFrame, numeric_cols: List[str]) -> pd.DataFrame:
    """
    결측치가 있는 행을 선형 보간(limit=2)하고 is_interpolated 플래그 추가.
    원본이 NaN이었던 위치를 1로 마킹.
    """
    if df.empty:
        return df
    df = df.copy()
    was_null = df[numeric_cols].isnull().any(axis=1)
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].interpolate(method="linear", limit=2)
    df["is_interpolated"] = was_null.astype(int)
    return df


# ---------------------------------------------------------------------------
# 파일명 생성 헬퍼
# ---------------------------------------------------------------------------
def _fname(prefix: str, start: str, end: str) -> str:
    s = start.replace("-", "")
    e = end.replace("-", "")
    return f"{prefix}_{s}_{e}.csv"


# ---------------------------------------------------------------------------
# 메인 오케스트레이터
# ---------------------------------------------------------------------------
class KVPPOrchestrator:
    """
    공공데이터 수집기 및 태양광 발전소 표준데이터를 호출하여 raw CSV를 저장하는 파이프라인.
    특성공학에 넘길 수 있도록 각 소스를 독립 파일로 저장.
    """

    def __init__(self):
        self.config = APIConfig()
        self.logger = setup_logger("KVPPOrchestrator")
        self.raw_dir = os.path.join("data", "raw")
        os.makedirs(self.raw_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def _save(self, df: pd.DataFrame, filename: str) -> str:
        path = os.path.join(self.raw_dir, filename)
        if df.empty:
            self.logger.warning(f"  → 저장 건너뜀 (빈 DataFrame): {filename}")
            return path
        df.to_csv(path, index=False, encoding="utf-8-sig")
        self.logger.info(f"  → 저장 완료: {path} ({len(df):,}행)")
        return path

    # ------------------------------------------------------------------
    def run(
        self,
        start_date: str = "2022-01-01",
        end_date:   Optional[str] = None,
        plant_csv_path: Optional[str] = None,
        smp_csv_path:   Optional[str] = None,
        solar_csv_path: Optional[str] = None,
        collect_forecast: bool = False,
        sources: str = "all",
    ) -> dict:
        """
        전체 파이프라인 또는 선택된 소스 실행.

        Parameters
        ----------
        start_date       : 수집 시작일 (YYYY-MM-DD)
        end_date         : 수집 종료일 (YYYY-MM-DD), 생략 시 오늘
        plant_csv_path   : 전국 태양광 발전소 허가정보 CSV 경로 (None이면 자동 탐색)
        smp_csv_path     : EPSIS 등에서 다운로드한 SMP CSV 경로 (None이면 자동 탐색/API 호출)
        solar_csv_path   : 다운로드한 태양광 발전량 CSV 경로 (None이면 API 호출)
        collect_forecast : 기상청 단기예보도 수집할지 여부 (기본 False)
        sources          : 실행할 소스 (예: "smp", "solar", "kma", "plant" 또는 "all")

        Returns
        -------
        dict  {소스명: 저장된 파일 경로}
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        s, e = start_date, end_date
        saved_files: dict = {}

        # 소스 필터링
        raw_sources = [x.strip().lower() for x in sources.split(",") if x.strip()]
        if "all" in raw_sources or not raw_sources:
            selected = {"kma", "solar", "smp", "plant"}
        else:
            selected = set(raw_sources)

        safe_print("=" * 70)
        safe_print("▶ K-VPP Insight 공공데이터 수집 및 변환 파이프라인 가동")
        safe_print(f"▶ 대상 소스: {', '.join(sorted(selected))}")
        safe_print(f"▶ 기간 범위: {s} ~ {e}")
        safe_print("=" * 70)

        # ── 1. 기상청 지상관측(ASOS) ──────────────────────────────────────
        if "kma" in selected:
            self.logger.info("[1/4] 기상청 API허브 지상관측 수집 및 병합 시작...")
            import glob
            existing_kma_files = sorted(glob.glob(os.path.join(self.raw_dir, "kma_obs_*.csv")))
            existing_kma_dfs = []
            for kf in existing_kma_files:
                try:
                    kdf = pd.read_csv(kf, low_memory=False)
                    if not kdf.empty and "timestamp" in kdf.columns:
                        existing_kma_dfs.append(kdf)
                except Exception:
                    pass

            if existing_kma_dfs:
                base_kma = pd.concat(existing_kma_dfs, ignore_index=True)
                base_kma["timestamp"] = pd.to_datetime(base_kma["timestamp"], errors="coerce")
                base_kma = base_kma.dropna(subset=["timestamp"])
                max_ts = base_kma["timestamp"].max()
                max_date_str = max_ts.strftime("%Y-%m-%d")
                if max_date_str < e:
                    from datetime import timedelta
                    next_start = (max_ts + timedelta(days=1)).strftime("%Y-%m-%d")
                    self.logger.info(f"  → 기존 기상 데이터({max_date_str}까지) 확인됨. 추가 구간 수집: {next_start} ~ {e}")
                    new_obs = KMAObsCollector().collect(start_date=next_start, end_date=e)
                    if not new_obs.empty:
                        new_obs = mark_interpolated(
                            new_obs, ["temp_c", "humidity_pct", "wind_speed_ms", "solar_radiation_mj"]
                        )
                        obs_df = pd.concat([base_kma, new_obs], ignore_index=True)
                    else:
                        obs_df = base_kma
                else:
                    obs_df = base_kma
                obs_df["timestamp"] = pd.to_datetime(obs_df["timestamp"], errors="coerce")
                obs_df = (
                    obs_df.drop_duplicates(subset=["timestamp", "region_code"])
                    .sort_values(["timestamp", "region_code"])
                    .reset_index(drop=True)
                )
            else:
                obs_df = KMAObsCollector().collect(start_date=s, end_date=e)
                obs_df = mark_interpolated(
                    obs_df, ["temp_c", "humidity_pct", "wind_speed_ms", "solar_radiation_mj"]
                )

            if not obs_df.empty:
                kma_s = pd.to_datetime(obs_df["timestamp"]).min().strftime("%Y%m%d")
                kma_e = pd.to_datetime(obs_df["timestamp"]).max().strftime("%Y%m%d")
                saved_files["kma_obs"] = self._save(obs_df, f"kma_obs_{kma_s}_{kma_e}.csv")

        # ── 2. 기상청 단기예보 (선택) ──────────────────────────────────────
        if collect_forecast and ("kma" in selected or "forecast" in selected):
            self.logger.info("[선택] 기상청 단기예보 수집 시작...")
            fcst_df = KMAForecastCollector().collect(start_date=s, end_date=e)
            saved_files["kma_forecast"] = self._save(fcst_df, _fname("kma_forecast", s, e))

        # ── 3. KPX 태양광 발전량 ──────────────────────────────────────────
        if "solar" in selected:
            self.logger.info("[2/4] KPX 지역별 시간별 태양광 발전량 수집 및 로컬 CSV 변환 시작...")
            solar_collector = KPXSolarCollector()
            import glob
            existing_solar_files = sorted(glob.glob(os.path.join(self.raw_dir, "kpx_solar_20220101_*.csv")))

            if existing_solar_files and not solar_csv_path:
                latest_solar_path = existing_solar_files[-1]
                self.logger.info(f"  → 기존 변환된 최신 태양광 데이터 로드: {latest_solar_path}")
                solar_df = pd.read_csv(latest_solar_path, low_memory=False)
                solar_df["timestamp"] = pd.to_datetime(solar_df["timestamp"], errors="coerce")
            else:
                target_solar = solar_csv_path
                if not target_solar:
                    cand_solar = (
                        glob.glob("*태양광*.csv") + glob.glob("*solar*.csv") + glob.glob("*SOLAR*.csv") + glob.glob("*풍력*.csv")
                    )
                    cand_solar = [
                        c for c in cand_solar
                        if "data/raw" not in c.replace("\\", "/") and "허가정보" not in c
                    ]
                    if cand_solar:
                        target_solar = sorted(list(dict.fromkeys(cand_solar)))

                if target_solar:
                    self.logger.info(f"  → 🚀 로컬 태양광 CSV 파일 감지 및 변환 진행 ({len(target_solar) if isinstance(target_solar, list) else 1}개 파일)")
                    solar_df = solar_collector.load_from_csv(target_solar, min_date=s)
                else:
                    solar_df = solar_collector.collect(start_date=s, end_date=e)

            # 부족한 최신 구간(예: 2026-01-01 ~ end_date) API 추가 수집 및 병합
            if not solar_df.empty and "date" in solar_df.columns:
                max_sol_date = str(solar_df["date"].max())[:10]
                if max_sol_date < e:
                    from datetime import timedelta
                    next_sol_start = (datetime.strptime(max_sol_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    self.logger.info(f"  → 로컬 CSV 이후 부족 구간 API 추가 수집: {next_sol_start} ~ {e}")
                    api_sol_df = solar_collector.collect(start_date=next_sol_start, end_date=e)
                    if not api_sol_df.empty:
                        solar_df = pd.concat([solar_df, api_sol_df], ignore_index=True)
                        solar_df["timestamp"] = pd.to_datetime(solar_df["timestamp"], errors="coerce")
                        solar_df = (
                            solar_df.drop_duplicates(subset=["timestamp", "region_code"])
                            .sort_values(["timestamp", "region_code"])
                            .reset_index(drop=True)
                        )

            if not solar_df.empty:
                sol_s = str(solar_df["date"].min()).replace("-", "")[:8]
                sol_e = str(solar_df["date"].max()).replace("-", "")[:8]
                solar_fname = f"kpx_solar_{sol_s}_{sol_e}.csv"
                saved_files["kpx_solar"] = self._save(solar_df, solar_fname)
            else:
                self.logger.warning("  ⚠️ 유효한 태양광 발전량 데이터가 수집되지 않았습니다.")

        # ── 4. KPX SMP (로컬 CSV 전용 안전 처리) ─────────────────────────
        if "smp" in selected:
            self.logger.info("[3/4] KPX 계통한계가격(SMP) 로컬 CSV 처리 시작...")
            smp_collector = KPXSMPCollector()
            target_smp = smp_csv_path
            if not target_smp:
                import glob
                cand = glob.glob("*smp*.csv") + glob.glob("*SMP*.csv") + glob.glob("data/*smp*.csv")
                cand = [c for c in cand if "data/raw" not in c.replace("\\", "/")]
                if cand:
                    target_smp = sorted(list(set(cand)))

            if target_smp:
                self.logger.info(f"  → 🚀 로컬 EPSIS SMP CSV 파일 감지 및 변환 진행: {target_smp}")
                smp_df = smp_collector.load_from_csv(target_smp)
                if not smp_df.empty:
                    smp_s = smp_df["date"].min().replace("-", "")
                    smp_e = smp_df["date"].max().replace("-", "")
                    smp_fname = f"kpx_smp_{smp_s}_{smp_e}.csv"
                    saved_files["kpx_smp"] = self._save(smp_df, smp_fname)
            else:
                self.logger.warning("  ⚠️ [안내] 일일 100회 한도인 SMP API 호출은 비활성화되어 있습니다.")
                self.logger.warning("  → EPSIS에서 다운로드한 CSV 파일(예: smp_land.csv)을 프로젝트 폴더에 넣어주시면 자동 변환됩니다.")

        # ── 5. 전국 태양광 발전소 허가정보 표준데이터 로드 ────────────────
        if "plant" in selected:
            self.logger.info("[4/4] 전국 태양광 발전소 전기사업허가 표준데이터 로드 시작...")
            plant_loader = SolarPlantCollector()
            plant_df = plant_loader.load_from_csv(plant_csv_path)
            if not plant_df.empty:
                saved_files["solar_plants"] = self._save(plant_df, "solar_plants_permit.csv")

        # ── 완료 요약 ─────────────────────────────────────────────────────
        safe_print("\n" + "=" * 70)
        safe_print("▶ 처리 완료! 저장된 데이터 파일 목록:")
        safe_print("=" * 70)
        for src, path in saved_files.items():
            safe_print(f"  [{src}] {path}")
        safe_print(f"\n저장 위치: {os.path.abspath(self.raw_dir)}")

        return saved_files


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="K-VPP Insight: 공공데이터 수집 및 태양광 발전소 데이터 → data/raw/ CSV 저장"
    )
    parser.add_argument("--start",      default="2022-01-01", help="수집 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end",        default=None,         help="수집 종료일 (YYYY-MM-DD, 기본: 오늘)")
    parser.add_argument("--sources",    default="all",        help="수집/변환할 소스 (예: smp 또는 kma,solar,smp,plant, 기본: all)")
    parser.add_argument("--plant-csv",  default=None,         dest="plant_csv",
                        help="전국 태양광 발전소 허가정보 CSV 파일 경로")
    parser.add_argument("--smp-csv",    default=None,         dest="smp_csv",
                        help="EPSIS에서 다운로드한 SMP CSV 파일(또는 폴더) 경로")
    parser.add_argument("--solar-csv",  default=None,         dest="solar_csv",
                        help="다운로드한 태양광 발전량 CSV 파일 경로")
    parser.add_argument("--forecast",   action="store_true",
                        help="기상청 단기예보도 함께 수집")
    args = parser.parse_args()

    orchestrator = KVPPOrchestrator()
    orchestrator.run(
        start_date=args.start,
        end_date=args.end,
        plant_csv_path=args.plant_csv,
        smp_csv_path=args.smp_csv,
        solar_csv_path=args.solar_csv,
        collect_forecast=args.forecast,
        sources=args.sources,
    )


if __name__ == "__main__":
    main()
