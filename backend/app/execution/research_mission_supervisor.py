from __future__ import annotations

from typing import Protocol

from app.agents.web_evidence import normalize_dimension
from app.execution.research_agent import build_research_agent_llm_config
from app.harness.artifacts import ArtifactStore
from app.llm.client import LLMClient
from app.schemas import (
    AgentRole,
    EvidenceCoverage,
    InformationNeed,
    ResearchGap,
    ResearchMission,
    ResearchMissionBudgetState,
    ResearchMissionDecision,
    ResearchMissionState,
    ResearchWorkerResult,
)


class MissionSupervisor(Protocol):
    def decide(
        self,
        *,
        task_id: str,
        mission: ResearchMission,
        state: ResearchMissionState,
        worker_result: ResearchWorkerResult | None,
        coverage: list[EvidenceCoverage],
        gaps: list[ResearchGap],
        budget_state: ResearchMissionBudgetState,
    ) -> ResearchMissionDecision: ...


class LLMMissionSupervisor:
    """LLM lead researcher that only emits a bounded structured decision."""

    def __init__(self, *, llm_client: LLMClient, store: ArtifactStore):
        self.llm_client = llm_client
        self.store = store

    def decide(
        self,
        *,
        task_id: str,
        mission: ResearchMission,
        state: ResearchMissionState,
        worker_result: ResearchWorkerResult | None,
        coverage: list[EvidenceCoverage],
        gaps: list[ResearchGap],
        budget_state: ResearchMissionBudgetState,
    ) -> ResearchMissionDecision:
        needs = [
            InformationNeed(**raw)
            for raw in self.store.load_many(
                task_id, "research_information_needs"
            )
            if raw.get("id") in mission.information_need_ids
        ]
        artifacts = {
            "research_mission": [
                {
                    "id": mission.id,
                    "competitor": mission.competitor,
                    "goal": mission.goal,
                    "information_need_ids": mission.information_need_ids,
                }
            ],
            "mission_state_summary": [
                {
                    "version": state.version,
                    "query_count": len(state.attempted_queries),
                    "visited_url_count": len(state.visited_urls),
                    "source_ids": state.source_ids[-12:],
                    "verified_evidence_ids": state.verified_evidence_ids[-16:],
                    "confirmed_official_domains": (
                        state.confirmed_official_domains[-8:]
                    ),
                    "outcome_by_need": state.outcome_by_need,
                    "coverage_status_by_need": state.coverage_status_by_need,
                    "remaining_need_by_id": state.remaining_need_by_id,
                }
            ],
            "research_worker_result": (
                [worker_result.model_dump(mode="json")]
                if worker_result is not None
                else []
            ),
            "mission_information_needs": [
                item.model_dump(mode="json") for item in needs
            ],
            "information_need_coverage": [
                item.model_dump(mode="json") for item in coverage
            ],
            "mission_research_gaps": [
                item.model_dump(mode="json") for item in gaps
            ],
            "mission_budget_state": [budget_state.model_dump(mode="json")],
        }
        raw, _call, _output = self.llm_client.generate_structured(
            task_id=task_id,
            agent_role=AgentRole.ORCHESTRATOR,
            agent_run_id=f"run_mission_supervisor_{mission.id}",
            node_id=f"mission_supervisor_{mission.id}_v{state.version}",
            context_bundle=None,
            output_schema="ResearchMissionDecision",
            prompt_id="research_mission_supervisor_v1",
            prompt_version="v1",
            prompt_summary=(
                "你是受严格预算约束的研究主管。根据 Mission goal、最新 Worker "
                "结构化结果、真实 InformationNeed、Coverage、ResearchGap 和预算，"
                "只决定下一步：为尚未覆盖的真实 need 创建 focused Research Unit、"
                "要求某个 need 补充证据，或 FINISH。target_need 必须逐字复用输入"
                " InformationNeed id；不得生成搜索 Query、调用工具或创建新概念。"
                "达到充分覆盖、最大轮数、unit/source/action 预算或没有可执行缺口时"
                "必须 FINISH；不要为了完美而继续研究。"
            ),
            artifacts=artifacts,
        )
        decision = ResearchMissionDecision(**raw["item"])
        if (
            decision.target_need
            and decision.target_need not in mission.information_need_ids
        ):
            raise ValueError("Supervisor target_need 不属于当前 Mission")
        return decision


def build_llm_mission_supervisor(
    *, store: ArtifactStore
) -> LLMMissionSupervisor:
    return LLMMissionSupervisor(
        llm_client=LLMClient(
            config=build_research_agent_llm_config(), store=store
        ),
        store=store,
    )


def mission_coverage_and_gaps(
    *, store: ArtifactStore, task_id: str, mission: ResearchMission
) -> tuple[list[EvidenceCoverage], list[ResearchGap]]:
    coverage = [
        EvidenceCoverage(**raw)
        for raw in store.load_many(task_id, "evidence_coverage")
        if str(raw.get("competitor") or "").casefold()
        == mission.competitor.casefold()
    ]
    dimensions = {
        normalize_dimension(item.dimension)
        for item in (
            InformationNeed(**raw)
            for raw in store.load_many(
                task_id, "research_information_needs"
            )
            if raw.get("id") in mission.information_need_ids
        )
    }
    gaps = [
        ResearchGap(**raw)
        for raw in store.load_many(task_id, "research_gaps")
        if any(
            competitor.casefold() == mission.competitor.casefold()
            for competitor in raw.get("competitors", [])
        )
        and normalize_dimension(str(raw.get("dimension") or "")) in dimensions
    ]
    return coverage, gaps
