# TicketGuard AWS 인프라 코드화 작업 계획

- 목적: 콘솔에서 만든 AWS 리소스를 Terraform 코드로 저장소에 기록 · 변경 이력 추적 · 재현 가능 구성 확보 · 발표에서 AWS 설계·운영 역량 제시
- 브랜치: `infra` (develop 기준) → 완료 후 `develop` 대상 PR
- 범위: 기존 스테이징 리소스를 **새로 만들지 않고 import로 연결** · 첫 목표는 `terraform plan` 결과 `No changes`
- 기준 시점: 2026-11-26 AWS CLI 조회 결과

## 1. 작업 시작 전 준비 (집 PC)

1. 저장소 최신화 후 브랜치 전환

   ```bash
   git fetch origin
   git switch infra
   git pull
   ```

2. Terraform 설치 (1.5 이상 · import 블록 사용)

   ```bash
   winget install Hashicorp.Terraform
   terraform -version
   ```

3. AWS 로그인 · 리전 서울

   ```bash
   aws login
   aws sts get-caller-identity
   ```

   - `Account` = `589792084208` · `Arn` = `user/student` 확인

## 2. 현재 AWS 리소스 목록 (import 대상)

| 구분 | 리소스 | ID · 값 |
|---|---|---|
| 계정 · 리전 | AWS 계정 | `589792084208` · `ap-northeast-2` |
| 네트워크 | 기본 VPC (관리 대상 아님, data 참조) | `vpc-03cb561a16c1282ee` · `172.31.0.0/16` |
| 네트워크 | 서브넷 (data 참조) | `subnet-0117c4c11c1469e9c` · `ap-northeast-2a` |
| 보안 그룹 | `ticketguard_staging_sg` | `sg-06824ead7c1315fe5` · 설명 `launch-wizard-1 created 2026-11-24T01:19:51.950Z` |
| 인바운드 | 80/tcp · `0.0.0.0/0` | `sgr-0039da6212244db22` |
| 인바운드 | 22/tcp · 관리자 IP /32 | `sgr-0760377096c68a1ac` |
| 인바운드 | 443/tcp · 관리자 IP /32 | `sgr-0d84194917ff5ac41` |
| 아웃바운드 | 전체 · `0.0.0.0/0` | `sgr-0dbcd0af299e35eef` |
| EC2 | `ticketguard_staging` | `i-05b288c5ca0f2e2a4` · `t3.medium` · 태그 `Name` |
| EC2 | AMI | `ami-0abf039e1df293d2f` (Ubuntu 24.04 · 20261120) |
| EC2 | 루트 볼륨 | `vol-0fc0e8e6a83924952` · 30 GB gp3 · 3000 IOPS · 125 MB/s · 암호화 · 종료 시 삭제 |
| EC2 | 메타데이터 | IMDSv2 필수 (`http_tokens = required`) · hop limit 2 |
| EC2 | 기타 | user_data 없음 · 상세 모니터링 꺼짐 · EBS 최적화 켜짐 |
| EC2 | 키 페어 | `ticketguard-staging` (`key-00b8fa850bade7419`) · 이름만 참조, 관리 대상 아님 |
| Elastic IP | `43.200.205.217` | `eipalloc-09d0e48d258532fff` · 연결 `eipassoc-0d9011ff971cec7a8` |
| IAM (EC2) | 역할 `ticketguard-ec2-ssm` | 신뢰 주체 `ec2.amazonaws.com` · 최대 세션 3600 |
| IAM (EC2) | 인스턴스 프로파일 | `ticketguard-ec2-ssm` |
| IAM (EC2) | 정책 연결 | `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` |
| SSM | Session 문서 4개 (JSON) | `TicketGuard-Mongo-PortForward` (v3) · `TicketGuard-QA-Mongo-PortForward` · `TicketGuard-Spark-Mongo-PortForward` · `TicketGuard-WebState-Mongo-PortForward` |
| IAM (팀) | 관리형 정책 | `arn:aws:iam::589792084208:policy/TicketGuardMongoPortForwardAccess` |
| IAM (팀) | 사용자 | `ticketguard_Hong` · `ticketguard_Ryu` |
| IAM (팀) | 직접 연결 정책 (두 사용자 동일) | `TicketGuardMongoPortForwardAccess` · `IAMReadOnlyAccess` · `AmazonEC2ReadOnlyAccess` · `AWSCloudShellFullAccess` |
| IAM (팀) | 인라인 정책 (두 사용자 동일) | `TicketGuardQAMongoPortForwardAccess` · `TicketGuardSparkPortForwardAccess` · `TicketGuardWebStatePortForwardAccess` · `TicketGuardShellAccess` |

### 코드에 넣지 않는 것

| 대상 | 이유 |
|---|---|
| 기본 VPC · 서브넷 | 계정 기본 리소스 · `data` 참조만 |
| `student` 사용자 · `Encore*` · `Nds*` 역할과 정책 · `sts` 정책 | 교육기관 관리 리소스 |
| 키 페어 | `aws_key_pair`는 공개키 값 필요 · import 시 교체 계획 발생 → 이름 문자열만 사용 |
| 팀원 콘솔 비밀번호 · Access Key | 비밀 정보 |
| Docker 컨테이너 · 앱 설정 | 기존 `compose*.yml` 담당 |

## 3. 폴더 구조

```text
infra/
├── WORK_PLAN.md          # 이 문서
└── terraform/
    ├── README.md         # 사용법 · 아키텍처 그림 · 보안 설계 표
    ├── versions.tf       # terraform · aws provider 버전
    ├── provider.tf       # region · 기본 태그
    ├── variables.tf      # admin_cidr · instance_type 등
    ├── data.tf           # 기본 VPC · 서브넷
    ├── network.tf        # 보안 그룹 · 규칙
    ├── ec2.tf            # 인스턴스 · EIP · EIP 연결
    ├── iam_ec2.tf        # EC2 역할 · 인스턴스 프로파일 · 정책 연결
    ├── ssm_documents.tf  # 포트 포워딩 문서 4개
    ├── iam_team.tf       # 팀원 정책 · 연결
    ├── imports.tf        # import 블록 (No changes 확인 후 삭제 가능)
    ├── outputs.tf        # instance_id · public_ip
    ├── policies/         # 정책 JSON (aws CLI로 내려받은 원문)
    ├── documents/        # SSM 문서 JSON (aws CLI로 내려받은 원문)
    └── terraform.tfvars.example
```

`.gitignore` 추가 항목

```text
infra/terraform/.terraform/
infra/terraform/*.tfstate
infra/terraform/*.tfstate.*
infra/terraform/terraform.tfvars
infra/terraform/crash.log
```

- `.terraform.lock.hcl`은 커밋 (provider 버전 고정)

## 4. 원문 JSON 내려받기

정책 · 문서 내용을 손으로 옮기지 않고 원문 그대로 저장 → plan 차이 최소화

```bash
cd infra/terraform
mkdir policies documents

aws iam get-role --role-name ticketguard-ec2-ssm --query Role.AssumeRolePolicyDocument > policies/ec2_ssm_trust.json

v=$(aws iam get-policy --policy-arn arn:aws:iam::589792084208:policy/TicketGuardMongoPortForwardAccess --query Policy.DefaultVersionId --output text)
aws iam get-policy-version --policy-arn arn:aws:iam::589792084208:policy/TicketGuardMongoPortForwardAccess --version-id $v --query PolicyVersion.Document > policies/mongo_port_forward.json

for p in TicketGuardQAMongoPortForwardAccess TicketGuardSparkPortForwardAccess TicketGuardWebStatePortForwardAccess TicketGuardShellAccess; do
  aws iam get-user-policy --user-name ticketguard_Hong --policy-name $p --query PolicyDocument > policies/$p.json
done

for d in TicketGuard-Mongo-PortForward TicketGuard-QA-Mongo-PortForward TicketGuard-Spark-Mongo-PortForward TicketGuard-WebState-Mongo-PortForward; do
  aws ssm get-document --name $d --query Content --output text > documents/$d.json
done
```

- Git Bash 기준 문법 · PowerShell에서는 `for` 반복을 한 줄씩 실행
- Ryu 인라인 정책이 Hong과 같은지 확인: 같은 명령을 `--user-name ticketguard_Ryu`로 실행 후 비교
- 관리자 IP: `aws ec2 describe-security-group-rules --filters Name=group-id,Values=sg-06824ead7c1315fe5` 결과의 `CidrIpv4` → `terraform.tfvars`의 `admin_cidr`에만 기록

## 5. import 블록 목록

```hcl
import { to = aws_security_group.staging                       id = "sg-06824ead7c1315fe5" }
import { to = aws_vpc_security_group_ingress_rule.http          id = "sgr-0039da6212244db22" }
import { to = aws_vpc_security_group_ingress_rule.ssh           id = "sgr-0760377096c68a1ac" }
import { to = aws_vpc_security_group_ingress_rule.https         id = "sgr-0d84194917ff5ac41" }
import { to = aws_vpc_security_group_egress_rule.all            id = "sgr-0dbcd0af299e35eef" }
import { to = aws_instance.staging                              id = "i-05b288c5ca0f2e2a4" }
import { to = aws_eip.staging                                   id = "eipalloc-09d0e48d258532fff" }
import { to = aws_eip_association.staging                       id = "eipassoc-0d9011ff971cec7a8" }
import { to = aws_iam_role.ec2_ssm                              id = "ticketguard-ec2-ssm" }
import { to = aws_iam_instance_profile.ec2_ssm                  id = "ticketguard-ec2-ssm" }
import { to = aws_iam_role_policy_attachment.ec2_ssm_core       id = "ticketguard-ec2-ssm/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" }
import { to = aws_ssm_document.port_forward["TicketGuard-Mongo-PortForward"]          id = "TicketGuard-Mongo-PortForward" }
import { to = aws_ssm_document.port_forward["TicketGuard-QA-Mongo-PortForward"]       id = "TicketGuard-QA-Mongo-PortForward" }
import { to = aws_ssm_document.port_forward["TicketGuard-Spark-Mongo-PortForward"]    id = "TicketGuard-Spark-Mongo-PortForward" }
import { to = aws_ssm_document.port_forward["TicketGuard-WebState-Mongo-PortForward"] id = "TicketGuard-WebState-Mongo-PortForward" }
import { to = aws_iam_policy.mongo_port_forward                 id = "arn:aws:iam::589792084208:policy/TicketGuardMongoPortForwardAccess" }
```

- HCL에서는 `to`와 `id`를 줄바꿈으로 구분 (위 표기는 한 줄 요약)
- 팀원 정책 import ID 형식
  - 인라인: `aws_iam_user_policy` → `ticketguard_Hong:TicketGuardShellAccess`
  - 연결: `aws_iam_user_policy_attachment` → `ticketguard_Hong/arn:aws:iam::aws:policy/IAMReadOnlyAccess`
  - 사용자 2명 × 인라인 4개 · 연결 4개 = 16개 → `for_each`로 작성
- 사용자 자체는 `data "aws_iam_user"` 참조 (로그인 프로필 · 키를 Terraform 관리 밖에 둠)

## 6. 진행 순서

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1 | `versions.tf` · `provider.tf` · `data.tf` 작성 → `terraform init` | `.terraform.lock.hcl` 생성 |
| 2 | 보안 그룹 · 규칙 + import | 해당 리소스 plan 차이 0 |
| 3 | EC2 · EIP + import | 차이 0 · **replace 계획이 하나라도 나오면 apply 금지** |
| 4 | EC2용 IAM 역할 · 프로파일 + import | 차이 0 |
| 5 | SSM 문서 4개 + import | 차이 0 |
| 6 | 팀원 정책 · 연결 + import | 차이 0 |
| 7 | 전체 `terraform plan` | `No changes` 또는 `N to import, 0 to add, 0 to change, 0 to destroy` |
| 8 | `terraform apply` (import만 반영) | state에 전체 리소스 기록 · 이후 plan `No changes` |
| 9 | README · 아키텍처 그림 · CI(`fmt -check` · `validate`) | PR 준비 |

### EC2 import 시 차이 줄이기

- `ami`는 현재 값 고정 · `lifecycle { ignore_changes = [ami, user_data] }`
- `root_block_device`: `volume_size = 30` · `volume_type = "gp3"` · `encrypted = true` · `delete_on_termination = true`
- `metadata_options`: `http_tokens = "required"` · `http_put_response_hop_limit = 2` · `http_endpoint = "enabled"`
- `iam_instance_profile = aws_iam_instance_profile.ec2_ssm.name` · `key_name = "ticketguard-staging"`
- 보안 그룹은 `vpc_security_group_ids` 사용 (`security_groups` 사용 시 교체 발생)
- 보안 그룹 `description`은 현재 문자열 그대로 · 변경 시 보안 그룹 교체 발생

### 금지 사항

- plan에 `must be replaced` · `destroy` 표시 상태에서 apply
- `terraform.tfstate` · `terraform.tfvars` 커밋
- 콘솔 직접 변경 후 코드 미반영 (코드와 실제 불일치)
- 여러 PC 동시 apply

### state 위치

- 기본: 작업 PC 로컬 state · 저장소 제외
- 모든 리소스가 import 기반 → state 분실 시 다른 PC에서 `terraform apply`(import)로 재생성 가능
- 확장: S3 backend + `use_lockfile` (버킷 생성은 강사 확인 후)

## 7. 결정 필요 항목

| 항목 | 현재 | 제안 |
|---|---|---|
| 팀원 권한 구조 | 사용자별 정책 8개씩 개별 연결 | No changes 확인 후 `ticketguard-team` 그룹으로 통합 |
| 22번 포트 | 관리자 IP 허용 | SSM 접속 사용 중 → 제거 검토 |
| 80번 포트 | 전체 공개 | 서비스 공개 의도 확인 |
| 셸 접속 로그 | Session Manager 기본 설정 없음 | 세션 로그 저장 설정 검토 |
| 추가 리소스 | 없음 | CloudWatch 알람 · S3 백업 → 강사 확인 후 |

## 8. AWS 활용 어필 정리

| 항목 | 근거 | 상태 |
|---|---|---|
| IaC | Terraform import · plan `No changes` 화면 | 이번 작업 |
| SSH 없는 서버 접근 | SSM Session Manager · 포트 포워딩 문서 4개 · DB 포트 비공개 | 구성 완료 · 코드화 대상 |
| 최소 권한 | 인스턴스 · 문서 단위 제한 · 조회와 셸 권한 분리 | 구성 완료 · 코드화 대상 |
| 메타데이터 보호 | IMDSv2 필수 | 구성 완료 · 코드화 대상 |
| 접근 감사 | CloudTrail 조회 이력 확인 | 확인 완료 |
| 운영 감시 | CloudWatch 알람 (CPU · 상태 검사 · 디스크) | 다음 단계 |
| 데이터 보존 | S3 백업 (MongoDB 덤프 · QA 리포트) | 다음 단계 |
| 아키텍처 그림 | VPC → EC2(Docker: Django · Kafka · Spark · MongoDB) · SSM · CloudWatch | README에 포함 |

## 9. 예상 작업량

| 단계 | 예상 |
|---|---|
| 준비 · 원문 JSON | 0.5시간 |
| 코드 작성 | 3~4시간 |
| import · plan 차이 맞추기 | 2~3시간 |
| README · 아키텍처 그림 | 1~2시간 |
| CI | 0.5~1시간 |
| 합계 | 1~1.5일 |
