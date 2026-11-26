# ------------------------------------------------------------
# 생성 이유: Terraform · AWS provider 버전 고정으로 PC · CI 간 동작 차이 제거
# 관리 대상: terraform 블록 · required_providers
# 수정 시점: provider 메이저 버전 상향 시 (잠금 파일 동시 갱신)
# ------------------------------------------------------------

terraform {
  # import 블록의 for_each 사용 조건 (1.7 이상)
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
  }
}
