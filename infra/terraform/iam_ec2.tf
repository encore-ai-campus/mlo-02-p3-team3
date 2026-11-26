# ------------------------------------------------------------
# 생성 이유: 인스턴스의 SSM 접속 권한(역할 · 프로파일)의 코드 기록
# 관리 대상: ticketguard-ec2-ssm 역할 · 인스턴스 프로파일 · SSM 정책 연결
# 수정 시점: 인스턴스에 부여하는 AWS 권한 변경 시
# ------------------------------------------------------------

# EC2 서비스가 맡는 역할 · 신뢰 정책 원문은 policies/ec2_ssm_trust.json
resource "aws_iam_role" "ec2_ssm" {
  name                 = var.instance_profile_name
  description          = "Allows EC2 instances to call AWS services on your behalf."
  max_session_duration = 3600
  assume_role_policy   = file("${path.module}/policies/ec2_ssm_trust.json")
}

# 인스턴스에 역할을 붙이는 프로파일 · 역할과 같은 이름
resource "aws_iam_instance_profile" "ec2_ssm" {
  name = var.instance_profile_name
  role = aws_iam_role.ec2_ssm.name
}

# SSM Session Manager 접속에 필요한 AWS 관리형 정책
resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2_ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
