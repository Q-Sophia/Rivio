from __future__ import annotations

from pathlib import Path

import httpx

from app.collection.service import CollectorQueueService
from app.execution.research_agent import (
    LLMResearchActionDecider,
    ProductionResearchTools,
    ResearchEvidenceAgentService,
    _query_preserves_object_anchor,
)
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    OfficialDomainContext,
    ResearchAgentAction,
    ResearchAgentRun,
    ResearchTask,
    SourceDocument,
    WebPageContent,
    WebSearchResult,
)
from app.tools.search_provider import SearchHit
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.trace import TraceRecorder


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_task(
    task_id: str,
    research_task_id: str,
    *,
    competitor: str = "Acme",
    seed_urls: list[str] | None = None,
) -> ResearchTask:
    return ResearchTask(
        id=research_task_id,
        task_id=task_id,
        information_need_id=f"need_{research_task_id}",
        title=f"{competitor} 产品能力",
        objective=f"核实 {competitor} 当前产品能力与计费信息",
        competitor=competitor,
        dimension="feature",
        query_hints=[f"{competitor} 产品能力 官方"],
        preferred_source_types=["docs"],
        seed_urls=seed_urls or [],
        stop_condition="bounded",
    )


def build_web_tool() -> WebCollectorTool:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Acme feature docs</title></head><body>"
                "Acme feature documentation describes current capabilities, "
                "billing controls, integrations, and help center guidance."
                "</body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    return WebCollectorTool(
        transport=httpx.MockTransport(handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        enable_browser_fallback=False,
    )


class FixtureSearchProvider:
    name = "fixture_search"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        self.calls.append(
            {"query": query, "count": count, "domain_filter": domain_filter}
        )
        return [
            SearchHit(
                title=f"Acme Official Feature Documentation {index}",
                url=f"https://docs.acme.example/features/{index}",
                snippet=(
                    "Acme official product documentation describes current feature "
                    "capabilities, billing controls, integrations, and help center guidance."
                ),
                site_name="Acme Docs",
                published_at=f"2026-0{index + 1}-01" if index < 7 else "",
            )
            for index in range(7)
        ][:count]

    def close(self) -> None:
        return None


class CapturingLLMClient:
    def __init__(self) -> None:
        self.artifacts: dict = {}

    def generate_structured(self, **kwargs):
        self.artifacts = kwargs["artifacts"]
        task = self.artifacts["research_task"][0]
        return (
            {
                "item": {
                    "task_id": task["task_id"],
                    "research_task_id": task["id"],
                    "action": "SEARCH",
                    "rationale": "fixture",
                    "query": f"{task['competitor']} 帮助中心 计费",
                }
            },
            None,
            None,
        )


class SequenceDecider:
    def __init__(self, actions: list[ResearchAgentAction]) -> None:
        self.actions = list(actions)

    def decide(self, **_kwargs) -> ResearchAgentAction:
        return self.actions.pop(0)


def check_official_confidence_and_compact_observation(root: Path) -> None:
    task_id = "task_search_foundation_official"
    store = ArtifactStore(root / "official")
    for artifact_type in (
        "official_domain_contexts",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "sources",
        "web_pages",
        "collection_attempts",
        "tool_calls",
    ):
        store.save_many(task_id, artifact_type, [])
    task = make_task(task_id, "research_acme").model_copy(
        update={"preferred_source_types": ["official_site", "docs"]}
    )
    store.save_many(task_id, "research_tasks", [task])
    provider = FixtureSearchProvider()
    tools = ProductionResearchTools(
        store=store,
        recorder=TraceRecorder(store=store, task_id=task_id),
        collector=CollectorQueueService(
            store=store,
            web_tool=build_web_tool(),
            search_provider=provider,
            load_search_provider_from_env=False,
        ),
    )
    first = tools.search(
        task_id=task_id,
        research_task=task,
        query="Acme 帮助中心 计费",
        search_scope="auto",
        limit=7,
    )
    contexts = [
        OfficialDomainContext(**item)
        for item in store.load_many(task_id, "official_domain_contexts")
    ]
    require(contexts and contexts[0].confidence == "probable", "probable 候选未记录")
    require(first["official_domains"] == [], "probable 被暴露为 confirmed official")
    require(
        provider.calls == [
            {"query": "Acme 帮助中心 计费", "count": 7, "domain_filter": ""}
        ],
        "probable 触发了 domain-restricted Search",
    )
    persisted = store.load_many(task_id, "web_search_results")
    require(
        all(not item["metadata"].get("first_party") for item in persisted),
        "probable SearchResult 被标 first_party",
    )
    require(len(first["results"]) == 5, "SEARCH Observation 未限制 top 5")
    expected_fields = {
        "title",
        "url",
        "snippet",
        "source_level",
        "official_confidence",
        "freshness",
    }
    require(
        all(set(item) == expected_fields for item in first["results"]),
        "SEARCH Observation 字段未压缩",
    )
    require(
        all(len(item["snippet"]) <= 180 for item in first["results"]),
        "SEARCH snippet 未截断",
    )
    probable_fetch = tools.fetch(
        task_id=task_id,
        research_task=task,
        url=first["results"][0]["url"],
    )
    probable_source = next(
        item
        for item in store.load_many(task_id, "sources")
        if item["id"] == probable_fetch["source_id"]
    )
    require(
        probable_source["source_type"] != "official_site"
        and not probable_source["metadata"].get("first_party"),
        "probable SourceDocument 在 Fetch 阶段被升格为 official_site",
    )

    store.save_many(
        task_id,
        "official_domain_contexts",
        [item.model_copy(update={"confidence": "confirmed"}) for item in contexts],
    )
    provider.calls.clear()
    second = tools.search(
        task_id=task_id,
        research_task=task,
        query="Acme 开放平台 新功能",
        search_scope="auto",
        limit=7,
    )
    require(
        provider.calls
        and all(item["domain_filter"] == "acme.example" for item in provider.calls),
        "confirmed official domain 未触发限定域 Search",
    )
    require(second["official_domains"] == ["acme.example"], "confirmed 域未复用")
    confirmed_results = [
        item
        for item in store.load_many(task_id, "web_search_results")
        if item["query"] == "Acme 开放平台 新功能"
    ]
    require(
        confirmed_results
        and all(item["metadata"].get("first_party") for item in confirmed_results),
        "confirmed 官网结果未标 first_party",
    )
    tools.close()


def check_search_anchor_and_state_summary() -> None:
    douyin = make_task("task_anchor", "research_douyin", competitor="抖音")
    xhs = make_task("task_anchor", "research_xhs", competitor="小红书")
    require(
        _query_preserves_object_anchor(
            "抖音 巨量引擎 定价 官方",
            task=douyin,
            associated_domains=set(),
        ),
        "新商业化产品词被 SEARCH guard 拒绝",
    )
    require(
        _query_preserves_object_anchor(
            "site:xiaohongshu.com 帮助中心 计费",
            task=xhs,
            associated_domains={"xiaohongshu.com"},
        ),
        "site:/domain 对象锚点被 SEARCH guard 拒绝",
    )
    require(
        not _query_preserves_object_anchor(
            "小红书 帮助中心 定价",
            task=douyin,
            associated_domains={"douyin.com"},
        ),
        "明显跳到无关 competitor 的 Query 未拒绝",
    )

    client = CapturingLLMClient()
    state = ResearchAgentRun(
        task_id=douyin.task_id,
        research_task_id=douyin.id,
        attempted_queries=[f"query-{index}" for index in range(8)],
        visited_urls=[f"https://visited.test/{index}" for index in range(8)],
        rejected_sources=[f"https://rejected.test/{index}" for index in range(8)],
    )
    LLMResearchActionDecider(llm_client=client).decide(
        task_id=douyin.task_id,
        research_task=douyin,
        information_need=None,
        state=state,
        recent_observations=[],
    )
    compact = client.artifacts["research_state"][0]
    for key in ("attempted_queries", "visited_urls", "rejected_sources"):
        require(
            compact[key]["count"] == 8 and len(compact[key]["recent"]) == 3,
            f"{key} 未改为 compact summary",
        )


def check_cross_task_source_reuse(root: Path) -> None:
    task_id = "task_search_foundation_reuse"
    store = ArtifactStore(root / "reuse")
    for artifact_type in (
        "analysis_tasks",
        "agent_runs",
        "dag_nodes",
        "tool_calls",
        "research_agent_runs",
        "research_agent_actions",
        "research_agent_observations",
        "source_task_associations",
        "source_chunks",
        "source_retrieval_runs",
        "evidence",
        "product_cards",
        "evidence_coverage",
        "collection_attempts",
    ):
        store.save_many(task_id, artifact_type, [])
    url = "https://docs.acme.example/shared-feature"
    task_one = make_task(task_id, "research_one", seed_urls=[url])
    task_two = make_task(task_id, "research_two")
    task_three = make_task(task_id, "research_three")
    store.save_many(task_id, "research_tasks", [task_one, task_two, task_three])
    source = SourceDocument(
        id="source_shared",
        task_id=task_id,
        title="Acme Shared Feature",
        url=url,
        competitor="Acme",
        content_excerpt="Acme shared feature documentation with current product details.",
        metadata={"research_task_id": task_one.id},
    )
    page = WebPageContent(
        id="page_shared",
        task_id=task_id,
        source_id=source.id,
        requested_url=url,
        final_url=url,
        title=source.title,
        text=source.content_excerpt,
        content_hash="fixture_hash",
    )
    search_result = WebSearchResult(
        id="searchresult_task_two",
        task_id=task_id,
        research_task_id=task_two.id,
        search_attempt_id="attempt_task_two",
        provider="fixture",
        query="Acme shared feature",
        rank=1,
        title=source.title,
        url=url,
        snippet=source.content_excerpt,
        selected_for_collection=True,
    )
    store.save_many(task_id, "sources", [source])
    store.save_many(task_id, "web_pages", [page])
    store.save_many(task_id, "web_search_results", [search_result])
    collector = CollectorQueueService(
        store=store,
        web_tool=build_web_tool(),
        load_search_provider_from_env=False,
    )
    reused = collector.fetch_and_persist_url(
        task_id=task_id,
        research_task=task_two,
        url=url,
        search_result_id=search_result.id,
        collection_method="fixture",
    )
    require(reused["reused"] is True, "全局 SourceDocument 未复用")
    require(len(store.load_many(task_id, "sources")) == 1, "SourceDocument 全局去重失效")
    associations = store.load_many(task_id, "source_task_associations")
    require(
        len(associations) == 1
        and associations[0]["research_task_id"] == task_two.id
        and associations[0]["source_id"] == source.id,
        "合法 Search/Fetch 未建立 task-local association",
    )

    tools = ProductionResearchTools(
        store=store,
        recorder=TraceRecorder(store=store, task_id=task_id),
        collector=collector,
    )
    service = ResearchEvidenceAgentService(store=store)
    service.run_once(
        task_id,
        research_task_id=task_two.id,
        decider=SequenceDecider(
            [
                ResearchAgentAction(
                    task_id=task_id,
                    research_task_id=task_two.id,
                    action="READ",
                    rationale="read associated source",
                    source_id=source.id,
                ),
                ResearchAgentAction(
                    task_id=task_id,
                    research_task_id=task_two.id,
                    action="FINISH",
                    rationale="fixture complete",
                    finish_status="PARTIAL",
                ),
            ]
        ),
        tools=tools,
    )
    service.run_once(
        task_id,
        research_task_id=task_three.id,
        decider=SequenceDecider(
            [
                ResearchAgentAction(
                    task_id=task_id,
                    research_task_id=task_three.id,
                    action="READ",
                    rationale="attempt unassociated source",
                    source_id=source.id,
                ),
                ResearchAgentAction(
                    task_id=task_id,
                    research_task_id=task_three.id,
                    action="FINISH",
                    rationale="fixture exhausted",
                    finish_status="EXHAUSTED",
                ),
            ]
        ),
        tools=tools,
    )
    observations = store.load_many(task_id, "research_agent_observations")
    task_two_read = next(
        item
        for item in observations
        if item["research_task_id"] == task_two.id and item["action"] == "READ"
    )
    task_three_read = next(
        item
        for item in observations
        if item["research_task_id"] == task_three.id and item["action"] == "READ"
    )
    require(task_two_read["status"] == "completed", "合法跨 task Source reuse 不可 READ")
    require(task_three_read["status"] == "failed", "未关联 Source 可跨 task READ")
    tools.close()


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_search_foundation_fx1"
    )
    check_official_confidence_and_compact_observation(root)
    check_search_anchor_and_state_summary()
    check_cross_task_source_reuse(root)
    print("check_r1_search_foundation_fx1: PASS")
    print("probable_first_party=false")
    print("confirmed_domain_search=true")
    print("search_anchor_guard=true")
    print("search_observation_top5=true")
    print("task_local_source_reuse=true")
    print("unassociated_read=false")
    print("deepseek_calls=0")
    print("tavily_calls=0")
    print("external_network_calls=0")


if __name__ == "__main__":
    main()
