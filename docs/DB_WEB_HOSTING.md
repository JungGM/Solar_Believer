# PostgreSQL에 저장한 웹 파일 제공

웹 화면의 HTML, JavaScript, CSS, 이미지 파일은 서비스 PostgreSQL의
`public.web_assets` 테이블에 원본 바이트로 저장한다. FastAPI가 DB에서 읽어
응답하고, 기존 3000 포트의 Nginx는 API와 웹 요청 모두를 FastAPI에 전달한다.
화면, 기능, 쿼리 주소(`?page=weather&demo=false`)는 그대로 사용한다.
Nginx 컨테이너에는 서비스용 정적 웹 파일을 넣지 않는다.

## 배포

```sh
docker compose build api migrate web-publish web
docker compose run --rm migrate
docker compose run --rm --no-deps web-publish
docker compose up -d --no-deps api web
```

일반 `docker compose up -d --build`도 migrate → web-publish → web 순서를 따른다.
빌드 결과 `/app/web-dist`는 DB 적재용 배포 재료이며 HTTP 요청에서 읽지 않는다.
적재는 한 트랜잭션으로 실행하므로 중간 상태는 노출되지 않는다. 이전 페이지를
열어 둔 브라우저를 위해 기존 해시 이름의 JS/CSS 파일은 보존한다.
ETag와 조건부 요청을 지원하고, HTML 및 파일은 재검증하도록 응답한다.

현재 제공 중인 웹 파일을 그대로 이전할 때는 기존 웹 컨테이너의
`/usr/share/nginx/html`을 별도 디렉터리에 복사하고 해당 디렉터리를 적재한다.
`python -m backend.app.publish_web /path/to/build`로 다른 빌드도 적재할 수 있다.
적재 스크립트는 인증 없는 HTTP 업로드 엔드포인트로 공개하지 않는다.

## 확인 및 백업

```sql
SELECT path, content_type, octet_length(content) AS bytes, sha256
FROM public.web_assets ORDER BY path;
```

PostgreSQL 전체 백업/복원에 웹 파일도 포함된다. 소스 수정은 기존 Git 저장소에서
계속 진행한다. 서비스 실행에는 DB, API, 웹 프록시가 필요하므로 이 PC에서 실행하는
동안에는 PC를 끄면 접속이 중단된다. DB에 파일을 저장하는 것이 외부 호스팅은 아니다.

## 2026-11-28 적용 확인

- 기존 운영 웹 파일 6개, 총 745,504바이트를 그대로 적재했다.
- DB 저장본, API 직접 응답, 웹 프록시 응답, 외부 터널 응답의 SHA-256이 모두 원본과 일치했다.
- 웹 프록시의 정적 파일 디렉터리에 파일이 없으며, 준비 상태와 외부 summary API는 HTTP 200이다.
- 새 테스트는 SQLite/격리된 PostgreSQL 각각에서 파일 원본 삭제 후 DB 제공, 조건부 요청,
  HEAD, SPA 경로, API 경로 보존, 재배포, 이전 청크 보존, 불완전 배포 거부를 검증했다.
- 관련 회귀 테스트는 47개 통과, 기존 대시보드/작업공간 집계 테스트 12개 실패였다.
  새 웹 라우터를 제거한 비교 실행에서도 동일한 12개가 실패했다. 이 작업에서 집계 로직은 변경하지 않았다.
