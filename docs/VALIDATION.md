# 1차 구조 검증 기록

검증일: 2026-11-17 (KST). Windows 로컬 Docker Desktop의 Linux containers에서 실행했다.
기준 원본은 `main@12e9e44`, 변경 브랜치는 `codex/initial-service-architecture`다.

| 확인 항목 | 결과 | 근거 |
|---|---|---|
| Docker Desktop | 통과 | 최초 엔진 미기동, `docker desktop start` 후 Server 4.99.0 / Engine 29.9.3 / linux/amd64 연결 |
| Compose | 통과 | v5.7.0, `docker compose config --quiet` |
| 전체 이미지 빌드 | 통과 | Python 3.12 runtime/test, PostgreSQL 17.7, React/Vite 7.3.8→Nginx |
| 서비스 기동 | 통과 | DB/API/Web healthy, worker 실행, migrate exit 0 |
| DB migration | 통과 | `upgrade head`, 재실행, `alembic check`: 변경 누락 없음 |
| 코드 검사 | 통과 | `docker compose run --rm tests python -m ruff check backend scripts` |
| 업무 테스트 | 통과 | **22 passed**: SQLite 및 실제 PostgreSQL 격리 schema에서 실행 |
| 조회·프록시 | 통과 | `scripts/smoke.py`: readiness, 6종 DB 조회, web→API 프록시, React HTML |
| 데모 수집 | 통과 | 명시적 demo 실행으로 3건 저장, 재실행 저장 건수 0, 출처·demo 표시 유지 |
| 미연동 작업 | 통과 | predict/settle/retrain skipped, 학습 시도도 training_runs에 남음 |
| 스케줄 | 통과 | 월별 cron의 다음 두 실행이 12월/1월 1일 03:00 KST; 실제 worker 예측 예약 실행 skipped 확인 |
| 재시작 내구성 | 통과 | db/api 재시작 후 3건 유지, 전체 HTTP 확인 재통과 |
| 브라우저 | 통과 | 기본 화면의 빈 상태, 데모 3건, 작업 완료·연동 대기·저장 건수와 KST 표시 확인 |
| 프런트 의존성 | 통과 | `npm audit`: 0 vulnerabilities (확인 당시), npm ci 및 production build 성공 |
| 기존 파일 보존 | 통과 | 수정 대상 4개를 제외한 기존 9개 내용 동일, 기존 README 원문 백업 동일 |
| 키 관리 | 통과 | 현재 APIConfig 기본값 공백, 실제 키 생성/사용 없음, `.env` Git 제외 |

테스트는 중복 원천 데이터·실행 키, 미설정 상태, 실패 시 부분 데이터 롤백 및 예외 메시지,
예보 발표/입수 가능 시각·demo 필터, 모델 승인 전 적용 차단, SHA-256/경로 검사,
활성 모델 전환, DB 조회와 목록 제한을 검증한다. PostgreSQL 테스트는 매번 무작위
`test_<uuid>` schema를 생성·제거하며 서비스의 `public` schema를 변경하지 않는다.

실제 PostgreSQL 확인 중 ORM INSERT의 rowcount가 -1을 반환하는 문제를 발견했다.
반환된 INSERT ID 수로 집계하도록 수정하고 신규 3건·중복 0건을 두 DB에서 검증했다.
검증 초기에 생성한 데모 작업 2개의 잘못된 건수도 실제 저장 결과(3, 0)로 정정했다.

상위 라이브러리 Starlette TestClient에서 AnyIO alias 사용 중단 경고 1개가 있다.
테스트 결과에는 영향이 없었으며 후속 의존성 갱신 때 정리할 항목이다.
CI 설정은 `docs/ci/service-scaffold.yml` 템플릿으로 제공한다. 현재 Git 인증에 workflow 권한이 없어 활성 workflow 경로의 푸시가 거부되었다. 따라서 원격 CI는 아직 실행되지 않았으며 위 결과는 로컬 검증 결과다. 권한 있는 개발자가 템플릿을 `.github/workflows/service-scaffold.yml`로 복사해 활성화할 수 있다.

## 검증 범위 밖

실제 외부 API 호출/인증/쿼터, 원천 데이터 정확성, 모델 성능·추론 호환성,
실제 학습, 정산 규칙, 장기 스케줄 운영, worker 강제 종료 후 lease 복구,
모바일 실기기·운영 배포·인증/백업은 검증 완료가 아니다.
기본 데이터 3건은 명확히 가상값이며 모델·정산 계산 결과가 아니다.

## 후속: 예보 8회 일정과 발전소별 VPP 방향 반영

사용자 승인에 따라 예보를 02:20·05:20·08:20·11:20·14:20·17:20·20:20·23:20 KST로
별도 예약했다. 현재 worker의 네 작업 collect/forecast/predict/retrain trigger를 확인했다.
예보 발표분 선택은 20분 버퍼, 자정 이전 날짜, UTC→KST 변환을 테스트한다.
실제 예보 호출·발전소 격자 매핑·미제공 재시도는 미구현이며 live도 skipped를 기록한다.

변경 후 Ruff와 **33개 테스트**가 통과했고 Compose 재기동 및 HTTP smoke도 통과했다.
DB는 여전히 업무 테이블 6개와 alembic_version 1개다. 발전소/VPP 구성 테이블은 추가되지 않았다.
키 발급·활용 신청은 다른 팀원이 맡으며, 키 값을 출력하지 않고 확인한 로컬 설정은 두 키 모두 미설정이다.
Docker 없는 개발 방법은 문서화했으며 별도 팀원의 비Docker 환경에서 실행 검증한 것은 아니다.

가짜 예보 응답을 사용해 05시 발표분 중복 저장 방지와 08시 발표분 별도 보존도 두 DB에서 확인했다. 실제 API 요청은 발생시키지 않았다.
