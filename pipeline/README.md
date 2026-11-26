<!--
[홍석환] Spark 처리 모듈과 실행 안내
기존 구조를 유지하면서 한 시간 지표 처리와 재현 가능한 검사 위치를 안내한다.
-->
# Spark 파이프라인

환경 설정, 처리 기능, DB 설정, 점검 도구와 문서를 역할별로 분리한다.

```text
pipeline/
├─ main.py                  실행 진입점: ingest / check / flow
├─ Dockerfile               컨테이너 빌드 및 실행
├─ configuration/           환경 설정
│  ├─ ingest.py             기존 적재 설정
│  ├─ user_flow.py          사용자 흐름 설정
│  ├─ flow.env.example      사용자 흐름 환경변수 예시
│  ├─ requirements.txt      Python 의존성
│  └─ log4j2.properties     Spark 로깅 설정
├─ ingestion/               기존 이벤트 적재 기능
│  ├─ records.py            Kafka 레코드 변환
│  ├─ normalization.py      이벤트 정규화
│  └─ mongo_writer.py       MongoDB 적재
├─ user_flow/               사용자 흐름 지표 기능
│  ├─ events.py             이벤트 해석·관측값 추출
│  ├─ metrics.py            지표 계산
│  ├─ state.py              복구 상태·청크 관리
│  ├─ verification.py       원본 DB 대조 검증
│  ├─ store.py              결과 저장·공개
│  └─ runtime.py            처리 연결·주기적 갱신
├─ database/                DB 권한 설정
│  └─ flow_permissions.js
├─ tools/                   빌드·실행 점검 도구
│  ├─ resolve_dependencies.py
│  ├─ check.py
│  └─ check_imports.py        외부 연결 없는 모듈 경로 확인
└─ docs/                    설계·운영·검증 문서
   ├─ user_flow.md
   ├─ contract.md
   └─ validation.md
```

실행 진입점과 Docker 빌드 위치는 유지한다. 저장소 루트의 `compose.yml`은 기존 적재, `compose.flow.yml`은 사용자 흐름 처리에 사용한다. 체크포인트와 DB의 데이터 경로는 이번 정리로 변경하지 않는다.

- [사용자 흐름 실행·구현 안내](docs/user_flow.md)
- [DB 저장·대시보드 조회 형식](docs/contract.md)
- [실행 준비와 관리자 브랜치 후속 작업](docs/connection_work.md)
- [이전 구현 검증 기록과 제한](docs/validation.md)

`tests/`에는 현재 코드의 계산·실패 복구 검사와 운영에서 분리된 Kafka·Spark·MongoDB 통합 검사가 있다. 운영 이미지에는 포함하지 않는다. 과거 검증 기록과 이번 검사 결과를 구분한다.

## AWS staging 실행 기본값

- 기준 사양: t3.medium, 2 vCPU, RAM 4 GiB, gp3 30 GiB. 아래 값은 초기 설정이며 200 VU 처리 성능을 보장하지 않는다.
- ingest: `SPARK_TRIGGER_SECONDS=1`, `SPARK_MAX_OFFSETS_PER_TRIGGER=2000`.
- flow: `FLOW_TRIGGER_SECONDS=1`, `SPARK_MAX_OFFSETS_PER_TRIGGER=2000`.
- 두 모드는 Compose 서비스 환경에서 값을 받는다. 명시한 환경 값이 기본값보다 우선하며, 별도 프로젝트 실행 시 서비스별 환경 파일로 조정할 수 있다.
- network 모드의 5초 주기·최대 offsets 1000은 유지한다.
- 공통 Spark 이미지: `local[2]`, `--driver-memory 1g`. driver heap 외 Python·JVM 부가 메모리는 별도이며, 컨테이너 전체 메모리 제한을 뜻하지 않는다.
- Kafka와 Spark 서비스(ingest/flow/network)는 Docker `json-file`, `max-size: 10m`, `max-file: 3`으로 로그를 회전한다. Kafka 토픽 보존과는 별개다.
- 입장 제한 40명 / 5초, Kafka heap 512 MiB·3 partitions·RF 1, Mongo bulk 200과 기존 체크포인트·consumer group은 유지한다.
- ingest·flow·network는 공통 이미지 변경을 반영하려면 각각 다시 빌드해야 한다. Compose 환경·로그 정책은 컨테이너 재생성 후 적용된다. 기존 프로젝트명·Compose 파일 조합·환경 파일·볼륨을 그대로 사용한다.
- 이번 설정 변경에는 토픽 수정이나 체크포인트 초기화가 필요하지 않다. QA 시나리오와 판정 기준도 변경하지 않는다.
