# Open-Meteo 과거 예보 우선 수집과 내일 예보 자동 누적

2026-11-22 사용자 요청으로 기상청 대용 기상예보 수집기를 추가했다.
**지금은 학습용 과거 예보 수집이 최우선**이며 모델 학습은 시작하지 않는다.
기존 PostgreSQL `solar_believer`에 직접 적재한다. SQLite 대체 저장소는 만들지 않는다.

## 실제 수집 범위

- 목표 대상일: **2022-01-01부터 실행 당일까지**. 날짜가 바뀌면 추가 날짜를 이어받는다.
- 1시간 단위 24시간 전체를 원문 그대로 저장한다. 기상값에 발전량 야간 0 정책을 적용하지 않는다.
- 현재 등록 메타데이터에 좌표가 있는 **23개 자원·23개 위치**를 사용한다.
  남동 등록 좌표의 호기별 정확도는 미검증이다. 다른 발전사의 좌표를 이름이나 대표 지역으로 추정하지 않는다.
  `data/source_catalogs/generator_metadata.csv`에 정확한 `source,resource_id,latitude,longitude,coordinate_source`를
  추가하면 1시간 이내에 위치와 과거 작업을 추가한다. 발전기 수 상한은 없다.
- 모델: `gfs_global`(메타데이터 `ncep_gfs013`), `jma_gsm`.
  JMA GSM은 전지구 약 55km·원래 6시간 자료를 시간별로 보간하므로 발전소별 실측 해상도와 다르다.
- 과거 예보: Previous Runs API의 **24시간/48시간 전 예보**를 각각 보존한다.
  고정 lead 자료는 한 번의 전날 08시 발표분을 내일 전체에 적용한 자료와 다르다.
  과거 실행의 정확한 발표/배포 시각은 확인 없이 만들어 넣지 않는다.

요청 변수 20개:

```text
temperature_2m, relative_humidity_2m, dew_point_2m,
cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high,
wind_speed_10m, wind_direction_10m, pressure_msl,
precipitation, snowfall, snow_depth,
shortwave_radiation, direct_radiation, diffuse_radiation,
direct_normal_irradiance, terrestrial_radiation, sunshine_duration, is_day
```

**요청한 변수가 모든 기간에 실제 존재한다는 뜻은 아니다.**
2022년 JMA GSM 시험에서 기온·습도·구름량·풍속·강수량을 확인했지만 일사량은 NULL이었다.
GFS의 2022년 24시간 전 일사량·구름량도 시험에서 NULL이었다. 2024년 GFS 표본에서는 일사량을 확인했다.
값이 없으면 그대로 NULL, `missing_variables`와 변수별 확보 시간 수에 기록한다.
관측·재분석·Historical Forecast 연속 시계열을 하루 전 예보로 바꿔 넣지 않는다.
전기간 학습 가능 여부는 수집 후 모델/변수/날짜별 확보율과 검증 결과로 결정한다.

## 과거 수집 우선순위와 예상 시간

아직 처리하지 않은 과거 작업을 2022년부터 월별로 우선 수행한다.
같은 월에는 기본 기상이 실제 존재하는 JMA GSM, 이어 GFS를 수집한다.
24시간 lead를 먼저 받고 48시간 lead를 별도로 받는다.
요청 실패는 5분부터 지수 재시도하고, 최근 3일의 부분 응답은 6시간 뒤 다시 확인한다.
오래된 부분 응답은 30일, 전부 결측인 응답은 90일 뒤 다시 확인하며 매번 원문을 보존한다.

초기 23곳 × 59개월 × 2모델 × 2lead = **5,428개 HTTP 작업**이다.
변수 수와 기간을 가중한 보수적 로컬 예산은 **31,648회**다.
분당 400·시간당 4,000·최근 24시간 9,000 이내로 제한하고, 500은 내일 예보 몫으로 남긴다.
따라서 PC/DB/네트워크가 계속 동작할 때 **4~5일 계획 추정**이다.
이는 원천에 없는 변수까지 확보한다는 보장이 아니라 현재 계획의 첫 전체 조회 추정이다.
자료가 부분 확보됐는지와 작업을 한 번 처리했는지는 별도로 보고한다.

## 언제 들어오고 어느 날짜 자료인가

- 운영 예보는 **오늘 조회해서 내일 00~23시 KST**의 24개 시간값을 받는다.
  예: 2026-11-22에 조회하면 대상일은 2026-11-23이다.
- GFS 모델 계산 기준 시각: **03·09·15·21시 KST**, 매 6시간.
- 기준 시각은 API 제공 시각이 아니다. 2026-11-22 확인에서 09시 기준 GFS는
  **14:30:35 KST** 제공으로 표시됐다. 이 한 번의 지연을 모든 날의 고정 시간으로 일반화하지 않는다.
- Open-Meteo metadata API의 `last_run_availability_time`을 5분 간격으로 확인하고
  **실제 제공 시각 + 10분**이 지난 새 실행만 수집한다. 제공자 서버 간 복제 지연을 고려한다.
- 사용자 요청대로 과거 미처리 작업이 우선이다. 과거용 분/시간/일 예산을 모두 쓰거나
  처리 가능한 과거 작업이 없을 때 남겨 둔 예산으로 새 내일 예보를 확인한다.
  따라서 과거 수집 중에는 새 예보를 제공 직후 항상 받는다는 보장은 없다.
  과거 1차 수집 뒤에는 5분 확인 주기로 운영한다.
- 같은 대상일의 새 예보를 받으면 이전 버전도 보존한다. 날짜가 바뀌면 내일 대상일도 이동한다.
- 실시간 API는 seamless 자료다. metadata의 초기화 시각은 참고값으로 보존하며
  모든 변수의 정확한 실행 시각이라고 단정하지 않는다. 서비스 `issued_at`도 임의로 채우지 않는다.

## PostgreSQL 테이블과 학습 시 시간 연결

| 테이블 | 내용 |
|---|---|
| `public.open_meteo_locations` | 요청 좌표와 정확한 원천 자원 키 연결·좌표 근거 |
| `public.open_meteo_tasks` | 월별/추가 날짜 작업·진행·오류·재시도 시각 |
| `public.open_meteo_calls` | 가중 호출 장부; rolling minute/hour/day 예산 |
| `public.open_meteo_requests` | gzip HTTP 원문·SHA-256·인자·입수시각·모델 메타데이터·변수별 확보 시간 |
| `public.open_meteo_hourly` | 요청별 시간값·단위·NULL 변수 목록·고정 lead 참고 시각 |
| `public.open_meteo_state` | 진행 상태·과거 seed 종료일·운영 예보 실행별 체크포인트 |
| `public.observations` | 운영 내일 예보의 서비스 조회용 사본; `source=open_meteo_*` |

과거 입력은 `mode=historical_fixed_lead`, 운영 입력은 `mode=live_forecast`로 분리된다.
시간별 행 수는 위치×모델×lead×대상시각의 조합 수이며 발전기 전체 확보율이 아니다.
`reference_at=target_at-lead_days`는 고정 lead 기준이지 입수시각이나 확정 발표시각이 아니다.
실제 입수시각은 `received_at`이다. 예보 마감 시점 검증에서 이 둘을 혼동하지 않는다.

타임스탬프는 UTC로 저장하고 요청 대상 날짜는 KST로 자른다.
일사량 W/m²와 강수량은 표시 시각 **직전 1시간**의 평균/누적이며 기온은 해당 시각 값이다.
발전량 원천 1시는 KST 00~01시 구간이므로 일사량은 **01시 표시 값**과 연결한다.
태양광 발전량 kWh와 일사량 W/m²를 같은 단위로 취급하지 않는다.

## 실행과 중지

현재 PC에서는 Docker의 `open-meteo-collector`가 실행되며 `restart: unless-stopped`를 사용한다.
동일 DB에 대해 PostgreSQL advisory lock을 걸어 PC/컨테이너 중복 수집을 막는다.
공공 발전량/SMP 수집기를 이 작업 때문에 새로 시작하지 않는다.

```powershell
# Docker: 새 PC에서는 먼저 기존 PostgreSQL 자료를 복원한다.
docker compose up -d db
docker compose --profile collector up -d --build open-meteo-collector
docker compose logs --tail 30 open-meteo-collector

# 실제 DB 진행량·변수별 확보 시간 확인
.venv\Scripts\python.exe -m scripts.open_meteo_status

# Docker 중지: 진행 요청 보존을 위해 STOP 요청 후 종료 상태를 확인한다.
.\scripts\stop_open_meteo_collector.ps1
docker compose --profile collector stop open-meteo-collector

# Docker 수집기를 중지한 상태에서만 네이티브 실행
.\scripts\start_open_meteo_collector.ps1
.\scripts\stop_open_meteo_collector.ps1
```

기존 `start_collector.ps1`의 기본 기상 수집은 Open-Meteo로 바뀌었다.
기상청 Hub는 기존 원본과 코드를 보존하며 Compose `kma` 프로필에서 명시적으로만 실행한다.
PC 절전/종료 또는 Docker/DB 중지 동안은 수집하지 못한다. 재개하면 DB 작업 상태에서 이어받는다.
무료 API는 비상업 용도·출처 표기가 필요하다. 상업 운영 시 이용계약을 확인한다.
API 데이터와 개인 좌표 연결 원문은 `data/` 및 PostgreSQL에 두고 Git에는 코드·집계 보고만 올린다.

## 검증과 근거

- `scripts/tests/test_open_meteo.py`: KST 경계·NULL/0 구분·잘린 응답 거부·윤년·가중 한도,
  PostgreSQL 재수신/개정 보존·실패 재시도·10분 대기·내일 대상일·실행별 중복 방지.
- 과거 우선 실행·중복 seed·추가 날짜 이어받기·PostgreSQL 적재 회귀 검사를 포함한 13개 테스트 통과.
  Ruff 및 Compose 설정 검사 통과.
- 현재 수집의 실제 DB 집계는 [2026-11-22 확인 보고](reports/open_meteo_collection_20261122.json)를 본다.
- [Previous Runs](https://open-meteo.com/en/docs/previous-runs-api),
  [Forecast](https://open-meteo.com/en/docs), [JMA](https://open-meteo.com/en/docs/jma-api),
  [모델 갱신·메타데이터](https://open-meteo.com/en/docs/model-updates),
  [호출 가중 계산](https://github.com/open-meteo/open-meteo/discussions/1666),
  [무료 한도](https://open-meteo.com/en/pricing).
