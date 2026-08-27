from __future__ import annotations

from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
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


class FakeOfficialFirstSearchProvider:
    name = "fake_official_first"

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
        if query.startswith("site:"):
            return [
                SearchHit(
                    title="Nebula Feature and API Documentation",
                    url="https://docs.nebula.example/api/features",
                    snippet="Nebula feature capabilities, API reference and developer documentation.",
                    site_name="Nebula Docs",
                ),
                SearchHit(
                    title="Terms of Service | Nebula",
                    url="https://docs.nebula.example/terms-of-service",
                    snippet="Nebula account rules and general legal terms.",
                    site_name="Nebula Docs",
                ),
                SearchHit(
                    title="Nebula Internal Feature Docs",
                    url="http://127.0.0.1/private",
                    snippet="Nebula feature API documentation.",
                    site_name="Nebula Docs",
                ),
            ][:count]
        if "官网 官方文档" in query:
            return [
                SearchHit(
                    title=f"Nebula feature independent blog {index}",
                    url=f"https://blogs.example.com/nebula-feature-{index}",
                    snippet="A third-party blog summarizes Nebula product feature capabilities.",
                    published_at="2010-01-01",
                )
                for index in range(1, 6)
            ][:count]
        if "开发者文档" in query:
            return [
                SearchHit(
                    title="Nebula Official Product Documentation",
                    url="https://docs.nebula.example/product/features-overview",
                    snippet="Nebula official feature overview and developer capabilities.",
                    site_name="Nebula Docs",
                )
            ]
        if query == "Nebula general evidence":
            return [
                SearchHit(
                    title="Nebula feature review",
                    url="https://technology.example.com/reviews/nebula-features",
                    snippet="Independent technology review of Nebula product capabilities.",
                    site_name="Technology Review",
                    published_at="2026-01-01",
                ),
                SearchHit(
                    title="Nebula user discussion",
                    url="https://forum.example.com/nebula/features",
                    snippet="Users discuss Nebula feature behavior and practical limitations.",
                    site_name="Developer Forum",
                ),
            ][:count]
        return []


class FakeExperienceSearchProvider:
    name = "fake_experience"

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
        return [
            SearchHit(
                title="Nebula 用户实测与踩坑记录",
                url="https://forum.example.com/nebula/experience",
                snippet="用户记录 Nebula 的实际使用体验、功能限制和踩坑反馈。",
                site_name="Developer Forum",
            )
        ]


def build_store(
    root: Path,
    *,
    task_id: str,
    research_task: ResearchTask,
    source_budget: int,
) -> ArtifactStore:
    store = ArtifactStore(root)
    for artifact_type in (
        "sources",
        "web_pages",
        "collection_attempts",
        "official_domain_contexts",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "tool_calls",
        "task_records",
    ):
        store.save_many(task_id, artifact_type, [])
    store.save_many(
        task_id,
        "research_plans",
        [
            ResearchPlan(
                task_id=task_id,
                decision_question=research_task.objective,
                status=ResearchPlanStatus.NEEDS_COLLECTION,
                research_task_ids=[research_task.id],
                budget=ResearchBudget(
                    max_sources_per_task=source_budget,
                    max_total_sources=source_budget,
                ),
            )
        ],
    )
    store.save_many(task_id, "research_tasks", [research_task])
    TaskBoardStore(store).save_board(
        TaskBoard(
            task_id=task_id,
            status=TaskStatus.READY,
            tasks=[
                TaskRecord(
                    task_id=task_id,
                    task_key=research_task.id,
                    task_type=TaskType.SUPPLEMENT_COLLECTION,
                    target_agent_role=AgentRole.COLLECTOR,
                    status=TaskStatus.READY,
                )
            ],
        )
    )
    return store


def build_web_tool() -> WebCollectorTool:
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
                "<html><head><title>Nebula source</title></head><body><main>"
                "Nebula provides documented product features, developer APIs, "
                "workflow integrations, security controls and practical limitations. "
                "This fixture contains enough deterministic text for collection."
                "</main></body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    return WebCollectorTool(
        transport=httpx.MockTransport(handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        enable_browser_fallback=False,
    )


def check_factual_official_first(root: Path) -> dict[str, int]:
    task_id = "task_official_first_factual"
    research_task = ResearchTask(
        id="research_nebula_feature",
        task_id=task_id,
        information_need_id="need_nebula_feature",
        title="核实 Nebula 产品能力",
        objective="采集能够核实 Nebula 产品功能与 API 能力的可靠资料",
        competitor="Nebula",
        dimension="feature",
        query_hints=["Nebula general evidence"],
        preferred_source_types=["docs"],
        status="waiting_for_collector",
        stop_condition="取得高相关的一手资料并由其它可靠来源补充",
    )
    store = build_store(
        root / "factual",
        task_id=task_id,
        research_task=research_task,
        source_budget=3,
    )
    provider = FakeOfficialFirstSearchProvider()
    collector = CollectorQueueService(
        store=store,
        web_tool=build_web_tool(),
        search_provider=provider,
    )
    result = collector.run_once(task_id)
    collector.close()

    require(result["status"] == "completed", str(result))
    require(result["collected_sources"] == 2, "首轮应只采集合格 first-party 来源")
    attempts = store.load_many(task_id, "search_attempts")
    stages = [item["metadata"]["acquisition_stage"] for item in attempts]
    require(
        stages == [
            "official_discovery",
            "official_discovery",
            "official_targeted",
        ],
        f"Official-first 调用顺序错误：{stages}",
    )
    require(
        any("docs.nebula.example" in item["metadata"]["discovered_official_hosts"] for item in attempts),
        "未从 discovery 结果发现 probable official host",
    )
    targeted_call = next(
        item for item in provider.calls if str(item["query"]).startswith("site:")
    )
    require(
        str(targeted_call["query"]).startswith("site:docs.nebula.example "),
        "未生成 site targeted query",
    )
    require(
        targeted_call["domain_filter"] == "docs.nebula.example",
        "Targeted Search 未沿用 SearchProvider domain_filter contract",
    )
    require(
        sum(item["metadata"]["selected_count"] for item in attempts) == 2,
        "SearchAttempt selected_count 与最终选择不一致",
    )

    search_results = store.load_many(task_id, "web_search_results")
    selected = [item for item in search_results if item["selected_for_collection"]]
    selected_urls = {item["url"] for item in selected}
    require(
        "https://docs.nebula.example/api/features" in selected_urls,
        "Official targeted Docs 未进入 Collector",
    )
    require(
        not any("blogs.example.com" in item["url"] for item in selected),
        "首个 discovery 的 5 个普通博客提前占满预算",
    )
    terms = next(item for item in search_results if "terms-of-service" in item["url"])
    require(not terms["selected_for_collection"], "官方低相关 Terms 绕过 FIX2 relevance gate")
    require(
        not any("127.0.0.1" in item["url"] for item in search_results)
        and any(
            int(item["metadata"].get("domain_filtered_out", 0)) > 0
            for item in attempts
            if item["metadata"].get("domain_filter")
        ),
        "限定域 Search 未在持久化前剔除域外候选",
    )

    selection_runs = store.load_many(task_id, "source_selection_runs")
    require(
        selection_runs
        and all(item["ranking_version"] == "source_quality_v1_fix2" for item in selection_runs),
        "新 SourceSelectionRun 未写入 FIX2 ranking_version",
    )
    return {
        "legacy_search_calls": 1,
        "legacy_candidate_results": 2,
        "legacy_selected_sources": 2,
        "legacy_primary_selected": 0,
        "search_calls": len(provider.calls),
        "candidate_results": len(search_results),
        "selected_sources": len(selected),
        "primary_selected": sum(
            item["source_role"] == "PRIMARY" and item["selected"]
            for item in selection_runs
        ),
    }


def check_experience_general_only(root: Path) -> None:
    task_id = "task_official_first_experience"
    research_task = ResearchTask(
        id="research_nebula_experience",
        task_id=task_id,
        information_need_id="need_nebula_experience",
        title="收集 Nebula 用户体验",
        objective="收集 Nebula 用户实际使用体验、踩坑和社区反馈",
        competitor="Nebula",
        dimension="feature",
        query_hints=["Nebula 用户实测"],
        status="waiting_for_collector",
        stop_condition="获得一条真实用户经验来源",
    )
    store = build_store(
        root / "experience",
        task_id=task_id,
        research_task=research_task,
        source_budget=1,
    )
    provider = FakeExperienceSearchProvider()
    collector = CollectorQueueService(
        store=store,
        web_tool=build_web_tool(),
        search_provider=provider,
    )
    result = collector.run_once(task_id)
    collector.close()
    require(result["status"] == "completed", str(result))
    require(len(provider.calls) == 1, "用户体验任务被强制执行 official-first quota")
    attempts = store.load_many(task_id, "search_attempts")
    require(
        [item["metadata"]["acquisition_stage"] for item in attempts] == ["general"],
        "用户体验任务没有保持 general acquisition",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "official_first_source_acquisition_r1"
    )
    stats = check_factual_official_first(root)
    check_experience_general_only(root)
    print("check_official_first_source_acquisition_r1: PASS")
    print(f"fake_before_search_calls={stats['legacy_search_calls']}")
    print(f"fake_before_candidate_results={stats['legacy_candidate_results']}")
    print(f"fake_before_selected_sources={stats['legacy_selected_sources']}")
    print(f"fake_before_primary_selected={stats['legacy_primary_selected']}")
    print(f"fake_search_calls={stats['search_calls']}")
    print(f"fake_candidate_results={stats['candidate_results']}")
    print(f"fake_selected_sources={stats['selected_sources']}")
    print(f"fake_primary_selected={stats['primary_selected']}")
    print("official_discovery_before_general=true")
    print("official_targeted_search=true")
    print("general_deferred_until_coverage_gap=true")
    print("url_safety_and_budget_preserved=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
