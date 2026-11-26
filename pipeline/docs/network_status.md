# 네트워크 상태 지표 게시

기존 Kafka와 원본 적재기를 사용한다. 별도의 Spark 실행 모드 `network`가 Kafka 위치와 원본 바이트를 대조하고, 청크를 재조립한 뒤 Django의 `server_request`를 요청 ID별로 한 번만 센다. `server_response_headers`는 요청 수에 포함되지 않는다.

- Spark 소비 그룹은 `ticketguard-network-v1`, 체크포인트는 전용 볼륨의 `/checkpoints/network-v1`이다. 기존 적재·사용자 흐름 체크포인트를 연결하지 않는다.
- 집계 범위는 현재 5분 경계 직전의 완료된 12개 구간이다. 범위에 포함된 지연 도착은 다음 게시 버전에서 반영한다. 기본 상태 여유는 300초이며 `NETWORK_GRACE_SECONDS`로 조정한다. 화면 범위를 벗어난 과거 구간은 다시 공개하지 않는다.
- 수집 완료를 증명하는 별도 신호가 없어 요청이 없는 구간은 미관측이다. 실제 요청이 있는 구간의 4xx·5xx가 0이면 관측된 요청 중 0건을 의미한다.
- `NETWORK_HEADERS_THRESHOLD_MS`가 없으면 측정 건수만 표시한다. 설정 시 기준을 초과한 값만 센다.
- Nginx 업스트림 오류와 게이트웨이 경유 웹 점검은 이번 입력에 없다. 향후 두 출처의 이벤트를 기존 Kafka와 원본 적재 경로에 연결하고, 네트워크 집계·공개 계약에서 출처별 관측 근거를 검증한다.

## 기존 로컬 환경에 연결

1. 시스템 MongoDB의 기존 `ticketguard`에서 [network_views.js](../database/network_views.js)를 실행한다. 해당 DB의 기존 관리 작업 계정에 뷰 생성 권한이 있어야 한다. 스크립트는 뷰 두 개만 준비하며 DB 계정이나 역할을 만들지 않는다. 관리자 앱의 기존 MongoDB 계정은 두 뷰를 읽을 권한이 필요하다.
2. 저장소 루트의 기존 `.env`에 `NETWORK_RAW_MONGO_URI`(기존 원본 DB의 읽기 연결)와 `NETWORK_TARGET_MONGO_URI`(기존 `ticketguard` 쓰기 연결)를 설정한다. 원본 적재기와 같은 `RAW_SOURCE_ID`를 사용한다. 웹의 관리자 URI는 기존 값을 유지한다.
3. 원본 적재가 먼저 동작하는지 확인한 뒤 다음 명령을 실행한다.

```powershell
docker compose --env-file .env -p ticket-guard-network -f compose.network.yml config --quiet
docker compose --env-file .env -p ticket-guard-network -f compose.network.yml up -d --build
docker compose --env-file .env -p ticket-guard-network -f compose.network.yml logs --tail 50 network-metrics
```

Spark는 요약·기능별 수치와 시계열을 동일한 버전으로 저장한 뒤 공개 포인터를 교체한다. 원본 불일치나 저장 실패 시 포인터는 이전 버전을 유지한다. 관리자 화면의 카드·차트·표는 이 공개 결과를 같은 버전으로 읽는다. 차트에 수치를 보려면 완료된 5분 구간에 검증된 요청이 있어야 한다.

## 초별 집계 계약 v2

현재 기본 집계는 요청 발생 시각 기준 1초 구간이며, 최근 완료된 10개 구간만 게시한다. network의 Kafka 마이크로배치와 무이벤트 갱신 주기는 각각 1초다. 입력 상한 1000, 소비 그룹, 체크포인트와 원본 대조·중복 제거는 유지한다. 지연 도착은 아직 최근 10초 범위인 구간만 보정하며, 그 이전 관측은 최신 그래프에 다시 표시하지 않는다. 무요청을 증명할 독립 신호가 없으면 0 대신 미관측이다. 위 문서의 5분·12구간은 구버전 계약 v1 설명이다.
