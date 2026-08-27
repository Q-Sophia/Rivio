from __future__ import annotations

from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    ResearchTask,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.tools.search_provider import SearchHit
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeSearchProvider:
    name = "fake_source_acquisition_r2"

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
        if query.startswith("site:"):
            return [
                SearchHit(
                    title="Nebula Official Feature Documentation",
                    url="https://docs.nebula.example/features",
                    snippet="Nebula official developer documentation for feature capabilities and API.",
                    site_name="Nebula Docs",
                )
            ]
        if "官网 官方文档" in query:
            return [
                SearchHit(
                    title="Nebula feature independent overview",
                    url="https://media.example.com/nebula-overview",
                    snippet="Independent report about Nebula feature capabilities.",
                )
            ]
        if "开发者文档" in query:
            return [
                SearchHit(
                    title="Nebula 官方开发者文档",
                    url="https://docs.nebula.example/overview",
                    snippet="Nebula 官方开发者 documentation 与 API 能力说明。",
                    site_name="Nebula Docs",
                )
            ]
        return [
            SearchHit(
                title="Nebula independent feature analysis",
                url="https://technology.example.com/nebula-feature-analysis",
                snippet="Independent technology report about Nebula product feature capabilities.",
                site_name="Technology Report",
            ),
            SearchHit(
                title="Nebula 用户使用体验",
                url="https://community.example.com/nebula-experience",
                snippet="用户讨论 Nebula 功能的实际使用体验、反馈和限制。",
                site_name="Nebula Community",
            ),
        ][:count]


class LowScoreFirstPartyProvider(FakeSearchProvider):
    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        if query.startswith("site:"):
            self.calls.append(
                {"query": query, "count": count, "domain_filter": domain_filter}
            )
            return [
                SearchHit(
                    title="Nebula",
                    url="https://docs.nebula.example/download",
                    snippet="Nebula official product download.",
                    site_name="Nebula",
                )
            ]
        if "about product" in query:
            self.calls.append(
                {"query": query, "count": count, "domain_filter": domain_filter}
            )
            return [
                SearchHit(
                    title="Nebula Official Homepage",
                    url="https://docs.nebula.example/home",
                    snippet="Nebula official product website.",
                    site_name="Nebula",
                )
            ]
        return super().search(
            query,
            count=count,
            domain_filter=domain_filter,
        )


def make_task(
    task_id: str,
    *,
    objective: str = "核实 Nebula 产品功能与 API 能力",
    collection_round: int = 1,
    research_gap_id: str = "",
) -> ResearchTask:
    return ResearchTask(
        id=f"research_nebula_feature_round_{collection_round}",
        task_id=task_id,
        information_need_id="need_nebula_feature",
        title="核实 Nebula 产品能力",
        objective=objective,
        competitor="Nebula",
        dimension="feature",
        query_hints=["Nebula independent feature evidence"],
        status="waiting_for_collector",
        stop_condition="取得可追溯的功能证据",
        collection_round=collection_round,
        research_gap_id=research_gap_id,
    )


def make_store(root: Path, task: ResearchTask) -> ArtifactStore:
    store = ArtifactStore(root)
    store.save_many(task.task_id, "research_tasks", [task])
    for artifact_type in (
        "sources",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "tool_calls",
        "evidence_coverage",
    ):
        store.save_many(task.task_id, artifact_type, [])
    return store


def discover(
    root: Path,
    task: ResearchTask,
    *,
    coverage_status: EvidenceCoverageStatus | None = None,
    provider: FakeSearchProvider | None = None,
) -> tuple[ArtifactStore, FakeSearchProvider, ResearchTask]:
    store = make_store(root, task)
    if coverage_status is not None:
        store.save_many(
            task.task_id,
            "evidence_coverage",
            [
                EvidenceCoverage(
                    task_id=task.task_id,
                    competitor=task.competitor,
                    dimension=task.dimension,
                    status=coverage_status,
                )
            ],
        )
    provider = provider or FakeSearchProvider()
    collector = CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        search_provider=provider,
    )
    discovered, _selected_ids = collector._discover_seed_urls(
        task_id=task.task_id,
        research_task=task,
        limit=3,
    )
    collector.close()
    return store, provider, discovered


def stages(store: ArtifactStore, task_id: str) -> list[str]:
    return [
        item["metadata"]["acquisition_stage"]
        for item in store.load_many(task_id, "search_attempts")
    ]


def check_first_party_then_coverage_fallback(root: Path) -> None:
    initial = make_task("task_source_acquisition_r2_initial")
    store, _provider, discovered = discover(root / "initial", initial)
    initial_stages = stages(store, initial.task_id)
    require(
        initial_stages
        == ["official_discovery", "official_discovery", "official_targeted"],
        f"首轮没有停在 first-party：{initial_stages}",
    )
    require(
        discovered.seed_urls
        and all("docs.nebula.example" in url for url in discovered.seed_urls),
        f"首轮混入非 first-party URL：{discovered.seed_urls}",
    )

    sufficient = make_task("task_source_acquisition_r2_sufficient")
    store, _provider, _discovered = discover(
        root / "sufficient",
        sufficient,
        coverage_status=EvidenceCoverageStatus.SUFFICIENT,
    )
    require(
        "general" not in stages(store, sufficient.task_id),
        "Coverage 已充分仍执行 general search",
    )

    partial = make_task(
        "task_source_acquisition_r2_partial",
        collection_round=2,
        research_gap_id="gap_nebula_feature_partial",
    )
    store, _provider, discovered = discover(
        root / "partial",
        partial,
        coverage_status=EvidenceCoverageStatus.PARTIAL,
    )
    partial_stages = stages(store, partial.task_id)
    require(
        partial_stages
        == [
            "official_discovery",
            "official_discovery",
            "official_targeted",
            "general",
        ],
        f"Coverage 不足时 fallback 顺序错误：{partial_stages}",
    )
    general_attempt = next(
        item
        for item in store.load_many(partial.task_id, "search_attempts")
        if item["metadata"]["acquisition_stage"] == "general"
    )
    require(
        general_attempt["metadata"]["general_search_reason"]
        == "evidence_coverage_insufficient",
        "General fallback 没有绑定 Coverage 不足原因",
    )
    require(
        discovered.seed_urls[0].startswith("https://docs.nebula.example/"),
        "Fallback 轮没有保持 first-party 优先",
    )
    require(
        any("technology.example.com" in url for url in discovered.seed_urls),
        "Coverage 不足时未补充高质量二手来源",
    )


def check_community_direct_general(root: Path) -> None:
    task = make_task(
        "task_source_acquisition_r2_community",
        objective="收集 Nebula 用户体验、实际使用和社区反馈",
    )
    store, _provider, discovered = discover(root / "community", task)
    require(
        stages(store, task.task_id) == ["general"],
        "明确社区需求被强制 official-first",
    )
    require(discovered.seed_urls, "社区需求没有获得 general 候选")


def check_first_party_generic_threshold_exception(root: Path) -> None:
    task = make_task("task_source_acquisition_r2_low_score_first_party").model_copy(
        update={
            "dimension": "positioning",
            "objective": "核实 Nebula 的目标用户与适用场景",
            "query_hints": ["Nebula target audience evidence"],
        }
    )
    store, _provider, discovered = discover(
        root / "low_score_first_party",
        task,
        provider=LowScoreFirstPartyProvider(),
    )
    candidate = next(
        item
        for item in store.load_many(task.task_id, "source_selection_runs")
        if item["url"] == "https://docs.nebula.example/download"
    )
    result = next(
        item
        for item in store.load_many(task.task_id, "web_search_results")
        if item["url"] == candidate["url"]
    )
    require(candidate["final_score"] < 20.0, "fixture 未进入普通阈值以下")
    require(
        candidate["selected"]
        and result["selected_for_collection"]
        and candidate["selection_reason"].startswith("selected_first_party"),
        f"已识别 first-party 仍被普通 quality line 淘汰：{candidate}",
    )
    require(candidate["url"] in discovered.seed_urls, "first-party URL 未进入采集集合")


def check_redirect_provenance(root: Path) -> None:
    task_id = "task_source_acquisition_r2_redirect"
    discovered_url = "https://old.nebula.example/discovered"
    final_url = "https://docs.nebula.example/final"
    task = make_task(task_id).model_copy(update={"seed_urls": [discovered_url]})
    store = make_store(root / "redirect", task)
    TaskBoardStore(store).save_board(
        TaskBoard(
            task_id=task_id,
            status=TaskStatus.READY,
            tasks=[
                TaskRecord(
                    task_id=task_id,
                    task_key=task.id,
                    task_type=TaskType.SUPPLEMENT_COLLECTION,
                    target_agent_role=AgentRole.COLLECTOR,
                    status=TaskStatus.READY,
                )
            ],
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/discovered":
            return httpx.Response(
                302,
                headers={"location": final_url},
                request=request,
            )
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Nebula Docs</title></head><body>"
                "Nebula official feature documentation describes API capabilities, "
                "security controls, integrations and product limitations in detail."
                "</body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    collector = CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            transport=httpx.MockTransport(handler),
            url_policy=URLSafetyPolicy(resolve_dns=False),
            respect_robots=False,
            enable_browser_fallback=False,
        ),
        load_search_provider_from_env=False,
    )
    result = collector.run_once(task_id)
    collector.close()
    require(result["status"] == "completed", str(result))
    source = store.load_many(task_id, "sources")[0]
    page = store.load_many(task_id, "web_pages")[0]
    attempt = store.load_many(task_id, "collection_attempts")[0]
    require(source["url"] == final_url, "SourceDocument 未使用 redirect final URL")
    require(
        source["metadata"]["discovered_url"] == discovered_url,
        "SourceDocument 未保留 discovered URL",
    )
    require(
        page["requested_url"] == discovered_url and page["final_url"] == final_url,
        "WebPage redirect provenance 不完整",
    )
    require(
        attempt["requested_url"] == discovered_url
        and attempt["final_url"] == final_url,
        "CollectionAttempt redirect provenance 不完整",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "source_acquisition_r2_first_party_first"
    )
    check_first_party_then_coverage_fallback(root)
    check_community_direct_general(root)
    check_first_party_generic_threshold_exception(root)
    check_redirect_provenance(root)
    print("check_source_acquisition_r2_first_party_first: PASS")
    print("official_targeted_before_general=true")
    print("sufficient_coverage_skips_general=true")
    print("insufficient_coverage_falls_back_general=true")
    print("community_objective_direct_general=true")
    print("first_party_below_general_quality_line_selected=true")
    print("redirect_final_url_and_discovered_url_preserved=true")
    print("real_network_used=false")


if __name__ == "__main__":
    main()
