# 파일별 역할과 수정 위치

`API/`는 기존 수집기다. `backend/`는 새 서비스다. 처음 작업할 때는 README →
TEAM_HANDOFF → ARCHITECTURE → 이 문서 순서로 읽는다. REPOSITORY_AUDIT은 최초 조사 기록이다.

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
| `backend/app/models.py` | 10개 업무 테이블·관계·제약 | DB 변경 시 새 migration 동반 |
| `backend/app/schemas.py` | API 응답 타입 | 클라이언트 계약 변경 |
| `backend/app/main.py` | 상태·요약·6종 조회 API | 조회·필터 추가 |
| `backend/app/dashboard.py` | 지도/그룹/기간/모델 조회·엄격한 집계·가상 데모 | 필터·집계 정책 변경 |
| `backend/app/contracts.py` | 수집·추론·학습·정산 계약 | A/B 합의 후 인터페이스 변경 |
| `backend/app/adapters/__init__.py` | 연동 패키지 | 일반적으로 수정 불필요 |
| `backend/app/adapters/demo.py` | 명시적 가상 수집값 | 인프라 확인용 데이터 변경 |
| `backend/app/adapters/legacy.py` | 기존 API·모델·학습·정산 연결 자리 | 실제 연동 구현의 시작점 |
| `backend/app/services.py` | 중복 방지 저장·cutoff·모델 승인/적용 | 공통 업무 로직 추가 |
| `backend/app/jobs.py` | 작업 실행·상태·롤백·학습 이력 | 연동 결과의 DB 저장 연결 |
| `backend/app/worker.py` | cron·월 1회 학습·예측 후 정산 호출 자리 | 스케줄 정책 변경 |
| `backend/app/forecast_schedule.py` | 하루 8회 예보의 발표분 선택·날짜 경계 처리 | 예보 입수 버퍼 정책 변경 |
| `backend/app/cli.py` | 수동 작업·승인·적용 | 운영자 명령 추가 |
| `backend/alembic.ini` | migration 경로 | 보통 수정 불필요 |
| `backend/migrations/env.py` | 환경변수에서 DB 연결·메타데이터 | migration 공통 설정 |
| `backend/migrations/script.py.mako` | 새 revision 템플릿 | 팀 migration 양식 변경 |
| `backend/migrations/versions/0001_initial_schema.py` | 초기 DB 스키마 | 공유 후 새 revision으로 확장 |
| `backend/migrations/versions/0002_vpp_dashboard.py` | 발전소·VPP·구성원·VPP 정산 스키마 추가 | 공유 후 새 revision으로 확장 |
| `backend/tests/conftest.py` | 메모리 DB·격리 PostgreSQL schema fixture | 테스트 기반 변경 |
| `backend/tests/test_jobs.py` | 중복·미설정·실패 롤백·cutoff·월별 일정 | 수집/작업 계약 변경 |
| `backend/tests/test_forecast_schedule.py` | 8개 예약 시각·예보 발표분·UTC/KST 날짜 경계 | 예보 일정 변경 |
| `backend/tests/test_registry.py` | 승인·해시·활성 전환·경로 검증 | 모델 생명주기 변경 |
| `backend/tests/test_api.py` | 빈 목록·DB 조회·페이지 크기 | API 계약 변경 |
| `backend/tests/test_dashboard.py` | 그룹/모델·cutoff·기간 경계·결측·가격/정산 버전·데모 불변성 | 대시보드 집계 변경 |
| `frontend/package.json` | React/Vite·개발/빌드 명령 | UI 의존성 변경 |
| `frontend/package-lock.json` | 설치 재현용 전체 의존성 | npm으로 갱신 후 커밋 |
| `frontend/Dockerfile` | Node 빌드→Nginx 이미지 | UI 배포 방식 변경 |
| `frontend/.dockerignore` | node_modules/dist 이미지 제외 | 빌드 출력 추가 |
| `frontend/nginx.conf` | `/api` 프록시·SPA 라우팅 | API upstream/경로 변경 |
| `frontend/vite.config.js` | 로컬 UI 개발 프록시 | 개발 서버 연결 변경 |
| `frontend/index.html` | 한국어 문서·React root | 메타데이터 변경 |
| `frontend/src/api.js` | 조회·HTTP 오류 처리 | 클라이언트 공통 연결 |
| `frontend/src/main.jsx` | 앱 셸·통합 대시보드·기존 관리 탭 전환 | 전체 탐색 구조 변경 |
| `frontend/src/Dashboard.jsx` | 그룹/모델/일·월·연 선택·요약·정산·확보율·URL 상태 | 대시보드 화면 변경 |
| `frontend/src/components/KoreaMap.jsx` | 대한민국 벡터 지도·그룹색·발전소 마커·정보 | 지도 표시 변경 |
| `frontend/src/components/EnergyCharts.jsx` | 발전량 비교·SMP·오차·금액 그래프 | 차트 표시 변경 |
| `frontend/src/components/RecordsPanel.jsx` | 기존 6종 기록 조회 탭 | 관리 표·컬럼 변경 |
| `frontend/src/data/korea.json` | 출처가 기록된 대한민국 지리 형상 | MAP_SOURCE.md와 함께 갱신 |
| `frontend/src/styles.css` | 반응형 레이아웃·색·타이포 | 디자인 변경 |
| `docs/ci/service-scaffold.yml` | CI 활성화용 템플릿: lint·단위·Compose·HTTP·migration | workflow 권한 있는 개발자가 활성화 |
| `.github/pull_request_template.md` | 공동 검토 체크 항목 | 팀 리뷰 양식 |
| `docs/REPOSITORY_AUDIT.md` | 기존 13개 파일 전수 조사 | 후속 조사 근거 추가 |
| `docs/ARCHITECTURE.md` | 시스템·DB·작업·모델 설계 | 구조 결정 변경 |
| `docs/INTEGRATIONS.md` | API·모델·학습·정산 후속 연결 절차 | 계약 구체화 |
| `docs/TEAM_HANDOFF.md` | 2026-11-19 데이터 조사·200개 지속 수집 목표·작업 ID·코드 결함·실행 순서 | 팀원의 실제 호출·구현·검증 결과로 상태 갱신 |
| `docs/SETTLEMENT_RULES.md` | 20MW 초과 조건·SMP 판매금액 추정·예측 인센티브·예외·검증 항목 | 규칙 버전·정산 엔진 구현 시 함께 갱신 |
| `API/koen_collector.py` | 남동발전 개별 호기 시간별 실측·하드코딩 메타데이터 | API 응답·신규 ID·실제 용량·좌표 검증 |
| `data/raw/` | main에 추가된 6개 원천 CSV | 원본 보존. DB 자동 적재가 아니며 수정 결과는 별도 버전으로 관리 |
| `docs/DATA_REQUIREMENTS.md` | VPP·정산 목적의 원천 목록·갱신 주기·부족한 입력 | 데이터/수익 범위 결정 |
| `docs/SYSTEM_OVERVIEW.md` | 현재 Docker·DB 구조도와 발전소별 20MW VPP 확장안 | 현재/예정 schema 변경 |
| `docs/DASHBOARD.md` | 조회 API·시간/결측/오차·정산 집계 정책·데모 구분 | 대시보드 계약 변경 |
| `docs/MAP_SOURCE.md` | 지도 데이터 URL·해시·라이선스·축척 | 지도 자산 변경 |
| `docs/DEVELOPMENT_WITHOUT_DOCKER.md` | Docker 없는 팀원의 Python·React·PostgreSQL 개발 | 로컬 실행 절차 변경 |
| `docs/COLLABORATION.md` | 2인 담당 제안·브랜치·DB 변경 | 협업 방식 변경 |
| `docs/STATUS.md` | 완료/진행 중/예정 | 매 PR 갱신 |
| `docs/ROADMAP.md` | 목표·단계·완료 조건 | 우선순위 변경 |
| `docs/VALIDATION.md` | 이번 실행 검증 근거·한계 | 검증 후 갱신 |
| `docs/LEGACY_README.md` | 기존 README 원문 | 과거 기록 보존 |

기존 `API/`의 각 파일, `api.py`, 기존 `requirements.txt`의 역할은
[전체 조사표](REPOSITORY_AUDIT.md)에 설명되어 있다. 자동 생성된 캐시·로컬 `.env`·
볼륨 데이터는 공유 파일이 아니며 Git에 올리지 않는다.
