# TicketGuard · AWS 인프라 (Terraform)

콘솔에서 만든 스테이징 AWS 리소스를 import로 코드에 연결한 구성.
작업 규칙: [`../AGENTS.md`](../AGENTS.md) · 작업 계획: [`../WORK_PLAN.md`](../WORK_PLAN.md)

> 상태: 뼈대 작성 완료 · 리소스 코드는 단계별로 추가 중 (아키텍처 그림 · 보안 설계 표는 9단계에서 작성)

## 파일 구성

| 파일 | 역할 |
|---|---|
| `versions.tf` · `backend.tf` · `provider.tf` | Terraform · provider 버전 · state 위치 · 리전 |
| `variables.tf` · `locals.tf` · `data.tf` | 입력 값 · 이름 규칙과 목록 · 읽기 전용 참조 |
| `network.tf` · `ec2.tf` | 보안 그룹 · 서버 · 고정 IP |
| `iam_ec2.tf` · `iam_team.tf` · `ssm_documents.tf` | 서버 권한 · 팀원 권한 · 포트 포워딩 문서 |
| `monitoring.tf` · `backup.tf` | 알람 · 백업 (기본 꺼짐) |
| `imports.tf` · `outputs.tf` | import 이력 · 출력 값 |
| `policies/` · `documents/` | AWS에서 내려받은 원문 JSON |

## 사용법

```powershell
copy terraform.tfvars.example terraform.tfvars   # 관리자 IP 입력
terraform init
terraform plan
```

- apply 전에 plan에 `must be replaced` · `destroy`가 없는지 확인
- 비밀 정보 · state · tfvars는 저장소에 올리지 않음
