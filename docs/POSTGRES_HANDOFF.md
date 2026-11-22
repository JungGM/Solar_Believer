# PostgreSQL 적재와 다른 PC에서 자동수집 이어받기

현재 수집기는 **PostgreSQL만** 사용한다. 새 SQLite DB를 만들거나 SQLite로 대체하지 않는다.
기존 SQLite 파일은 이전 원본 보존용이며 `scripts/migrate_collected_data.py`에서 읽기 전용으로만 접근한다.
API 응답 저장, 일별 원천 레코드, 서비스 시간별 관측값을 PostgreSQL에 저장한다.
원천 레코드와 서비스 관측값은 같은 트랜잭션으로 커밋한다.

## 저장 위치

| 자료 | PostgreSQL 테이블 |
|---|---|
| 발전량·SMP·지역 대표 격자 예보 | `public.observations` |
| 원천 발전기 키·이름·확인된 메타데이터 | `public.collector_resource_registry` |
| 전국 기상예보 격자 원문·발표/대상시각 | `public.weather_forecast_grids` |
| 기존 CSV 7개의 모든 행 | `public.source_dataset_rows` |
| 이어받기 큐·수집 상태 | `public.collector_runtime_state` |
| 원천별 원본·개정 이력·호출 장부 | `collector_*` 스키마의 `records`, `revisions`, `requests`, `raw_responses`, `calls` 등 |

`collector_*` 이름은 프로젝트 `data/` 아래의 상대 경로로 결정하므로 PC 경로가 달라도 동일하다.
로컬 원문 파일이 없어도 PostgreSQL의 `raw_responses`에서 이전 응답을 읽어 이어받는다.
호출 장부는 원천·요청시각으로 중복을 제거하므로 복원 뒤 과거 호출을 중복 집계하지 않는다.

## 새 Windows PC에서 복원

Docker Desktop과 Python 3.12를 준비하고 Docker Desktop을 실행한다.
기존 PC의 수집기는 중지한 뒤 새 PC에서 시작한다. 같은 키로 두 PC가 동시에 수집하지 않는다.

```powershell
git clone https://github.com/JungGM/Solar_Believer.git
cd Solar_Believer
git switch codex/team-data-handoff
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts/init_env.py
```

API 키는 `collector_credentials.py`에 기본값이 내장돼 있다. 새 PC에서는 수동 입력하지 않아도 자동으로 읽는다. `init_env.py`도 내장 키를 자동으로 `.env`에 설정한다. 환경변수나 `.env`에 다른 키를 넣으면 그 값을 우선한다.
새 DB 비밀번호는 초기화 스크립트가 만든 값을 사용해도 된다. 백업 복원에 기존 비밀번호는 필요하지 않다.

```powershell
docker compose up -d db
.venv\Scripts\python.exe -m scripts.postgres_handoff restore transfer/postgres_20261122/manifest.json
docker compose --profile collector up -d --build api web collector kma-collector
docker compose logs --tail 30 collector kma-collector
```

복원은 비어 있는 DB에서만 진행된다. 이미 테이블이 있으면 자동으로 지우지 않고 중단한다.
백업 조각별 SHA-256과 전체 SHA-256을 확인하고, 복원 뒤 원천별 관측·CSV·격자 건수를 대조한다.
확인이 끝나면 DB에 들어 있는 큐·상태를 로컬에 복원한다.
발전량·SMP와 예보 수집기는 `restart: unless-stopped`로 재시작한다. PC 절전·종료 중에는 수집하지 못한다.

## 현재 PC에서 실행·중지

```powershell
# PostgreSQL 실행 후 숨김 백그라운드 수집
docker compose up -d db
.\scripts\start_collector.ps1
# 중지
.\scripts\stop_collector.ps1
# PostgreSQL 기준 최신 현황
.venv\Scripts\python.exe -m scripts.collection_status
```

남동·남부는 5분, 중부·SMP는 40분, 서부는 60분 간격이다.
서부·SMP·중부는 기존 호출 한도를 지킨다. 한도에 도달하면 KST 다음 날까지 기다린다.
최신 조회는 오늘 날짜까지 계속 이동한다. `2026-10-31`은 과거 파일의 보관 분할 기준이다.
과거 오류와 공백은 기록하고 건너뛰며, 발전기 수에는 상한이 없다.

## 새 백업 만들기

수집기를 중지하고 진행 중 요청이 끝난 뒤 실행한다.

```powershell
.venv\Scripts\python.exe -m scripts.postgres_handoff export --output transfer/postgres_20261122
```

`postgres.dump.partNNN`과 `manifest.json`을 함께 옮긴다. 합쳐진 `postgres.dump`는 로컬 검증용이다.
Git에는 각 40MiB의 백업 조각을 올린다. Docker 이미지에는 DB 백업을 포함하지 않는다.
API 키는 사용자 승인에 따라 공개 코드 `collector_credentials.py`에 포함한다. DB 비밀번호는 새 PC에서 자동 생성한다. DB 백업 조각 자체에는 API 키와 DB 비밀번호를 넣지 않았다.

## 데이터 해석

발전량 단위가 독립 검증되지 않은 필드는 `generation_raw`와 `unit_status`로 보존한다.
검증 없이 kWh로 바꾸지 않는다. SMP는 `smp_krw_kwh`에 저장한다.
격자 예보는 원래 격자 순서를 보존하며 발전기 좌표와의 매핑 완료를 뜻하지 않는다.
첫·마지막 관측일은 준공·폐지일을 증명하지 않는다. 원천 키 수를 독립 태양광 발전기 수로 단정하지 않는다.
모델 학습·정산 완료를 의미하지 않는다.
