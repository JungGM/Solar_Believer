# 2인 협업 안내

실제 이름·계정 배정은 팀이 정한다. 아래는 충돌을 줄이기 위한 역할 제안이다.

| 담당 | 주 작업 | 자주 수정할 위치 | 교차 검토 |
|---|---|---|---|
| 개발자 A | API·DB·수집·Compose | `backend/app/main.py`, `models.py`, `services.py`, `adapters/legacy.py`, `compose.yaml` | DB/계약 변경을 B가 검토 |
| 개발자 B | React·모델·학습 | `frontend/src/`, `contracts.py`, 모델·학습 adapter, `jobs.py` | 모델 적용·누수 검사를 A가 검토 |
| 공동 | 정산 규칙·피처·로드맵 | `docs/INTEGRATIONS.md`, `docs/STATUS.md` | 서로 승인 후 반영 |

## 개발 순서

1. `main`을 갱신하고 `feat/<topic>` 또는 `fix/<topic>` 브랜치 생성.
2. 작업 범위와 계약 변경을 PR 설명에 기록. 키는 각자의 `.env`로 관리.
3. DB 변경은 `models.py`와 새 Alembic migration을 함께 커밋.
   이미 공유된 migration을 고치지 않고 새 revision을 만든다.
4. 기능과 관련된 테스트, Compose 검증, `docs/STATUS.md`·`FILE_GUIDE.md`를 갱신.
5. PR에서 상대가 실행 방법과 변경 결과를 검토한 후 병합.

두 개발자의 로컬 PostgreSQL·데이터·볼륨은 독립이다. DataGrip은 선택 도구이며
서버 구성 요소가 아니다. GUI에서 한 스키마 변경은 자동으로 Git에 남지 않으므로
스키마 변경은 반드시 migration으로 재현한다.

## DB 변경 예시

```bash
# models.py 수정 후, 현재 DB가 기존 head에 있는지 확인
docker compose run --rm migrate alembic -c backend/alembic.ini current
```

로컬 소스에 migration을 생성하려면 Python 3.12 가상환경에서 서비스 의존성을
설치하고 `alembic -c backend/alembic.ini revision --autogenerate -m "description"`을 실행한다.
`.env`의 `POSTGRES_HOST=localhost`와 실제 로컬 포트를 사용한다.
컨테이너 안에서 생성한 파일은 로컬 Git에 자동 반영되지 않는다.
생성된 upgrade/downgrade를 검토한 뒤 `docker compose up -d --build`로 적용한다.
스키마 자동 생성(`create_all`)을 서버 시작 코드에 넣지 않는다.

## 검토 기준

- 지역 집계와 개별 발전소 ID가 섞이지 않는가?
- 예측 당시 알 수 없던 미래 관측값이나 결측 발전량 0 대체가 들어가지 않는가?
- 수집 재실행이 중복을 만들거나 기존 원본을 덮어쓰지 않는가?
- 모델 검증·승인·적용이 구분되고 실패 시 현재 모델을 유지하는가?
- 금액의 규칙 버전과 시뮬레이션 표시가 있는가?
- 키·원본 데이터·학습 바이너리가 커밋되지 않았는가?
