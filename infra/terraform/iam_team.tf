# ------------------------------------------------------------
# 생성 이유: 팀원 접근 권한(정책 · 연결)의 코드 기록
# 관리 대상: 팀 관리형 정책 · 사용자별 인라인 정책 · 사용자별 연결 정책
# 수정 시점: 팀원 · 권한 변경 시 (입력 목록은 locals.tf)
# ------------------------------------------------------------

# 팀 공통 관리형 정책 · RAW MongoDB 포트 포워딩 허용 · 원문은 policies/mongo_port_forward.json
resource "aws_iam_policy" "mongo_port_forward" {
  name   = "TicketGuardMongoPortForwardAccess"
  path   = "/"
  policy = file("${path.module}/policies/mongo_port_forward.json")
}

# 사용자 × 인라인 정책 · 정책 본문은 policies/<정책 이름>.json 을 모든 사용자가 공유
resource "aws_iam_user_policy" "team" {
  for_each = local.team_inline

  user   = data.aws_iam_user.team[each.value.user].user_name
  name   = each.value.policy
  policy = file("${path.module}/policies/${each.value.policy}.json")
}

# 사용자 × 연결 정책 · 팀 관리형 정책과 AWS 관리형 정책 구분 없이 ARN 으로 연결
resource "aws_iam_user_policy_attachment" "team" {
  for_each = local.team_attached

  user       = data.aws_iam_user.team[each.value.user].user_name
  policy_arn = each.value.policy_arn

  # ARN을 문자열로 받으므로 생성 순서 명시 · 새로 구축 시 팀 관리형 정책 생성 후 연결
  depends_on = [aws_iam_policy.mongo_port_forward]
}
