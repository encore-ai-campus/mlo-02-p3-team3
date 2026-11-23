# 이상 관측 적재와 관리자 화면

## 적용 범위와 실행

기준 코드는 main `7cdaab7`이며 사용자 승인에 따라 main에서 구현했다. 기존 `ingest`의
Kafka 토픽·Consumer Group·Spark 체크포인트·clean/quarantine 저장을 유지한다.
`flow`, `network` 모드는 변경하지 않는다. 별도 Spark 애플리케이션이나 토픽은 추가하지 않는다.
기존 raw Consumer Group과 raw DB는 읽거나 수정하지 않는다.

`write_partition`은 정상화된 기존 clean 문서를 보존하고 서버 이벤트에서 최소 관측을
`anomaly_observations`에 멱등 저장한다. 같은 `foreachBatch`의 드라이버에서 관측을 재계산하고
`anomaly_events` → clean 판정 필드 → `anomaly_public` → `anomaly_health=ready` 순으로 저장한다.
분산 작업자는 기존 Spark 파티션에서 적재하지만 상관 계산은 현재 드라이버의 제한된 시간창에서 수행한다.
대규모 분산 상관 계산을 제공하는 구현은 아니다.

무이벤트 시간에도 ingest 진입점이 15초마다 같은 잠금 아래 판정을 재계산한다. 종료·실패 중에는
게시하지 않는다. 별도 쓰기는 트랜잭션이 아니며 중간 실패 시 게시 상태를 blocked로 바꾸고
Spark 배치를 실패시킨다. 동일 입력의 재시도가 부분 쓰기를 복구한다.
관리자 읽기는 health를 앞뒤로 확인하고 게시 시각·규칙 버전이 일치할 때만 수치를 제공한다.

## 설정

루트 `.env`에서 아래 정책을 설정하면 기존 compose의 spark 서비스에 전달된다.
모두 기본값은 비어 있으며, 빈 항목은 판정하지 않는다. 설정 예시는 운영 기준이 아니다.

| 변수 | 의미 | 범위 |
|---|---|---|
| ANOMALY_OVERLAP_ENABLED | 구매 요청의 서버 관측 구간 중첩 | 1 활성, 0 또는 빈 값 미설정 |
| ANOMALY_RETRY_SECONDS | 동일 예매 시도의 후속 구매 요청 시간창 | 양의 유한 초, 최대 3600 |
| ANOMALY_PAGES_SECONDS | 동일 계정·공연·회차에서 다른 페이지의 구매 요청 시간창 | 양의 유한 초, 최대 3600 |
| ANOMALY_FAST_SECONDS | 대기열 진입부터 구매 성공까지의 관측 시간이 이 값 미만 | 양의 유한 초, 최대 3600 |
| ANOMALY_PURCHASE_SECONDS | 서로 다른 구매 성공이 두 건 이상 발생한 시간창 | 양의 유한 초, 최대 3600 |
| ANOMALY_ACCOUNT_LIMIT | 동일 계정·공연·회차의 최근 1시간 관측 구매 수량 한도 | 양의 정수, 최대 10000 |

한도는 전체 기간이나 회차 전체의 누적 구매 제한을 의미하지 않는다. 전체 누적 한도가 필요하면
관측 시간창 밖의 구매 원장을 연결하는 별도 계약이 필요하다. 시안 수치를 운영 정책으로 채우지 않는다.

## 저장 계약

| 저장 대상 | 추가 내용 |
|---|---|
| clean_events | `isAnomaly`: BSON Boolean 또는 미평가 null, `evaluationStatus`, `evaluatedRules`, `ruleVersion` |
| anomaly_observations | 서버 이벤트의 식별자·계정·시도·공연·회차·시각·구매 수량·요청 구간과 해시 |
| anomaly_events | 이상 근거, `isAnomaly=true`, 규칙·기준값·관측값·참조 이벤트 |
| anomaly_public | 범위별 `_id`와 최근 1시간의 단일 화면 게시 문서 |
| anomaly_health | ingest의 processing/ready/blocked 및 게시 시각·규칙 버전 |

`false`는 `evaluatedRules`에 나열된 규칙을 평가했고 해당 관측 범위에서 비해당이라는 뜻이다.
모든 행동이 정상임을 보장하지 않는다. `pending`은 지연 도착 유예 300초,
`unavailable`은 정책 미설정 또는 근거 부족, `unsupported`는 지원하지 않는 이벤트다.
`context_only`는 표시 시간창 이전의 상관 근거, `outside_window`는 계산 시간창 밖,
`future`는 아직 관측 시각에 도달하지 않은 입력이다.
이 상태는 null을 사용한다. 판정 필드가 없는 과거 자료는 정상으로 읽지 않는다.
raw_events와 quarantine_events에는 행동 판정을 임의로 추가하지 않는다.

정규화 성공한 이상 이벤트도 clean_events에 유지한다. 기존 clean 필드는 삭제하지 않는다.
이상 문서 ID는 규칙 버전·종류·기준 이벤트 ID의 해시다. 같은 원본 이벤트 ID의 다른 최소 관측은
거부한다. 지연된 좌석 경쟁·재고 부족 근거로 취소된 신호는 anomaly_events에서 삭제하지 않고
`isAnomaly=false`, `evaluationStatus=retracted`로 정정한다. 규칙 버전이 다른 이력은 유지한다.
새 정책으로 과거 원본 전체를 재처리하거나 백필하지 않는다.

## 판단 근거와 한계

- 서버의 구매 업무 이벤트와 `/api/action/`의 confirm 관측만 사용한다. 브라우저 판정값은 신뢰하지 않는다.
- 폴링·로그인·page_enter·일반적인 화면 전환은 규칙 입력에서 제외한다.
- 요청 시작·종료는 `server_request.occurredAt`과 `observationDurationMs`로 재구성한 서버 관측 구간이다.
  브라우저 전송 시간이나 네트워크 왕복 시간으로 표시하지 않는다. 요청 ID·시도·공연·회차가 필요하다.
- capture에 회차가 없으면 같은 예매 시도의 유일한 서버 업무 이벤트에서만 보완한다.
- 재요청은 서버가 수락한 `booking_confirm_request`의 후속 관측이다. SEAT_SOLD/INSUFFICIENT_STOCK
  실패에 따른 후속 구매 요청은 제외한다. 이미 성공한 구매 재응답은 구매 성공으로 다시 세지 않는다.
- 다중 페이지는 서로 다른 pageInstanceId에서 구매 요청이 관측되었다는 뜻이며 공유 계정·봇 확정이 아니다.
- 구매는 성공한 bookingId별로 중복 제거하고 quantity를 합산한다. 요청 횟수와 실제 구매 수량은 다르다.
- 빠른 완료는 대기열 진입 → 구매 성공의 시간이다. `durationMs`의 서버 함수 처리 시간을 조작 속도로 쓰지 않는다.
- 한 시도에 여러 신호가 있어도 고유 감지 시도는 한 번 센다. 사전 규칙 카드는 고유 시도,
  다중 페이지·사후 카드는 고유 계정, 차트는 분당 신호 이벤트 수다.
  검토 대상은 사후 평가가 가능한 범위의 실제 구매 성공 계정 수다.
- 전체 재고와 요청→구매 결과의 정확한 상관 계약은 현재 부족하여 상세에 미수집 상태를 표시한다.
- 지원 입력이 없거나 해당 규칙의 근거가 없으면 0 대신 미수집/근거 부족을 표시한다.
  관측 이벤트가 없는 분은 차트 선을 연결하지 않는다. 수집 완전성은 보장하지 않는다.

## 용량과 보존

최근 1시간을 표시하며 상관 계산에는 앞선 1시간과 300초 유예를 추가로 읽는다.
한 번에 최대 25000개 관측, 전체 포함 101개 범위, 게시물 8MiB까지 허용한다.
초과하면 일부 결과를 정상으로 공개하지 않고 배치를 실패시킨다.
관측은 이벤트 시간 기준 TTL로 제거한다. 원본·정답표·본문·쿠키·토큰은 복제하지 않는다.
화면 목록은 200개, 타임라인은 100개로 제한하며 카드 집계는 전체 관측 범위다.
저장된 이상 이력에는 자동 삭제 정책을 추가하지 않았다. 운영 보존 기간은 별도 결정이 필요하다.

## 화면과 배포 경계

- 실제 사전 화면: `/admin/#anomaly`
- 실제 사후 화면: `/admin/?anomaly_mode=post#anomaly`
- 고정 시안: `/admin/preview#anomaly`, `/admin/preview?anomaly_mode=post#anomaly`
- 실제 화면은 조회 실패 시 시안으로 대체하지 않는다. 120초를 넘긴 게시물도 수치 공개를 보류한다.
- 기존 `scope`의 전체/정확한 [simulation, show, schedule] 조합을 공유한다.
- 게이트웨이 관리자 인증·기존 공개 포트를 유지한다. 관리자 앱은 읽기만 수행한다.
- Spark 쓰기 DB와 관리자 URI의 대상 DB가 같아야 한다. 관리자에는 anomaly_public/anomaly_health find만 필요하다.
  기존 DB 전체 read 계정은 권한 추가 없이 조회 가능하다. 제한 역할을 쓰면 DB 담당자가
  `database/anomaly_permissions.js`를 먼저 사전 검사하고, 명시적 플래그로 역할 생성 및 기존 계정 역할 연결을 검토해야 한다.
  스크립트는 기존 계정·역할을 자동 수정하지 않는다.
- 이 작업은 운영 계정·권한·데이터·체크포인트 변경을 실행하지 않는다. 운영 ingest는 별도 적용이 필요하다.

## 검사

`PYTHONPATH=pipeline`으로 `pipeline/tests/test_anomaly.py`와 기존 관리자·QA 테스트를 실행한다.
`tests/anomaly_integration.py`는 고정된 anomaly-test-kafka/anomaly-test-mongo 호스트의
폐쇄 시험 네트워크에서만 실행한다. `ANOMALY_ISOLATED_TEST=1`이 필요하다.
실제 Kafka → Spark foreachBatch → MongoDB의 정상·격리·이상 저장과 새 체크포인트 재소비를 검증한다.
운영 토픽과 DB를 시험에 사용하지 않는다.
