from __future__ import annotations

from pathlib import Path

from app.collection import CollectorQueueService
from app.collection.service import competitor_entities
from app.collection.source_quality import SourceCandidateRanker
from app.harness.artifacts import ArtifactStore
from app.schemas import OfficialConfidence, ResearchTask, SourceRole, WebSearchResult
from app.tools.search_provider import SearchHit
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeTavilyProvider:
    name = "tavily"

    def __init__(self, *, official_available: bool = True) -> None:
        self.official_available = official_available
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
        if domain_filter == "acme.com":
            return [
                SearchHit(
                    title="Acme Product Documentation",
                    url="https://docs.acme.com/product/features",
                    snippet="Acme official product and API documentation.",
                    site_name="Acme",
                )
            ]
        if domain_filter == "nova.com":
            return [
                SearchHit(
                    title="Nova API Documentation",
                    url="https://developer.nova.com/docs/api",
                    snippet="Nova official developer documentation.",
                    site_name="Nova",
                )
            ]
        if self.official_available:
            values = [
                SearchHit(
                    title="深度剖析 Acme 官方产品 | 人人都是产品经理",
                    url="https://www.woshipm.com/evaluating/6073724.html",
                    snippet="文章引用 Acme 官方资料并分析产品定位。",
                    site_name="人人都是产品经理",
                ),
                SearchHit(
                    title="Acme Official Product",
                    url="https://www.acme.com/product",
                    snippet="Acme official product capabilities and positioning.",
                    site_name="Acme",
                ),
                SearchHit(
                    title="Acme 官方产品资料",
                    url="https://producthub.example/product/acme",
                    snippet="Acme 官方产品资料汇总。",
                    site_name="Product Hub",
                ),
            ]
            if "Nova" in query:
                values.append(
                    SearchHit(
                        title="Nova Official Developer Platform",
                        url="https://www.nova.com/product/api",
                        snippet="Nova official product and developer platform.",
                        site_name="Nova",
                    )
                )
            return values[:count]
        target = query.split()[0]
        return [
            SearchHit(
                title=f"Independent {target} product analysis",
                url=f"https://technology-review.example/reports/{target.casefold()}",
                snippet=(
                    f"Independent review of {target} feature and capability, "
                    "including product functions and limitations."
                ),
                site_name="Independent Technology Review",
            )
        ]


def task(task_id: str, *, competitor: str = "Acme") -> ResearchTask:
    return ResearchTask(
        id=f"research_{task_id}",
        task_id=task_id,
        information_need_id=f"need_{task_id}",
        title=f"核实 {competitor} 产品功能",
        objective=f"采集并核实 {competitor} 的产品功能、价格与 API 事实",
        competitor=competitor,
        dimension="feature",
        query_hints=[f"{competitor} 产品功能 官方"],
        preferred_source_types=["docs"],
        status="waiting_for_collector",
        stop_condition="优先取得官方资料，资料不足时允许可靠第三方补充",
    )


def result(
    *,
    task_id: str,
    url: str,
    metadata: dict,
) -> WebSearchResult:
    return WebSearchResult(
        task_id=task_id,
        research_task_id=f"research_{task_id}",
        search_attempt_id=f"attempt_{task_id}",
        provider="tavily",
        query="Acme 产品功能 官方",
        rank=1,
        title="Acme 官方产品资料",
        url=url,
        snippet="Acme 官方产品功能与 API 资料。",
        site_name="Product Hub",
        metadata=metadata,
    )


def build_collector(
    root: Path,
    *,
    provider: FakeTavilyProvider,
) -> tuple[ArtifactStore, CollectorQueueService]:
    store = ArtifactStore(root)
    collector = CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        search_provider=provider,
    )
    return store, collector


def reset_task(store: ArtifactStore, task_id: str) -> None:
    for artifact_type in (
        "evidence_coverage",
        "official_domain_contexts",
        "search_attempts",
        "source_selection_runs",
        "sources",
        "tool_calls",
        "web_search_results",
    ):
        store.save_many(task_id, artifact_type, [])


def check_resolution_and_official_first(root: Path) -> None:
    provider = FakeTavilyProvider()
    store, collector = build_collector(root / "resolution", provider=provider)
    research_task = task("task_resolver_r2")
    reset_task(store, research_task.task_id)
    collector.search_agent_query(
        task_id=research_task.task_id,
        research_task=research_task,
        query=research_task.query_hints[0],
        limit=3,
    )
    collector.close()

    contexts = store.load_many(research_task.task_id, "official_domain_contexts")
    by_domain = {item["domain"]: item for item in contexts}
    require(
        by_domain["acme.com"]["confidence"] == "confirmed",
        "品牌域与站点身份信号未确认官方域",
    )
    require(
        by_domain["woshipm.com"]["confidence"] == "rejected",
        "第三方文章因摘要包含‘官方’而未被拒绝",
    )
    require(
        by_domain["producthub.example"]["confidence"] == "probable",
        "弱候选没有保持 probable",
    )

    search_results = store.load_many(research_task.task_id, "web_search_results")
    official = next(item for item in search_results if "acme.com" in item["url"])
    publisher = next(item for item in search_results if "woshipm.com" in item["url"])
    probable = next(
        item for item in search_results if "producthub.example" in item["url"]
    )
    require(official["metadata"]["first_party"] is True, "confirmed 未映射 first_party")
    require(official["metadata"]["source_role"] == "PRIMARY", "confirmed 未获得 PRIMARY")
    require(publisher["metadata"]["first_party"] is False, "rejected 被标为 first_party")
    require(
        publisher["metadata"]["official_confidence"] == "rejected",
        "rejected 状态未进入候选 artifact",
    )
    require(probable["metadata"]["first_party"] is False, "probable 被标为 first_party")
    require(
        probable["metadata"]["source_role"] != "PRIMARY",
        "probable 获得了官方来源角色",
    )
    selected = [item for item in search_results if item["selected_for_collection"]]
    require(selected and all(item["metadata"]["first_party"] for item in selected), "官方结果充足时第三方抢占预算")
    official_source_count = sum(
        item["metadata"].get("official_confidence") == "confirmed"
        and item["metadata"].get("first_party") is True
        for item in selected
    )
    require(official_source_count == len(selected), "probable 被计入官方来源统计")


def check_probable_has_no_official_boost() -> None:
    ranker = SourceCandidateRanker()
    probable_task = task("task_probable_no_boost").model_copy(
        update={"metadata": {"probable_official_domains": ["producthub.example"]}}
    )
    unknown_task = task("task_unknown_no_boost")
    probable = ranker.score(
        result(
            task_id=probable_task.task_id,
            url="https://producthub.example/product/acme",
            metadata={},
        ),
        probable_task,
    )
    unknown = ranker.score(
        result(
            task_id=unknown_task.task_id,
            url="https://producthub.example/product/acme",
            metadata={},
        ),
        unknown_task,
    )
    require(probable.official_confidence == "probable", "probable 状态丢失")
    require(probable.source_role != SourceRole.PRIMARY, "probable 被提升为 PRIMARY")
    require(
        probable.final_score == unknown.final_score,
        "probable 仍获得 official boost",
    )
    preferred_task = task("task_preferred_domain_is_hint").model_copy(
        update={"preferred_domains": ["producthub.example"]}
    )
    preferred = ranker.score(
        result(
            task_id=preferred_task.task_id,
            url="https://producthub.example/product/acme",
            metadata={},
        ),
        preferred_task,
    )
    require(
        preferred.official_confidence == "probable"
        and preferred.source_role != SourceRole.PRIMARY,
        "preferred_domains 未经验证就获得 confirmed/PRIMARY",
    )


def check_cache_and_multi_competitor(root: Path) -> None:
    provider = FakeTavilyProvider()
    store, collector = build_collector(root / "shared", provider=provider)
    first = task("task_cache_seed")
    reset_task(store, first.task_id)
    collector.search_agent_query(
        task_id=first.task_id,
        research_task=first,
        query=first.query_hints[0],
        limit=1,
    )
    provider.calls.clear()
    reused = task("task_cache_reuse")
    reset_task(store, reused.task_id)
    collector.search_agent_query(
        task_id=reused.task_id,
        research_task=reused,
        query=reused.query_hints[0],
        limit=1,
    )
    require(
        provider.calls
        and any(item["domain_filter"] == "acme.com" for item in provider.calls),
        "后续任务没有复用 confirmed 官方域缓存",
    )

    provider.calls.clear()
    combined = task("task_combined", competitor="Acme、Nova")
    reset_task(store, combined.task_id)
    collector.search_agent_query(
        task_id=combined.task_id,
        research_task=combined,
        query=combined.query_hints[0],
        limit=3,
    )
    collector.close()
    require(
        competitor_entities(combined.competitor) == ["Acme", "Nova"],
        "多竞品没有独立拆分",
    )
    contexts = store.load_many(combined.task_id, "official_domain_contexts")
    require(
        any(item["competitor"] == "Nova" and item["domain"] == "nova.com" for item in contexts),
        "组合任务没有独立解析 Nova 官方域",
    )


def check_third_party_fallback(root: Path) -> None:
    provider = FakeTavilyProvider(official_available=False)
    store, collector = build_collector(root / "fallback", provider=provider)
    research_task = task("task_fallback", competitor="Orbit")
    reset_task(store, research_task.task_id)
    collector.search_agent_query(
        task_id=research_task.task_id,
        research_task=research_task,
        query=research_task.query_hints[0],
        limit=1,
    )
    collector.close()
    selected = [
        item
        for item in store.load_many(research_task.task_id, "web_search_results")
        if item["selected_for_collection"]
    ]
    require(len(selected) == 1, "没有 confirmed 官方域时研究被错误阻断")
    require(selected[0]["metadata"]["first_party"] is False, "降级来源身份失真")
    require(selected[0]["metadata"]["source_role"] != "PRIMARY", "第三方降级被伪装成官方")


def check_zhihu_is_not_official_resolution_input() -> None:
    candidate = WebSearchResult(
        task_id="task_zhihu_boundary",
        research_task_id="research_zhihu_boundary",
        search_attempt_id="attempt_zhihu_boundary",
        provider="zhihu",
        query="Acme 用户体验",
        rank=1,
        title="Acme 官方产品体验",
        url="https://www.zhihu.com/question/1",
        snippet="讨论 Acme 官方产品的真实用户体验。",
        site_name="知乎",
        metadata={"source_tool": "zhihu_search", "channel": "zhihu"},
    )
    ranked = SourceCandidateRanker().score(candidate, task("task_zhihu_boundary"))
    require(ranked.source_role != SourceRole.PRIMARY, "知乎候选进入官方来源角色")


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "official_domain_resolver_r2"
    )
    ArtifactStore(root / "cache").save_many(
        "official_domains",
        "official_domain_contexts",
        [],
    )
    check_resolution_and_official_first(root)
    check_probable_has_no_official_boost()
    check_cache_and_multi_competitor(root)
    check_third_party_fallback(root)
    check_zhihu_is_not_official_resolution_input()
    print("check_official_domain_resolver_r2: PASS")
    print("woshipm_official_boost=false")
    print("confirmed_official_first=true")
    print("probable_official_metrics=false")
    print("third_party_fallback=true")
    print("multi_competitor_resolution=true")
    print("zhihu_official_resolution=false")
    print("real_network_used=false")


if __name__ == "__main__":
    main()
