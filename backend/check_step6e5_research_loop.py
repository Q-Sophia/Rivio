from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app.api.main as api_main
from app.collection import CollectorQueueService
from app.execution import ResearchLoopRunner
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisTask,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    SourceDocument,
    SourceType,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


TASK_ID = "task_step6e5_research_loop_complete"
BUDGET_TASK_ID = "task_step6e5_research_loop_budget"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reset_artifacts(store: ArtifactStore, task_id: str) -> None:
    for artifact_type in [
        "analysis_tasks",
        "research_plans",
        "research_tasks",
        "task_board",
        "task_records",
        "sources",
        "web_pages",
        "collection_attempts",
        "evidence",
        "evidence_extraction_attempts",
        "product_cards",
        "evidence_coverage",
        "research_gaps",
        "agent_runs",
        "dag_nodes",
        "tool_calls",
        "llm_calls",
        "research_loop_runs",
        "research_loop_events",
    ]:
        store.save_many(task_id, artifact_type, [])


def seed_research_task(
    store: ArtifactStore,
    *,
    task_id: str,
    seed_urls: list[str],
    max_total_sources: int,
) -> ResearchTask:
    task = AnalysisTask(
        id=task_id,
        query="核实测试会议产品的协作能力。",
        industry="企业协作",
        competitors=["测试会议"],
        focus_areas=["产品能力"],
        metadata={"execution_started": False},
    )
    research_task = ResearchTask(
        id=f"researchtask_{task_id}_feature_round_1",
        task_id=task_id,
        information_need_id="need_test_feature",
        title="采集测试会议产品能力",
        objective="获得两条可逐字追溯的官方产品能力证据",
        competitor="测试会议",
        dimension="产品能力",
        seed_urls=seed_urls,
        preferred_source_types=["official_site"],
        status="waiting_for_collector",
        stop_condition="获得两条直接证据",
    )
    plan = ResearchPlan(
        id=f"researchplan_{task_id}",
        task_id=task_id,
        decision_question="测试会议是否具备稳定的企业协作能力？",
        status=ResearchPlanStatus.NEEDS_COLLECTION,
        research_task_ids=[research_task.id],
        missing_competitors=["测试会议"],
        missing_dimensions=["产品能力"],
        budget=ResearchBudget(
            max_collection_rounds=2,
            max_sources_per_task=2,
            max_total_sources=max_total_sources,
            max_real_llm_calls=0,
        ),
    )
    record = TaskRecord(
        id=f"queue_{research_task.id}",
        task_id=task_id,
        task_key=research_task.id,
        task_type=TaskType.SUPPLEMENT_COLLECTION,
        target_agent_role=AgentRole.COLLECTOR,
        status=TaskStatus.READY,
        reason=research_task.objective,
    )
    store.save_many(task_id, "analysis_tasks", [task])
    store.save_many(task_id, "research_plans", [plan])
    store.save_many(task_id, "research_tasks", [research_task])
    TaskBoardStore(store).save_board(
        TaskBoard(task_id=task_id, status=TaskStatus.READY, tasks=[record])
    )
    return research_task


def build_mock_collector(store: ArtifactStore) -> CollectorQueueService:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nAllow: /",
                request=request,
            )
        suffix = "甲" if request.url.path.endswith("one") else "乙"
        return httpx.Response(
            200,
            text=f"""
            <html><head><title>测试会议能力说明{suffix}</title></head><body><main>
            <p>测试会议支持屏幕共享、会议录制和实时字幕，帮助企业完成远程协作。</p>
            <p>测试会议提供多人音视频会议和跨终端协作功能，本页为官方能力说明{suffix}。</p>
            </main></body></html>
            """,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    return CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            transport=httpx.MockTransport(handler),
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        load_search_provider_from_env=False,
    )


def check_completed_loop(root: Path) -> None:
    store = ArtifactStore(root)
    reset_artifacts(store, TASK_ID)
    seed_research_task(
        store,
        task_id=TASK_ID,
        seed_urls=[
            "https://step6e5.test/one",
            "https://step6e5.test/two",
        ],
        max_total_sources=4,
    )
    runner = ResearchLoopRunner(
        store=store,
        collector_factory=build_mock_collector,
    )

    original_runner = api_main.get_research_loop_runner
    api_main.get_research_loop_runner = lambda: runner
    try:
        client = TestClient(api_main.app)
        response = client.post(f"/api/analysis-tasks/{TASK_ID}/research-loop")
        require(response.status_code == 202, response.text)
        require(
            response.json()["research_loop_run"]["status"] in {"queued", "running"},
            "启动接口没有立即返回后台状态",
        )
        completed = runner.wait(TASK_ID, timeout=30)
        require(completed.status == "completed", completed.error or completed.message)
        require(
            completed.stop_reason == "coverage_sufficient",
            f"停止原因错误：{completed.stop_reason}",
        )
        require(completed.collector_runs == 1, "Collector 执行次数错误")
        require(completed.extractor_runs == 1, "Extractor 执行次数错误")
        require(completed.coverage_runs == 1, "Coverage 执行次数错误")
        require(completed.actions_completed == 3, "研究循环动作数错误")

        status_response = client.get(
            f"/api/analysis-tasks/{TASK_ID}/research-loop"
        )
        require(status_response.status_code == 200, status_response.text)
        payload = status_response.json()
        require(payload["terminal"] is True, "完成循环未标记 terminal")
        events = payload["events"]
        require(
            [item["sequence"] for item in events]
            == list(range(1, len(events) + 1)),
            "研究循环事件序号不连续",
        )
        stages = [
            item["stage"]
            for item in events
            if item["event_type"] == "action_started"
        ]
        require(
            stages == ["collector", "extractor", "coverage"],
            f"任务调度顺序错误：{stages}",
        )
        with client.stream(
            "GET",
            f"/api/analysis-tasks/{TASK_ID}/research-loop/events/stream?after=0",
        ) as stream:
            sse_text = "".join(stream.iter_text())
        require("event: research-loop" in sse_text, "SSE 没有研究循环事件")
        require('"event_type": "completed"' in sse_text, "SSE 没有终态事件")

        sources = store.load_many(TASK_ID, "sources")
        evidence = store.load_many(TASK_ID, "evidence")
        records = store.load_many(TASK_ID, "task_records")
        require(len(sources) == 2, "来源数量错误或来源预算未生效")
        require(
            len({item["url"] for item in sources}) == len(sources),
            "出现重复 URL",
        )
        evidence_keys = {
            (item["source_id"], item["snippet"])
            for item in evidence
        }
        require(len(evidence_keys) == len(evidence), "出现重复证据")
        require(
            len({item["task_key"] for item in records}) == len(records),
            "出现重复 TaskRecord",
        )
        require(
            all(item["source_id"] in {source["id"] for source in sources} for item in evidence),
            "SourceEvidence 不能追溯到 SourceDocument",
        )
        require(not store.load_many(TASK_ID, "llm_calls"), "验收意外调用了 LLM")

        reloaded = ResearchLoopRunner(store=store)
        require(
            reloaded.get_latest_run(TASK_ID).status == "completed",
            "刷新后无法读取持久化循环状态",
        )
        before_counts = (len(sources), len(evidence), len(records))
        repeat = client.post(f"/api/analysis-tasks/{TASK_ID}/research-loop")
        require(repeat.status_code == 422, "终态研究循环被重复启动")
        after_counts = (
            len(store.load_many(TASK_ID, "sources")),
            len(store.load_many(TASK_ID, "evidence")),
            len(store.load_many(TASK_ID, "task_records")),
        )
        require(after_counts == before_counts, "重复启动改变了证据链产物")
    finally:
        api_main.get_research_loop_runner = original_runner


def check_budget_stop(root: Path) -> None:
    store = ArtifactStore(root)
    reset_artifacts(store, BUDGET_TASK_ID)
    seed_research_task(
        store,
        task_id=BUDGET_TASK_ID,
        seed_urls=["https://step6e5.test/blocked"],
        max_total_sources=1,
    )
    store.save_many(
        BUDGET_TASK_ID,
        "sources",
        [
            SourceDocument(
                id="src_existing_budget",
                task_id=BUDGET_TASK_ID,
                title="已有来源",
                url="https://existing.test/source",
                source_type=SourceType.OFFICIAL_SITE,
                competitor="测试会议",
                content_excerpt="已有资料仍不足以关闭全部研究缺口。",
            )
        ],
    )

    def forbidden_collector(_store: ArtifactStore):
        raise AssertionError("来源预算耗尽后不应创建或调用 Collector")

    runner = ResearchLoopRunner(
        store=store,
        collector_factory=forbidden_collector,
    )
    runner.submit(BUDGET_TASK_ID)
    stopped = runner.wait(BUDGET_TASK_ID, timeout=10)
    require(stopped.status == "requires_human", "预算耗尽后未转人工")
    require(stopped.stop_reason == "budget_exhausted", "预算停止原因错误")
    require(stopped.actions_completed == 0, "预算耗尽后仍执行了任务")
    require(stopped.source_count == 1, "来源预算计数错误")


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6e5"
    root.mkdir(parents=True, exist_ok=True)
    check_completed_loop(root)
    check_budget_stop(root)
    print("check_step6e5_research_loop: PASS")
    print("automatic_collector_extractor_coverage_loop=true")
    print("coverage_sufficient_stop=true")
    print("budget_exhausted_requires_human=true")
    print("persistent_state_reload=true")
    print("sse_event_stream=true")
    print("duplicate_start_blocked=true")
    print("duplicate_url_evidence_taskrecord=false")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
