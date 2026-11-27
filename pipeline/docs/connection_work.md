<!--
[홍석환] 사용자 흐름 실행 준비와 브랜치별 잔여 작업
한 시간 처리의 실행 절차와 격리 시험·운영 확인의 차이를 기록한다.
-->
# 사용자 흐름 실행과 후속 작업

## 현재 변경

- 기본 집계·시계열 범위는 한 시간이다. 현재 공개값 자체는 자동 삭제하지 않으며, 이전 버전은 30초 조회 유예 후 TTL 정리 대상이 된다. 원본 DB 보존 정책은 바꾸지 않는다.
- 실시간 경로는 원본 DB 대조와 원본 경로 재계산을 하지 않는다. 정합성은 사후 검토가 담당한다. 범위 밖 시각·형식 오류 이벤트는 건너뛰고 `flow_health.skipped`에 건수를 기록한다. DB 저장 중 실패하면 범위별 마지막 공개값을 유지한다. 여러 범위가 동시에 같은 버전으로 바뀌는 구조는 아니다.
- 중단된 시점의 빈 예매 구간은 0으로 만들지 않는다. 실제 시작 시도가 관측된 구간은 늦은 성공으로 수정될 수 있다.
- 기존 원본 본문은 지표 DB에 복사하지 않는다. 복구용 최소 관측·미완성 청크는 전용 체크포인트에 보관한다.
- 재공개한 현재 버전의 이전 만료 예약을 해제한다. 공개 뷰는 검증 상태·버전·범위가 일치하는 결과를 결합한다.

## 실행 준비

1. `pipeline/configuration/flow.env.example`을 참고해 무시되는 `.env.flow`를 준비한다. 저장소에 실제 값을 올리지 않는다. 원본 읽기 계정, 지표 쓰기·읽기 계정, 원본 적재기와 같은 `RAW_SOURCE_ID`, 실제 네트워크를 확인한다.
2. `.env.flow`에 DB 관리자용 `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD`를 별도로 전달해야 준비 스크립트를 실행할 수 있다. 관리자와 쓰기·읽기 계정은 분리한다.
3. DB 준비는 기본 사전 검사다. 실제 생성이 필요할 때만 `FLOW_APPLY_SETUP=1`로 바꿔 명시 실행한다. 기존 계정·권한·뷰가 다르면 수정하지 않고 실패한다. 기본 검사에서도 관리자 인증이 필요하다.

```powershell
docker compose --env-file .env.flow -p ticket-guard-flow-setup -f compose.flow.setup.yml run --rm flow-db-setup
```

기존 공개 뷰의 정의가 이전 코드와 다르면 자동 교체하지 않는다. 두 정의를 비교해 별도의 명시적인 변경 작업으로 준비해야 한다. 이 스크립트는 기존 계정 비밀번호를 재설정하지 않는다. 원본 읽기 계정은 원본 DB의 기존 준비 절차에서 설정한다.

4. 빌드와 읽기 전용 연결 검사를 실행한다.

```powershell
docker compose --env-file .env.flow -p ticket-guard-flow -f compose.flow.yml build flow
docker compose --env-file .env.flow -p ticket-guard-flow -f compose.flow.yml run --rm --entrypoint python3 flow -m tools.flow_preflight
```

기존 관리자 URI가 검사 프로세스에 전달된 경우 공개 뷰 조회도 검사한다. 지표 DB 이름만 비교하므로 호스트 별칭이 같은 서버인지도 별도로 확인해야 한다. 관리자 계정에는 원본·내부 컬렉션 접근을 부여하지 않는다. 사전 검사는 쓰기 권한 검증이나 실제 지표 공개를 대신하지 않는다.

5. 기존 Kafka·원본 DB·원본 적재기가 정상이며 같은 Spark 그룹의 다른 작업이 없는지 확인한 뒤 실행한다.

```powershell
docker compose --env-file .env.flow -p ticket-guard-flow -f compose.flow.yml up -d flow
docker compose --env-file .env.flow -p ticket-guard-flow -f compose.flow.yml logs --tail 50 flow
```

`flow` 전용 이미지는 `ticket-guard-user-flow:dev`이다. 기존 적재 이미지 이름을 덮어쓰지 않는다. 기본 시작 위치는 earliest다. 보존 범위 밖의 과거 이벤트는 공개를 멈추지 않고 건너뛰며 `flow_health.skipped.late`에 건수를 남긴다. 오프셋을 latest로 바꿔 누락을 숨기지 않는다.

## 검증 실행

단위 검사에는 개발 환경의 pytest와 mongomock이 필요하다. 운영 이미지에는 tests/를 넣지 않는다.

```powershell
$env:PYTHONPATH='pipeline'
.venv/Scripts/python.exe -m pytest pipeline/tests -q -p no:cacheprovider --basetemp=.venv/flow-test-run
docker build -t ticket-guard-user-flow:dev pipeline
docker compose -p ticketguard-flow-isolated -f pipeline/tests/compose.integration.yml up -d flow
docker compose -p ticketguard-flow-isolated -f pipeline/tests/compose.integration.yml run --rm --no-deps verify
docker compose -p ticketguard-flow-isolated -f pipeline/tests/compose.integration.yml down
```

통합 시험은 새 프로젝트에서 시작한다. MongoDB·Kafka·체크포인트는 시험용 임시 저장소를 사용하므로 중지·정리하면 사라진다. 운영 네트워크·볼륨·공개 포트는 연결하지 않는다. 입력은 합성 예매 3건과 동일 이벤트 재전송이며 Kafka 2개 파티션의 마지막 오프셋까지 공개 결과에 반영됐는지 확인한다. 읽기 계정의 내부 컬렉션 조회와 쓰기가 거부되는지도 확인한다. 이 시험은 웹 수집·실제 관리자 화면의 자동 갱신이나 장시간 부하 시험이 아니다.

## 관리자 브랜치에서 이어갈 정확한 변경

- `user_flow/presentation.py`: 최근 12시간 필터를 한 시간으로 변경한다. 카드 값은 최신 공개 결과를 유지하고 과거 시계열을 현재 시각 기준으로 자른다.
- `templates/monitor_user_flow.html`, `static/monitor_charts.js`: 시간축·설명·접근성 문구를 한 시간으로 맞춘다. 시안 데이터와 실제 조회 공급 경로는 계속 분리한다.
- `static/monitor_navigation.js`와 별도 실제 화면 전용 갱신 모듈: 인증된 기존 페이지를 다시 요청하고 새 버전의 카드·단계·연결선·시계열을 함께 교체한다. 동일 범위·버전이면 재생성하지 않는다.
- `templates/monitor_live.html`: 갱신 상태와 마지막 성공 시각을 표시한다. 첫 실패는 빈 오류, 후속 실패는 기존 값 유지로 구분한다.
- 필터·요청 순번·관점 변경 시 이전 요청을 취소하거나 응답을 폐기한다. 중복 요청을 막고 비활성 탭과 다른 관점에서는 조회를 중단한다.
- `/admin/preview`, `/admin/db`, QA·홈·예매 화면에는 자동 갱신을 넣지 않는다. 초기 갱신 간격은 한 곳에 두고 성능 보장으로 표현하지 않는다.
- 관리자 전용 테스트·지정 QA MongoDB 회귀 및 브라우저 검사로 실패·복구·늦은 응답·범위 변경·같은 버전을 검증한다.

이 DB 브랜치에서는 관리자 파일을 수정하지 않는다. 따라서 DB 처리가 한 시간이더라도 현재 배포된 화면은 아직 이전 시간 문구와 수동 조회를 사용할 수 있다.
