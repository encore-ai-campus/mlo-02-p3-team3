# ------------------------------------------------------------
# 생성 이유: 다른 도구 · 문서가 참조하는 값의 공개 지점
# 관리 대상: 출력 값 (민감 정보 제외)
# 수정 시점: 새 리소스의 ID · 주소를 외부에서 쓸 때
# ------------------------------------------------------------

output "account_id" {
  description = "AWS 계정 ID"
  value       = data.aws_caller_identity.current.account_id
}

output "vpc_id" {
  description = "기본 VPC ID"
  value       = data.aws_vpc.default.id
}

output "subnet_id" {
  description = "인스턴스 서브넷 ID"
  value       = data.aws_subnet.staging.id
}
