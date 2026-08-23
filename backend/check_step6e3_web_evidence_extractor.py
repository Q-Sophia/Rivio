from __future__ import annotations

from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.extraction import ExtractorQueueService
from app.schemas import ResearchTask
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore
from check_step6e2_web_collector import build_store


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            text="""
            <html><head><title>腾讯会议产品能力</title></head><body><main>
            <h1>腾讯会议</h1>
            <p>腾讯会议面向企业、学校和个人用户提供稳定的在线视频会议服务。</p>
            <p>产品支持屏幕共享、云端录制、实时字幕、分组讨论和多终端协作功能。</p>
            <p>管理员可以通过开放 API 和应用集成能力连接企业内部工作流程。</p>
            <p>未来，腾讯会议持续更新，为您带来更多丰富新功能。</p>
            <p>点击订阅获取最新资讯，即表示同意腾讯会议通过邮件推送产品信息。</p>
            <script>这里是不能成为证据的脚本内容。</script>
            </main></body></html>
            """,
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    task_id = "task_step6e3_web_evidence"
    research_task = ResearchTask(
        id="researchtask_tencent_meeting_feature",
        task_id=task_id,
        information_need_id="need_feature",
        title="核实腾讯会议产品能力",
        objective="采集并抽取腾讯会议官方功能事实",
        competitor="腾讯会议",
        dimension="产品能力",
        seed_urls=["https://meeting.tencent.test/product"],
        preferred_source_types=["official_site"],
        status="waiting_for_collector",
        stop_condition="获得至少一条可追溯官方证据",
    )
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6e3"
    store = build_store(root, task_id, research_task)
    for artifact_type in [
        "evidence",
        "evidence_extraction_attempts",
        "agent_runs",
        "dag_nodes",
        "tool_calls",
        "llm_calls",
    ]:
        store.save_many(task_id, artifact_type, [])
    web_tool = WebCollectorTool(
        transport=httpx.MockTransport(handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        enable_browser_fallback=False,
    )
    collected = CollectorQueueService(
        store=store,
        web_tool=web_tool,
        load_search_provider_from_env=False,
    ).run_once(task_id)
    require(collected["extraction_ready"] is True, str(collected))

    extracted = ExtractorQueueService(store=store).run_once(task_id)
    require(extracted["status"] == "completed", str(extracted))
    evidence = store.load_many(task_id, "evidence")
    pages = {item["id"]: item for item in store.load_many(task_id, "web_pages")}
    sources = {item["id"]: item for item in store.load_many(task_id, "sources")}
    require(1 <= len(evidence) <= 8, "证据数量没有遵守每页预算")
    require(
        not any("订阅获取" in item["snippet"] or "持续更新" in item["snippet"] for item in evidence),
        "营销或订阅文案被错误抽取为证据",
    )
    for item in evidence:
        require(item["source_id"] in sources, "证据没有引用有效 SourceDocument")
        page = pages[item["metadata"]["web_page_id"]]
        quoted = page["text"][item["source_text_start"]:item["source_text_end"]]
        require(quoted == item["snippet"], "证据原文位置不能逐字回放")
        require(item["metadata"]["quote_verified"] is True, "证据没有通过原文校验")
        require(item["metadata"]["source_url"].startswith("https://"), "证据缺少 URL")
    attempts = store.load_many(task_id, "evidence_extraction_attempts")
    require(attempts and attempts[0]["status"] == "completed", "缺少抽取审计")
    board = TaskBoardStore(store).require_board(task_id)
    require(board.status == "ready", "抽取后证据覆盖评估任务没有进入 ready")
    require(
        any(item.task_type == "evaluate_evidence_coverage" for item in board.tasks),
        "没有向 Analyst 发布 Step6E.4 证据覆盖评估任务",
    )
    research = store.load_many(task_id, "research_tasks")
    require(research[0]["status"] == "evidence_extracted", "研究任务状态未回写")
    require(not store.load_many(task_id, "llm_calls"), "确定性抽取不应调用大模型")

    print("check_step6e3_web_evidence_extractor: PASS")
    print(f"evidence_count={len(evidence)}")
    print("source_links_valid=true")
    print("quote_offsets_replayable=true")
    print("source_urls_preserved=true")
    print("extraction_audit_saved=true")
    print("marketing_copy_filtered=true")
    print("coverage_evaluation_task_published=true")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
