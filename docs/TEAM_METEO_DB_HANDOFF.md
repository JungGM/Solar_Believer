# 팀원 원격 DB 연결과 Open-Meteo 수집분 이관

2026-11-25 사용자 요청에 따른 최신 안내는
**[팀원 DB 연결·최소 설치·수집기 전환·기존 응답 이관 요청](../handoff/kim_suyoung/DB_연결과_수집전환_요청.md)**을 따른다.

- 기존 Python을 재사용하고, 없는 경우에만 Tailscale과 `psycopg`를 추가한다.
- DB 접속 정보: Tailscale `100.102.164.7:15432`, database `solar_believer`, 현재 user `solar`.
- Tailscale 장치 공유 초대와 DB 비밀번호는 소유자가 비공개로 전달한다.
- [읽기 전용 연결 검사](../handoff/kim_suyoung/check_db.py)와
  [비밀번호 없는 설정 예제](../handoff/kim_suyoung/db_connection.example.json)를 제공한다.
- DB 인증 후 팀원은 최신 담당 월부터 역순으로 받는 기존 수집기를 메인 DB 자동 저장 방식으로 수정한다.
- 이미 받은 API 응답은 재호출 없이 검증·중복 제거해 기존 DB에 병합한다.
- 위 두 구현 작업과 검증 결과의 Git 기록을 팀원에게 요청한다. 이번 문서 작성으로 구현·이관이 끝난 것은 아니다.

기존 [DB 백업·복원](POSTGRES_HANDOFF.md) 및 [Open-Meteo 전달](OPEN_METEO_COLLECTION.md)의
빈 DB 복원 절차는 이미 데이터가 있는 메인 DB로의 병합 절차가 아니다.
이번 목적은 기존 메인 DB의 공동 사용이다. 팀원 PC의 DB 서버 설치·로컬 복원은 필요 없다.
