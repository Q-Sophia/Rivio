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
from app.tools.browser_renderer import BrowserRenderResult
from app.tools.search_provider import BochaSearchProvider, SearchHit, ZhipuSearchProvider
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeBrowserRenderer:
    def render(self, url: str) -> BrowserRenderResult:
        return BrowserRenderResult(
            engine="fake_headless",
            html=(
                "<html><head><title>动态页面</title></head><body><main>"
                "腾讯会议支持在线会议、屏幕共享、会议录制、字幕和多端协作，"
                "这段正文由测试浏览器完成 JavaScript 渲染后返回。"
                "</main></body></html>"
            ),
        )


class MockSearchProvider:
    name = "mock_search"

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        return [
            SearchHit(
                title="腾讯会议官方产品页",
                url="https://meeting.tencent.com/product",
                snippet="腾讯会议产品能力和应用场景",
                site_name="腾讯会议",
            ),
            SearchHit(title="不安全结果", url="http://127.0.0.1/private"),
        ][:count]


def build_store(root: Path, task_id: str, research_task: ResearchTask) -> ArtifactStore:
    store = ArtifactStore(root)
    store.save_many(
        task_id,
        "research_plans",
        [
            ResearchPlan(
                task_id=task_id,
                decision_question="补齐腾讯会议资料",
                status=ResearchPlanStatus.NEEDS_COLLECTION,
                research_task_ids=[research_task.id],
            )
        ],
    )
    store.save_many(task_id, "research_tasks", [research_task])
    for artifact_type in [
        "sources",
        "web_pages",
        "collection_attempts",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "tool_calls",
    ]:
        store.save_many(task_id, artifact_type, [])
    record = TaskRecord(
        task_id=task_id,
        task_key=research_task.id,
        task_type=TaskType.SUPPLEMENT_COLLECTION,
        target_agent_role=AgentRole.COLLECTOR,
        status=TaskStatus.READY,
    )
    TaskBoardStore(store).save_board(
        TaskBoard(task_id=task_id, status=TaskStatus.READY, tasks=[record])
    )
    return store


def main() -> None:
    def dynamic_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            text="<html><body><div id='app'>加载中</div></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    browser_tool = WebCollectorTool(
        transport=httpx.MockTransport(dynamic_handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        browser_renderer=FakeBrowserRenderer(),
    )
    rendered = browser_tool.fetch("https://dynamic.test/product")
    require(rendered.render_mode == "browser", "短正文没有触发 Browser Fallback")
    require("屏幕共享" in rendered.text, "浏览器渲染正文没有进入结构化结果")

    def search_api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "webPages": {
                    "value": [
                        {
                            "name": "腾讯会议官网",
                            "url": "https://meeting.tencent.com/",
                            "snippet": "腾讯会议",
                            "siteName": "腾讯会议",
                        }
                    ]
                }
            },
            request=request,
        )

    bocha = BochaSearchProvider(
        "test-key",
        transport=httpx.MockTransport(search_api_handler),
    )
    require(bocha.search("腾讯会议")[0].site_name == "腾讯会议", "博查响应解析失败")
    bocha.close()

    zhipu = ZhipuSearchProvider(
        "test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "search_result": [
                        {
                            "title": "腾讯会议官网",
                            "link": "https://meeting.tencent.com/",
                            "content": "腾讯会议产品资料",
                            "media": "腾讯会议",
                            "publish_date": "2026-01-01",
                        }
                    ]
                },
                request=request,
            )
        ),
    )
    require(zhipu.search("腾讯会议")[0].site_name == "腾讯会议", "智谱响应解析失败")
    zhipu.close()

    def page_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            text=(
                "<html><head><title>腾讯会议官方产品页</title></head><body>"
                "腾讯会议面向企业与个人提供在线会议、屏幕共享、会议录制、"
                "实时字幕和多终端协作能力，适用于远程办公和在线教学。"
                "</body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    task_id = "task_step6e2_search_discovery"
    research_task = ResearchTask(
        id="researchtask_tencent_meeting",
        task_id=task_id,
        information_need_id="need_tencent_meeting",
        title="补齐腾讯会议能力资料",
        objective="寻找并采集腾讯会议官方资料",
        competitor="腾讯会议",
        dimension="产品能力",
        query_hints=["腾讯会议 官方 产品 功能"],
        seed_urls=[],
        preferred_source_types=["official_site"],
        status="waiting_for_collector",
        stop_condition="获得至少一条可追溯的官方来源",
    )
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6e2_browser_search"
    store = build_store(root, task_id, research_task)
    page_tool = WebCollectorTool(
        transport=httpx.MockTransport(page_handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        enable_browser_fallback=False,
    )
    result = CollectorQueueService(
        store=store,
        web_tool=page_tool,
        search_provider=MockSearchProvider(),
    ).run_once(task_id)
    require(result["status"] == "completed", str(result))
    require(result["search_used"] is True, "缺失 URL 时没有调用 SearchProvider")
    search_results = store.load_many(task_id, "web_search_results")
    search_attempts = store.load_many(task_id, "search_attempts")
    require(
        len(search_results) == sum(item["result_count"] for item in search_attempts),
        "多阶段搜索结果没有完整保存为 artifacts",
    )
    require(
        [item["metadata"]["acquisition_stage"] for item in search_attempts[:2]]
        == ["official_discovery", "official_discovery"],
        "事实型任务没有先执行 official discovery",
    )
    require(sum(item["selected_for_collection"] for item in search_results) == 1, "URL 安全筛选错误")
    require(len(store.load_many(task_id, "sources")) == 1, "搜索结果没有进入采集链路")

    print("check_step6e2_browser_search: PASS")
    print("browser_fallback_triggered=true")
    print("rendered_dom_structured=true")
    print("bocha_response_parsed=true")
    print("zhipu_response_parsed=true")
    print("search_results_audited=true")
    print("unsafe_search_url_rejected=true")
    print("search_then_collect_completed=true")
    print("real_network_used=false")


if __name__ == "__main__":
    main()
