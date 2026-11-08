"""
K-VPP Insight Data Pipeline Configuration
전국 17개 시도(서울~제주), 전력거래소(KPX), 기상청(KMA), 전국태양광발전소 매핑 및 공식 엔드포인트 정의
"""

import os
from dataclasses import dataclass
from pathlib import Path

# .env 및 .env.txt 자동 탐색 및 로드
base_dir = Path(__file__).resolve().parent.parent
possible_env_files = [base_dir / ".env", base_dir / ".env.txt"]

loaded = False
try:
    from dotenv import load_dotenv
    for env_f in possible_env_files:
        if env_f.exists():
            load_dotenv(dotenv_path=env_f, override=True)
            loaded = True
            break
except ImportError:
    pass

if not loaded:
    for env_f in possible_env_files:
        if env_f.exists():
            try:
                with open(env_f, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip().strip("'\"")
                break
            except Exception:
                pass


@dataclass
class APIConfig:
    # API Keys
    DATA_GO_KR_API_KEY: str = os.getenv("DATA_GO_KR_API_KEY", "10b8c48d24d2040dbd08f80b9b147dff13c506294c3443e82e3e75c49733103c")
    KMA_APIHUB_KEY: str = os.getenv("KMA_APIHUB_KEY", "0-EyJ4d1Q9ehMieHdfPXcw")

    # 1. 전력거래소(KPX) 공식 엔드포인트 (400 에러 해결: 정확한 서비스명 및 오퍼레이션명)
    # 한국전력거래소_지역별 시간별 태양광 발전량 정보 (공공데이터포털 #15103243)
    KPX_PV_URL: str = "https://apis.data.go.kr/B552115/PvAmountByLocHr/getPvAmountByLocHr"
    # 한국전력거래소_계통한계가격 및 수요예측(하루전 발전계획용) (#15131225)
    KPX_SMP_URL: str = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"

    # 2. 기상청(KMA)
    # 기상청 직통 API허브 (지상시간자료)
    KMA_APIHUB_SFCTM_URL: str = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"
    # 기상청 단기예보 REST API (#15084084)
    KMA_VILAGE_FCST_URL: str = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"

    # 3. 전국 태양광 발전소 허가정보 표준데이터
    PV_PERMIT_URL: str = "https://api.data.go.kr/openapi/tn_pubr_public_tot_pv_elcty_bsn_prm_api"
    DEFAULT_PLANT_CSV_PATH: str = "전국태양광발전소전기사업허가정보표준데이터.csv"


# 전국 17개 시도 마스터 매핑 테이블
NATIONWIDE_REGIONS = {
    "11": {"name": "서울", "smp_zone": "land", "asos_stn": 108, "nx": 60, "ny": 127},
    "26": {"name": "부산", "smp_zone": "land", "asos_stn": 159, "nx": 98, "ny": 76},
    "27": {"name": "대구", "smp_zone": "land", "asos_stn": 143, "nx": 89, "ny": 90},
    "28": {"name": "인천", "smp_zone": "land", "asos_stn": 112, "nx": 55, "ny": 124},
    "29": {"name": "광주", "smp_zone": "land", "asos_stn": 156, "nx": 58, "ny": 74},
    "30": {"name": "대전", "smp_zone": "land", "asos_stn": 133, "nx": 67, "ny": 100},
    "31": {"name": "울산", "smp_zone": "land", "asos_stn": 152, "nx": 102, "ny": 84},
    "36": {"name": "세종", "smp_zone": "land", "asos_stn": 239, "nx": 66, "ny": 103},
    "41": {"name": "경기", "smp_zone": "land", "asos_stn": 119, "nx": 60, "ny": 120},
    "42": {"name": "강원", "smp_zone": "land", "asos_stn": 101, "nx": 73, "ny": 134},
    "43": {"name": "충북", "smp_zone": "land", "asos_stn": 131, "nx": 69, "ny": 107},
    "44": {"name": "충남", "smp_zone": "land", "asos_stn": 177, "nx": 68, "ny": 100},
    "45": {"name": "전북", "smp_zone": "land", "asos_stn": 146, "nx": 63, "ny": 89},
    "46": {"name": "전남", "smp_zone": "land", "asos_stn": 165, "nx": 51, "ny": 67},
    "47": {"name": "경북", "smp_zone": "land", "asos_stn": 136, "nx": 91, "ny": 106},
    "48": {"name": "경남", "smp_zone": "land", "asos_stn": 155, "nx": 91, "ny": 77},
    "49": {"name": "제주", "smp_zone": "jeju", "asos_stn": 184, "nx": 52, "ny": 38},
}

REGION_NAME_TO_CODE = {v["name"]: k for k, v in NATIONWIDE_REGIONS.items()}
