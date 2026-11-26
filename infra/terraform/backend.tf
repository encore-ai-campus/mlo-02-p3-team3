# ------------------------------------------------------------
# 생성 이유: state 저장 위치를 한 파일에 고정하여 저장소 이전 시 수정 지점 단일화
# 관리 대상: terraform backend 설정
# 수정 시점: S3 backend 전환 시 (버킷 생성은 강사 확인 후)
# ------------------------------------------------------------

# 현재: 작업 PC 로컬 state · 저장소 제외 대상 (.gitignore)
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}

# 전환 예시 (S3 · 잠금 파일 방식). 위 local 블록 삭제 후 아래 블록 사용.
#
# terraform {
#   backend "s3" {
#     bucket       = "<state 버킷 이름>"
#     key          = "ticketguard/staging/terraform.tfstate"
#     region       = "ap-northeast-2"
#     encrypt      = true
#     use_lockfile = true
#   }
# }
