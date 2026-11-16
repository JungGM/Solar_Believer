# 실제 API와 모델을 연결할 위치

## 외부 API → DB

1. `contracts.Collector.collect(start, end)`를 구현한다.
2. `adapters/legacy.py`에서 필요한 `API.*Collector`만 지연 import해 재사용한다.
   서비스 시작 시 API 호출이나 CSV 자동 탐색을 하지 않는다.
3. `CollectedRecord`로 변환한다. 안정적인 원천 키에 지역/시장·대상시각·예보 발표시각·
   기상 category를 포함하고, 단위·집계 수준·결측·시간 구간을 검증한다.
4. `store_records`로 트랜잭션 저장한다. 예보는 `issued_at` 필수이고 naive 시각을 거부한다.
   동일 출처/키의 중복은 유지한다. 수정된 원천 값은 개정 번호를 키에 넣어 새 버전으로 보존한다.
5. 성공/부분 성공/실패 및 쿼터 정책을 정하고 테스트한 뒤 `PIPELINE_MODE=live`를 연결한다.
   현재 live는 의도적으로 skipped다. 기존 키를 자동 사용하지 않는다.

`docs/REPOSITORY_AUDIT.md`의 기존 결측·시간·로깅 문제를 먼저 해결한다.
발전소 허가 정보와 KPX 지역 집계 발전량을 개별 발전소 실측으로 혼용하지 않는다.

## 기존 GitHub 모델 재사용

현재 저장소에는 모델이 없다. 이후 팀이 제공하는 모델 저장소·커밋·라이선스·
학습 데이터 출처를 먼저 기록한다. 학습 라이브러리와 버전을 모델 전용 의존성으로 추가하고,
신뢰할 수 있는 아티팩트만 `/artifacts/<version>/`에 새로 저장한다.

모델 manifest에는 다음 항목이 필요하다.

- 버전, 원본 저장소/커밋, SHA-256, 상대 아티팩트 경로
- 피처명·순서·타입·단위·결측 처리·지역 집계 기준
- 예측 대상·horizon·cutoff, 학습/검증 기간, 데이터 스냅샷
- 검증 지표와 기존 활성 모델 비교 결과

`ExistingModelAdapter.predict`에 로딩과 추론을 구현하고 `jobs.execute_job`의
predict 분기에 피처 생성·결과 검증·`Prediction` 저장을 연결한다.
`cutoff_records`는 입수 가능 시각 필터의 기본 도구다. 관측 지연,
피처별 적용 가능한 대상시각·기간 선택은 모델별 피처 코드에서 추가로 제한해야 한다.
리스트 길이·NaN·음수·대상/모델 FK를 검증한 뒤 DB에 쓴다.
pickle/joblib을 공개 API 업로드로 로딩하지 않는다.

## 월별 학습과 검증

`MonthlyTrainer.train/validate` 및 retrain 분기에 데이터 스냅샷, 시간 순 holdout,
성능/누수 검사, 신규 경로 저장, `TrainingRun`·`ModelVersion` 후보 등록을 구현한다.
누적 데이터 전체를 무조건 재사용하지 않고 승인된 공개 데이터 범위와 예측 당시
사용 가능한 피처만 사용한다. 실패·성능 미달 시 현재 모델은 계속 유지한다.
비교 지표·허용 성능 하락·최소 표본 수는 팀 합의 전 임의의 숫자로 정하지 않는다.

검증된 후보만 다음 명령으로 승인·적용할 수 있다. CLI의 승인은 성능 검증을 대신하지 않는다.

```bash
docker compose exec worker python -m backend.app.cli approve-model VERSION --approver GITHUB_ID
docker compose exec worker python -m backend.app.cli activate-model VERSION
# 롤백: 이전에 승인된 버전을 다시 activate-model
```

적용 명령은 아티팩트 존재와 해시를 확인하고 단일 활성 모델을 전환한다.
프로그램이 기존 모델 파일을 덮어쓰지 않는다. 아티팩트 배포·후보 등록 CLI와
승인 권한/감사 강화는 후속 범위다.

## 정산 규칙

`UnconfiguredSettlementRule`은 항상 설정 필요 예외를 발생시킨다.
시장, 적용 시점, 가격 종류, 발전량 단위, 정산 대상, 수수료·인센티브 및
결측 처리가 확정되면 `SettlementRule`을 구현하고 `Settlement` 저장에 연결한다.
근거 문서·규칙 버전·입력 출처를 `details`에 남기고 시뮬레이션 여부를 명시한다.
단순 SMP×발전량이나 대회 손실식을 실제 정산 공식으로 간주하지 않는다.
