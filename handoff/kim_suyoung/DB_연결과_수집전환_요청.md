# 팀원 Open-Meteo 수집 → 메인 PostgreSQL 연결 및 이관 요청

2026-11-25 KST 사용자 요청. 팀원 PC에서 실행 중인 **최신 담당 월부터 과거 방향으로 수집하는 작업**을 유지한다.
메인 DB 연결 확인 후, 이후 API 응답은 메인 DB에도 자동 저장하도록 코드를 수정하고, 이미 받은 응답도 이관한다.
이 문서는 연결 절차와 팀원에게 전달할 작업 요청이다. 현재 `collector.py`에 DB 저장·이관 기능이 구현됐다는 뜻은 아니다.

## 1. 최소 준비물

| 항목 | 준비 방법 |
|---|---|
| 기존 Python | 기존 수집기가 실행되는 Python 3.10 이상을 그대로 사용한다. 재설치·3.12 업그레이드 불필요. |
| Tailscale | 없을 때만 [공식 다운로드](https://tailscale.com/download)에서 설치하고 본인 계정으로 로그인한다. |
| PostgreSQL Python 드라이버 | 기존 환경에 `psycopg`가 없을 때만 아래 설치 명령을 1회 실행한다. |
| 새 안내 파일 | 기존 Git을 사용하거나 GitHub 브라우저에서 파일을 내려받는다. Git 신규 설치는 필수가 아니다. |

팀원 PC에는 **Docker, PostgreSQL 서버, DataGrip/DBeaver, Node.js를 새로 설치할 필요가 없다.**
전체 프로젝트의 `requirements.txt`도 설치하지 않는다. 현재 표준 라이브러리 수집기에 DB 드라이버만 더한다.
이미 전용 Python 환경을 사용 중이면 아래 `py -3` 대신 그 환경의 `python.exe`를 사용한다.
Python 버전 확인은 `py -3 --version`, 드라이버 설치 여부는 `py -3 -c "import psycopg; print(psycopg.__version__)"`으로 확인한다.

## 2. 소유자가 먼저 전달할 것

1. Tailscale 관리 화면의 Machines에서 메인 DB 장치 `node`를 선택하여 **Share**로 팀원에게 장치 공유 초대를 전달한다.
   팀원은 자신의 Tailscale 계정으로 초대를 수락한다. 계정 비밀번호를 공유하지 않는다.
   이미 같은 tailnet의 허용된 구성원이면 추가 초대는 필요 없다.
2. 팀원에서 `100.102.164.7:15432`에 접근할 수 있도록 Tailscale 접근 정책과 서버 방화벽을 확인한다.
   Tailscale 설치만으로 접근 권한까지 생기는 것은 아니다.
3. 아래 DB 계정의 실제 비밀번호를 팀원에게 **별도 비공개 채널**로 전달한다. 현재 값은 메인 DB 관리자가 확인한다.
   비밀번호, 초대 링크, 인증 토큰은 공개 Git 문서·Issue·코드·로그에 기록하지 않는다.

| 연결 항목 | 현재 값 |
|---|---|
| 방식 | Tailscale 사설 연결 |
| Host | `100.102.164.7` |
| Port | `15432` (이 원격 접속에서는 `5432`가 아님) |
| Database | `solar_believer` |
| User | 현재 접속에 확인된 계정 `solar`; 별도 쓰기 계정을 발급받으면 그 계정 사용 |
| Password | 관리자가 별도 전달. Git에는 미포함. |
| 설정 예제 | 같은 폴더의 `db_connection.example.json` (비밀번호 없음) |

서로 다른 집·학교 인터넷이어도 위 Tailscale 연결을 사용한다. 일반 공인 IP 포트포워딩은 이 절차에 필요 없다.
DB PC가 켜져 있고 Tailscale·PostgreSQL이 실행돼 있어야 한다. 다른 PC에서 받은 권한은 팀원 PC에서 다시 확인한다.
전용 계정이 있으면 필요한 Open-Meteo 테이블의 SELECT/INSERT/UPDATE와 사용하는 sequence 권한을 관리자가 부여한다.
진단 결과의 `insert=true`는 실제 쓰기 성공이나 sequence 권한까지 검증했다는 뜻은 아니다.

## 3. 설치와 연결 확인

브랜치: **`codex/open-meteo-collection`**.
전체 프로젝트 복제 없이도 이 폴더의 `check_db.py`, `db_connection.example.json`, `requirements-db.txt`와 이 문서만 받을 수 있다.
기존 실행 중인 수집기 파일·저장 폴더·`state.json`·`local_config.json`을 먼저 보존한다.
기존 `transfer/김수영.zip`은 파일 수집용 이전 패키지이며 이번 DB 전환 기능을 포함하지 않는다.

PowerShell을 위 파일이 있는 폴더에서 열어 실행한다.

```powershell
# 추가 Python 패키지나 비밀번호 없이 네트워크부터 확인
py -3 check_db.py --tcp-only

# import psycopg 확인이 실패한 경우에만, 실제 수집에 쓰는 Python에 설치
py -3 -m pip install -r requirements-db.txt

# 실제 DB 인증·테이블·권한 확인. 비밀번호는 화면에 표시되지 않으며 저장되지 않음
py -3 check_db.py
```

`check_db.py`는 기존 Python 표준 라이브러리 + `psycopg`만 사용한다. `python-dotenv`는 필요 없다.
비밀번호는 대화형으로 입력하거나 현재 프로세스의 `POSTGRES_PASSWORD` 환경변수를 사용한다.
전용 계정 등 설정을 바꾸려면 예제를 `db_connection.local.json`으로 복사하고
`py -3 check_db.py --config db_connection.local.json`을 사용한다. 로컬 설정은 Git에서 제외한다.

성공 기준은 `authenticated=true`, `database=solar_believer`, `transaction_read_only=on`이다.
6개 `public.open_meteo_*` 테이블의 존재와 SELECT/INSERT/UPDATE 권한도 출력한다.
진단은 **읽기 전용**이며 테이블 생성, 데이터 변경, API 호출, 자동수집 시작을 수행하지 않는다.
TCP만 성공한 상태와 DB 인증 성공, 실제 수집·적재 성공은 별도로 구분한다.
테이블이 없거나 권한이 부족하면 관리자에게 결과를 전달한다. 팀원 PC에서 메인 DB 초기화·복원·migration을 실행하지 않는다.

## 4. 팀원에게 요청할 수집기 코드 수정

DB 인증이 끝나면 다음 내용을 구현하고 검증해 달라.

1. **현재 저장 경로·진행 상태·최신→과거 작업 순서·합의된 담당 기간을 유지**한다.
   이 패키지의 기존 기본 범위는 `2026-09-30 → 2022-01-01`이다. 실제 실행 옵션과 상태를 확인하고,
   사용자가 말한 '최신'을 임의로 오늘까지 범위 확대하는 것으로 해석하지 않는다. 합의한 최근 월부터 역순을 유지한다.
   저장소의 `scripts/collect_open_meteo.py`는 현재 오래된 날짜부터 처리하므로 그대로 대체 실행하지 않는다.
2. 메인 DB에 직접 쓰는 선택 기능을 추가한다. 인증정보는 환경변수 또는 Git 제외 로컬 설정으로 읽는다.
   `check_db.py`의 읽기 전용 옵션을 실제 writer 연결에 복사하지 않는다. `compose.laptop.yaml`도 조회용이므로 쓰기 수집에 사용하지 않는다.
3. 원문 파일을 먼저 원자적으로 보존한 뒤, 검증한 원문과 시간별 값을 **같은 DB 트랜잭션**으로 커밋한다.
   DB 커밋 확인 후에만 DB 적재 체크포인트를 기록한다. 파일 수집 완료와 DB 적재 완료 상태를 구분한다.
4. DB 연결이 끊기면 원문과 미적재 큐를 보존하고 재연결 후 재시도한다. 저장된 응답을 적재하기 위해 API를 다시 호출하지 않는다.
   로컬 파일을 안전한 재시도 버퍼로 쓰며 새 SQLite DB를 만들지 않는다.
5. 동일 자료 재실행 시 중복 행이 늘지 않게 안정된 식별자와 충돌 처리를 적용한다.
   다른 응답 버전·model·lead·위치가 섞이지 않도록 하고, 기존 유효값을 NULL로 덮어쓰지 않는다.
6. 다른 PC와 기간·모델·lead·위치가 겹치지 않는지 확인하고 작업 잠금/분담을 적용한다.
   호출 장부를 함께 확인하고 공유 예산을 원자적으로 예약한다. IP가 다르다고 호출 한도를 늘리거나 우회하지 않는다.
   기존 중앙 수집기와 별도 JMA 수집기가 있다면 담당 범위와 실행 여부를 확인한 뒤 DB writer를 가동한다.
7. 요청/수신 시각, 원문 SHA-256, 실제 모델·좌표·자원 연결·변수·단위·NULL을 보존한다.
   과거 예보를 관측/재분석으로 대체하거나 다운로드 시각을 과거 발표시각으로 바꾸지 않는다.

기존 저장 구조는 `scripts/open_meteo_store.py`의 DDL, `parse_hourly`, `Store.save`를 참고한다.
다만 `Store()` 생성자는 스키마 준비 DDL도 실행하므로 쓰기 계정 권한과 기존 테이블을 확인하고 무조건 호출하지 않는다.
독립 패키지에서 전체 backend를 설치하지 않도록 필요한 쓰기 부분만 분리하거나 기존 테이블에 맞는 가벼운 writer를 구현한다.

| 대상 테이블 | 저장 내용 |
|---|---|
| `public.open_meteo_locations` | 좌표·원천 자원 연결과 근거 |
| `public.open_meteo_requests` | 요청 인자·모델·lead·실제 요청/수신 시각·gzip 원문·SHA-256·확보율 |
| `public.open_meteo_hourly` | 응답별 UTC 대상시각·변수값·단위·결측 목록 |
| `public.open_meteo_tasks` | 수집 작업별 진행·오류·재시도 상태; 기존 작업과 충돌 여부 확인 |
| `public.open_meteo_calls` | 실제 API 호출 시각·비용·결과; 이관 자체를 새 API 호출로 기록하지 않음 |
| `public.open_meteo_state` | 재개·DB 적재 체크포인트; 팀원용 키를 분리하고 중앙 상태를 통째로 덮어쓰지 않음 |

과거 고정 lead 자료는 `mode=historical_fixed_lead`로 저장한다. 기존 발전량/SMP/기상청 테이블은 변경하지 않는다.
UTC 저장과 KST 대상일, 24/48시간 lead와 실제 발표·입수시각을 구분한다.
값이 없는 변수는 NULL로 남기며, 일사량·강수량의 직전 1시간 구간과 기온의 시점값을 보존한다.

## 5. 이미 수집한 API 응답도 메인 DB에 넣어 달라는 요청

1. 실제 저장 폴더를 기존 `local_config.json`과 실행 옵션에서 확인한다. 이 경로는 공개 Git에 올리지 않는다.
   `responses/YYYY-MM/*.json.gz`, `state.json`, `locations.json`을 삭제·이동하지 않고 목록·파일 수·SHA-256 manifest를 만든다.
   먼저 한 번의 목록 스냅샷으로 처리 범위를 고정하고, 새로 수집된 파일은 다음 이관 단계에서 이어받는다.
2. `.json.gz` envelope의 `format_version`, `task`, `task_id`, `params`, `variables`, `requested_at_utc`,
   `received_at_utc`, `body_base64`, `body_sha256`을 읽는다. gzip과 base64를 풀어 SHA-256·시간축·배열 길이·좌표/모델을 검사한다.
   `coverage`도 실제 값에서 재집계한다. 잘린 응답·잘못된 해시·해석 불가능한 파일은 실패 목록에 남긴다.
3. API 재호출 없이 원문과 파싱한 시간별 행을 기존 테이블 구조에 맞춰 배치 적재한다.
   문자열 날짜는 실제 date, UTC 시각은 timezone-aware datetime으로 변환한다.
   파일 패키지와 중앙 수집기의 task/request 식별자 생성 방식이 다르므로, 공통 task 구성을 정규화하고
   원본 task_id·body_sha256와 DB request_id 대응을 별도 적재 장부/체크포인트에 남긴다.
   기존 DB와 의미상 같은 요청인지 확인한다. 단순한 `ON CONFLICT DO NOTHING`만으로 서로 다른 식별 체계의 중복이 해결되지는 않는다.
4. 같은 파일을 다시 이관해도 중복이 늘지 않는지 검증하고 파일별 커밋 성공 후에만 체크포인트를 기록한다.
   검증 실패/DB 실패는 원본을 보존하고 재실행으로 복구한다. 실패 시 새 API 요청으로 대체하지 않는다.
5. 기존 DB 전체나 `open_meteo_state` 전체를 덮어쓰지 않는다. `scripts/open_meteo_handoff.py restore`는
   대상에 Open-Meteo 테이블이 없는 빈 환경용이므로 **이미 자료가 있는 메인 DB 병합에 사용하지 않는다.**
6. 완료 후 원본 파일 수, 검증 통과/실패, 신규/기존 중복/실패 응답 수, 실제 신규 시간별 행 수,
   모델·lead·위치·대상기간별 확보율과 NULL 수를 보고한다.
   다른 수집기도 쓰는 DB의 전체 행 수 차이만으로 팀원의 이관 건수를 계산하지 말고 해당 manifest/request_id로 대조한다.

요청 범위는 팀원이 수집한 Open-Meteo API 응답이다. 별도 원천 API를 수집한 경우 먼저 원천별 목록·스키마를 확인한다.
원문 ZIP이나 비밀번호를 Git에 추가하는 대신 DB에 저장하고, Git에는 코드·사용법·집계 검증 보고를 올린다.

## 6. 팀원 AI에게 그대로 전달할 작업 요청

```text
Solar_Believer의 codex/open-meteo-collection 브랜치에서
handoff/kim_suyoung/DB_연결과_수집전환_요청.md와 AGENTS.md를 읽고 작업해줘.

기존 Python과 수집 경로/state.json/local_config.json을 보존하고 신규 설치를 최소화해.
없는 경우에만 Tailscale과 psycopg DB 드라이버를 추가해. Git 신규 설치, Docker,
PostgreSQL 서버, DataGrip, Python 재설치는 필요 없어. DB 비밀번호는 소유자에게
비공개로 받고 코드/Git/로그에 남기지 마. 초대/비밀번호가 없으면 독립적으로 준비할
수 있는 코드와 파일 검증을 먼저 하고, DB 연결·적재 완료라고 보고하지 마.

메인 DB는 Tailscale 100.102.164.7:15432, database solar_believer, 현재 user solar야.
먼저 check_db.py --tcp-only, 그다음 check_db.py로 읽기 전용 연결을 확인해.
연결이 끝나면 지금 실행 중인 최신 담당 월→과거 순서를 유지하면서 새 API 응답을
메인 PostgreSQL에도 자동 저장하도록 기존 수집기를 수정해줘. 저장된 파일을 DB에
재시도할 때 API를 다시 호출하지 말고, DB 장애에도 원본과 진행 상태를 보존해.

이미 받은 responses/YYYY-MM/*.json.gz도 해시·시간축·변수·NULL을 검증해서
원문/요청 메타데이터와 시간별 값을 메인 DB에 중복 없이 병합해줘. 기존 발전량/SMP,
다른 수집기의 상태와 데이터를 보존하고 DB 초기화·전체 restore·migration은 하지 마.
기존 파일 수집기는 아직 DB writer가 없으니 별도 구현이 필요해. 실제 적재 전에
중복 방지·재시작·DB 장애 복구·기존 상태 보존을 시험해. 최종적으로 작은 기존 응답
배치를 이관하고 같은 배치를 재실행해 중복이 안 늘어나는지 확인한 뒤 전체를 이관해.
실제 새 API 호출은 추가 검증에 꼭 필요한 경우만 하고 기존 원문으로 먼저 검증해.

신규 수집→DB 저장 코드, 기존 파일 이관 코드, 실행 방법과 집계 검증 결과를 Git에
올려줘. 원문 데이터·비밀번호·개인 설정은 올리지 마. 연결 확인/코드 준비/실제 이관/
자동수집 가동 여부를 각각 구분해서 보고해줘. 원본은 이관 후에도 보존해.
```

## 7. 이 안내를 작성한 쪽의 확인 범위

2026-11-25 KST 이 노트북에서 Tailscale 상태 Running과 메인 주소 TCP 접속을 확인했다.
비공개 로컬 인증정보로 `solar_believer` / `solar` 인증, 읽기 전용 세션 `on`,
`public.open_meteo_hourly` 존재를 실제 조회했다. 쓰기·이관·외부 API 호출·수집기 시작은 하지 않았다.
이 결과는 팀원 PC의 초대 수락·권한·설치 완료를 뜻하지 않으며 팀원 PC에서 다시 확인해야 한다.
이번 변경에는 연결 검사 도구와 요청 문서가 포함되며 DB writer/이관 도구의 구현은 팀원에게 요청하는 후속 작업이다.
새 `check_db.py`로도 TCP/인증과 테이블 6개의 존재·SELECT/INSERT/UPDATE 권한을 읽기 전용으로 확인했다.
잘못된 접속 대상의 네트워크 시도 차단과 오류 메시지의 비밀정보 비노출을 검사했다.
기존 파일 수집기의 오프라인 unittest 4개가 통과했으며 실제 API는 호출하지 않았다.

참고: [Tailscale 시작](https://tailscale.com/docs/how-to/quickstart),
[장치 공유](https://tailscale.com/docs/features/sharing),
[PostgreSQL 충돌 처리](https://www.postgresql.org/docs/17/sql-insert.html),
[기존 중앙 수집 구조](../../docs/OPEN_METEO_COLLECTION.md).
