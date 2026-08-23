from __future__ import annotations

from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_store(root: Path, task_id: str, research_task: ResearchTask) -> ArtifactStore:
    store = ArtifactStore(root)
    plan = ResearchPlan(
        task_id=task_id,
        decision_question="验证真实网页采集链路",
        status=ResearchPlanStatus.NEEDS_COLLECTION,
        research_task_ids=[research_task.id],
    )
    record = TaskRecord(
        task_id=task_id,
        task_key=research_task.id,
        task_type=TaskType.SUPPLEMENT_COLLECTION,
        target_agent_role=AgentRole.COLLECTOR,
        status=TaskStatus.READY,
        reason=research_task.objective,
    )
    store.save_many(task_id, "research_plans", [plan])
    store.save_many(task_id, "research_tasks", [research_task])
    for artifact_type in ["sources", "web_pages", "collection_attempts"]:
        store.save_many(task_id, artifact_type, [])
    TaskBoardStore(store).save_board(TaskBoard(task_id=task_id, status=TaskStatus.READY, tasks=[record]))
    return store


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6e2"
    root.mkdir(parents=True, exist_ok=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        if request.url.path == "/seed":
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        html = """
        <html><head><title>ClassIn 功能页</title><script>不要提取脚本噪音</script></head>
        <body><main><h1>互动课堂</h1><p>支持多人实时音视频互动和互动白板，支持屏幕共享、课堂录制、课件演示与多种课堂协作功能。</p></main></body></html>
        """
        return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"}, request=request)

    tool = WebCollectorTool(
        transport=httpx.MockTransport(handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
    )
    task_id = "task_step6e2_seed_collection"
    research_task = ResearchTask(
        id="researchtask_seed_page",
        task_id=task_id,
        information_need_id="need_feature",
        title="核实 ClassIn 产品能力",
        objective="采集 ClassIn 官方功能资料",
        competitor="ClassIn",
        dimension="产品能力",
        seed_urls=["https://provider.test/seed"],
        preferred_source_types=["official_site"],
        status="waiting_for_collector",
        stop_condition="获得至少一条官方网页资料",
    )
    store = build_store(root, task_id, research_task)
    result = CollectorQueueService(
        store=store,
        web_tool=tool,
        load_search_provider_from_env=False,
    ).run_once(task_id)
    require(result["status"] == "completed", str(result))
    sources = store.load_many(task_id, "sources")
    pages = store.load_many(task_id, "web_pages")
    attempts = store.load_many(task_id, "collection_attempts")
    require(len(sources) == len(pages) == len(attempts) == 1, "网页产物数量错误")
    require("多人实时音视频互动" in pages[0]["text"], "没有提取正文")
    require("脚本噪音" not in pages[0]["text"], "错误提取 script 内容")
    require(attempts[0]["final_url"].endswith("/final"), "没有审计重定向后的 URL")
    board = TaskBoardStore(store).require_board(task_id)
    require(board.status == "ready", "采集后没有把证据抽取任务发布为 ready")
    require(
        any(item.task_type == "extract_source_evidence" for item in board.tasks),
        "没有生成 Extractor 任务",
    )

    no_seed_id = "task_step6e2_no_seed"
    no_seed = research_task.model_copy(
        update={"id": "researchtask_no_seed", "task_id": no_seed_id, "seed_urls": []}
    )
    no_seed_store = build_store(root, no_seed_id, no_seed)
    no_seed_result = CollectorQueueService(
        store=no_seed_store,
        web_tool=tool,
        load_search_provider_from_env=False,
    ).run_once(no_seed_id)
    require(no_seed_result["status"] == "requires_human", "缺 URL 的任务没有停下来")

    for unsafe in ["file:///etc/passwd", "http://127.0.0.1/admin", "http://localhost/"]:
        try:
            URLSafetyPolicy(resolve_dns=False).validate(unsafe)
        except ValueError:
            continue
        raise AssertionError(f"未阻止危险 URL: {unsafe}")

    print("check_step6e2_web_collector: PASS")
    print("collector_claims_ready_task=true")
    print("html_visible_text_extracted=true")
    print("redirect_audited=true")
    print("ssrf_policy_passed=true")
    print("missing_seed_requires_human=true")
    print("extractor_task_published=true")
    print("real_network_used=false")


if __name__ == "__main__":
    main()
