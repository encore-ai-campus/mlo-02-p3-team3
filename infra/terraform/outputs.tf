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

output "instance_id" {
  description = "스테이징 서버 인스턴스 ID"
  value       = aws_instance.staging.id
}

output "public_ip" {
  description = "스테이징 서버 고정 공인 IP (Elastic IP)"
  value       = aws_eip.staging.public_ip
}

# 팀원 DB 접속용 명령 예시 · 문서 이름은 local.ssm_documents 목록 기준
output "ssm_port_forward_commands" {
  description = "문서별 SSM 포트 포워딩 접속 명령"
  value = {
    for name in local.ssm_documents :
    name => "aws ssm start-session --target ${aws_instance.staging.id} --document-name ${name} --region ${var.aws_region}"
  }
}
