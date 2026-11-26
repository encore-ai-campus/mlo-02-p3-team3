# ------------------------------------------------------------
# 생성 이유: 이름 규칙과 반복 목록을 한 곳에 모아 변경 지점 단일화
# 관리 대상: name_prefix · SSM 문서 목록 · 팀원 목록 · 팀원 정책 목록
# 수정 시점: 팀원 추가 · 포트 포워딩 문서 추가 · 팀 정책 추가 시 (목록에 항목만 추가)
# ------------------------------------------------------------

locals {
  # 리소스 이름 접두어 (예: ticketguard-staging)
  name_prefix = "${var.project_name}-${var.environment}"

  # 포트 포워딩 SSM 문서 이름. documents/<이름>.json 파일과 1:1 대응
  ssm_documents = [
    "TicketGuard-Mongo-PortForward",
    "TicketGuard-QA-Mongo-PortForward",
    "TicketGuard-Spark-Mongo-PortForward",
    "TicketGuard-WebState-Mongo-PortForward",
  ]

  # 팀원 IAM 사용자. 사용자 자체는 data 참조 (로그인 프로필 · 키는 관리 밖)
  team_users = [
    "ticketguard_Hong",
    "ticketguard_Ryu",
  ]

  # 팀원 공통 인라인 정책 이름. policies/<이름>.json 파일과 1:1 대응
  team_inline_policies = [
    "TicketGuardQAMongoPortForwardAccess",
    "TicketGuardSparkPortForwardAccess",
    "TicketGuardWebStatePortForwardAccess",
    "TicketGuardShellAccess",
  ]

  # 팀원 공통 연결 정책 ARN. 계정 ID는 data에서 조회
  team_attached_policy_arns = [
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/TicketGuardMongoPortForwardAccess",
    "arn:aws:iam::aws:policy/IAMReadOnlyAccess",
    "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess",
    "arn:aws:iam::aws:policy/AWSCloudShellFullAccess",
  ]

  # 사용자 × 인라인 정책 조합. 키 형식 "사용자:정책" (import ID와 동일)
  team_inline = {
    for pair in setproduct(local.team_users, local.team_inline_policies) :
    "${pair[0]}:${pair[1]}" => { user = pair[0], policy = pair[1] }
  }

  # 사용자 × 연결 정책 조합. 키 형식 "사용자/ARN" (import ID와 동일)
  team_attached = {
    for pair in setproduct(local.team_users, local.team_attached_policy_arns) :
    "${pair[0]}/${pair[1]}" => { user = pair[0], policy_arn = pair[1] }
  }
}
