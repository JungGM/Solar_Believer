# K-VPP Insight: 공공데이터 파이프라인 및 가상발전소(VPP) 데이터 인프라

> **전국 17개 시도 공공데이터(KPX 태양광 발전량, SMP, 기상청 ASOS 지상관측) 및 5만 개 태양광 발전소 허가 표준데이터 자동 수집·정제 파이프라인 (2022-01-01 ~ 현재, 시간 단위 Hourly)**

---

## 📌 1. 프로젝트 개요

본 프로젝트는 기상 변화에 따른 태양광 발전량의 변동성을 분석하고 가상발전소(VPP) 최적 운영 시뮬레이션을 수행하기 위해 구축된 **공공데이터 자동 수집 및 표준화 데이터 엔지니어링 파이프라인**입니다.

* **전국 17개 시도 단위 시간별(Hourly) 시계열 구축**
* **공공데이터포털 및 기상청 API허브 다중 데이터 통합**
* **대용량 파일데이터(CSV) 및 Open API 자동 감지·Unpivot 로더 탑재**
* **모든 데이터셋을 `data/raw/` 하위에 소스별 독립 CSV로 보존**하여 팀원들의 특성공학 및 머신러닝 모델 개발 시 유연하게 활용 가능

---

## 📁 2. 디렉터리 구조

```text
심설/
├── API/                          # 공공데이터 수집 및 전처리 패키지
│   ├── config.py                 # 전국 17개 시도 마스터 테이블, 관측소 지점, 격자좌표, 설정 로더
│   ├── utils.py                  # RobustSession (429 Rate Limit 백오프 재시도), 날짜 청크
│   ├── kma_collector.py          # 기상청 API허브 지상관측(ASOS) 시간자료 & 단기예보 수집기
│   ├── kpx_collector.py          # KPX 태양광 발전량 & 계통한계가격(SMP) 로더 (풍력/제주 자동 분기)
│   ├── plant_collector.py        # 전국 태양광 발전소 50,000건 전기사업허가 표준데이터 로더
│   ├── api_collector.py          # 전체 통합 오케스트레이터 (KVPPOrchestrator)
│   └── README.md                 # API 모듈 상세 명세서
│
├── data/raw/                     # 정제 및 표준화 완료된 원천 데이터셋 (Git 제외)
│   ├── kma_obs_*.csv             # 기상청 17개 시도 지상관측 시간자료
│   ├── kpx_solar_*.csv           # KPX 지역별 시간별 태양광 발전량 (2022~2025 4년 완결본)
│   ├── kpx_smp_*.csv             # KPX 육지/제주 시간별 계통한계가격 (2022~2026 완결본)
│   └── solar_plants_permit.csv   # 전국 태양광 발전소 50,000건 메타데이터
│
├── api.py                        # 파이프라인 루트 진입점 CLI
├── requirements.txt              # 의존성 패키지 목록 (requests, pandas 등)
├── .env.example                  # API 인증키 설정 템플릿
├── .gitignore                    # 보안 키(.env) 및 대용량 데이터(data/) 버전관리 제외
└── README.md                     # 본 문서
```

---

## 📡 3. 수집 및 구축된 4대 원천 데이터셋

| # | 데이터 소스 | 데이터명 | 파일명 (`data/raw/`) | 구축 규모 |
|---|------------|--------|---------------------|----------|
| 1 | **기상청 API허브** | 지상관측(ASOS) 시간자료 | `kma_obs_20220101_20221231.csv`<br>`kma_obs_20230101_20231231.csv` | **약 300,000행** (17개 시도, 기온/습도/풍속/일사량/전운량) |
| 2 | **전력거래소(KPX)** | 지역별 시간별 태양광 발전량 | `kpx_solar_20220101_20251231.csv` | **596,088행** (2022~2025년 4년치, 17개 시도) |
| 3 | **전력거래소(KPX)** | 계통한계가격(SMP) | `kpx_smp_20220101_20261031.csv` | **84,696행** (2022~2026년 육지/제주 시간별) |
| 4 | **공공데이터포털** | 전국 태양광 발전소 전기사업허가 | `solar_plants_permit.csv` | **50,000건** (전국 설비용량, 위경도, 17개 시도 코드 매핑) |

---

## 🚀 4. 빠른 시작 (Quick Start)

### 1단계: 패키지 설치
```bash
pip install -r requirements.txt
```

### 2단계: 인증키 설정 (`.env`)
프로젝트 루트에 `.env` 파일을 만들고 키를 입력합니다 (Git에는 커밋되지 않습니다):
```env
DATA_GO_KR_API_KEY=발급받은_공공데이터포털_인증키
KMA_APIHUB_KEY=발급받은_기상청_API허브_인증키
```

### 3단계: 파이프라인 실행
```bash
# 기본 전체 실행 (기상청 + 태양광 발전량 + SMP + 발전소 데이터)
python api.py --start 2022-01-01 --end 2023-12-31

# 특정 소스만 실행 (예: SMP만 로컬 CSV에서 변환)
python api.py --sources smp

# 태양광 발전량만 로컬 CSV에서 자동 감지하여 변환
python api.py --sources solar

# 기상청 단기예보도 함께 수집 시
python api.py --start 2023-01-01 --end 2023-01-03 --forecast
```

---

## 👥 5. 팀원들을 위한 데이터 활용 가이드

팀원들이 머신러닝(예측) 및 최적화(VPP 입찰) 모델을 개발할 때, `data/raw/`의 파일들을 읽어서 바로 사용하실 수 있습니다:

```python
import pandas as pd

# 1. 4개년 태양광 발전량 로드
df_solar = pd.read_csv("data/raw/kpx_solar_20220101_20251231.csv")

# 2. 시간별 SMP(계통한계가격) 로드
df_smp = pd.read_csv("data/raw/kpx_smp_20220101_20261031.csv")

# 3. 기상청 ASOS 기상 데이터 로드
df_weather = pd.read_csv("data/raw/kma_obs_20230101_20231231.csv")

# 공통 키: ['timestamp', 'region_code'] 또는 ['date', 'hour']로 1:1 결합(Join) 가능
```
