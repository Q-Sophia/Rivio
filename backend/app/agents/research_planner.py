from __future__ import annotations

import json
from pathlib import Path

from app.agents.base import BaseAgent
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentContext,
    AgentResult,
    AgentRole,
    InformationNeed,
    KeyIntelligenceQuestion,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    DatasetCompatibilityAssessment,
    TaskMode,
    TaskPriority,
)


DEFAULT_DIMENSIONS = ["产品定位", "产品能力", "定价与成本", "生态与集成"]
SNAPSHOT_SOURCE_PATH = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "online_education" / "sources.json"


class ResearchPlannerAgent(BaseAgent):
    """Step6E.1 mock planner: turn one AnalysisTask into auditable research work."""

    def __init__(self, *, store: ArtifactStore):
        super().__init__(
            name="research_planner_agent",
            role=AgentRole.ORCHESTRATOR,
            input_artifacts=["analysis_tasks", "dataset_profile"],
            output_artifacts=[
                "research_plans",
                "research_kiqs",
                "research_information_needs",
                "research_tasks",
            ],
        )
        self.store = store

    def execute(self, context: AgentContext) -> AgentResult:
        task = context.task
        assessment = DatasetCompatibilityAssessment(**context.metadata["dataset_assessment"])
        dimensions = task.focus_areas or DEFAULT_DIMENSIONS
        supported = set(assessment.supported_focus_areas)
        snapshot_sources = json.loads(SNAPSHOT_SOURCE_PATH.read_text(encoding="utf-8"))
        sources_by_competitor: dict[str, list[dict]] = {}
        for source in snapshot_sources:
            sources_by_competitor.setdefault(source["competitor"], []).append(source)

        research_competitors = list(task.competitors)
        discovered_competitors: list[str] = []

        research_brief = dict(
            task.metadata.get("research_brief", {})
        )

        competitor_discovery_required = bool(
            research_brief.get(
                "competitor_discovery",
                task.metadata.get(
                    "competitor_discovery_required",
                    False,
                ),
            )
        )

        primary_target = str(
            research_brief.get("primary_target", "") or ""
        ).strip()

        # V1 discovery strategy:
        # expand only from the fixed local competitor/source catalog.
        if competitor_discovery_required and primary_target:
            primary = assessment.matched_competitor_map.get(
                primary_target,
                primary_target,
            )

            if primary in sources_by_competitor:
                for candidate in sources_by_competitor:
                    if (
                        candidate != primary
                        and candidate not in research_competitors
                    ):
                        research_competitors.append(candidate)
                        discovered_competitors.append(candidate)

        kiqs: list[KeyIntelligenceQuestion] = []
        needs: list[InformationNeed] = []
        research_tasks: list[ResearchTask] = []
        for index, dimension in enumerate(dimensions):
            priority = TaskPriority.HIGH if index < 2 else TaskPriority.MEDIUM
            kiq = KeyIntelligenceQuestion(
                task_id=task.id,
                question=f"各候选产品在{dimension}上有哪些可验证差异，这些差异如何影响用户决策？",
                decision_link=task.query,
                dimensions=[dimension],
                priority=priority,
            )
            need = InformationNeed(
                task_id=task.id,
                question_id=kiq.id,
                dimension=dimension,
                required_facts=[
                    f"每个竞品关于{dimension}的当前事实",
                    "事实对应的来源、发布时间与适用范围",
                    "能够支持横向比较的统一口径",
                ],
                preferred_source_types=["official_site", "docs", "pricing_page"],
                comparability_basis=f"使用相同的{dimension}口径比较全部候选对象",
                decision_link=task.query,
            )
            kiqs.append(kiq)
            needs.append(need)
            for competitor in research_competitors:
                canonical = assessment.matched_competitor_map.get(
                    competitor,
                    competitor if competitor in sources_by_competitor else "",
                )
                seed_sources = sources_by_competitor.get(canonical, [])
                covered = bool(seed_sources) and (
                    not task.focus_areas or dimension in supported
                )
                revalidate_seed_urls = task.mode == TaskMode.LIVE and bool(seed_sources)
                research_tasks.append(
                    ResearchTask(
                        task_id=task.id,
                        information_need_id=need.id,
                        title=f"核实 {competitor} 的{dimension}信息",
                        objective=(
                            f"从已知 URL 重新采集并核实 {competitor} 的{dimension}证据"
                            if revalidate_seed_urls
                            else f"复核人工快照中 {competitor} 的{dimension}证据"
                            if covered
                            else f"采集能够回答 {competitor} 在{dimension}方面表现的可靠资料"
                        ),
                        competitor=competitor,
                        dimension=dimension,
                        query_hints=[
                            f"{competitor} {dimension} 官方",
                            f"{competitor} {dimension} 文档",
                        ],
                        seed_urls=[item["url"] for item in seed_sources],
                        snapshot_source_ids=[item["id"] for item in seed_sources],
                        preferred_source_types=need.preferred_source_types,
                        priority=priority,
                        status=(
                            "waiting_for_collector"
                            if revalidate_seed_urls
                            else "covered_by_snapshot"
                            if covered
                            else "waiting_for_collector"
                        ),
                        stop_condition=(
                            "至少获得 1 条可追溯官方证据；若无公开信息，记录已检索范围与信息缺口。"
                        ),
                    )
                )

        waiting = [item for item in research_tasks if item.status == "waiting_for_collector"]
        plan = ResearchPlan(
            task_id=task.id,
            decision_question=task.query,
            status=(
                ResearchPlanStatus.NEEDS_COLLECTION
                if waiting
                else ResearchPlanStatus.READY_FOR_ANALYSIS
            ),
            kiq_ids=[item.id for item in kiqs],
            information_need_ids=[item.id for item in needs],
            research_task_ids=[item.id for item in research_tasks],
            covered_competitors=[
                competitor
                for competitor in research_competitors
                if assessment.matched_competitor_map.get(competitor, competitor)
                in sources_by_competitor
            ],
            missing_competitors=[
                competitor
                for competitor in research_competitors
                if assessment.matched_competitor_map.get(competitor, competitor)
                not in sources_by_competitor
            ],
            covered_dimensions=assessment.supported_focus_areas,
            missing_dimensions=assessment.unsupported_focus_areas,
            budget=ResearchBudget(),
            metadata={
                "network_used": False,
                "real_llm_used": False,
                "planning_method": "deterministic_mock_v1",
                "waiting_for_collector_count": len(waiting),
                "competitor_discovery_required": competitor_discovery_required,
                "competitor_discovery_strategy": "local_catalog_v1",
                "competitor_discovery_method": (
                    "local_snapshot_source_catalog"
                    if discovered_competitors
                    else "not_run"
                ),
                "discovered_competitors": discovered_competitors,
                "known_url_revalidation": task.mode == TaskMode.LIVE,
            },
        )
        self.store.save_many(task.id, "research_plans", [plan])
        self.store.save_many(task.id, "research_kiqs", kiqs)
        self.store.save_many(task.id, "research_information_needs", needs)
        self.store.save_many(task.id, "research_tasks", research_tasks)
        return self.make_result(
            context,
            output_summary=(
                f"生成 {len(kiqs)} 个 KIQ、{len(needs)} 个信息需求和 "
                f"{len(research_tasks)} 个研究任务，其中 {len(waiting)} 个等待采集。"
            ),
            output_artifacts={
                "research_plans": [plan.id],
                "research_kiqs": [item.id for item in kiqs],
                "research_information_needs": [item.id for item in needs],
                "research_tasks": [item.id for item in research_tasks],
            },
        )
