# Docker 없는 팀원의 개발 방법

Docker는 전체 개발 환경을 동일하게 재현하는 권장 경로이며 코드 수정의 필수 조건은 아니다.
화면 개발에는 Node, Python 코드·기본 테스트에는 Python, DB 조회·작업 실행에는 PostgreSQL이 필요하다.
DataGrip은 DB 관리 클라이언트이며 PostgreSQL 서버를 대신하지 않는다.

## 작업별 준비

| 할 일 | 준비 | 확인 범위 |
|---|---|---|
| 코드·문서 수정 | Git·편집기 | Docker 불필요 |
| React 개발·빌드 | Node 24·npm | 화면 수정 가능. API 미실행 시 조회 오류 안내가 표시됨 |
| Python 단위 테스트 | Python 3.12·개발 의존성 | SQLite 테스트 가능. PostgreSQL 케이스는 기본 skip |
| FastAPI·수집 작업·통합 테스트 | 위 환경 + 개인 개발용 PostgreSQL 17 | 실제 DB 연결, migration, 조회·중복 저장 검증 |
| 전체 Compose 검증 | Docker 있는 팀원 환경 | PR 공동 검토 때 재현 |

## Python 준비

저장소 루트의 PowerShell에서 실행한다. 가상환경 활성화 없이 실행 파일 경로를 사용한다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe scripts/init_env.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend scripts
```

`py` 명령이 없으면 설치한 Python 3.12 실행 파일 경로를 사용한다.
기본 pytest는 SQLite 케이스를 실행하고 `RUN_POSTGRES_TESTS`가 설정되지 않은 PostgreSQL 케이스는 건너뛴다.
API 키는 이 테스트에 필요하지 않다. 기존 수집기 전용 루트 `requirements.txt`와 새 서비스 의존성을 구별한다.

## DB와 백엔드 실행

1. PostgreSQL 17을 설치·실행한다.
2. 개인 개발 DB `solar_believer`, 로그인 사용자 `solar`를 생성하고 해당 DB 소유자로 지정한다.
   암호는 로컬 `.env`의 `POSTGRES_PASSWORD`와 맞춘다. DB 생성은 관리자 연결에서 진행한다.
3. `.env`의 `POSTGRES_HOST=localhost`, `POSTGRES_PORT=5432`를 로컬 설치에 맞춘다.
4. 루트에서 아래 명령으로 schema를 적용한 뒤 서버를 실행한다.

```powershell
.\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head
.\.venv\Scripts\uvicorn.exe backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

별도 터미널에서 필요할 때만 worker를 실행한다.

```powershell
.\.venv\Scripts\python.exe -m backend.app.worker
```

실제 PostgreSQL 테스트는 개인 개발 DB를 지정한 환경에서 실행한다.
테스트는 무작위 `test_<uuid>` schema를 사용하며 서비스의 `public` schema를 수정하지 않는다.

```powershell
$env:RUN_POSTGRES_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:RUN_POSTGRES_TESTS
```

## 프런트 실행

```powershell
cd frontend
npm ci
npm run dev
```

기본 주소는 `http://localhost:5173`이며 `/api` 요청은 로컬 8000번 FastAPI로 프록시한다.
Docker 경로는 `http://localhost:3000`이므로 주소를 구별한다. 같은 컴퓨터에서
Docker API와 로컬 API를 함께 실행하려면 포트 충돌을 해소해야 한다.
실제 API 키 발급을 맡은 팀원이 준비하기 전까지 live 수집은 미연동 상태로 둔다.
