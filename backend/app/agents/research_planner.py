from __future__ import annotations

import json
from pathlib import Path

from app.agents.base import BaseAgent
from app.frameworks import (
    DEFAULT_FRAMEWORK_ID,
    DEFAULT_FRAMEWORK_VERSION,
    load_framework,
)
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentContext,
    AgentResult,
    AgentRole,
    DimensionDefinition,
    FrameworkDefinition,
    InformationNeed,
    KeyIntelligenceQuestion,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    DatasetCompatibilityAssessment,
    TaskMode,
)


SNAPSHOT_SOURCE_PATH = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "online_education" / "sources.json"


def _dimension_key(value: str) -> str:
    return "".join(str(value).strip().casefold().replace("-", "_").split())


def _dimension_aliases(dimension: DimensionDefinition) -> set[str]:
    return {
        _dimension_key(value)
        for value in (
            dimension.dimension_id,
            dimension.label,
            dimension.evidence_dimension,
            *dimension.aliases,
        )
    }


def _resolve_dimensions(
    framework: FrameworkDefinition,
    focus_areas: list[str],
) -> list[DimensionDefinition]:
    by_id = {item.dimension_id: item for item in framework.dimensions}
    if not focus_areas:
        return [by_id[item] for item in framework.default_dimension_ids]

    aliases: dict[str, DimensionDefinition] = {}
    for dimension in framework.dimensions:
        for alias in _dimension_aliases(dimension):
            aliases[alias] = dimension
    selected: list[DimensionDefinition] = []
    unknown: list[str] = []
    for focus in focus_areas:
        dimension = aliases.get(_dimension_key(focus))
        if dimension is None:
            unknown.append(focus)
        elif dimension.dimension_id not in {item.dimension_id for item in selected}:
            selected.append(dimension)
    if unknown:
        raise ValueError(
            "当前 Framework 不包含以下研究维度: " + ", ".join(unknown)
        )
    return selected


def _render(
    template: str,
    *,
    competitor: str,
    dimension: DimensionDefinition,
    decision_question: str,
) -> str:
    return template.format(
        competitor=competitor,
        dimension_label=dimension.label,
        decision_question=decision_question,
    )


class ResearchPlannerAgent(BaseAgent):
    """Turn one AnalysisTask and FrameworkDefinition into auditable work."""

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
        framework_payload = context.metadata.get("framework_definition")
        framework = (
            FrameworkDefinition(**framework_payload)
            if framework_payload
            else load_framework(DEFAULT_FRAMEWORK_ID, DEFAULT_FRAMEWORK_VERSION)
        )
        dimensions = _resolve_dimensions(framework, task.focus_areas)
        supported = {_dimension_key(item) for item in assessment.supported_focus_areas}
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
        for dimension in dimensions:
            research_intent = dimension.research_intent
            priority = dimension.priority
            kiq = KeyIntelligenceQuestion(
                task_id=task.id,
                question="；".join(
                    _render(
                        question,
                        competitor="各候选产品",
                        dimension=dimension,
                        decision_question=task.query,
                    )
                    for question in dimension.research_questions
                ),
                decision_link=task.query,
                dimensions=[dimension.evidence_dimension],
                priority=priority,
            )
            need = InformationNeed(
                task_id=task.id,
                question_id=kiq.id,
                dimension=dimension.evidence_dimension,
                research_intent=research_intent,
                required_facts=dimension.required_facts,
                preferred_source_types=dimension.preferred_source_types,
                comparability_basis=dimension.comparability_basis,
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
                    not task.focus_areas
                    or bool(_dimension_aliases(dimension) & supported)
                )
                revalidate_seed_urls = task.mode == TaskMode.LIVE and bool(seed_sources)
                research_tasks.append(
                    ResearchTask(
                        schema_version="v2",
                        task_id=task.id,
                        information_need_id=need.id,
                        title=f"核实 {competitor} 的{dimension.label}信息",
                        objective=(
                            "从已知 URL 重新采集并核实："
                            + _render(
                                dimension.objective_template,
                                competitor=competitor,
                                dimension=dimension,
                                decision_question=task.query,
                            )
                            if revalidate_seed_urls
                            else "复核人工快照："
                            + _render(
                                dimension.objective_template,
                                competitor=competitor,
                                dimension=dimension,
                                decision_question=task.query,
                            )
                            if covered
                            else _render(
                                dimension.objective_template,
                                competitor=competitor,
                                dimension=dimension,
                                decision_question=task.query,
                            )
                        ),
                        competitor=competitor,
                        dimension=dimension.evidence_dimension,
                        research_intent=research_intent,
                        query_hints=[
                            _render(
                                template,
                                competitor=competitor,
                                dimension=dimension,
                                decision_question=task.query,
                            )
                            for template in dimension.query_templates
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
                        stop_condition="；".join(dimension.completion_criteria),
                        framework_id=framework.framework_id,
                        framework_version=framework.version,
                        framework_dimension_id=dimension.dimension_id,
                        framework_content_hash=framework.content_hash,
                        metadata={
                            "framework_dimension_label": dimension.label,
                        },
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
                "planning_method": "framework_registry_v1",
                "framework_id": framework.framework_id,
                "framework_version": framework.version,
                "framework_content_hash": framework.content_hash,
                "framework_dimension_ids": [
                    item.dimension_id for item in dimensions
                ],
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
