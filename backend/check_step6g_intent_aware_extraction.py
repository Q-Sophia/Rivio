from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.agents.web_evidence import extract_evidence_from_page
from app.execution.research_analysis import ResearchAnalysisService
from app.harness.artifacts import ArtifactStore
from app.intake.research_planning import ResearchPlanningService
from app.intake.service import IntentDraftService
from app.schemas import (
    AnalysisTask,
    AnalysisTaskDraft,
    AgentRole,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    SourceDocument,
    TaskBoard,
    TaskMode,
    TaskRecord,
    TaskStatus,
    TaskType,
    WebPageContent,
)
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    draft = AnalysisTaskDraft(
        id="draft_step6g_single_target",
        request_text="请调研 ClassIn 的竞品分析",
        decision_question="识别 ClassIn 的主要竞品并形成阶段性竞品分析。",
        competitors=["ClassIn"],
        research_mode="TARGET_CENTRIC_COMPETITIVE_ANALYSIS",
        primary_target="ClassIn",
        target_profiling=True,
        market_scoping=True,
        competitor_discovery=True,
        cross_competitor_comparison=True,
    )
    normalized = IntentDraftService._normalize_draft(
        draft,
        request_text=draft.request_text,
        provider="mock",
        model="mock",
        prompt_version="test",
    )
    require(normalized.ready_for_confirmation, "单对象任务仍被阻断")
    require(not normalized.industry, "测试不应伪造行业")
    require(
        normalized.metadata.get("competitor_discovery_required") is True,
        "单对象任务没有标记后续竞品发现",
    )

    planning_root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6g_single_target"
    planning_store = ArtifactStore(planning_root)
    analysis_task = AnalysisTask(
        id="task_step6g_single_classin_live_v2",
        query="识别 ClassIn 的主要竞品并比较产品能力。",
        competitors=["ClassIn"],
        focus_areas=["产品能力"],
        report_subject="ClassIn 竞品分析",
        mode=TaskMode.LIVE,
        metadata={
            "research_brief": {
                "research_mode": "TARGET_CENTRIC_COMPETITIVE_ANALYSIS",
                "primary_target": "ClassIn",
                "comparison_targets": [],
                "reference_products": [],
                "target_profiling": True,
                "market_scoping": True,
                "competitor_discovery": True,
                "cross_competitor_comparison": True,
                "decision_oriented_analysis": False,
                "research_gap_tracking": True,
            },
            "competitor_discovery_required": True,
            "competitor_discovery_strategy": "local_catalog_v1",
        },
    )
    planning_store.save_many(analysis_task.id, "analysis_tasks", [analysis_task])
    planning_payload = ResearchPlanningService(store=planning_store).build(analysis_task.id)
    discovered = planning_payload["research_plan"]["metadata"].get("discovered_competitors") or []
    require("BigBlueButton" in discovered, "没有从本地来源目录发现候选竞品")
    require(
        all(item["status"] == "waiting_for_collector" for item in planning_payload["research_tasks"]),
        "Live 单对象任务没有把已知 URL 交给 Collector 重新验证",
    )

    page_text = "\n".join(
        [
            "ClassIn 支持实时音视频互动、互动白板、课堂录制和多种课件协作。",
            "ClassIn 提供实时字幕与屏幕共享，方便教师开展在线课堂互动。",
            "平台提供 API 和 SDK，可与 LMS 教学管理系统进行集成。",
            "自托管部署需要学校承担服务器维护、版本升级和日常运维责任。",
        ]
    )
    source = SourceDocument(
        task_id="task_step6g_check",
        id="src_step6g_check",
        title="ClassIn 能力说明",
        url="https://example.test/classin",
        competitor="ClassIn",
    )
    page = WebPageContent(
        task_id="task_step6g_check",
        source_id=source.id,
        requested_url=source.url,
        final_url=source.url,
        title=source.title,
        text=page_text,
        content_hash=hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
    )

    def extract(key: str, objective: str, hints: list[str]):
        task = ResearchTask(
            id=f"research_{key}",
            task_id="task_step6g_check",
            information_need_id=f"need_{key}",
            title=objective,
            objective=objective,
            competitor="ClassIn",
            dimension="产品能力",
            query_hints=hints,
            seed_urls=[source.url],
            status="collected",
            stop_condition="取得与意图相关的逐字证据",
        )
        return extract_evidence_from_page(
            task_id="task_step6g_check",
            research_task=task,
            source=source,
            page=page,
            max_items=4,
        )

    teaching = extract(
        "teaching",
        "核实互动白板、录制、字幕和课堂协作能力。",
        ["互动", "白板", "录制", "字幕", "课堂"],
    )
    integration = extract(
        "integration",
        "核实 API、SDK、LMS 集成、自托管部署和运维责任。",
        ["API", "SDK", "LMS", "集成", "自托管", "部署", "运维"],
    )
    teaching_text = " ".join(item.snippet for item in teaching)
    integration_text = " ".join(item.snippet for item in integration)
    require("互动白板" in teaching_text and "字幕" in teaching_text, "教学意图证据不完整")
    require("API" in integration_text and "自托管" in integration_text, "集成意图证据不完整")
    require(
        {item.snippet for item in teaching}.isdisjoint({item.snippet for item in integration}),
        "不同意图仍返回相同证据",
    )
    require(
        all(page.text[item.source_text_start:item.source_text_end] == item.snippet for item in teaching + integration),
        "存在无法逐字回放的证据",
    )

    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        fetch_count += 1
        return httpx.Response(
            200,
            text="<html><head><title>ClassIn</title></head><body><p>ClassIn 支持互动白板、课堂录制和多种课件协作，并提供 API、SDK 与教学管理系统集成能力。</p></body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    reuse_root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6g_collector_reuse"
    reuse_store = ArtifactStore(reuse_root)
    reuse_task_id = "task_step6g_collector_reuse"
    shared_url = "https://provider.test/classin"
    reuse_tasks = [
        ResearchTask(
            id=f"research_reuse_{index}",
            task_id=reuse_task_id,
            information_need_id=f"need_reuse_{index}",
            title=f"复用网页抽取维度 {dimension}",
            objective=f"核实 ClassIn 的{dimension}",
            competitor="ClassIn",
            dimension=dimension,
            seed_urls=[shared_url],
            status="waiting_for_collector",
            stop_condition="固定 URL 完成一次采集后可被不同抽取任务复用",
        )
        for index, dimension in enumerate(("产品能力", "生态与集成"), start=1)
    ]
    reuse_store.save_many(
        reuse_task_id,
        "research_plans",
        [ResearchPlan(task_id=reuse_task_id, decision_question="验证 URL 复用", status=ResearchPlanStatus.NEEDS_COLLECTION)],
    )
    reuse_store.save_many(reuse_task_id, "research_tasks", reuse_tasks)
    for artifact_type in ("sources", "web_pages", "collection_attempts", "web_search_results"):
        reuse_store.save_many(reuse_task_id, artifact_type, [])
    TaskBoardStore(reuse_store).save_board(
        TaskBoard(
            task_id=reuse_task_id,
            status=TaskStatus.READY,
            tasks=[
                TaskRecord(
                    task_id=reuse_task_id,
                    task_key=item.id,
                    task_type=TaskType.SUPPLEMENT_COLLECTION,
                    target_agent_role=AgentRole.COLLECTOR,
                    status=TaskStatus.READY,
                )
                for item in reuse_tasks
            ],
        )
    )
    collector = CollectorQueueService(
        store=reuse_store,
        web_tool=WebCollectorTool(
            transport=httpx.MockTransport(handler),
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        load_search_provider_from_env=False,
    )
    first_collection = collector.run_once(reuse_task_id)
    second_collection = collector.run_once(reuse_task_id)
    collector.close()
    require(first_collection["collected_sources"] == 1, "首次固定 URL 没有采集")
    require(second_collection["reused_sources"] == 1, "第二个研究维度没有复用已采集网页")
    require(fetch_count == 1, "同一 URL 被重复联网采集")

    check_root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6g_analysis_gate"
    store = ArtifactStore(check_root)
    plan = ResearchPlan(
        task_id="task_step6g_partial",
        decision_question="允许基于阶段性证据分析",
        status=ResearchPlanStatus.NEEDS_COLLECTION,
    )
    store.save_many(plan.task_id, "research_plans", [plan])
    loaded = ResearchAnalysisService(store=store)._require_analyzable_plan(plan.task_id)
    require(loaded.status == ResearchPlanStatus.NEEDS_COLLECTION, "资料不完整仍被 Analyst 阻断")

    print("check_step6g_intent_aware_extraction: PASS")
    print("single_target_confirmation=true")
    print("competitor_discovery_marked=true")
    print("local_snapshot_competitors_discovered=true")
    print("known_urls_queued_for_revalidation=true")
    print("intent_specific_evidence=true")
    print("quote_replay_verified=true")
    print("collected_page_reused_across_intents=true")
    print("partial_evidence_analysis_allowed=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
