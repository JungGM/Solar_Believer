# NOAA GFS 과거 예보 수집

2026-11-22 요청: 2022-01-01부터 현재 KST 날짜까지 과거 날짜순으로 수집한다.
`scripts/start_gfs_collector.ps1`는 숨김 백그라운드 프로세스를 실행한다.
날짜가 바뀌면 범위를 늘리고 PostgreSQL 완료 기록에서 이어받는다.

- 원천: 공개 S3 `noaa-gfs-bdp-pds`, GFS 0.25도 예보.
- 대상: 기존 정책대로 06~21시 KST, 1시간 간격.
- 실행분: 대상일 이틀 전 12 UTC(전날 08 KST 기준 11시간 전 초기화).
  실제 과거 배포 시각은 미검증이며 초기화 시각을 입수 가능 시각으로 간주하지 않는다.
- 영역: 북위 32~40도·동경 124~132도, 원래 격자 1,089점. 발전소 매핑은 별도다.
- 변수: 2m 기온·상대습도, 10m U/V 바람, 전운량, 하향 단파복사, 강수 누적량.
- 원 단위와 결측을 보존한다. startStep/endStep/stepType을 함께 저장한다.
  매시 파일이라도 일사·강수는 여러 시간 평균·누적일 수 있으므로 시간당 값으로 단정하지 않는다.
- HTTP Range로 필요한 GRIB 메시지만 받고 실행·유효시각을 검증한다.
  한국 영역 원 격자값·좌표는 gzip JSON으로 PostgreSQL에 저장한다.
  전체 지구 GRIB 원문은 보관하지 않고 URL·바이트 범위·인덱스·메시지 SHA-256을 남긴다.
- DB: `public.noaa_gfs_forecast_grids`, `public.noaa_gfs_collection_tasks`.
  기존 기상청 테이블과 구분한다. 새 SQLite DB를 만들지 않으며 학습 입력 자동 병합은 하지 않는다.
- 현재 PC의 기존 DB 포트 55432가 응답하지 않아 GFS 전용 PostgreSQL을
  `127.0.0.1:55433/solar_gfs`에 실행했다. 기존 `.env` DB 설정은 바꾸지 않았다.
  런처만 자식 프로세스에 GFS DB 연결 환경변수를 전달한다.
  DB 원본은 사용자 홈 `.cache/solar-gfs-postgres/cluster`에 있다.
  한글 경로 PostgreSQL 초기화 오류와 OneDrive DB 동기화를 피하기 위해 분리했다.
  시작 스크립트는 DB도 시작하며 계정·암호는 기존 `.env` 설정을 사용한다.
  다른 PC로 옮길 때는 이 전용 DB도 별도 백업·복원해야 한다.
- 오래된 대상일부터 하루씩 처리하고 오류는 15분 뒤 재시도한다.
  대기 중인 오류가 있어도 다른 날짜는 진행한다. 부분 성공 변수는 재다운로드하지 않는다.
- 현황: `data/noaa_gfs/collector_status.json`, PostgreSQL `collector_runtime_state`에도 저장한다.
  7개 변수 격자 필드가 한 대상 시각을 구성한다.
- 정상 중지: `New-Item data/noaa_gfs/STOP -ItemType File -Force`.
  진행 중인 하루 배치 뒤 중지한다. 재시작하면 오래된 STOP은 무시한다.
- PC 절전·종료 중에는 수집하지 못한다. 재부팅 뒤 시작 스크립트로 이어받는다.

```powershell
.\scripts\start_gfs_collector.ps1
Get-Content data/noaa_gfs/collector_status.json
```

의존성: `pip install -r requirements-gfs.txt`.
Docker가 없는 현재 PC에서는 공식 EDB PostgreSQL 17.7 바이너리를 사용한다.
전체 지구 GRIB에서 지역 자료를 추출하므로 장기 수집 다운로드량은 상당하다.
연속성과 완료 여부는 tasks 상태로 검증하며 가장 늦은 저장 시각만으로 완료를 판단하지 않는다.
