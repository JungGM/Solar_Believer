# 파일별 역할과 수정 위치

`API/`는 기존 수집기다. `backend/`는 새 서비스다. 처음 작업할 때는 README →
REPOSITORY_AUDIT → ARCHITECTURE → 이 문서 순서로 읽는다.

| 파일 | 역할 | 수정할 때 |
|---|---|---|
| `README.md` | 실행·검증·문서 입구 | 실행 명령/범위 변경 |
| `.env.example` | 키 없는 환경변수 목록 | 설정 추가, 실제 키 금지 |
| `.gitignore` | 데이터·키·빌드 출력 제외 | 새 출력 경로 추가 |
| `.dockerignore` | Python 이미지 컨텍스트에서 개인 파일 제외 | 이미지에 필요한 경로 변경 |
| `compose.yaml` | DB·migration·API·worker·web·tests 연결 | 포트/볼륨/서비스 변경 |
| `pyproject.toml` | pytest 검색 및 Ruff 규칙 | 테스트·검사 설정 |
| `scripts/init_env.py` | 기존 `.env` 보존, 로컬 DB 암호 생성 | 초기 환경 설정 변경 |
| `scripts/smoke.py` | 실행 중 HTTP·프록시·조회 검사 | API 경로 변경 |
| `backend/Dockerfile` | Python runtime/test 이미지 | OS/런타임 의존성 변경 |
| `backend/requirements.txt` | 새 서비스 패키지 버전 | Python 패키지 추가 |
| `backend/requirements-dev.txt` | 테스트·검사 도구 | 개발 도구 변경 |
| `backend/__init__.py` | 서비스 패키지 식별 | 일반적으로 수정 불필요 |
| `backend/app/__init__.py` | 앱 패키지 식별 | 일반적으로 수정 불필요 |
| `backend/app/config.py` | 타입 있는 환경 설정, DB URL | 설정 추가 |
| `backend/app/db.py` | Engine·Session·ORM Base | DB 연결/트랜잭션 공통 정책 |
| `backend/app/models.py` | 6개 업무 테이블·관계·제약 | DB 변경 시 새 migration 동반 |
| `backend/app/schemas.py` | API 응답 타입 | 클라이언트 계약 변경 |
| `backend/app/main.py` | 상태·요약·6종 조회 API | 조회·필터 추가 |
| `backend/app/contracts.py` | 수집·추론·학습·정산 계약 | A/B 합의 후 인터페이스 변경 |
| `backend/app/adapters/__init__.py` | 연동 패키지 | 일반적으로 수정 불필요 |
| `backend/app/adapters/demo.py` | 명시적 가상 수집값 | 인프라 확인용 데이터 변경 |
| `backend/app/adapters/legacy.py` | 기존 API·모델·학습·정산 연결 자리 | 실제 연동 구현의 시작점 |
| `backend/app/services.py` | 중복 방지 저장·cutoff·모델 승인/적용 | 공통 업무 로직 추가 |
| `backend/app/jobs.py` | 작업 실행·상태·롤백·학습 이력 | 연동 결과의 DB 저장 연결 |
| `backend/app/worker.py` | cron·월 1회 학습·예측 후 정산 호출 자리 | 스케줄 정책 변경 |
| `backend/app/cli.py` | 수동 작업·승인·적용 | 운영자 명령 추가 |
| `backend/alembic.ini` | migration 경로 | 보통 수정 불필요 |
| `backend/migrations/env.py` | 환경변수에서 DB 연결·메타데이터 | migration 공통 설정 |
| `backend/migrations/script.py.mako` | 새 revision 템플릿 | 팀 migration 양식 변경 |
| `backend/migrations/versions/0001_initial_schema.py` | 초기 DB 스키마 | 공유 후 새 revision으로 확장 |
| `backend/tests/conftest.py` | 메모리 DB·격리 PostgreSQL schema fixture | 테스트 기반 변경 |
| `backend/tests/test_jobs.py` | 중복·미설정·실패 롤백·cutoff·월별 일정 | 수집/작업 계약 변경 |
| `backend/tests/test_registry.py` | 승인·해시·활성 전환·경로 검증 | 모델 생명주기 변경 |
| `backend/tests/test_api.py` | 빈 목록·DB 조회·페이지 크기 | API 계약 변경 |
| `frontend/package.json` | React/Vite·개발/빌드 명령 | UI 의존성 변경 |
| `frontend/package-lock.json` | 설치 재현용 전체 의존성 | npm으로 갱신 후 커밋 |
| `frontend/Dockerfile` | Node 빌드→Nginx 이미지 | UI 배포 방식 변경 |
| `frontend/.dockerignore` | node_modules/dist 이미지 제외 | 빌드 출력 추가 |
| `frontend/nginx.conf` | `/api` 프록시·SPA 라우팅 | API upstream/경로 변경 |
| `frontend/vite.config.js` | 로컬 UI 개발 프록시 | 개발 서버 연결 변경 |
| `frontend/index.html` | 한국어 문서·React root | 메타데이터 변경 |
| `frontend/src/api.js` | 조회·HTTP 오류 처리 | 클라이언트 공통 연결 |
| `frontend/src/main.jsx` | 6개 탭·요약·빈 상태·에러·KST 표시 | 화면/컬럼 추가 |
| `frontend/src/styles.css` | 반응형 레이아웃·색·타이포 | 디자인 변경 |
| `docs/ci/service-scaffold.yml` | CI 활성화용 템플릿: lint·단위·Compose·HTTP·migration | workflow 권한 있는 개발자가 활성화 |
| `.github/pull_request_template.md` | 공동 검토 체크 항목 | 팀 리뷰 양식 |
| `docs/REPOSITORY_AUDIT.md` | 기존 13개 파일 전수 조사 | 후속 조사 근거 추가 |
| `docs/ARCHITECTURE.md` | 시스템·DB·작업·모델 설계 | 구조 결정 변경 |
| `docs/INTEGRATIONS.md` | API·모델·학습·정산 후속 연결 절차 | 계약 구체화 |
| `docs/COLLABORATION.md` | 2인 담당 제안·브랜치·DB 변경 | 협업 방식 변경 |
| `docs/STATUS.md` | 완료/진행 중/예정 | 매 PR 갱신 |
| `docs/ROADMAP.md` | 목표·단계·완료 조건 | 우선순위 변경 |
| `docs/VALIDATION.md` | 이번 실행 검증 근거·한계 | 검증 후 갱신 |
| `docs/LEGACY_README.md` | 기존 README 원문 | 과거 기록 보존 |

기존 `API/`의 각 파일, `api.py`, 기존 `requirements.txt`의 역할은
[전체 조사표](REPOSITORY_AUDIT.md)에 설명되어 있다. 자동 생성된 캐시·로컬 `.env`·
볼륨 데이터는 공유 파일이 아니며 Git에 올리지 않는다.
