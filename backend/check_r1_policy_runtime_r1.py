from __future__ import annotations

from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.execution.research_agent import (
    ProductionResearchTools,
    ResearchEvidenceAgentService,
)
from app.schemas import (
    ResearchAgentAction,
    ResearchAgentBudget,
    ResearchAgentObservation,
    SourceDocument,
    WebPageContent,
)
from app.tools.browser_renderer import BrowserRenderResult
from app.tools.search_provider import SearchHit
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.trace import TraceRecorder
from check_research_agent_r1 import (
    FakeResearchTools,
    TrajectoryDecider,
    action,
    require,
    setup,
)


AGENT_QUERY = "ClassIn 年度服务费 官方定价"
BAD_URL = "https://runtime.test/fail"
GOOD_URL = "https://runtime.test/good"


class FakeSearchProvider:
    name = "fake-policy-search"

    def __init__(self):
        self.queries: list[tuple[str, int, str]] = []

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        self.queries.append((query, count, domain_filter))
        return [
            SearchHit(
                title="ClassIn 官方定价与年度服务费说明",
                url="https://docs.classin.example/pricing",
                snippet=(
                    "ClassIn 官方文档说明 EDU 教育版采用年度服务费，"
                    "包含定价、计费周期与采购说明。"
                ),
                site_name="ClassIn Docs",
            )
        ]

    def close(self) -> None:
        return None


class FakeBrowserRenderer:
    def __init__(self):
        self.urls: list[str] = []

    def render(self, url: str) -> BrowserRenderResult:
        self.urls.append(url)
        return BrowserRenderResult(
            html=(
                "<html><head><title>Browser rendered pricing</title></head>"
                "<body><main>ClassIn 官方浏览器渲染正文，包含年度服务费、"
                "计费周期、采购说明以及足够长度的可验证页面内容。</main></body></html>"
            ),
            engine="fake-browser",
        )


class SpyCollector(CollectorQueueService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.policy_fetch_calls: list[str] = []

    def fetch_and_persist_url(self, *, url: str, **kwargs):
        self.policy_fetch_calls.append(url)
        return super().fetch_and_persist_url(url=url, **kwargs)


def reset_runtime_artifacts(store, task_id: str) -> None:
    for artifact_type in (
        "collection_attempts",
        "official_domain_contexts",
        "search_attempts",
        "source_selection_runs",
        "source_chunks",
        "source_retrieval_runs",
    ):
        store.save_many(task_id, artifact_type, [])


def build_web_tool(
    *,
    browser_renderer=None,
) -> WebCollectorTool:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        if request.url.path == "/fail":
            return httpx.Response(503, text="unavailable", request=request)
        if request.url.path == "/short":
            return httpx.Response(
                200,
                text="<html><body>short</body></html>",
                headers={"content-type": "text/html"},
                request=request,
            )
        return httpx.Response(
            200,
            text=(
                "<html><head><title>ClassIn pricing</title></head><body><main>"
                "ClassIn EDU 教育版采用年度服务费，页面同时说明计费周期、"
                "采购方式、合同范围和官方报价口径。"
                "</main></body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    return WebCollectorTool(
        transport=httpx.MockTransport(handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        browser_renderer=browser_renderer,
        enable_browser_fallback=browser_renderer is not None,
        respect_robots=True,
    )


def check_action_normalization_and_invalid_policy(root: Path) -> None:
    task_id, research_task, store = setup(root, "action_normalization")
    reset_runtime_artifacts(store, task_id)
    missing_scope = ResearchAgentAction(
        task_id=task_id,
        research_task_id=research_task.id,
        action="SEARCH",
        rationale="Agent selects the query",
        query="ClassIn 官方 定价 收费",
    )
    invalid_scope = ResearchAgentAction(
        task_id=task_id,
        research_task_id=research_task.id,
        action="SEARCH",
        rationale="Agent selects the query; runtime scope is malformed",
        query="ClassIn 官方 定价 收费",
        search_scope="unsupported-engineering-value",
    )
    require(missing_scope.search_scope == "auto", "缺少 search_scope 未默认 auto")
    require(invalid_scope.search_scope == "auto", "非法 search_scope 未 normalize")

    invalid_query = ResearchAgentAction(
        task_id=task_id,
        research_task_id=research_task.id,
        action="SEARCH",
        rationale="Invalid core policy fixture",
        query="",
    )
    tools = FakeResearchTools(store, task_id, research_task)
    payload = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=research_task.id,
        decider=TrajectoryDecider(
            [
                invalid_query,
                invalid_scope,
                action(
                    task_id,
                    research_task.id,
                    "FINISH",
                    finish_status="EXHAUSTED",
                ),
            ]
        ),
        tools=tools,
        budget=ResearchAgentBudget(
            max_steps=4,
            max_searches=2,
            max_sources=2,
            max_failed_actions=2,
        ),
    )
    observations = [
        ResearchAgentObservation(**item)
        for item in store.load_many(task_id, "research_agent_observations")
    ]
    require(payload["status"] == "completed", "非法核心字段导致 ResearchTask crash")
    require(
        observations[0].status == "rejected"
        and observations[0].payload.get("action_identity") == "SEARCH:"
        and observations[0].payload.get("remaining_budget", {}).get("steps") == 3,
        "非法 SEARCH 未形成带预算的结构化 rejected Observation",
    )
    require(
        tools.search_scopes == ["auto"],
        "非法工程字段未由 runtime 默认，或空 query 被执行",
    )


def check_agent_query_only_search_runtime(root: Path) -> None:
    task_id, research_task, store = setup(root, "agent_query_only")
    reset_runtime_artifacts(store, task_id)
    provider = FakeSearchProvider()
    collector = CollectorQueueService(
        store=store,
        web_tool=build_web_tool(),
        search_provider=provider,
        load_search_provider_from_env=False,
    )
    runtime = ProductionResearchTools(
        store=store,
        recorder=TraceRecorder(store=store, task_id=task_id),
        collector=collector,
    )
    try:
        payload = runtime.search(
            task_id=task_id,
            research_task=research_task,
            query=AGENT_QUERY,
            search_scope="auto",
            limit=2,
        )
    finally:
        runtime.close()
    require(
        provider.queries
        and all(item[0] == AGENT_QUERY for item in provider.queries),
        f"Runtime 擅自构造或追加 query：{provider.queries}",
    )
    require(
        [item[2] for item in provider.queries]
        == [""],
        f"probable official domain 不应触发限定域搜索：{provider.queries}",
    )
    require(payload["query"] == AGENT_QUERY, "Observation 未返回 Agent 原始 query")
    require(
        all(item["query"] == AGENT_QUERY for item in store.load_many(task_id, "search_attempts")),
        "SearchAttempt 中出现非 Agent query",
    )


def check_shared_fetch_runtime_and_browser_fallback(root: Path) -> None:
    task_id, research_task, store = setup(root, "shared_fetch_browser")
    reset_runtime_artifacts(store, task_id)
    browser = FakeBrowserRenderer()
    collector = SpyCollector(
        store=store,
        web_tool=build_web_tool(browser_renderer=browser),
        load_search_provider_from_env=False,
    )
    runtime = ProductionResearchTools(
        store=store,
        recorder=TraceRecorder(store=store, task_id=task_id),
        collector=collector,
    )
    short_url = "https://runtime.test/short"
    try:
        payload = runtime.fetch(
            task_id=task_id,
            research_task=research_task,
            url=short_url,
        )
    finally:
        runtime.close()
    source = SourceDocument(**store.load_many(task_id, "sources")[0])
    page = WebPageContent(**store.load_many(task_id, "web_pages")[0])
    require(collector.policy_fetch_calls == [short_url], "R1 FETCH 未走共享 V1 runtime")
    require(browser.urls == [short_url], "短 HTTP 正文未进入已有 Browser fallback")
    require(
        payload["render_mode"] == "browser"
        and page.render_mode == "browser"
        and page.browser_engine == "fake-browser",
        "Browser fallback 结果未持久化",
    )
    require(
        source.metadata.get("collection_method") == "research_agent_policy_runtime_v1"
        and source.metadata.get("content_hash") == page.content_hash,
        "共享 runtime 未保持 provenance/content hash",
    )


def check_failure_observation_redecision_and_guardrail(root: Path) -> None:
    task_id, research_task, store = setup(root, "failure_redecision")
    reset_runtime_artifacts(store, task_id)
    research_task = research_task.model_copy(update={"seed_urls": [BAD_URL, GOOD_URL]})
    store.save_many(task_id, "research_tasks", [research_task])
    collector = SpyCollector(
        store=store,
        web_tool=build_web_tool(),
        load_search_provider_from_env=False,
    )
    runtime = ProductionResearchTools(
        store=store,
        recorder=TraceRecorder(store=store, task_id=task_id),
        collector=collector,
    )
    try:
        payload = ResearchEvidenceAgentService(store=store).run_once(
            task_id,
            research_task_id=research_task.id,
            decider=TrajectoryDecider(
                [
                    action(task_id, research_task.id, "FETCH", url=BAD_URL),
                    action(task_id, research_task.id, "FETCH", url=BAD_URL),
                    action(task_id, research_task.id, "FETCH", url=GOOD_URL),
                    action(
                        task_id,
                        research_task.id,
                        "FINISH",
                        finish_status="EXHAUSTED",
                    ),
                ]
            ),
            tools=runtime,
            budget=ResearchAgentBudget(
                max_steps=5,
                max_searches=2,
                max_sources=3,
                max_failed_actions=3,
            ),
        )
    finally:
        runtime.close()
    observations = [
        ResearchAgentObservation(**item)
        for item in store.load_many(task_id, "research_agent_observations")
    ]
    require(payload["status"] == "completed", "Tool failure 后 ResearchTask crash")
    require(
        [item.status for item in observations] == ["failed", "rejected", "completed"],
        f"失败/重复 guard/redecision 闭环错误：{[item.status for item in observations]}",
    )
    first_failure = observations[0].payload
    require(
        first_failure.get("failed_url") == BAD_URL
        and first_failure.get("action_identity") == f"FETCH:{BAD_URL}"
        and first_failure.get("remaining_budget", {}).get("sources") == 3,
        "失败 Observation 缺 URL/action identity/remaining budget",
    )
    require(
        collector.policy_fetch_calls == [BAD_URL, GOOD_URL],
        "重复失败动作未被 guardrail 阻止，或 Agent 未能改选 URL",
    )
    attempts = store.load_many(task_id, "collection_attempts")
    require(
        [item["status"] for item in attempts] == ["failed", "completed"],
        "deterministic runtime 未持久化成功/失败 CollectionAttempt",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_policy_runtime_r1"
    )
    check_action_normalization_and_invalid_policy(root)
    check_agent_query_only_search_runtime(root)
    check_shared_fetch_runtime_and_browser_fallback(root)
    check_failure_observation_redecision_and_guardrail(root)
    print("check_r1_policy_runtime_r1: PASS")
    print("search_scope_missing_defaults_auto=true")
    print("search_scope_invalid_normalized=true")
    print("invalid_core_policy_observation=true")
    print("runtime_generated_fallback_query=false")
    print("v1_search_source_quality_reused=true")
    print("v1_fetch_persistence_reused=true")
    print("browser_fallback_reused=true")
    print("tool_failure_redecision=true")
    print("repeated_failed_action_guarded=true")
    print("deepseek_calls=0")
    print("tavily_calls=0")
    print("external_network_calls=0")


if __name__ == "__main__":
    main()
