# Docker 실행과 업데이트

2026-11-23 KST 기준 작업 브랜치는 `codex/open-meteo-collection`이다.
현재 `main`에는 이 Compose 구성이 없으므로 아래 브랜치를 선택한다.

## 새 PC에서 웹·API 실행

Docker Desktop의 Linux containers, Git, Python을 준비한다.
Python은 환경파일 생성용이며 웹 실행에는 로컬 Python/Node 패키지 설치가 필요 없다.

```powershell
git clone https://github.com/JungGM/Solar_Believer.git
cd Solar_Believer
git switch codex/open-meteo-collection
python scripts/init_env.py
docker version
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
python scripts/smoke.py
```

`init_env.py`는 임의의 DB 비밀번호와 승인된 수집용 키 기본값으로 `.env`를 생성한다.
기존 `.env`는 덮어쓰지 않는다. 다른 키를 쓰면 환경변수 또는 `.env`의 비어 있지 않은 값으로 지정한다.

- 웹: [http://localhost:3000](http://localhost:3000)
- API 문서: [http://localhost:8000/docs](http://localhost:8000/docs)
- DB 준비 상태: [http://localhost:8000/health/ready](http://localhost:8000/health/ready)

처음 실행한 DB에는 과거 자료가 없다. Git 복제나 이미지 빌드가 기존 DB를 가져오지는 않는다.
자료 이전은 [PostgreSQL 백업·복원](POSTGRES_HANDOFF.md)을 따른다.
`PIPELINE_MODE=disabled`가 기본이며 모델·정산은 설정과 실제 결과가 있어야 표시된다.

## 서비스 구성

| 서비스 | 역할 | 실행 조건·저장 위치 |
|---|---|---|
| `db` | PostgreSQL 17 | 기본, `postgres_data` 볼륨 |
| `migrate` | Alembic 스키마 적용 | DB healthy 후 1회 실행, 정상 종료 코드 0 |
| `api` | FastAPI | migration 성공 후, `/app/data`는 호스트 `data/` 읽기 전용 |
| `worker` | 작업 스케줄러 | migration 성공 후, 기본 파이프라인 미설정 |
| `web` | React production build·Nginx | API healthy 후, `/api/`를 API로 프록시 |
| `collector` | 발전량·SMP 수집 | `collector` 프로필, 명시적 선택 실행 |
| `open-meteo-collector` | 과거 예보·내일 예보 수집 | `collector` 프로필, 명시적 선택 실행 |
| `kma-collector` | 보존된 기상청 Hub 경로 | `kma` 프로필, 명시적 선택 실행 |
| `tests` | pytest·Ruff | `test` 프로필, 실제 PostgreSQL 테스트 포함 |

수집기는 같은 Python runtime 설정을 사용하고 migration 성공 후 실행한다.
`.env`는 Compose 환경변수로 전달하며 컨테이너 안에 파일로 마운트하지 않는다.
수집기 `data/`는 읽기/쓰기이며 종료 요청 후 최대 90초를 기다린다.
API의 `SOLAR_ASSET_DATA_DIR=/app/data`는 로컬 자료 조회 경로다. 해당 파일이 없으면 자료가 생기지 않는다.
DB·모델은 named volume, 수집 상태·원문 파일은 호스트 `data/`에 보관한다.
`data/`, `transfer/`, `.env`, 로컬 캐시와 프런트 빌드 출력은 Python 이미지 빌드에서 제외한다.

## 기존 PC 업데이트

로컬 수정이 있으면 먼저 `git status`로 확인하고 저장한다. `pull --ff-only`가 실패하면 강제 초기화하지 않는다.

```powershell
git status
git pull --ff-only
python scripts/init_env.py
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
python scripts/smoke.py
docker compose ps -a
```

위 명령은 기본 웹 서비스 업데이트다. 수집 프로필을 켜지 않으므로 중지된 수집기를 새로 시작하지 않는다.
이미 실행 중인 수집기는 아래 해당 서비스 업데이트 명령으로 별도 반영한다.
`restart`만으로는 변경된 이미지·환경설정이 반영되지 않으므로 `up -d --build`를 사용한다.
`docker compose down`은 볼륨을 보존한다. 기존 DB를 유지할 때 `down -v`를 사용하지 않는다.
이미 초기화한 DB의 암호는 `.env` 값만 바꿔도 변경되지 않는다.

## 수집 PC에서만 선택 실행

같은 키를 사용하는 다른 PC·네이티브 수집기의 실행 여부를 먼저 확인한다.
아래 명령은 해당 수집을 담당하는 PC에서 필요한 서비스만 실행한다.

```powershell
# 발전량·SMP
docker compose --profile collector up -d --build collector
# Open-Meteo 기상
docker compose --profile collector up -d --build open-meteo-collector
# 기존 기상청 경로가 필요한 경우에만
docker compose --profile kma up -d --build kma-collector
docker compose logs --tail 30 collector open-meteo-collector
```

프로필 이름만 주고 서비스 이름을 생략하면 그 프로필의 모든 수집기가 실행된다.
기상 수집 범위·중지 절차는 [Open-Meteo 안내](OPEN_METEO_COLLECTION.md)를 따른다.
PC 절전·종료 또는 Docker 중지 동안은 수집할 수 없다.

## 검증과 문제 확인

```powershell
docker compose --profile test build tests
docker compose run --rm tests python -m ruff check backend scripts
docker compose run --rm tests
docker compose run --rm migrate alembic -c backend/alembic.ini check
docker compose logs --tail 80 api web worker migrate
```

테스트 이미지는 환경파일 생성 검사에 필요한 `.env.example`도 포함한다.
프런트 지도 `frontend/src/data/korea.json`은 수집 원본과 달리 Git에 포함하는 빌드 자산이다.
테스트의 PostgreSQL schema는 격리 생성·제거하며, 외부 수집기를 시작하지 않는다.
테스트 서비스는 실제 `.env`의 수집용 키를 빈 환경변수로 덮어써 API 키 유무에 따라 테스트 결과가 달라지는 것을 막는다.
포트 충돌 시 `.env`의 `POSTGRES_PORT`, `API_PORT`, `WEB_PORT`를 변경하고 `up -d`로 적용한다.
예를 들어 API 8001·웹 3001이면 다음과 같이 확인한다.

```powershell
python scripts/smoke.py --api-url http://localhost:8001 --web-url http://localhost:3001
```

실제 검증 결과와 한계는 [검증 기록](VALIDATION.md)을 따른다.
