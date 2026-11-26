# AGENTS.md · 티켓가드 인프라

`infra/` 폴더에서 일하는 사람과 AI 에이전트가 지켜야 할 규칙이에요.
작업 순서와 리소스 목록은 [`WORK_PLAN.md`](WORK_PLAN.md)를 따라요. WORK_PLAN은 가이드라인이고, 더 나은 방법이 확인되면 이 규칙 안에서 수정해요.

---

## 0. 적용 범위

- 이 문서는 **`infra/` 폴더 안에서만** 적용돼요. 다른 폴더는 각 팀 규칙(`AGENTS.md`, `qa/AGENTS.md`)을 따라요.
- 작업 브랜치: `infra` (main 기준) → 완료 후 `main` 대상 PR.
  - 팀 PR은 main에 병합됨 · `develop`은 갱신되지 않아 대상으로 쓰면 다른 팀 변경이 섞임 (2026-11-26 확인)
- 목표: 콘솔에서 만든 스테이징 AWS 리소스를 Terraform으로 **import**해 코드로 기록하고 `terraform plan` 결과 `No changes`를 확인해요.

## 1. 절대 규칙

1. **AI 에이전트는 사용자가 직접 말하기 전에는 커밋 · 푸시하지 않아요.** 허락은 그 한 번에만 해당해요.
2. **`terraform apply`는 사용자 확인 후에만 실행해요.** plan 결과(import · add · change · destroy 수)를 먼저 보고해요.
3. **plan에 `must be replaced` · `destroy`가 하나라도 있으면 apply하지 않아요.** 원인을 코드에서 고쳐 차이를 없애요.
4. **비밀 정보는 저장소에 올리지 않아요.** 저장소는 public이에요.
   - 금지: 비밀번호 · Access Key · 세션 토큰 · `terraform.tfstate*` · `terraform.tfvars` · 관리자 IP 값.
   - 허용: 계정 ID · 리소스 ID(`i-…`, `sg-…`) · 정책 ARN · 공개 IP(Elastic IP)는 문서와 코드에 써도 돼요.
5. **AWS에 쓰는 명령은 import 반영 외에는 하지 않아요.** 콘솔 · CLI로 리소스를 직접 만들거나 지우지 않아요.
6. **여러 PC에서 동시에 apply하지 않아요.** state는 작업 PC 로컬에 둬요.

## 2. 관리 대상 경계

| 구분 | 처리 |
|---|---|
| 보안 그룹 · 규칙, EC2, EIP, EC2용 IAM 역할, SSM 문서, 팀원 정책 | Terraform 관리 (import) |
| 기본 VPC · 서브넷, 팀원 IAM 사용자 | `data` 참조만 |
| 키 페어 | 이름 문자열만 사용 |
| `student` 사용자, `Encore*` · `Nds*` 리소스 | 교육기관 관리 · 코드에 넣지 않음 |
| Docker 컨테이너 · 앱 설정 | `compose*.yml` 담당 · infra에서 수정하지 않음 |

- `infra/` 밖 파일은 꼭 필요한 연결만 최소로 수정하고 이유를 보고해요. 예: 루트 `.gitignore`, `.github/workflows/`의 Terraform 검사.
- 다른 팀 코드(`qa/`, `pipeline/`, `web/`, compose 등)는 수정하지 않아요.

## 3. 작업 방식

- 정책 · SSM 문서 JSON은 AWS CLI로 내려받은 원문을 그대로 써요. 손으로 옮기지 않아요.
- 리소스 단위로 하나씩 import → plan 차이 0 확인 → 다음 단계로 넘어가요.
- 자동 생성 코드(`generated.tf`)는 다듬은 뒤 역할별 파일로 옮기고 삭제해요.
- 정책 변경(포트 정리 · 그룹 통합 등)은 `No changes` 확인 **이후** 별도 단계로 해요.

## 4. 검증과 보고

- 코드를 바꿀 때마다 `terraform fmt -check` · `terraform validate` · `terraform plan`을 실행해요.
- 보고에 포함: 바뀐 파일 · 대상 리소스 · plan 요약 · 남은 차이 · 커밋 · 푸시 여부.
- 시간 기준은 WORK_PLAN 9절이에요. 크게 넘길 것 같으면 먼저 알려요.
