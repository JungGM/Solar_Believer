# 지도 데이터 출처

Natural Earth 1:50m Admin 0 countries의 대한민국(KOR) geometry만 추출했습니다.
개략적인 발전소 위치 표시용이며 법적 경계나 정밀 지도 용도가 아닙니다. 일부 작은 섬은 이 축척에서 생략될 수 있습니다.

- 원본: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson
- 원본 다운로드 SHA-256: `3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb`
- 취득일: 2026-11-17 KST
- 라이선스: Public domain — https://www.naturalearthdata.com/about/terms-of-use/
- 로컬 경로: `frontend/src/data/korea.json`

지도와 발전소 좌표는 경위도에서 같은 Mercator 투영으로 표시합니다. 런타임 지도 키·외부 타일 요청은 없습니다.
데모 발전소는 가상 지점이며 실제 사업자의 위치나 자산을 뜻하지 않습니다.
