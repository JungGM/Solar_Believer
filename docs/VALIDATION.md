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

## 후속: 한국 지도와 VPP·기간·모델 대시보드

`0002_vpp_dashboard` migration으로 발전소·VPP·구성원·VPP 정산 결과 테이블 4개를
추가했다. 기존 3개 demo observation은 유지되었고 서비스 DB의 plants/models는 0개로
유지되었다. UI 데모는 읽기 전용 메모리 생성이며 실제 자산·예측·금액을 등록하지 않는다.

최신 Ruff·**54개 테스트**가 통과했다. SQLite/실제 PostgreSQL에서 VPP 내부 집계 후
오차, 서로 다른 VPP 오차의 비상쇄, KST 경계·윤년·기간 집계, 결측과 실제 0 구분,
입수 자료 개정·원천 선택, 대상 이후 생성 예측 제외, 모델/그룹 필터, 가격 구역,
정산 버전 미혼합, 구성원 적용 기간, 좌표 누락, KST 수집값의 UTC 저장,
데모의 실제값 불변성과 DB 미변경을 확인했다.

전체 이미지 빌드와 migration 재적용·check, HTTP smoke에 대시보드 옵션·24개 시간 구간·
명시적 demo의 20MW/4개 발전소·금액 미설정을 포함해 확인한다.
브라우저에서 실제 빈 상태, demo 전체 8개/그룹 4개 지도, 발전소 선택 정보,
모델 변경 시 예측만 변경, 일/월/연 24/720/8760시간 조회와 차트, 좁은 화면 배치를
확인했다. 좁은 화면 document width와 scrollWidth가 375px로 같았으며 지도/차트가
세로로 재배치되었다. 브라우저 console error/warn은 없었다.

지도 데이터는 출처·다운로드 해시가 기록된 Natural Earth 형상을 번들에 넣었다.
프런트 npm 설치 audit에서 취약점 0개였고 production build가 통과했다.
차트·React 의존성을 별도 chunk로 나눴다. 원격 CI는 여전히 템플릿이며 실행되지 않았다.
실제 API/모델 성능·정산 엔진·자동 VPP 배치·대량 자산 부하 검증은 이번 결과에 포함하지 않는다.

## 2026-11-19 팀원 인계 문서 검증

기준: `main@2a58fe2`, 브랜치 `codex/team-data-handoff`. 이번 변경은 Markdown 문서만 포함한다.
위 11-17 기반 구조의 54 tests 통과 기록을 이후 live 어댑터의 통과 결과로 확대하지 않는다.

| 확인 항목 | 결과 | 범위 |
|---|---|---|
| 최신 원격 확인 | 완료 | origin/main의 4512c8b·2a58fe2와 기존 PR #1 상태 확인 |
| 원천 CSV 기본 집계 | 완료 | 6개 파일 행 수·시간 범위, 남동 23개 ID·메타데이터 등록 용량, SMP 육지/제주 행 수. TEAM_HANDOFF.md 참고 |
| 알려진 호출 인자 불일치 | 확인 | AST에서 얻은 실제 함수 서명에 호출 인자를 bind: 관측 api_key·예보 nx가 각각 TypeError. 네트워크 호출/런타임 통합 재현은 아님 |
| 예보 반환값 계약 | 확인 | 기존 함수 DataFrame 반환 컬럼과 원시 dict 목록을 가정한 어댑터 코드 대조 |
| 공식 API·규칙 근거 | 확인 | 남동·남부·중부·서부·SMP 명세와 2026-10-31 규칙 공고 확인. 실제 최신 응답·자원 수는 미검증 |
| 문서 검사 | 통과 | UTF-8 읽기·코드 fence 짝·변경 문서 상대 링크 대상·git diff --check |
| 코드·데이터 보존 | 확인 | 변경 파일은 README/docs의 Markdown만. 원천 CSV·수집기·모델·DB schema 수정 없음 |
| API 키 상태 | 로컬 미설정 | 이 PC .env/호스트 프로세스의 DATA_GO_KR_API_KEY·KMA_APIHUB_KEY는 비어 있음. 팀원 환경은 미확인. 키 값은 출력하지 않음 |
| Docker·실제 서비스 | 미검증 | docker info에서 Linux 엔진 named pipe 연결 실패. 이번에는 컨테이너·DB·HTTP·실제 API·학습·정산 테스트를 실행하지 않음 |

문서의 체크박스·완료 기준은 후속 구현 요구이며 실행 성공 증거가 아니다.
200개 이상의 지속 수집 자원 확보, 과거 예보, DB 적재·품질·정산 엔진 검증은 후속 작업으로 남는다.
