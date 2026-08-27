from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.collection.service import CollectorQueueService
from app.collection.source_quality import SourceCandidateRanker, canonical_dimension
from app.execution.research_agent import LLMResearchActionDecider, ProductionResearchTools
from app.harness.artifacts import ArtifactStore
from app.intake.step6e4 import build_step6e4_research_tasks, normalize_dimension
from app.schemas import (
    OfficialDomainContext,
    ResearchAgentRun,
    ResearchGap,
    ResearchTask,
    SourceEvidence,
    SourceRole,
    WebSearchResult,
)
from app.tools.search_provider import SearchHit
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.trace import TraceRecorder


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeOfficialSearchProvider:
    name = "fake_official_search"

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
            {
                "query": query,
                "count": count,
                "domain_filter": domain_filter,
            }
        )
        if domain_filter:
            return [
                SearchHit(
                    title="Acme Official Integration API",
                    url="https://docs.vendor.example/integrations/api",
                    snippet=(
                        "Acme official developer platform documents current "
                        "integration API and marketplace support."
                    ),
                    site_name="Acme Developer Docs",
                    published_at="2026-06-01",
                ),
                SearchHit(
                    title="Acme lookalike external page",
                    url="https://vendor.example.evil.test/integrations",
                    snippet="Acme official integration claim on an external host.",
                    site_name="External",
                    published_at="2026-06-01",
                ),
            ]
        return [
            SearchHit(
                title="Acme Official Website",
                url="https://www.vendor.example/",
                snippet="Acme official website and developer platform.",
                site_name="Acme Official",
            ),
            SearchHit(
                title="Acme integrations overview",
                url="https://general.test/acme-integrations",
                snippet="Acme integration overview from a general third party.",
                site_name="General Tech",
                published_at="2026-05-01",
            ),
        ]


class CapturingLLMClient:
    def __init__(self) -> None:
        self.prompt_summary = ""

    def generate_structured(self, **kwargs):
        self.prompt_summary = kwargs["prompt_summary"]
        task = kwargs["artifacts"]["research_task"][0]
        return (
            {
                "item": {
                    "task_id": task["task_id"],
                    "research_task_id": task["id"],
                    "action": "SEARCH",
                    "rationale": "look for direct official documentation",
                    "query": "Acme ecosystem integrations API",
                }
            },
            None,
            None,
        )


def make_task(task_id: str, *, research_task_id: str = "research_acme") -> ResearchTask:
    return ResearchTask(
        id=research_task_id,
        task_id=task_id,
        information_need_id="need_acme_ecosystem",
        title="Acme 生态与集成",
        objective="核实现行 Acme 生态与集成能力",
        competitor="Acme",
        dimension="生态与集成",
        query_hints=["Acme ecosystem integrations API"],
        preferred_source_types=["docs"],
        stop_condition="direct current evidence or bounded stop",
    )


def check_dimension_mapping() -> None:
    cases = {
        "生态与集成": "ecosystem",
        "ecosystem": "ecosystem",
        "integration": "ecosystem",
        "生态": "ecosystem",
        "生态与集成能力": "ecosystem",
        "产品能力": "feature",
        "feature": "feature",
        "产品定位": "positioning",
        "positioning": "positioning",
        "定价与成本": "pricing",
        "pricing": "pricing",
        "unrecognized-dimension": "other",
    }
    for raw, expected in cases.items():
        require(canonical_dimension(raw) == expected, f"canonical_dimension: {raw}")
        require(normalize_dimension(raw) == expected, f"normalize_dimension: {raw}")

    task_id = "task_source_policy_dimension"
    initial = make_task(task_id).model_copy(
        update={"status": "evidence_exhausted", "collection_round": 1}
    )
    gap = ResearchGap(
        id="gap_acme_ecosystem",
        task_id=task_id,
        competitors=["Acme"],
        dimension="ecosystem",
        missing_information="ecosystem evidence missing",
        decision_blocked="fixture",
        why_existing_evidence_is_insufficient="fixture",
        stop_condition="bounded",
    )
    supplement = build_step6e4_research_tasks(
        task_id,
        [gap],
        existing_tasks=[initial],
        max_collection_rounds=3,
    )[0]
    require(supplement.dimension == "ecosystem", "补采 ecosystem 被变成 other")


def check_official_first_prompt() -> None:
    task = make_task("task_source_policy_prompt")
    client = CapturingLLMClient()
    action = LLMResearchActionDecider(llm_client=client).decide(
        task_id=task.task_id,
        research_task=task,
        information_need=None,
        state=ResearchAgentRun(
            task_id=task.task_id,
            research_task_id=task.id,
        ),
        recent_observations=[],
    )
    require(action.query == "Acme ecosystem integrations API", "Agent query 被 Runtime 改写")
    for marker in ("官方官网", "官方帮助中心", "官方商业化平台", "官方开发者/开放平台"):
        require(marker in client.prompt_summary, f"official-first Prompt 缺少 {marker}")


def check_domain_reuse_fetch_rag_verify(root: Path) -> None:
    task_id = "task_r1_source_policy"
    store = ArtifactStore(root / "domain_reuse")
    for artifact_type in (
        "research_tasks",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "official_domain_contexts",
        "sources",
        "web_pages",
        "collection_attempts",
        "source_chunks",
        "source_retrieval_runs",
        "evidence",
        "tool_calls",
        "evidence_coverage",
    ):
        store.save_many(task_id, artifact_type, [])
    task = make_task(task_id)
    second_task = make_task(task_id, research_task_id="research_acme_round_2").model_copy(
        update={"collection_round": 2, "parent_research_task_id": task.id}
    )
    store.save_many(task_id, "research_tasks", [task, second_task])

    exact_sentence = (
        "Acme supports verified integration APIs, marketplace connectors, "
        "and partner platform workflows for current enterprise deployments."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nAllow: /",
                request=request,
            )
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Acme Integrations</title></head><body>"
                f"<p>{exact_sentence}</p>"
                "<p>Official developer documentation for Acme ecosystem.</p>"
                "</body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    provider = FakeOfficialSearchProvider()
    collector = CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            transport=httpx.MockTransport(handler),
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        search_provider=provider,
    )
    tools = ProductionResearchTools(
        store=store,
        recorder=TraceRecorder(store=store, task_id=task_id),
        collector=collector,
    )
    first = tools.search(
        task_id=task_id,
        research_task=task,
        query="Acme ecosystem integrations API",
        search_scope="auto",
        limit=5,
    )
    contexts = [
        OfficialDomainContext(**item)
        for item in store.load_many(task_id, "official_domain_contexts")
    ]
    require(
        [(item.competitor, item.domain) for item in contexts]
        == [("Acme", "vendor.example")],
        "official domain 未记录",
    )
    require(first["official_domains"] == [], "probable 域名被暴露为 confirmed official domain")
    require(
        first["probable_official_domains"] == ["vendor.example"],
        "probable official domain 未作为候选保存",
    )
    require(
        first["results"][0]["source_level"] != "first_party",
        "probable 域名在确认前被标为 first_party",
    )
    require(
        all("evil.test" not in item["url"] for item in first["results"]),
        "限定域 Search 的域外 URL 未被 Python post-filter",
    )
    require(exact_sentence not in str(first), "Search Observation 泄露了网页完整正文")

    store.save_many(
        task_id,
        "official_domain_contexts",
        [
            item.model_copy(update={"confidence": "confirmed"})
            for item in contexts
        ],
    )
    second = tools.search(
        task_id=task_id,
        research_task=second_task,
        query="Acme current marketplace connectors",
        search_scope="auto",
        limit=5,
    )
    require(
        provider.calls[-1]["domain_filter"] == "vendor.example",
        "后续 ResearchTask 未复用 official domain 做限定搜索",
    )
    official_lead = next(
        item
        for item in second["results"]
        if item["url"].endswith("/integrations/api")
    )
    require(
        official_lead["source_level"] == "first_party"
        and official_lead["official_confidence"] == "confirmed"
        and official_lead["freshness"] == "2026-06-01",
        "confirmed 官网 Search Observation 缺少简洁来源信息",
    )
    require(
        all(
            call["query"]
            in {
                "Acme ecosystem integrations API",
                "Acme current marketplace connectors",
            }
            for call in provider.calls
        ),
        "Runtime 构造了 Agent 未决定的 query",
    )

    fetched = tools.fetch(
        task_id=task_id,
        research_task=second_task,
        url=official_lead["url"],
    )
    persisted_source = next(
        item
        for item in store.load_many(task_id, "sources")
        if item["id"] == fetched["source_id"]
    )
    require(
        persisted_source["metadata"].get("first_party") is True
        and persisted_source["metadata"].get("source_level") == "first_party",
        "Search 来源策略信息未随 V1 SourceDocument 持久化",
    )
    read = tools.read(
        task_id=task_id,
        research_task=second_task,
        source_id=fetched["source_id"],
    )
    chunk = next(item for item in read["chunks"] if exact_sentence in item["text"])
    verified = tools.submit_evidence(
        task_id=task_id,
        research_task=second_task,
        source_id=fetched["source_id"],
        chunk_id=chunk["chunk_id"],
        exact_quote=exact_sentence,
        supports="Acme current ecosystem integrations",
    )
    evidence = SourceEvidence(**store.load_many(task_id, "evidence")[-1])
    require(verified["evidence_id"] == evidence.id, "官网子页面未通过 Verifier")
    require(evidence.source_id == fetched["source_id"], "Evidence provenance 断裂")
    require(
        store.load_many(task_id, "collection_attempts")
        and store.load_many(task_id, "web_pages")
        and store.load_many(task_id, "source_retrieval_runs"),
        "官网子页面未走 V1 Fetch→Persist→RAG",
    )
    tools.close()


def check_simple_priority_and_freshness() -> None:
    task = make_task("task_source_policy_rank")
    base = {
        "task_id": task.task_id,
        "research_task_id": task.id,
        "search_attempt_id": "attempt_rank",
        "provider": "fake",
        "query": "Acme current ecosystem integrations",
        "rank": 1,
        "created_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
    }
    official = WebSearchResult(
        **base,
        title="Acme Official Integration Docs",
        url="https://docs.acme.example/integrations",
        snippet="Acme official integration API marketplace documentation.",
        site_name="Acme Docs",
    )
    authoritative = WebSearchResult(
        **base,
        title="Acme integration research report",
        url="https://research.test/acme-integrations",
        snippet="Independent technology research report on Acme integrations.",
        site_name="Technology Research",
        published_at="2026-05-01",
    )
    general_recent = WebSearchResult(
        **base,
        title="Acme integrations overview",
        url="https://general.test/acme-integrations",
        snippet="Acme integration API marketplace overview.",
        site_name="General",
        published_at="2026-05-01",
    )
    general_old = general_recent.model_copy(
        update={
            "id": "searchresult_old",
            "url": "https://old.test/acme-integrations",
            "published_at": "2018-01-01",
        }
    )
    community = WebSearchResult(
        **base,
        title="Acme integration community discussion",
        url="https://community.test/acme-integrations",
        snippet="Community user feedback about Acme integrations.",
        site_name="Community",
        published_at="2026-05-01",
    )
    ranked = SourceCandidateRanker().rank(
        [community, general_old, general_recent, authoritative, official],
        task.model_copy(
            update={
                "metadata": {"confirmed_official_domains": ["acme.example"]}
            }
        ),
    )
    role_order = [item.source_role for item in ranked]
    require(role_order[0] == SourceRole.PRIMARY, "官方来源未优先")
    require(
        role_order.index(SourceRole.AUTHORITATIVE_SECONDARY)
        < role_order.index(SourceRole.GENERAL_THIRD_PARTY)
        < role_order.index(SourceRole.COMMUNITY),
        "来源等级未按 official→authoritative→general→community",
    )
    scores = {item.result.url: item for item in ranked}
    require(
        scores[general_recent.url].freshness_score
        > scores[general_old.url].freshness_score,
        "明显过旧第三方未在 current-state 查询中降权",
    )
    require(
        scores[official.url].freshness_score == 0.0
        and scores[official.url].source_role == SourceRole.PRIMARY,
        "无发布日期的在线官方文档被判 stale",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_source_policy_r1"
    )
    check_dimension_mapping()
    check_official_first_prompt()
    check_domain_reuse_fetch_rag_verify(root)
    check_simple_priority_and_freshness()
    print("check_r1_source_policy_r1: PASS")
    print("ecosystem_canonical_dimension=true")
    print("official_first_prompt=true")
    print("official_domain_recorded_and_reused=true")
    print("domain_restricted_search=true")
    print("off_domain_post_filter=true")
    print("v1_fetch_persist_rag_verifier=true")
    print("source_level_priority=true")
    print("stale_third_party_downranked=true")
    print("deepseek_calls=0")
    print("tavily_calls=0")
    print("external_network_calls=0")


if __name__ == "__main__":
    main()
