# ------------------------------------------------------------
# 생성 이유: AWS 접속 리전과 전체 리소스 공통 태그의 단일 정의
# 관리 대상: provider "aws"
# 수정 시점: 리전 변경 또는 공통 태그 도입 시 (값은 variables.tf · tfvars)
# ------------------------------------------------------------

provider "aws" {
  region = var.aws_region

  # 공통 태그 · 기본값 빈 맵 (import 직후 plan 차이 방지)
  # 태그 정리는 No changes 확인 후 extra_tags 값으로 반영
  default_tags {
    tags = var.extra_tags
  }
}
