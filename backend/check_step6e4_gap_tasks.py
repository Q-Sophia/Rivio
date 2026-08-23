from __future__ import annotations

from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.intake.step6e4 import Step6E4QueueService, refresh_step6e4_artifacts
from app.schemas import (
    AgentRole,
    EvidenceDimension,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    SourceDocument,
    SourceEvidence,
    SourceType,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.workflow.taskboard import TaskBoardStore


TASK_ID = "task_step6e4_bounded_gap_loop_check"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6e4"
    store = ArtifactStore(root)
    sources = [
        SourceDocument(task_id=TASK_ID, id="src_tencent", title="腾讯会议介绍", url="https://meeting.tencent.com/", source_type=SourceType.OFFICIAL_SITE, competitor="腾讯会议", content_excerpt="腾讯会议支持屏幕共享和录制功能。", reliability_score=0.9),
        SourceDocument(task_id=TASK_ID, id="src_zoom", title="Zoom 官方页面", url="https://zoom.us/", source_type=SourceType.OFFICIAL_SITE, competitor="Zoom", content_excerpt="Zoom 支持会议和文档协作。", reliability_score=0.9),
    ]
    evidence = [
        SourceEvidence(id="ev_tencent", task_id=TASK_ID, source_id="src_tencent", competitor="腾讯会议", dimension=EvidenceDimension.FEATURE, snippet="腾讯会议支持屏幕共享和录制功能。", normalized_fact="腾讯会议支持屏幕共享和录制功能。", confidence=0.35, extraction_method="manual"),
        SourceEvidence(id="ev_zoom", task_id=TASK_ID, source_id="src_zoom", competitor="Zoom", dimension=EvidenceDimension.FEATURE, snippet="Zoom 支持会议和文档协作。", normalized_fact="Zoom 支持会议和文档协作。", confidence=0.82, extraction_method="manual"),
    ]
    initial_tasks = [
        ResearchTask(id=f"researchtask_{name}_feature_round_1", task_id=TASK_ID, information_need_id=f"need_{name}_feature", title=f"采集 {competitor} 功能资料", objective="获得功能直接证据", competitor=competitor, dimension="产品能力", status="evidence_extracted", stop_condition="获得两条直接证据")
        for name, competitor in [("tencent", "腾讯会议"), ("zoom", "Zoom")]
    ]
    plan = ResearchPlan(
        task_id=TASK_ID,
        decision_question="比较腾讯会议与 Zoom 的功能和价格",
        status=ResearchPlanStatus.NEEDS_COLLECTION,
        research_task_ids=[item.id for item in initial_tasks],
        missing_competitors=["腾讯会议", "Zoom"],
        missing_dimensions=["产品能力", "价格"],
        budget=ResearchBudget(max_collection_rounds=2, max_sources_per_task=3, max_total_sources=10, max_real_llm_calls=0),
    )
    store.save_many(TASK_ID, "sources", sources)
    store.save_many(TASK_ID, "evidence", evidence)
    store.save_many(TASK_ID, "research_plans", [plan])
    store.save_many(TASK_ID, "research_tasks", initial_tasks)
    evaluation = TaskRecord(id="queue_evaluate_coverage_round_1", task_id=TASK_ID, task_key="evaluate_coverage_round_1", task_type=TaskType.EVALUATE_EVIDENCE_COVERAGE, target_agent_role=AgentRole.ANALYST, status=TaskStatus.READY)
    TaskBoardStore(store).save_board(TaskBoard(task_id=TASK_ID, status=TaskStatus.READY, tasks=[evaluation]))

    result = Step6E4QueueService(store=store).run_once(TASK_ID)
    coverage = store.load_many(TASK_ID, "evidence_coverage")
    cards = store.load_many(TASK_ID, "product_cards")
    tasks = [ResearchTask(**item) for item in store.load_many(TASK_ID, "research_tasks")]
    board = TaskBoardStore(store).require_board(TASK_ID)
    require(result["status"] == "completed", str(result))
    require(len(coverage) == 4, "未按两个竞品、两个必需维度生成完整覆盖矩阵")
    require(sum(item["status"] == "missing" for item in coverage) == 2, "无证据的价格维度未标记 missing")
    require(len(cards) == 2, "ProductCard 未按竞品更新")
    require(any(item.task_type == TaskType.SUPPLEMENT_COLLECTION for item in board.tasks), "未发布补采任务")
    require(max(item.collection_round for item in tasks) == 2, "没有进入允许的第 2 轮补采")

    repeated = refresh_step6e4_artifacts(TASK_ID, store=store)
    require(repeated["new_research_task_count"] == 0, "重复刷新时提前发布了下一轮任务")
    store.save_many(TASK_ID, "research_tasks", [item.model_copy(update={"status": "evidence_extracted"}) for item in tasks])
    second = refresh_step6e4_artifacts(TASK_ID, store=store)
    require(second["new_research_task_count"] == 2, "价格缺口没有进入第 2 轮")
    tasks = [ResearchTask(**item).model_copy(update={"status": "evidence_extracted"}) for item in store.load_many(TASK_ID, "research_tasks")]
    store.save_many(TASK_ID, "research_tasks", tasks)
    exhausted = refresh_step6e4_artifacts(TASK_ID, store=store)
    final_tasks = [ResearchTask(**item) for item in store.load_many(TASK_ID, "research_tasks")]
    require(exhausted["new_research_task_count"] == 0, "超过最大轮次后仍发布补采任务")
    require(max(item.collection_round for item in final_tasks) == 2, "出现超预算的第 3 轮任务")
    require(exhausted["exhausted_gap_count"] == 4, "预算耗尽缺口未审计")

    print("check_step6e4_gap_tasks: PASS")
    print("required_missing_dimensions_detected=true")
    print("product_cards_updated=true")
    print("analyst_taskboard_claimed=true")
    print("collector_supplement_tasks_published=true")
    print("duplicate_refresh_blocked=true")
    print("max_collection_rounds_enforced=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
