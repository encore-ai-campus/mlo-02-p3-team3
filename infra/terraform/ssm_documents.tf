# ------------------------------------------------------------
# 생성 이유: SSH 없는 DB 접근(포트 포워딩) 문서의 코드 기록
# 관리 대상: local.ssm_documents 목록의 Session 문서 (원문은 documents/)
# 수정 시점: 포워딩 대상 포트 · 문서 추가 시
# ------------------------------------------------------------

# 포트 포워딩 Session 문서 · documents/<이름>.json 원문을 정규화하여 사용
# 문서 추가 시 locals.tf 목록과 documents/ 파일만 추가 (이 블록 수정 불필요)
resource "aws_ssm_document" "port_forward" {
  for_each = toset(local.ssm_documents)

  name            = each.key
  document_type   = "Session"
  document_format = "JSON"

  # 원문 파일 그대로 사용 (끝 줄바꿈 포함 · 가공 금지)
  # jsonencode · trimspace 등 가공은 AWS 저장 값과 달라져 문서 새 버전 생성
  content = file("${path.module}/documents/${each.key}.json")
}
