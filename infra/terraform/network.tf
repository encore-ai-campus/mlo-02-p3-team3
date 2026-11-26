# ------------------------------------------------------------
# 생성 이유: 스테이징 서버 접근 경로(보안 그룹 · 규칙)의 코드 기록
# 관리 대상: ticketguard_staging_sg · 인바운드 80/22/443 · 아웃바운드 전체
# 수정 시점: 포트 개방 · 차단 정책 변경 시
# ------------------------------------------------------------

# 스테이징 서버 보안 그룹
# name · description 변경 시 보안 그룹 교체 발생 → 콘솔 생성 값 유지 (수정 금지)
resource "aws_security_group" "staging" {
  name        = "ticketguard_staging_sg"
  description = "launch-wizard-1 created 2026-11-24T01:19:51.950Z"
  vpc_id      = data.aws_vpc.default.id

  lifecycle {
    prevent_destroy = true
  }
}

# 인바운드 HTTP · 전체 공개 (공개 범위 재검토 대상 · README 현재 한계 참고)
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.staging.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# 인바운드 SSH · 관리자 IP 한정 (SSM 접속 사용 중 · 제거 검토 대상)
resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.staging.id
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.admin_cidr
}

# 인바운드 HTTPS · 관리자 IP 한정
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.staging.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = var.admin_cidr
}

# 아웃바운드 전체 허용 (프로토콜 -1 은 포트 지정 불가)
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.staging.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
