# ------------------------------------------------------------
# 생성 이유: 콘솔에서 만든 기존 리소스의 코드 연결 이력 보존
# 관리 대상: import 블록 전체 (적용 후에도 삭제하지 않고 이력으로 유지)
# 수정 시점: 기존 리소스를 새로 코드에 연결할 때만 (각 단계에서 추가)
# ------------------------------------------------------------

# ---------- 2단계 · 네트워크 ----------

import {
  to = aws_security_group.staging
  id = "sg-06824ead7c1315fe5"
}

import {
  to = aws_vpc_security_group_ingress_rule.http
  id = "sgr-0039da6212244db22"
}

import {
  to = aws_vpc_security_group_ingress_rule.ssh
  id = "sgr-0760377096c68a1ac"
}

import {
  to = aws_vpc_security_group_ingress_rule.https
  id = "sgr-0d84194917ff5ac41"
}

import {
  to = aws_vpc_security_group_egress_rule.all
  id = "sgr-0dbcd0af299e35eef"
}

# ---------- 3단계 · 서버 ----------

import {
  to = aws_instance.staging
  id = "i-05b288c5ca0f2e2a4"
}

import {
  to = aws_eip.staging
  id = "eipalloc-09d0e48d258532fff"
}

import {
  to = aws_eip_association.staging
  id = "eipassoc-0d9011ff971cec7a8"
}

# ---------- 4단계 · EC2용 IAM ----------

import {
  to = aws_iam_role.ec2_ssm
  id = "ticketguard-ec2-ssm"
}

import {
  to = aws_iam_instance_profile.ec2_ssm
  id = "ticketguard-ec2-ssm"
}

import {
  to = aws_iam_role_policy_attachment.ec2_ssm_core
  id = "ticketguard-ec2-ssm/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ---------- 5단계 · SSM 포트 포워딩 문서 ----------

import {
  for_each = toset(local.ssm_documents)
  to       = aws_ssm_document.port_forward[each.key]
  id       = each.key
}

# ---------- 6단계 · 팀원 정책 ----------

import {
  to = aws_iam_policy.mongo_port_forward
  id = "arn:aws:iam::589792084208:policy/TicketGuardMongoPortForwardAccess"
}

# 인라인 import ID 형식: 사용자:정책 이름
import {
  for_each = local.team_inline
  to       = aws_iam_user_policy.team[each.key]
  id       = each.key
}

# 연결 import ID 형식: 사용자/정책 ARN
import {
  for_each = local.team_attached
  to       = aws_iam_user_policy_attachment.team[each.key]
  id       = each.key
}
