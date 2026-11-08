"""
API/plant_collector.py
전국 태양광 발전소 전기사업 허가정보 표준데이터 로더 및 수집 모듈

[역할]
- 공공데이터포털 '전국태양광발전소전기사업허가정보표준데이터.csv' 로컬 파일 로드 및 전처리
- 가동상태, 설비용량(kW), 설치위치, 설치연도, 위경도 표준화
- 전국 17개 시도 행정코드(region_code, region_name) 매핑
"""

import os
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

from .config import APIConfig, NATIONWIDE_REGIONS
from .utils import setup_logger

_REGION_KEYWORDS = {
    "서울": "11", "서울특별시": "11",
    "부산": "26", "부산광역시": "26",
    "대구": "27", "대구광역시": "27",
    "인천": "28", "인천광역시": "28",
    "광주": "29", "광주광역시": "29",
    "대전": "30", "대전광역시": "30",
    "울산": "31", "울산광역시": "31",
    "세종": "36", "세종특별자치시": "36",
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


def extract_region(text: Any) -> tuple:
    """텍스트(주소/제공기관명)에서 17개 시도 코드 및 명칭 추출"""
    s = str(text).strip()
    if not s or s == "nan":
        return "00", "기타"
    for keyword, code in _REGION_KEYWORDS.items():
        if s.startswith(keyword) or f" {keyword}" in s or keyword in s:
            return code, NATIONWIDE_REGIONS.get(code, {}).get("name", keyword)
    return "00", "기타"


class SolarPlantCollector:
    """전국 태양광 발전소 전기사업허가 표준데이터 로더"""

    DEFAULT_CANDIDATES = [
        "전국태양광발전소전기사업허가정보표준데이터.csv",
        os.path.join("data", "전국태양광발전소전기사업허가정보표준데이터.csv"),
        os.path.join("data", "raw", "전국태양광발전소전기사업허가정보표준데이터.csv"),
    ]

    def __init__(self):
        self.logger = setup_logger("SolarPlantCollector")

    def load_from_csv(self, file_path: Optional[str] = None) -> pd.DataFrame:
        """
        로컬 CSV 파일 로드 및 표준화

        Parameters
        ----------
        file_path : 파일 경로 (None일 경우 프로젝트 루트 및 data 폴더 자동 탐색)

        Returns
        -------
        pd.DataFrame
            표준화된 전국 태양광 발전소 허가정보 데이터프레임
        """
        target_path = None
        if file_path and os.path.exists(file_path):
            target_path = file_path
        else:
            for cand in self.DEFAULT_CANDIDATES:
                if os.path.exists(cand):
                    target_path = cand
                    break

        if not target_path:
            self.logger.warning(
                f"전국 태양광 발전소 CSV 파일을 찾을 수 없습니다. (지정 경로: {file_path})"
            )
            return pd.DataFrame()

        self.logger.info(f"전국 태양광 발전소 CSV 로드 중: {target_path}")

        df = None
        for enc in ["cp949", "euc-kr", "utf-8-sig", "utf-8"]:
            try:
                df = pd.read_csv(target_path, encoding=enc, low_memory=False)
                self.logger.info(f"  → 인코딩 '{enc}' 적용 성공 ({len(df):,}행)")
                break
            except Exception:
                continue

        if df is None or df.empty:
            self.logger.error("CSV 파일 로드 실패 또는 내용이 비어있습니다.")
            return pd.DataFrame()

        # 컬럼명 표준화 매핑
        col_map = {
            "태양광발전시설명": "plant_name",
            "소재지도로명주소": "address_road",
            "소재지지번주소": "address_jibun",
            "위도": "latitude",
            "경도": "longitude",
            "설치상세위치구분명": "location_type",
            "가동상태구분명": "status",
            "설비용량": "capacity_kw",
            "공급전압": "voltage_v",
            "주파수": "frequency_hz",
            "설치연도": "install_year",
            "세부용도": "usage_detail",
            "허가일자": "permit_date",
            "허가기관": "permit_agency",
            "설치면적": "area_m2",
            "데이터기준일자": "data_standard_date",
            "제공기관코드": "provider_code",
            "제공기관명": "provider_name",
        }
        df = df.rename(columns=col_map)

        # 수치형 변환
        if "capacity_kw" in df.columns:
            df["capacity_kw"] = pd.to_numeric(df["capacity_kw"], errors="coerce").fillna(0.0)
        if "area_m2" in df.columns:
            df["area_m2"] = pd.to_numeric(df["area_m2"], errors="coerce")
        if "latitude" in df.columns:
            df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        if "longitude" in df.columns:
            df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

        # 시도 지역 매핑 (제공기관명 우선 탐색 -> 도로명주소 -> 지번주소)
        def resolve_row_region(row):
            for col in ["provider_name", "address_road", "address_jibun"]:
                val = row.get(col)
                if pd.notna(val):
                    code, name = extract_region(val)
                    if code != "00":
                        return code, name
            return "00", "기타"

        regions = df.apply(resolve_row_region, axis=1)
        df["region_code"] = [r[0] for r in regions]
        df["region_name"] = [r[1] for r in regions]

        # 발전소 고유 ID 부여 (제공기관코드 + 인덱스 or 시설명)
        df["plant_id"] = "SOLAR_" + df["region_code"] + "_" + df.index.astype(str).str.zfill(6)

        self.logger.info(
            f"  → 전국 발전소 허가정보 파싱 완료: 총 {len(df):,}개 시설 "
            f"(매핑된 시도: {df['region_code'].nunique()}개)"
        )
        return df
