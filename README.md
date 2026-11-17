# Solar Believer

태양광 발전량 예측 및 정산금 조회 웹서비스의 **2인 공동 개발용 초기 구조**입니다.
기존 공공데이터 수집 코드를 보존하고 PostgreSQL·FastAPI·React·작업 스케줄러를 추가했습니다.
현재 실제 API·모델·학습·정산 규칙 연동은 후속 단계이며, 예측/정산 숫자를 임의 생성하지 않습니다.
목표는 **개별 발전소별 발전량 예측 → VPP당 목표 20MW로 구성 → SMP·정산식 적용**입니다.
기존 시도별 발전량 수집기는 보존하며 개별 발전소 실측의 대용으로 사용하지 않습니다.

## 먼저 읽기

- [저장소 전체 조사](docs/REPOSITORY_AUDIT.md): 기존 13개 파일과 주의할 수집기 동작
- [시스템 아키텍처](docs/ARCHITECTURE.md): 서비스·DB·스케줄·모델 생명주기
- [전체 구조도·Docker/DB 구분](docs/SYSTEM_OVERVIEW.md) / [Docker 없는 팀원 개발](docs/DEVELOPMENT_WITHOUT_DOCKER.md)
- [지도·VPP·기간·모델 대시보드](docs/DASHBOARD.md) / [지도 데이터 출처](docs/MAP_SOURCE.md)
- [파일별 역할·수정 위치](docs/FILE_GUIDE.md) / [2인 협업](docs/COLLABORATION.md)
- [목표·로드맵](docs/ROADMAP.md) / [완료·진행 중·예정](docs/STATUS.md)
- [실제 연동 안내](docs/INTEGRATIONS.md) / [검증 기록](docs/VALIDATION.md)
- [필요한 데이터·API와 수집 주기](docs/DATA_REQUIREMENTS.md)
- [기존 README 원문](docs/LEGACY_README.md) / [기존 API 문서](API/README.md)

조사 기준 `main@12e9e44`에는 데이터 수집 코드만 있으며 모델 파일·학습 코드는 없습니다.
기존 설정의 인증키 기본값을 제거했습니다. 과거 Git에 들어간 키는 발급처에서 폐기·재발급하세요.

## 빠른 실행

필수: Git, Python(환경파일 생성용), Docker Desktop의 Linux containers와 Compose.
DataGrip은 선택이며 로컬 Python/Node 패키지 설치 없이 Compose로 실행할 수 있습니다.

```bash
git clone https://github.com/JungGM/Solar_Believer.git
cd Solar_Believer
# PR 병합 전: git switch codex/initial-service-architecture
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
실제 예보 API→DB 매핑과 미제공 시 재시도는 아직 미구현입니다.
빈 화면은 오류가 아니며 모델과 정산 기록은 연동 후 표시됩니다.
대시보드는 한국 지도와 VPP 그룹·모델·일/월/연 필터, 예측/실측·SMP·오차 그래프,
저장된 VPP 정산/손실 조회, 발전소 목록·데이터 확보율을 제공합니다.
UI의 데모는 메모리에서만 생성하며 DB를 변경하지 않습니다. 정산 규칙이 없으면 금액은 미설정입니다.

## 데이터 흐름 확인

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
최신 검증 시 54개 테스트가 통과했습니다. 자세한 근거와 미검증 범위는 [검증 기록](docs/VALIDATION.md)에 있습니다.

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
