# ------------------------------------------------------------
# 생성 이유: 스테이징 서버 본체와 고정 IP의 코드 기록
# 관리 대상: ticketguard_staging 인스턴스 · Elastic IP · EIP 연결
# 수정 시점: 인스턴스 타입 · 볼륨 · 메타데이터 정책 변경 시
# ------------------------------------------------------------

# 스테이징 서버 (Docker: Django · Kafka · Spark · MongoDB)
resource "aws_instance" "staging" {
  ami                  = var.instance_ami
  instance_type        = var.instance_type
  key_name             = var.key_pair_name
  subnet_id            = data.aws_subnet.staging.id
  iam_instance_profile = aws_iam_instance_profile.ec2_ssm.name

  # security_groups 사용 시 교체 발생 → 반드시 vpc_security_group_ids 사용
  vpc_security_group_ids = [aws_security_group.staging.id]

  ebs_optimized           = true
  monitoring              = false
  source_dest_check       = true
  disable_api_termination = false
  disable_api_stop        = false

  # t3 CPU 크레딧 방식 (현재 unlimited)
  credit_specification {
    cpu_credits = "unlimited"
  }

  # IMDSv2 필수 · 컨테이너에서 접근 가능하도록 hop limit 2
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    iops                  = 3000
    throughput            = 125
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "ticketguard_staging"
  }

  lifecycle {
    # AMI 갱신 · 최초 user_data 변경이 교체로 이어지지 않도록 제외
    ignore_changes  = [ami, user_data]
    prevent_destroy = true
  }
}

# 고정 공인 IP
resource "aws_eip" "staging" {
  domain = "vpc"

  lifecycle {
    prevent_destroy = true
  }
}

# 고정 IP와 인스턴스 연결
resource "aws_eip_association" "staging" {
  allocation_id = aws_eip.staging.id
  instance_id   = aws_instance.staging.id
}
