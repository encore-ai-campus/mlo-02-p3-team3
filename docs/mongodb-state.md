# MongoDB 상태 저장 설정

웹 로그인 세션·예매 상태·정책 기록과 파이프라인의 복구 상태는
`compose.state.yml`의 `mongo-state`에 저장한다. QA DB와 원본·집계 DB는
기존 인스턴스와 연결을 유지한다.

## 로컬 환경변수

기존 루트 `.env`를 유지하고 아래 항목을 추가한다. 비밀번호는 계정마다
서로 다른 값으로 정하고, 연결 URI의 사용자명·비밀번호는 URL 인코딩한다.
아래 `CHANGE_ME`는 실행 전에 실제 값으로 교체한다.

```dotenv
STATE_MONGO_ROOT_USERNAME=state_admin
STATE_MONGO_ROOT_PASSWORD=CHANGE_ME_ROOT
WEB_STATE_MONGO_USERNAME=web_state
WEB_STATE_MONGO_PASSWORD=CHANGE_ME_WEB
PIPELINE_STATE_MONGO_USERNAME=pipeline_state
PIPELINE_STATE_MONGO_PASSWORD=CHANGE_ME_PIPELINE
WEB_STATE_MONGO_URI=mongodb://web_state:CHANGE_ME_WEB@mongo-state:27017/ticketguard_web_state?authSource=ticketguard_web_state&replicaSet=state-rs
PIPELINE_STATE_MONGO_URI=mongodb://pipeline_state:CHANGE_ME_PIPELINE@mongo-state:27017/ticketguard_pipeline_state?authSource=ticketguard_pipeline_state&replicaSet=state-rs
```

`.env`는 Git에 포함하지 않는다. 기존 볼륨이 있는 환경에서는 해당 DB의
현재 계정을 사용한다. 초기화 스크립트는 기존 계정의 비밀번호를 변경하지 않는다.

## 웹·대시보드 실행

저장소 루트에서 기존 게이트웨이·QA 구성에 `compose.state.yml`을 추가한다.
관리자 대시보드가 사용하는 기존 시스템 MongoDB 네트워크도 실행되어 있어야 한다.

```powershell
docker compose --env-file .env -f compose.yml -f compose.state.yml -f compose.gateway.yml -f qa/compose.qa-db.yml config --quiet
docker compose --env-file .env -f compose.yml -f compose.state.yml -f compose.gateway.yml -f qa/compose.qa-db.yml up -d --build --wait
docker compose --env-file .env -f compose.yml -f compose.state.yml -f compose.gateway.yml -f qa/compose.qa-db.yml ps -a
```

`state-init`가 복제 세트의 primary 선출을 기다리고 DB별 계정을 준비한 뒤 웹이
시작된다. 초기화 컨테이너의 `Exited (0)`은 정상 완료 상태다.
웹 상태 DB는 내부 `state-backend` 네트워크만 사용하며 호스트 포트를 추가하지 않는다.

로그인 세션 저장 방식이 바뀐 직후에는 기존 쿠키 대신 다시 로그인한다.
이 구성은 기존 메모리·SQLite 상태를 자동으로 복사하지 않는다.
기존 MongoDB·QA 볼륨을 삭제하거나 초기화할 필요는 없다.

## 확인

`/health/`뿐 아니라 일반 사용자 로그인·공연 상세·예매 진입과 관리자 로그인 후
`/qa/`, `/admin/` 화면도 확인한다. `WEB_STATE_MONGO_URI` 누락은 Compose
구성 검사에서 실패하며, 연결·권한 오류는 상태 저장소 로그와 함께 점검한다.

테스트는 운영 상태 DB와 분리한다. 웹 테스트에는 `compose.state-test.yml`의
임시 MongoDB 복제 세트와 `TEST_MONGO_URI`를 사용하며, 상태 저장 테스트는
임시 DB를 생성·삭제한다. 웹·QA·운영 DB 주소를 테스트 주소로 사용하지 않는다.

## 회원가입 계정과 이벤트

`/signup/`은 `POST /api/action/`의 `action=signup`으로 계정을 생성한다.
웹 상태 DB의 `accounts` 컬렉션에 소문자 아이디, Django 비밀번호 해시,
생성 시각, 일반 사용자 역할을 저장한다. 아이디는 3~32자의 영문·숫자·밑줄·하이픈이며
영문·숫자로 시작한다. 비밀번호는 8~128자다. 개발용·관리자·QA 계정 이름은 예약된다.

`signup_requested`, `signup_success`, `signup_fail` 이벤트는 같은 DB의
`account_events`에 기록한다. 계정과 성공 이벤트는 같은 MongoDB 트랜잭션에서
저장되며, Kafka 전송은 기존 `outbox`로 이어진다. 이벤트에는 비밀번호·해시를 넣지 않는다.
가입 뒤 자동 로그인하지 않고 로그인 화면으로 이동한다.

기존 `raw-routing-v1`은 변경하지 않는다. 원본 적재기가 실행 중이면 새로운
`signup_*` 종류는 기존 계약의 `unclassified_events`로 보존되며, `auth_events`의
분류 변경은 원본 적재기의 버전 전환 작업으로 별도 진행한다. 계정과 가입 감사 기록은
원본 적재기 실행 여부와 관계없이 웹 상태 MongoDB에 남는다.

Kafka 장애 후 대기 이벤트는 기존 명령으로 재전송할 수 있다.

```powershell
docker compose --env-file .env -f compose.yml -f compose.state.yml -f compose.gateway.yml -f qa/compose.qa-db.yml exec -T web python manage.py drain_prevention_outbox
```
