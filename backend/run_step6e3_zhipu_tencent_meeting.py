from __future__ import annotations

import json

from app.collection import CollectorQueueService
from app.extraction import ExtractorQueueService
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisTask,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    TaskBoard,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.workflow.taskboard import TaskBoardStore


TASK_ID = "task_step6e3_zhipu_tencent_meeting_pilot"


def main() -> None:
    store = ArtifactStore()
    research_task = ResearchTask(
        id="researchtask_tencent_meeting_official_features",
        task_id=TASK_ID,
        information_need_id="need_tencent_meeting_official_features",
        title="核实腾讯会议官方产品能力",
        objective="搜索、采集并抽取腾讯会议官方产品功能资料",
        competitor="腾讯会议",
        dimension="产品能力",
        query_hints=["腾讯会议 官方 产品功能 在线会议 屏幕共享 录制 字幕"],
        seed_urls=[],
        preferred_domains=["meeting.tencent.com"],
        preferred_source_types=["official_site"],
        priority=TaskPriority.HIGH,
        status="waiting_for_collector",
        stop_condition="获得至少一条能逐字追溯到腾讯会议官方网页的产品能力证据",
    )
    analysis_task = AnalysisTask(
        id=TASK_ID,
        query="核实腾讯会议的产品功能并形成可引用证据",
        competitors=["腾讯会议"],
        industry="在线会议与协作",
        focus_areas=["产品能力"],
        report_subject="腾讯会议产品能力核实",
    )
    plan = ResearchPlan(
        task_id=TASK_ID,
        decision_question=analysis_task.query,
        status=ResearchPlanStatus.NEEDS_COLLECTION,
        research_task_ids=[research_task.id],
        missing_competitors=["腾讯会议"],
        missing_dimensions=["产品能力"],
        budget=ResearchBudget(max_sources_per_task=3, max_total_sources=3),
        planner_provider="manual_real_search_pilot_v1",
    )
    record = TaskRecord(
        id="queue_researchtask_tencent_meeting_official_features",
        task_id=TASK_ID,
        task_key=research_task.id,
        task_type=TaskType.SUPPLEMENT_COLLECTION,
        target_agent_role=AgentRole.COLLECTOR,
        status=TaskStatus.READY,
        priority=TaskPriority.HIGH,
        reason=research_task.objective,
    )
    for artifact_type in [
        "sources",
        "evidence",
        "web_pages",
        "collection_attempts",
        "search_attempts",
        "web_search_results",
        "evidence_extraction_attempts",
        "agent_runs",
        "dag_nodes",
        "tool_calls",
        "llm_calls",
    ]:
        store.save_many(TASK_ID, artifact_type, [])
    store.save_many(TASK_ID, "analysis_tasks", [analysis_task])
    store.save_many(TASK_ID, "research_plans", [plan])
    store.save_many(TASK_ID, "research_tasks", [research_task])
    TaskBoardStore(store).save_board(
        TaskBoard(task_id=TASK_ID, status=TaskStatus.READY, tasks=[record])
    )

    collector = CollectorQueueService(store=store)
    try:
        collection = collector.run_once(TASK_ID)
    finally:
        collector.close()
    extraction = None
    if collection.get("status") == "completed":
        extraction = ExtractorQueueService(store=store).run_once(TASK_ID)

    search_results = store.load_many(TASK_ID, "web_search_results")
    selected = [item for item in search_results if item.get("selected_for_collection")]
    sources = store.load_many(TASK_ID, "sources")
    evidence = store.load_many(TASK_ID, "evidence")
    summary = {
        "task_id": TASK_ID,
        "search_provider": (
            store.load_many(TASK_ID, "search_attempts")[0].get("provider")
            if store.load_many(TASK_ID, "search_attempts")
            else ""
        ),
        "search_results_count": len(search_results),
        "selected_search_results_count": len(selected),
        "selected_urls": [item["url"] for item in selected],
        "collection_status": collection.get("status"),
        "collected_sources_count": len(sources),
        "browser_fallback_count": collection.get("browser_fallback_count", 0),
        "extraction_status": (extraction or {}).get("status", "not_run"),
        "evidence_count": len(evidence),
        "quote_verified_count": sum(
            bool(item.get("metadata", {}).get("quote_verified")) for item in evidence
        ),
        "real_llm_calls": len(store.load_many(TASK_ID, "llm_calls")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
