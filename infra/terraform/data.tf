# ------------------------------------------------------------
# 생성 이유: 관리하지 않고 읽기만 하는 리소스의 참조 지점 단일화
# 관리 대상: 계정 · 기본 VPC · 서브넷 · 팀원 IAM 사용자
# 수정 시점: 참조 대상이 바뀔 때 (이 파일의 리소스는 생성 · 변경 · 삭제 불가)
# ------------------------------------------------------------

# 현재 로그인 계정 정보 (계정 ID 하드코딩 방지)
data "aws_caller_identity" "current" {}

# 기본 VPC (계정 기본 리소스 · 관리 대상 아님)
data "aws_vpc" "default" {
  default = true
}

# 인스턴스 서브넷 (기본 VPC 소속)
data "aws_subnet" "staging" {
  id = var.subnet_id
}

# 팀원 IAM 사용자 (정책 연결 대상으로만 사용)
data "aws_iam_user" "team" {
  for_each  = toset(local.team_users)
  user_name = each.value
}
