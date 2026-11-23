# 김수영 — 예보 수집 전용 전달 패키지

이 폴더를 AI에게 전달하고 **AGENTS.md를 읽고 실행해줘**라고 요청하세요.
DB, DataGrip, Docker, API 키, 모델 학습은 필요 없습니다. Python 3.10 이상과 인터넷만 필요합니다.

## 목적과 대상

2022-01-01~2026-09-30, 월 단위로 2026년 9월부터 과거 방향으로 GFS/JMA의 1일·2일 전 예보를 수집합니다. 좌표는 locations.json에 포함됩니다. 원천명·좌표 근거·대표점 정확도도 보존됩니다. 현재 패키지는 자동 최신예보 수집이나 모델 학습을 하지 않습니다.
기존 PC와 같은 작업이 겹칠 수 있습니다. 두 PC 담당 월을 합의하면 --start/--end로 나눌 수 있습니다. 기본은 사용자 요청한 전기간입니다.

## 실행

1. Python 3.10 이상 설치. 추가 pip 패키지는 없습니다.
2. ZIP을 영구 폴더에 풉니다.
3. PowerShell에서 `powershell -ExecutionPolicy Bypass -File .\START.ps1` 실행.
4. 저장 폴더를 입력합니다. 이후 실행은 local_config.json의 경로를 재사용합니다.

직접 실행도 가능합니다:

```powershell
py -3 collector.py --output "D:\MeteoData"
```

첫 응답만 시험하려면 `--max-requests 1`을 덧붙입니다. 시험도 실제 API 사용량이 발생합니다.
중지는 Ctrl+C. 같은 명령으로 다시 켜면 state.json을 읽고 이어받습니다. 절전/PC 종료 중에는 수집하지 못합니다. 재부팅 후 START.ps1을 다시 실행하세요. 같은 저장 폴더에서 중복 실행은 차단합니다.

## 상태와 결측

저장 폴더 status.json: collecting / waiting_for_quota / waiting_for_retry / first_pass_finished / stopped.
complete는 모든 변수 값이 유효한 응답, partial은 일부 결측, unavailable은 전체 결측입니다.
partial을 완전한 수집으로 세지 않습니다. 첫 순회는 결측 응답도 보존하고 다음 작업으로 이동합니다. 결측 원천은 반복 호출만으로 채워진다고 보장하지 않습니다. 첫 순회 종료는 학습데이터 완성을 뜻하지 않습니다.
호출 예산은 최근 24시간 8,500, 시간당 4,000, 분당 400 가중 호출입니다. 429는 1시간 후 재시도합니다. 다른 IP가 같은 애플리케이션의 독립 한도를 보장하지 않으므로 한도 우회 목적으로 사용하지 마세요.

## 저장되는 자료

responses/YYYY-MM/*.json.gz: 작업 조건, 좌표·자원·근거, 요청/수신 UTC 시각, 원본 응답(base64), SHA-256, 변수별 유효 시간 수. 원본의 시간축·단위·null을 보존합니다. 누락을 0으로 바꾸지 않습니다.
과거 자료는 고정 24/48시간 선행 예보이며, 특정 시각 발표본이나 당시 실제 입수 가능성을 증명하지 않습니다.
state.json은 진행/호출 장부이므로 지우지 마세요. 원본 결과도 수집 도중 이동·삭제하지 마세요. 저장 경로를 바꾸려면 전체 폴더를 옮기고 local_config.json을 수정하세요.

## 결과 압축과 Git 업로드

Ctrl+C로 수집을 종료한 뒤:

```powershell
py -3 export_results.py --source "D:\MeteoData" --dest "D:\MeteoExport_01"
```

비어 있는 별도 목적지를 사용합니다. 원본 해시·시간축·결측 집계를 검사하고 35MiB 이하 묶음 ZIP들과 manifest.json을 만듭니다. 원본은 삭제하지 않습니다. 각 ZIP을 풀면 원래 responses 경로로 합칠 수 있고, 받는 사람이 직접 DB에 적재합니다. DB 적재 코드는 이 패키지에 없습니다.

팀 저장소의 합의된 전달 폴더(예: transfer/meteo_suyoung/날짜/)에 **ZIP 전체와 manifest.json**을 함께 복사하고 해당 파일만 git add → commit → push 합니다. local_config.json, state.json, 로그, 개인 경로는 올리지 마세요. 결과가 크면 Git 전체 저장 용량/푸시 제한을 확인하고 여러 커밋으로 나누거나 Git LFS/Release 방식에 관해 저장소 소유자와 합의하세요. ZIP 분할은 총 용량을 줄이는 기능이 아닙니다. 이 수집기는 Git 자동 푸시를 하지 않습니다.

원본 검사와 코드 자체 시험: `py -3 -m unittest -v test_collector.py`
데이터 출처: Open-Meteo, GFS (NOAA), JMA. Weather data by Open-Meteo.com (https://open-meteo.com/), CC BY 4.0. 공식 제한: https://open-meteo.com/en/terms / https://open-meteo.com/en/pricing
