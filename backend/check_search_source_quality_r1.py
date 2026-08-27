from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from app.collection import CollectorQueueService
from app.collection.source_quality import (
    SourceCandidateRanker,
    _contains_target,
    _target_forms,
)
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    OfficialConfidence,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    SourceRole,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
    WebSearchResult,
)
from app.tools.search_provider import SearchHit
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def research_task(
    *,
    dimension: str,
    objective: str = "核实 Trae 的产品事实",
    competitor: str = "Trae",
    metadata: dict | None = None,
) -> ResearchTask:
    return ResearchTask(
        id=f"research_{dimension}_{competitor}",
        task_id="task_quality_unit",
        information_need_id="need_quality",
        title=f"核实 {competitor} 的{dimension}",
        objective=objective,
        competitor=competitor,
        dimension=dimension,
        query_hints=[f"{competitor} {dimension} 官方", f"{competitor} {dimension} 文档"],
        stop_condition="取得当前问题的可靠证据",
        metadata=metadata or {},
    )


def search_result(
    *,
    title: str,
    url: str,
    snippet: str = "",
    site_name: str = "",
    published_at: str = "2026-01-01",
    rank: int = 1,
) -> WebSearchResult:
    identity = hashlib.sha256(f"{title}|{url}".encode("utf-8")).hexdigest()[:10]
    return WebSearchResult(
        id=f"result_{identity}",
        task_id="task_quality_unit",
        research_task_id="research_quality",
        search_attempt_id="attempt_quality",
        provider="mock",
        query="Trae research",
        rank=rank,
        title=title,
        url=url,
        snippet=snippet,
        site_name=site_name,
        published_at=published_at,
    )


def check_ranker_policy() -> None:
    ranker = SourceCandidateRanker()

    cjk_forms = _target_forms("星云")
    ascii_forms = _target_forms("AB")
    require("星云" in cjk_forms, "2 字中文 target 未生成 target form")
    require("ab" in ascii_forms, "2 字母 ASCII target 未生成 target form")
    require(not _contains_target("cable capabilities", ascii_forms), "短 ASCII target 误命中更长 token")
    require(_contains_target("AB产品能力", ascii_forms), "短 ASCII target 无法识别中文邻接表达")

    short_feature_task = research_task(
        dimension="feature",
        competitor="AB",
        objective="核实 AB 的产品能力、安全和登录功能",
    )
    short_relevant = ranker.score(
        search_result(
            title="AB产品能力与安全功能",
            url="https://technology.example.com/reviews/ab-product",
            snippet="AB 提供消息、登录和安全能力，适合团队使用。",
        ),
        short_feature_task,
    )
    require(short_relevant.final_score >= 20, "短目标加明确 ResearchTask 语义仍未通过 relevance")

    short_accidental = ranker.score(
        search_result(
            title="行业新闻中提到 AB",
            url="https://news.example.com/general-industry",
            snippet="AB 仅出现在附录名单中，正文讨论通用管理方法。",
        ),
        short_feature_task,
    )
    require(short_accidental.final_score < 20, "短目标偶然出现且缺少任务上下文仍被抬高")

    short_collision = ranker.score(
        search_result(
            title="某品牌 AB 冰淇淋官降",
            url="https://auto.example.com/models/ab-ice-cream",
            snippet="该车型通过降低制造成本向消费者让利。",
        ),
        research_task(
            dimension="pricing",
            competitor="AB",
            objective="核实 AB 产品的定价与订阅信息",
        ),
    )
    require(short_collision.final_score < 20, "短目标同名实体碰撞进入正常采集线")

    short_official_docs = ranker.score(
        search_result(
            title="AB Developer Documentation",
            url="https://developers.vendor.example/ab/api",
            snippet="AB API and developer integration documentation.",
        ),
        short_feature_task,
    )
    require(
        short_official_docs.official_confidence == OfficialConfidence.PROBABLE,
        "短目标文档型一手页面未识别为 probable",
    )
    require(short_official_docs.final_score >= 20, "短目标官方文档未恢复 relevance")

    third_party_manual = ranker.score(
        search_result(
            title="AB Account Setup Manual",
            url="https://manuals.example/posts/ab-account-setup-manual",
            snippet="AB account setup, verification and profile instructions.",
            site_name="Support Manuals",
        ),
        short_feature_task,
    )
    require(
        third_party_manual.official_confidence == OfficialConfidence.UNKNOWN,
        "通用文档站仅凭文章路径和短目标被误判为 probable",
    )

    short_generic_tutorial = ranker.score(
        search_result(
            title="从零开始，产品经理手把手教你做 AB 小程序",
            url="https://community.example.com/developer/article/1001",
            snippet="文章以 AB 为例介绍通用小程序产品定位和开发方法。",
        ),
        research_task(
            dimension="positioning",
            competitor="AB",
            objective="核实 AB 的产品定位与价值主张",
        ),
    )
    require(
        short_generic_tutorial.final_score < 20,
        "短目标通用产品教程仍占用正常采集预算",
    )

    feature_task = research_task(dimension="feature")
    official_docs = ranker.score(
        search_result(
            title="TRAE Skills Documentation",
            url="https://docs.trae.ai/ide/skills",
            snippet="TraeCode provides built-in skills and debugging capabilities.",
        ),
        feature_task,
    )
    aggregate = ranker.score(
        search_result(
            title="AI 工具导航大全",
            url="https://tools.example.com/tags/ai",
            snippet="Trae 等 AI 工具合集与排行榜",
        ),
        feature_task,
    )
    require(official_docs.final_score > aggregate.final_score + 30, "官方 Docs 未显著高于导航聚合站")
    require(
        official_docs.source_role == SourceRole.GENERAL_THIRD_PARTY,
        "probable Trae Docs 不应在确认前识别为 PRIMARY",
    )
    require(aggregate.source_role == SourceRole.LOW_QUALITY, "导航聚合站未识别为 LOW_QUALITY")

    pricing_task = research_task(
        dimension="pricing",
        metadata={"confirmed_official_domains": ["trae.ai"]},
    )
    official_pricing = ranker.score(
        search_result(
            title="Trae Pricing and Billing",
            url="https://www.trae.ai/pricing",
            snippet="Trae Pro subscription plans and billing details.",
        ),
        pricing_task,
    )
    generic_pricing = ranker.score(
        search_result(
            title="成本分析及定价步骤与方法",
            url="https://documents.example.com/pricing-method",
            snippet="市场导向法、价值导向法与企业定价策略模板。",
        ),
        pricing_task,
    )
    require(official_pricing.final_score > generic_pricing.final_score + 50, "官方 Pricing 未显著高于通用定价内容")
    require(any("target_entity_missing" in item for item in generic_pricing.penalties), "目标实体缺失未触发强惩罚")
    require(any("generic_tutorial" in item for item in generic_pricing.penalties), "通用定价教程未触发强惩罚")

    positioning_task = research_task(dimension="positioning")
    generic_positioning = ranker.score(
        search_result(
            title="产品需求分析：从用户到需求文档",
            url="https://blog.example.com/product-manager",
            snippet="产品定位、市场调研、竞品分析和用户研究的方法论。",
        ),
        positioning_task,
    )
    require(generic_positioning.final_score < 0, "通用产品经理教程没有明显降权")

    ecosystem_task = research_task(dimension="ecosystem")
    official_integration = ranker.score(
        search_result(
            title="Trae MCP Integration Docs",
            url="https://docs.trae.ai/ide/mcp",
            snippet="Configure Trae integrations and the MCP marketplace.",
        ),
        ecosystem_task,
    )
    generic_mcp = ranker.score(
        search_result(
            title="MCP 入门教程",
            url="https://tutorial.example.com/mcp",
            snippet="通用 MCP 生态与服务器配置完整教程。",
        ),
        ecosystem_task,
    )
    require(official_integration.final_score > generic_mcp.final_score + 50, "官方 Integration 未高于泛 MCP 教程")

    experience_task = research_task(
        dimension="other",
        objective="收集 Trae 用户实际使用体验、踩坑和社区反馈",
    )
    community = ranker.score(
        search_result(
            title="Trae 用户实测：项目开发踩坑记录",
            url="https://forum.trae.cn/t/experience/123",
            snippet="开发者记录 Trae 的实际使用体验和问题。",
        ),
        experience_task,
    )
    require(community.source_role == SourceRole.COMMUNITY, "用户经验来源被误判为 LOW_QUALITY")
    require(community.authority_score == 20, "经验类任务没有提升 Community 权重")

    secondary = ranker.score(
        search_result(
            title="Trae AI IDE 深度实测",
            url="https://technology.example.com/reviews/trae",
            snippet="独立科技媒体测试 Trae 的 Agent 编程和代码补全能力。",
            site_name="Independent Technology Review",
        ),
        feature_task,
    )
    require(secondary.source_role == SourceRole.AUTHORITATIVE_SECONDARY, "高相关可信二手来源被一刀切")
    require(secondary.final_score > 40, "高相关二手来源分数过低")

    confirmed_task = research_task(
        dimension="feature",
        metadata={"confirmed_official_domains": ["vendor.example"]},
    )
    confirmed = ranker.score(
        search_result(
            title="Trae Feature Docs",
            url="https://docs.vendor.example/trae/features",
            snippet="Trae feature documentation.",
        ),
        confirmed_task,
    )
    unknown = ranker.score(
        search_result(
            title="Trae Feature Review",
            url="https://independent.example/reviews/trae",
            snippet="Trae feature review.",
        ),
        feature_task,
    )
    require(confirmed.official_confidence == OfficialConfidence.CONFIRMED, "confirmed 官方域名未向子域继承")
    require(official_docs.official_confidence == OfficialConfidence.PROBABLE, "品牌域名高吻合未识别 probable")
    require(unknown.official_confidence == OfficialConfidence.UNKNOWN, "未知域名被伪装成官方")

    broad_other_task = research_task(
        dimension="other",
        metadata={"confirmed_official_domains": ["trae.ai"]},
    )
    official_terms = ranker.score(
        search_result(
            title="Terms of Service | TRAE",
            url="https://www.trae.ai/terms-of-service",
            snippet="TRAE platform terms, account rules and legal conditions.",
        ),
        broad_other_task,
    )
    require(official_terms.source_role == SourceRole.PRIMARY, "官方 Terms 的来源角色不应因低任务相关性失真")
    require(official_terms.final_score < 20, "未请求法律条款时，官方 authority 仍越过 relevance gate")
    require(
        any("relevance_gate_legal_policy_not_requested" in item for item in official_terms.penalties),
        "官方 Terms 未记录任务相关性 gate",
    )

    official_generic_tutorial = ranker.score(
        search_result(
            title="Trae 弹窗拦截代码实现指南与实战案例",
            url="https://www.trae.ai/article/popup-blocking-guide",
            snippet="通用 JavaScript 弹窗拦截教程，使用 Trae IDE 运行示例代码。",
        ),
        broad_other_task,
    )
    require(official_generic_tutorial.source_role == SourceRole.PRIMARY, "官方域教程应保持 PRIMARY 身份")
    require(official_generic_tutorial.final_score < 20, "官方域通用教程仍由 authority 抬入采集预算")

    third_party_faq = ranker.score(
        search_result(
            title="Trae Skill 怎么用于代码审查？",
            url="https://knowledge.example.com/faq/trae-skill-review",
            snippet="Trae Skill 代码审查使用指南和完整教程。",
        ),
        broad_other_task,
    )
    require(third_party_faq.source_role == SourceRole.LOW_QUALITY, "第三方 FAQ 教程仍被当成可信二手来源")
    require(third_party_faq.final_score < 20, "第三方 FAQ 教程仍占用正常采集预算")

    official_solo_docs = ranker.score(
        search_result(
            title="Trae SOLO Agent Documentation",
            url="https://docs.trae.ai/ide/solo-coder",
            snippet=(
                "Trae SOLO Agent plans and executes complex development tasks. "
                "The documentation navigation also contains tool collections and MCP tutorials."
            ),
        ),
        broad_other_task,
    )
    require(official_solo_docs.source_role == SourceRole.PRIMARY, "SOLO Agent 官方文档未保持 PRIMARY")
    require(official_solo_docs.final_score >= 40, "SOLO Agent 官方文档被导航噪声误伤")
    require(official_solo_docs.final_score > official_terms.final_score + 20, "官方产品文档未显著高于无关 Terms")

    official_product_home = ranker.score(
        search_result(
            title="TRAE 3.0",
            url="https://www.trae.ai",
            snippet="TRAE 3.0 introduces SOLO development modes and core AI engineering capabilities.",
        ),
        broad_other_task,
    )
    require(official_product_home.source_role == SourceRole.PRIMARY, "官方产品首页未识别为 PRIMARY")
    require(official_product_home.final_score > official_terms.final_score + 30, "相关官方产品首页未高于无关 Terms")

    ecosystem_mismatch = ranker.score(
        search_result(
            title="Trae 国内版正式上线",
            url="https://technology.example.com/trae-launch",
            snippet="Trae 是面向开发者的 AI 集成开发环境，并公布了国内版本上线时间。",
        ),
        ecosystem_task,
    )
    require(ecosystem_mismatch.final_score < 20, "“集成开发环境”被误当成生态集成证据")

    same_candidate = search_result(
        title="Trae Subscription Pricing",
        url="https://www.trae.ai/pricing",
        snippet="Trae subscription prices and billing.",
    )
    pricing_score = ranker.score(same_candidate, pricing_task)
    ecosystem_score = ranker.score(same_candidate, ecosystem_task)
    other_target_score = ranker.score(
        same_candidate,
        research_task(dimension="pricing", competitor="OtherProduct"),
    )
    require(pricing_score.dimension_fit_score > ecosystem_score.dimension_fit_score, "Dimension policy 未隔离")
    require(pricing_score.final_score > other_target_score.final_score + 20, "Task target relevance 未隔离")

    ranked_first = ranker.rank([aggregate.result, official_docs.result, secondary.result], feature_task)
    ranked_second = ranker.rank([aggregate.result, official_docs.result, secondary.result], feature_task)
    first_artifacts = [
        item.to_artifact(selected=index == 0, selection_reason="test").model_dump(mode="json")
        for index, item in enumerate(ranked_first)
    ]
    second_artifacts = [
        item.to_artifact(selected=index == 0, selection_reason="test").model_dump(mode="json")
        for index, item in enumerate(ranked_second)
    ]
    require(first_artifacts == second_artifacts, "Ranking artifact 对同一输入不可复现")


class MockSearchProvider:
    name = "mock_quality_search"

    def search(self, query: str, *, count: int = 5, domain_filter: str = "") -> list[SearchHit]:
        if "第一组" in query:
            values = [
                SearchHit(
                    title="通用产品功能教程",
                    url="https://tutorial.example.com/generic-feature",
                    snippet="通用产品功能分析模板与教程。",
                ),
                SearchHit(
                    title="Trae Internal Feature Docs",
                    url="http://127.0.0.1/private",
                    snippet="Trae feature documentation.",
                ),
            ]
        else:
            values = [
                SearchHit(
                    title="Trae Official Feature Docs",
                    url="https://docs.trae.example/features",
                    snippet="Trae official feature documentation and changelog.",
                    site_name="Trae Docs",
                ),
                SearchHit(
                    title="Trae AI IDE independent review",
                    url="https://technology.example.com/trae-review",
                    snippet="Independent technology review of Trae Agent features.",
                    site_name="Technology Review",
                ),
            ]
        return values[:count]


def check_collection_integration() -> None:
    task_id = "task_search_source_quality_r1"
    task = ResearchTask(
        id="research_quality_collection",
        task_id=task_id,
        information_need_id="need_quality_collection",
        title="核实 Trae 产品能力",
        objective="采集 Trae 产品功能的一手文档或高质量实测",
        competitor="Trae",
        dimension="feature",
        query_hints=["Trae 第一组", "Trae 第二组"],
        preferred_source_types=["docs"],
        stop_condition="获得两条可靠资料",
        metadata={"confirmed_official_domains": ["trae.example"]},
    )
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "search_source_quality_r1"
    store = ArtifactStore(root)
    for artifact_type in (
        "research_plans",
        "research_tasks",
        "sources",
        "web_pages",
        "collection_attempts",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "tool_calls",
        "task_board",
        "task_records",
    ):
        store.save_many(task_id, artifact_type, [])
    store.save_many(
        task_id,
        "research_plans",
        [
            ResearchPlan(
                task_id=task_id,
                decision_question="Trae 有哪些可核实功能？",
                status=ResearchPlanStatus.NEEDS_COLLECTION,
                research_task_ids=[task.id],
                budget=ResearchBudget(max_sources_per_task=2, max_total_sources=2),
            )
        ],
    )
    store.save_many(task_id, "research_tasks", [task])
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

    fetched_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        fetched_urls.append(str(request.url))
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Trae product source</title></head><body>"
                "Trae provides Agent programming, code completion, project generation, "
                "documentation and developer workflow capabilities for software teams."
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
            enable_browser_fallback=False,
        ),
        search_provider=MockSearchProvider(),
    )
    result = collector.run_once(task_id)
    collector.close()
    require(result["status"] == "completed", str(result))
    require(result["collected_sources"] == 2, "Collector 原有 source budget 未保持")
    require(
        fetched_urls == [
            "https://docs.trae.example/features",
            "https://technology.example.com/trae-review",
        ],
        f"Quality Rank 未改变最终采集顺序/集合：{fetched_urls}",
    )
    results = store.load_many(task_id, "web_search_results")
    selected_urls = [item["url"] for item in results if item["selected_for_collection"]]
    require(set(selected_urls) == set(fetched_urls), "WebSearchResult selected 标记与实际采集不一致")
    unsafe = next(item for item in results if "127.0.0.1" in item["url"])
    require(unsafe["rejection_reason"].startswith("unsafe_url:"), "URL safety 被 Quality Rank 绕过")
    generic = next(item for item in results if "generic-feature" in item["url"])
    require(generic["rejection_reason"].startswith("quality_below_minimum:"), "低质量通用内容仍占采集预算")

    artifacts = store.load_many(task_id, "source_selection_runs")
    require(len(artifacts) == 4, "没有为全部候选记录 ranking artifact")
    required_fields = {
        "task_id",
        "research_task_id",
        "search_attempt_id",
        "search_result_id",
        "url",
        "domain",
        "source_role",
        "official_confidence",
        "relevance_score",
        "dimension_fit_score",
        "authority_score",
        "freshness_score",
        "penalties",
        "final_score",
        "selected",
        "selection_reason",
        "ranking_version",
        "created_at",
    }
    require(all(required_fields <= set(item) for item in artifacts), "Ranking artifact 字段不完整")
    require(sum(item["selected"] for item in artifacts) == 2, "Ranking artifact selected 状态错误")
    require(
        all(item["ranking_version"] == "source_quality_v1_fix2" for item in artifacts),
        "SourceSelectionRun 未显式写入当前 ranking version",
    )
    require(
        next(item for item in artifacts if item["url"].startswith("https://docs."))["quality_rank"] == 1,
        "官方 Docs 没有成为最终质量排序第一名",
    )


def main() -> None:
    check_ranker_policy()
    check_collection_integration()
    print("check_search_source_quality_r1: PASS")
    print("dimension_aware_ranking=true")
    print("community_not_low_quality=true")
    print("official_confidence_three_states=true")
    print("safety_and_budget_preserved=true")
    print("collector_selection_changed=true")
    print("ranking_artifact_reproducible=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
