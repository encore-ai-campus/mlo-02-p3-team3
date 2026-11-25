"""Gold 반영 시간 · 격리 건수 · 기획서 21쪽 · Gold p95 10초 이하"""
from ticket_guard_qa.domain.enums import SuiteOutcome
from ticket_guard_qa.domain.models import SuiteResult
from ticket_guard_qa.scoring.latency_metrics import calculate_p95
from ticket_guard_qa.test_suites.suite_interface import SuiteContext, SuiteGuide


class PipelineLatencySuite:
    suite_name = "pipeline_latency"
    title = "Gold 반영 지연"
    related_codes: tuple[str, ...] = ()
    guide = SuiteGuide(
        "클릭이 분석 결과(집계)에 반영되기까지 걸리는 시간",
        "클릭 시각과 집계 완료 시각의 차이 측정",
        "100건 중 95건이 10초 이내",
    )

    def run(self, context: SuiteContext) -> SuiteResult:
        # Live = 최근 15분 서버 이벤트 · 같은 Run의 트래픽 Suite 뒤에 실행
        pipeline_reader = context.system_target.pipeline_reader
        gold_latency_values_sec = pipeline_reader.read_gold_latency_sec(context.run_id)
        if not gold_latency_values_sec:
            return SuiteResult(self.suite_name, SuiteOutcome.SKIPPED,
                               message="최근 15분 처리 기록 없음 · 예매 흐름 등 트래픽 Suite와 함께 실행")
        quarantined_event_count = pipeline_reader.count_quarantined_events(context.run_id)
        return SuiteResult(
            suite_name=self.suite_name,
            outcome=SuiteOutcome.PASSED,  # 측정 완료 · 합격은 합격 기준이 판단
            metrics={
                "gold_latency_p95_sec": calculate_p95(gold_latency_values_sec),
                "quarantined_event_count": float(quarantined_event_count),
            },
        )
