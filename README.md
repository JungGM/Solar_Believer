> **2026-11-23 목표 변경:** VPP 구성은 범위에서 제외하고 **태양광 발전량 예측·정확도 검증**에 집중한다. 최신 발전 실측이 부족한 상황을 반영해, 자료가 겹치는 과거 기간의 시간 순 검증부터 추진한다. 발전기 200개·VPP 그룹 수·용량 요건은 선행 조건이 아니며 SMP·정산은 현재 필수 산출물에서 제외한다. [현재 목표·우선순위·완료 기준](docs/FORECAST_SCOPE.md)이 아래 이전 목표·계획보다 우선한다. 이번 변경은 문서에만 적용하며 실행 설정은 변경하지 않는다.

# Solar Believer

**2026-11-22 PostgreSQL 전환·데이터 이전:** [실제 적재·백업·다른 PC 복원](docs/POSTGRES_HANDOFF.md). 수집기 검증 74개 PostgreSQL 통과, 원천/서비스 건수 대조 통과.

**이전 전체 진행 기록:** [현재 구현·실제 수집 집계·실행 방법·남은 작업](docs/PROGRESS.md).
수집·전처리·7개 운영 메뉴의 개발 중 체크포인트이며 최신 테스트 실패와 미연결 범위도 기록했습니다.

기상청 단기예보의 현재 자동수집 범위·실행·진행 상태·다른 과거 예보 확보 경로는 [기상청 수집 안내](docs/KMA_COLLECTION.md)를 참고하세요.

태양광 발전량 예측 및 정산금 조회 웹서비스의 **2인 공동 개발용 초기 구조**입니다.
기존 공공데이터 수집 코드를 보존하고 PostgreSQL·FastAPI·React·작업 스케줄러를 추가했습니다.
남동·남부·중부·서부·SMP 로컬 파일 수집과 기상청 과거 예보 누적을 진행하고 있습니다.
수집 자료를 기존 PostgreSQL에 적재했고 이후 API 수신도 자동 반영합니다. 발전소 기상 매핑·단위·지속 운영 품질 검증은 남아 있습니다.
모델·학습·정산 엔진은 미설정이며 예측/정산 숫자를 임의 생성하지 않습니다.
목표는 **수집 가능한 모든 개별 태양광 발전기 → 발전기별 예측 → 최소 2개 VPP → SMP·정산 시뮬레이션**입니다. 발전기 수에 200개 등의 상한을 두지 않습니다.
예측제도 기준 주요자원 용량은 그룹별 **20MW 초과**이며, 개수 목표와 참여 자격은 별도로 검증합니다.
기존 시도별 발전량 수집기는 보존하며 개별 발전소 실측의 대용으로 사용하지 않습니다.

## 먼저 읽기

- **[전체 진행 기록](docs/PROGRESS.md)**: 작업 전체, 수집 근거, 구현 위치, 알려진 실패, 다음 순서
- **[팀원 작업 인계·우선순위·완료 기준](docs/TEAM_HANDOFF.md)**: 최신 인계 요약과 `main@2a58fe2` 당시 조사 원문
- [정산 구현 명세](docs/SETTLEMENT_RULES.md): SMP 판매금액 추정과 예측제도 인센티브, 2026-10-31 규칙·예외
- [저장소 전체 조사](docs/REPOSITORY_AUDIT.md): 기존 13개 파일과 주의할 수집기 동작
- [시스템 아키텍처](docs/ARCHITECTURE.md): 서비스·DB·스케줄·모델 생명주기
- [전체 구조도·Docker/DB 구분](docs/SYSTEM_OVERVIEW.md) / [Docker 없는 팀원 개발](docs/DEVELOPMENT_WITHOUT_DOCKER.md)
- [지도·VPP·기간·모델 대시보드](docs/DASHBOARD.md) / [지도 데이터 출처](docs/MAP_SOURCE.md)
- [파일별 역할·수정 위치](docs/FILE_GUIDE.md) / [2인 협업](docs/COLLABORATION.md)
- [목표·로드맵](docs/ROADMAP.md) / [완료·진행 중·예정](docs/STATUS.md)
- [실제 연동 안내](docs/INTEGRATIONS.md) / [검증 기록](docs/VALIDATION.md)
- [필요한 데이터·API와 수집 주기](docs/DATA_REQUIREMENTS.md)
- [기존 README 원문](docs/LEGACY_README.md) / [기존 API 문서](API/README.md)

최초 조사 기준 `main@12e9e44`에는 데이터 수집 코드만 있었습니다. `main@2a58fe2`에는 원천 CSV와 남동 수집 연결이 추가됐습니다.
모델·학습·정산은 여전히 미설정입니다. PostgreSQL 적재 완료가 전기간 수집 완료나 단위·발전기별 품질 검증 완료를 뜻하지 않습니다.
기존 설정의 인증키 기본값을 제거했습니다. 과거 Git에 들어간 키는 발급처에서 폐기·재발급하세요.

## 빠른 실행

필수: Git, Python(환경파일 생성용), Docker Desktop의 Linux containers와 Compose.
DataGrip은 선택이며 로컬 Python/Node 패키지 설치 없이 Compose로 실행할 수 있습니다.

```bash
git clone https://github.com/JungGM/Solar_Believer.git
cd Solar_Believer
python scripts/init_env.py
docker version
docker compose version
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
```

`init_env.py`는 `.env.example`에서 로컬 `.env`를 만들고 임의의 DB 암호를 생성합니다.
API 키는 비어 있습니다. 기존 `.env`가 있으면 보존하므로 새 변수를 직접 대조하세요.
복제한 `.env.example`을 사용할 때는 `POSTGRES_PASSWORD`를 반드시 채워야 합니다.
Docker 엔진이 꺼져 있으면 `docker desktop start` 후 다시 확인합니다.

- 대시보드: [http://localhost:3000](http://localhost:3000)
- 가상 데이터로 지도·필터 확인: [데모 대시보드](http://localhost:3000/?demo=true)
- FastAPI 문서: [http://localhost:8000/docs](http://localhost:8000/docs)
- DB 준비 상태: [http://localhost:8000/health/ready](http://localhost:8000/health/ready)

기본 `PIPELINE_MODE=disabled`: 수집·모델이 없어도 웹과 DB는 실행됩니다.
worker는 매시 수집/예측, 하루 8회 예보, 매월 1일 03:00 KST 재학습을 예약하며 미연동 작업은 skipped로 기록합니다.
예보는 `FORECAST_CRON=20 2,5,8,11,14,17,20,23 * * *`로 각 발표 20분 뒤 시도합니다.
실제 예보 API→DB 매핑 코드에는 호출 인자/반환형 불일치가 남아 있으며 미제공 재시도는 미구현입니다.
키 설정 전에 [인계 문서 D02](docs/TEAM_HANDOFF.md)의 결함을 수정해야 합니다.
빈 화면은 오류가 아니며 모델과 정산 기록은 연동 후 표시됩니다.
대시보드는 한국 지도와 VPP 그룹·모델·일/월/연 필터, 예측/실측·SMP·오차 그래프,
저장된 VPP 정산/손실 조회, 발전소 목록·데이터 확보율을 제공합니다.
UI의 데모는 메모리에서만 생성하며 DB를 변경하지 않습니다. 정산 규칙이 없으면 금액은 미설정입니다.

## 데이터 흐름 확인

### 실제 공공 API 파일 수집 및 주기 갱신

승인된 공공데이터포털 키를 로컬 `.env`의 `DATA_GO_KR_API_KEY`에 설정한다.
독립된 수집 프로세스가 기존 PostgreSQL 서비스 DB에 직접 저장한다. SQLite를 사용하지 않는다. `PIPELINE_MODE`는 기존 worker/모델 설정이며 새 수집기를 끄지 않는다.

```powershell
# 과거 기간 수집/중단 지점부터 재개
.venv\Scripts\python.exe api.py --approved-history --start 2022-01-01 --end 2026-10-31
# Windows에서 숨김 백그라운드 실행: 원천별 5/40/60분마다 확인
.\scripts\start_collector.ps1
# 중지: 진행 중인 HTTP 요청 후 종료
.\scripts\stop_collector.ps1
```

전경 실행은 `python api.py --continuous`, 1회 검증은 `--continuous --once`다.
Docker 사용 시 `docker compose --profile collector up -d --build collector`로 실행한다.
로컬과 Docker 수집기를 동시에 실행하지 않는다. Windows 실행은 PC와 프로세스가 실행 중일 때 동작하며
재부팅 후에는 시작 스크립트를 다시 실행한다. 중복 실행은 파일 잠금으로 방지한다.

- 남동·남부는 5분, 중부·SMP는 40분, 서부는 60분 간격으로 최신 자료를 우선 조회한다. 호출 한도를 지킨다.
- 예보는 현재 설정된 지역 대표 17개 격자로, 최신 발표분과 API가 제공하는 최근 3개 날짜의 누락 발표분을 누적한다.
  발전소별 정확한 격자 매핑이 완료되었다는 의미는 아니다. 2022~2026.10의 과거 예보는 이 API로 받을 수 없다.
- 최신 조회에 필요한 호출 횟수를 남긴 뒤 아직 요청하지 않은 과거 페이지와 최근 기간을 순서대로 받는다.
  하루 한도에 도달하면 KST 날짜가 바뀐 다음 주기에 재개한다. 공백 복구와 오류 재시도는 하지 않는다.
- API 오류는 기록하고 다음 페이지·월·발전기로 넘어간다. 전체 페이지 수를 알 수 없는 첫 페이지 오류는 그 요청 범위를 건너뛴다.
  과거에 실패한 요청도 다시 호출하지 않으며, 빈 자료는 그대로 누락으로 남긴다. 최신 자료의 정기 조회는 매시간 계속한다.
- 발전기 수 제한이나 200개에서의 중단은 없다. 남동·남부·서부는 모든 응답 페이지를 저장하고,
  중부는 공식 코드표를 자동 갱신하고 관측된 새 코드를 합쳐 전체 공개 태양광 호기를 조회한다. ESS는 발전기로 세지 않는다.
- 빈 응답은 0 발전량으로 채우지 않는다. 같은 값의 최초 입수시각을 유지하며, 수정 값과 이전 버전을 함께 보관한다.
- 원본 응답·요청 인자(키 제외)·해시·입수시각·이어받기 상태를 PostgreSQL에 보관한다. 발전량·SMP 수신 시 서비스 관측 테이블에도 자동 적재한다.

과거 결과는 `data/api_history_20220101_20261031/`, 주기 수집 결과는 `data/continuous_collection/live/`다.
**2026-10-31은 과거 보관 파일의 분할 기준이며 수집 종료일이 아니다. 최신 조회 대상은 매일 현재 날짜로 갱신한다.**
전체 수집 현황은 두 저장소를 합친 `data/continuous_collection/latest_collection_summary.json`을 기준으로 한다.
이 파일은 매 수집 주기에 갱신되며, 현재 목표일·실제 최신 저장일·원천 오류를 구분한다.
직접 갱신하려면 `python -m scripts.collection_status`를 실행한다.
실행 PID/다음 시각은 `data/continuous_collection/collector_status.json`, 기관별 상태는 같은 폴더의
`*_status.json`, 로그 위치는 `launcher.json`에서 확인한다. 서부 일일 한도는 기본 100회이며,
승인된 한도가 바뀐 경우에만 `--iwest-daily-limit`를 조정한다.
`*_backfill_queue.json`·`*_bridge_queue.json`에 다음 페이지와 건너뛴 요청을 보존한다.
`data/source_catalogs/komipo.json`은 전체 공개 코드표·원문 해시·갱신 상태를 기록한다.

과거 결과 검사는 `python scripts/audit_public_history.py`, 오프라인 수집기 테스트는
`python -m pytest scripts/tests -q`로 실행한다.

가상 수집값임을 명시하는 데모는 직접 선택해서 한 번 실행합니다.
실제 외부 API 요청이나 모델 예측·정산 계산은 하지 않습니다.

```bash
docker compose exec -e PIPELINE_MODE=demo worker python -m backend.app.cli run collect
docker compose exec worker python -m backend.app.cli run predict
docker compose exec worker python -m backend.app.cli run settle
docker compose exec worker python -m backend.app.cli run retrain
```

데모 수집은 관측·기상·시장 예시 3건을 DB에 저장합니다. 같은 시간대 재실행은 중복 저장하지 않습니다.
웹에서 새로고침하면 조회할 수 있고, 데모 표시는 유지됩니다. 예측·정산·학습은 skipped이며
학습 시도는 학습 이력에도 남습니다. 정산 규칙과 모델 승인 없이 실수치가 만들어지지 않습니다.
주기 데모를 원하면 `.env`의 모드를 `demo`로 바꾸고 `docker compose up -d`로 적용합니다.

## 구조

```text
API/                  기존 외부 데이터 수집기, 경로 보존
api.py                기존 CSV 수집 CLI, 경로 보존
requirements.txt      기존 수집기용 의존성
backend/app/          API·ORM·작업·계약·연동 adapter
backend/migrations/   버전 관리 DB migration
backend/tests/        작업·승인·API 테스트
frontend/             React + Vite, Nginx 프록시
scripts/              환경 생성·실행 검증
docs/                 조사·설계·협업·진행 문서
.github/              PR 양식 (CI 템플릿은 docs/ci/)
compose.yaml          전체 개발 환경
```

## 검증과 일상 명령

```bash
docker compose --profile test build tests
docker compose run --rm tests python -m ruff check backend scripts
docker compose run --rm tests
python scripts/smoke.py
docker compose run --rm migrate alembic -c backend/alembic.ini check
docker compose ps -a
docker compose logs --tail 100 api worker migrate
docker compose down
```

Compose 테스트는 SQLite와 실제 PostgreSQL의 격리 schema에서 실행합니다.
기반 구조 검증에서 54개 테스트가 통과했습니다. 이 과거 결과는 이후 추가한 live 수집의 검증 결과가 아닙니다.
2026-11-19 문서 인계 시점에는 이 PC의 Docker 엔진 연결이 안 되어 컨테이너 재검증을 하지 않았습니다.
자세한 근거와 미검증 범위는 [검증 기록](docs/VALIDATION.md)에 있습니다.

`down`은 컨테이너를 중지하고 볼륨을 보존합니다. `down -v`는 DB와 모델 볼륨을 지우므로
기존 데이터를 유지할 때 사용하지 마세요. 암호 변경은 초기화된 DB의 암호를 자동 변경하지 않습니다.
포트 충돌 시 `.env`의 POSTGRES_PORT/API_PORT/WEB_PORT를 바꾸고 실행합니다.
`smoke.py`의 기본 확인 포트도 함께 조정해야 합니다.

## DataGrip 및 로컬 개발

PostgreSQL 연결: host `localhost`, port `.env`의 POSTGRES_PORT(기본 5432),
database `solar_believer`, user `solar`, password는 로컬 `.env`의 값입니다.
비밀번호·DB 데이터는 Git에 올리지 않습니다. 스키마 변경은 GUI 수작업 대신 Alembic으로 기록하세요.

Python 개발은 Python 3.12 가상환경에서 `pip install -r backend/requirements-dev.txt`,
DB 기동·migration 적용 후 `uvicorn backend.app.main:app --reload`로 실행합니다.
UI 개발은 Node 24에서 `cd frontend`, `npm ci`, `npm run dev`로 실행하고
Vite가 로컬 8000번 API로 프록시합니다. API 8000번이 이미 컨테이너에서 사용 중이면
`docker compose stop api web` 후 로컬 API를 실행하세요. 로컬 UI만 개발하면 기존 컨테이너 API를 사용할 수 있습니다.

초기 구조는 인증 없는 로컬 개발 환경입니다. 공개 운영 배포·실 API 연결·모델 학습·정산 확정은
[로드맵](docs/ROADMAP.md)의 후속 범위입니다.

## PostgreSQL 데이터 이전과 다른 PC 이어받기

[DB 적재·백업·복원 안내](docs/POSTGRES_HANDOFF.md)를 따른다. 실제 적재 건수 대조는 `python -m scripts.verify_postgres_ingestion`으로 확인한다. DB 백업과 이어받기 상태는 `transfer/postgres_20261122/manifest.json` 및 백업 조각으로 제공한다.
