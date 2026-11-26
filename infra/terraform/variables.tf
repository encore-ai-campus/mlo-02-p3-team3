# ------------------------------------------------------------
# 생성 이유: 환경마다 달라질 수 있는 값을 코드 밖으로 분리하여 재사용성 확보
# 관리 대상: 모든 입력 변수 (기본값 · 설명 · 검증)
# 수정 시점: 새 설정 값이 필요할 때 (리소스 파일에는 값을 직접 쓰지 않음)
# ------------------------------------------------------------

# ---------- 공통 ----------

variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "project_name" {
  description = "리소스 이름 접두어에 쓰는 프로젝트 이름"
  type        = string
  default     = "ticketguard"
}

variable "environment" {
  description = "환경 이름 (staging 등)"
  type        = string
  default     = "staging"
}

variable "extra_tags" {
  description = "모든 리소스에 추가하는 공통 태그. 빈 맵이 기본값"
  type        = map(string)
  default     = {}
}

# ---------- 네트워크 ----------

variable "subnet_id" {
  description = "인스턴스가 속한 서브넷 ID (기본 VPC 안의 서브넷)"
  type        = string
  default     = "subnet-0117c4c11c1469e9c"
}

variable "admin_cidr" {
  description = "관리자 접속 허용 대역. /32 단일 IP 전용. 값은 terraform.tfvars에만 기록"
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && endswith(var.admin_cidr, "/32")
    error_message = "admin_cidr 형식 오류. 예: 203.0.113.10/32"
  }
}

# ---------- EC2 ----------

variable "instance_type" {
  description = "EC2 인스턴스 타입"
  type        = string
  default     = "t3.medium"
}

variable "instance_ami" {
  description = "인스턴스 AMI ID. 교체 방지를 위해 현재 값 고정 (ignore_changes 적용)"
  type        = string
  default     = "ami-0abf039e1df293d2f"
}

variable "key_pair_name" {
  description = "기존 키 페어 이름. 키 페어 자체는 Terraform 관리 대상 아님"
  type        = string
  default     = "ticketguard-staging"
}

variable "instance_profile_name" {
  description = "인스턴스에 연결된 IAM 인스턴스 프로파일 이름 (iam_ec2.tf 의 프로파일과 동일 값)"
  type        = string
  default     = "ticketguard-ec2-ssm"
}

variable "root_volume_size_gb" {
  description = "루트 볼륨 크기 (GB)"
  type        = number
  default     = 30
}

# ---------- 선택 기능 (기본 꺼짐) ----------

variable "enable_monitoring_alarms" {
  description = "CloudWatch 알람 생성 여부. 강사 확인 후 true"
  type        = bool
  default     = false
}

variable "enable_backup_bucket" {
  description = "S3 백업 버킷 생성 여부. 강사 확인 후 true"
  type        = bool
  default     = false
}
