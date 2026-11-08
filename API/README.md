# K-VPP Insight — 공공데이터 API 및 태양광 인프라 자동 수집 모듈

> 태양광 발전량 예측 및 VPP(가상발전소) 시뮬레이션 프로젝트를 위한  
> **공공데이터 API 자동 수집 및 인프라 데이터 로더** (2022-01-01 ~ 현재, 시간 단위 Hourly)

---

## 📁 디렉토리 구조

```
API/
├── config.py           # API Key 환경 설정, 전국 17개 시도 마스터 테이블
├── utils.py            # RobustSession (재시도/백오프), 날짜 청크 유틸
├── kma_collector.py    # 기상청 지상관측(ASOS) + 단기예보 수집기
├── kpx_collector.py    # 전력거래소 태양광 발전량 + SMP 수집기
├── plant_collector.py  # 전국 태양광 발전소 전기사업허가 표준데이터 로더
├── api_collector.py    # 전체 통합 오케스트레이터 (파이프라인 진입점)
└── README.md           # 본 문서
```

---

## 📡 수집 및 로드 대상 데이터

| # | 소스 | 데이터명 | 엔드포인트 / 원본 | 저장 파일 (`data/raw/`) |
|---|------|-------|-----------|----------|
| 1 | 기상청 API허브 | 지상관측(ASOS) 시간자료 | `apihub.kma.go.kr/.../kma_sfctm3.php` | `kma_obs_*.csv` |
| 2 | 기상청 공공데이터 | 단기예보 (선택) | `apis.data.go.kr/.../getVilageFcst` | `kma_forecast_*.csv` |
| 3 | 전력거래소(KPX) | 지역별 시간별 태양광 발전량 | `apis.data.go.kr/B552115/PvAmountByLocHr/...` | `kpx_solar_*.csv` |
| 4 | 전력거래소(KPX) | 계통한계가격(SMP) | `apis.data.go.kr/B552115/SmpWithForecastDemand/...` | `kpx_smp_*.csv` |
| 5 | 공공데이터포털 | 전국태양광발전소전기사업허가정보표준데이터 | `전국태양광발전소전기사업허가정보표준데이터.csv` | `solar_plants_permit.csv` |

> **저장 방식**: `data/raw/` 하위에 소스별 독립 CSV로 저장  
> (API 데이터와 인프라 메타데이터를 원형 그대로 보존하여, 팀원의 특성공학 모델 개발 시 자유롭게 활용 가능)

---

## 🔑 API 키 설정

`.env` 파일을 프로젝트 루트에 생성하세요:

```env
# 공공데이터포털 (data.go.kr) 일반 인증키 (Decoding)
DATA_GO_KR_API_KEY=발급받은_공공데이터포털_인증키

# 기상청 API허브 (apihub.kma.go.kr) 인증키
KMA_APIHUB_KEY=발급받은_기상청_API허브_키
```

---

## ▶ 실행 방법

```bash
# 기본 실행 (2022-01-01 ~ 오늘, 전국 태양광 발전소 CSV 자동 로드)
python api.py --start 2022-01-01

# 기간 지정 실행
python api.py --start 2023-01-01 --end 2023-12-31

# EPSIS에서 다운로드한 SMP CSV만 변환하여 data/raw/에 즉시 저장 (API 트래픽 소모 없음)
python api.py --sources smp --smp-csv ./smp_land.csv

# EPSIS 육지 및 제주 파일 동시 변환
python api.py --sources smp --smp-csv ./smp_land.csv,./smp_jeju.csv

# 발전소 허가 CSV 경로 직접 지정 시
python api.py --start 2023-01-01 --end 2023-01-03 --plant-csv ./전국태양광발전소전기사업허가정보표준데이터.csv

# 기상청 단기예보도 함께 수집 시
python api.py --start 2023-01-01 --end 2023-01-03 --forecast
```

### 옵션 설명

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--start` | `2022-01-01` | 수집 시작일 (YYYY-MM-DD) |
| `--end` | 오늘 | 수집 종료일 (YYYY-MM-DD) |
| `--sources` | `all` | 수집/변환할 소스 선택 (`smp`, `solar`, `kma`, `plant` 또는 콤마 구분) |
| `--smp-csv` | 자동 탐색 | EPSIS 등에서 다운로드한 SMP CSV 파일(또는 폴더) 경로 |
| `--solar-csv` | 미사용 | 다운로드한 태양광 발전량 CSV 파일 경로 |
| `--plant-csv` | 자동 탐색 | 전국 태양광 발전소 허가 CSV 파일 경로 |
| `--forecast` | 미사용 | 기상청 단기예보 수집 여부 플래그 |

---

## 📄 출력 파일 스키마

### 1. `kma_obs_*.csv` — 기상청 지상관측
* `timestamp`: 관측 시각 (1시간 단위, KST)
* `stn_id`: 관측소 지점 번호 (서울 108, 부산 159 등)
* `region_code` / `region_name`: 17개 시도 코드 및 명칭
* `temp_c`: 기온 (℃)
* `humidity_pct`: 상대습도 (%)
* `wind_speed_ms`: 풍속 (m/s)
* `solar_radiation_mj`: 일사량 (MJ/m²)
* `cloud_cover_10`: 전운량 (0~10)
* `is_interpolated`: 결측치 선형 보간 여부 (0/1)

### 2. `kpx_solar_*.csv` — KPX 태양광 발전량
* `timestamp` / `date` / `hour`: 거래 시각
* `region_code` / `region_name`: 17개 시도
* `plant_id`: 발전소 식별자 (`KPX_PV_{code}`)
* `pv_generation_mwh`: 태양광 발전량 (MWh)
* `pv_generation_kwh`: 발전량 (kWh = MWh × 1000)

### 3. `kpx_smp_*.csv` — KPX 계통한계가격
* `timestamp` / `date` / `hour`: 시각
* `smp_zone`: `land`(육지) / `jeju`(제주)
* `smp_krw_kwh`: 계통한계가격 (원/kWh)

### 4. `solar_plants_permit.csv` — 전국 태양광 발전소 허가정보 (50,000건)
* `plant_id`: 발전소 식별자 (`SOLAR_{region}_{index}`)
* `plant_name`: 발전소 명칭
* `capacity_kw`: 설비용량 (kW)
* `status`: 가동상태 (정상가동 등)
* `address_road` / `address_jibun`: 도로명/지번 주소
* `latitude` / `longitude`: 위도/경도
* `install_year` / `permit_date`: 설치연도 / 허가일자
* `region_code` / `region_name`: 매핑된 17개 시도

---

## 🗂 모듈 상태 요약

| 모듈 | 상태 | 비고 |
|------|------|------|
| `kma_collector.py` | ✅ 완료 | API허브 sfctm3 전국 1회 일괄 호출 |
| `kpx_collector.py` | ✅ 완료 | 시간별 발전량(tradeYmd) 및 SMP(date) |
| `plant_collector.py` | ✅ 완료 | 전국태양광 5만 건 cp949 자동 파싱 및 17개 시도 매핑 |
| `api_collector.py` | ✅ 완료 | 통합 파이프라인, `data/raw/` 개별 저장 |
| `config.py` | ✅ 완료 | 17개 시도 마스터 테이블, Key 환경설정 |
| `utils.py` | ✅ 완료 | RobustSession (지수 백오프/재시도), Chunker |
