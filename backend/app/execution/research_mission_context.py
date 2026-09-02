from __future__ import annotations

import hashlib

from app.agents.web_evidence import normalize_dimension
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    InformationNeed,
    ResearchAgentBudget,
    ResearchAgentRun,
    ResearchGap,
    ResearchMission,
    ResearchMissionBudgetState,
    ResearchMissionState,
    ResearchPlan,
    ResearchTask,
    ResearchWorkerContext,
    ResearchWorkerEvidenceContext,
    ResearchWorkerGapContext,
    ResearchWorkerNeedContext,
    ResearchWorkerSourceContext,
    SourceDocument,
    SourceEvidence,
    SourceTaskAssociation,
)


class ResearchMissionContextBuilder:
    """Build the filtered state visible to one focused R1 Worker."""

    def __init__(self, *, store: ArtifactStore):
        self.store = store

    def build(
        self,
        *,
        task_id: str,
        mission: ResearchMission,
        state: ResearchMissionState,
        research_task: ResearchTask,
        worker_budget: ResearchAgentBudget | None = None,
    ) -> ResearchWorkerContext:
        need = self._need(task_id, research_task.information_need_id)
        if need is None or need.id not in mission.information_need_ids:
            raise ValueError(
                "ResearchWorkerContext 只能引用 Mission 中真实存在的 InformationNeed"
            )
        budget = worker_budget or ResearchAgentBudget()
        dimension = normalize_dimension(need.dimension)
        mission_task_ids = set(mission.research_task_ids)
        current_need_task_ids = {
            item.id
            for item in self._research_tasks(task_id)
            if item.id in mission_task_ids
            and item.information_need_id == need.id
        }
        runs = [
            ResearchAgentRun(**raw)
            for raw in self.store.load_many(task_id, "research_agent_runs")
            if raw.get("research_task_id") in mission_task_ids
        ]
        current_terms = [
            term.term
            for run in runs
            if run.research_task_id in current_need_task_ids
            for term in run.observed_terms
        ]
        related_terms = self._unique(
            [*current_terms, *state.observed_terms]
        )[-20:]

        sources = {
            item.id: item
            for item in (
                SourceDocument(**raw)
                for raw in self.store.load_many(task_id, "sources")
            )
            if item.id in state.source_ids
            and item.competitor.casefold() == mission.competitor.casefold()
        }
        evidence = [
            item
            for item in (
                SourceEvidence(**raw)
                for raw in self.store.load_many(task_id, "evidence")
            )
            if item.id in state.verified_evidence_ids
            and item.competitor.casefold() == mission.competitor.casefold()
        ]
        evidence.sort(
            key=lambda item: normalize_dimension(item.dimension) != dimension
        )
        associated_to_need = {
            item.source_id
            for item in (
                SourceTaskAssociation(**raw)
                for raw in self.store.load_many(
                    task_id, "source_task_associations"
                )
            )
            if item.research_task_id in current_need_task_ids
        }
        evidence_source_ids = {
            item.source_id
            for item in evidence
            if normalize_dimension(item.dimension) == dimension
        }
        ordered_source_ids = self._unique(
            [
                *(
                    source_id
                    for source_id in state.source_ids
                    if source_id in associated_to_need or source_id in evidence_source_ids
                ),
                *state.source_ids,
            ]
        )
        related_source_ids = [
            source_id for source_id in ordered_source_ids if source_id in sources
        ][:8]
        gaps = [
            item
            for item in (
                ResearchGap(**raw)
                for raw in self.store.load_many(task_id, "research_gaps")
            )
            if any(
                competitor.casefold() == mission.competitor.casefold()
                for competitor in item.competitors
            )
            and normalize_dimension(item.dimension) == dimension
        ][:6]
        budget_state = self._budget_state(
            task_id=task_id,
            mission=mission,
            research_task=research_task,
            worker_budget=budget,
        )
        identity = (
            f"{task_id}|{mission.id}|{research_task.id}|{state.version}"
        )
        context = ResearchWorkerContext(
            id=(
                "researchworkercontext_"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            ),
            task_id=task_id,
            mission_id=mission.id,
            mission_goal=mission.goal,
            competitor=mission.competitor,
            current_need=ResearchWorkerNeedContext(
                id=need.id,
                question_id=need.question_id,
                dimension=normalize_dimension(need.dimension),
                required_facts=need.required_facts,
                preferred_source_types=need.preferred_source_types,
                comparability_basis=need.comparability_basis,
                decision_link=need.decision_link,
                coverage_status=state.coverage_status_by_need.get(need.id, ""),
                remaining_need=state.remaining_need_by_id.get(need.id, ""),
            ),
            related_discovered_terms=related_terms,
            confirmed_official_domains=state.confirmed_official_domains[-8:],
            related_sources=[
                ResearchWorkerSourceContext(
                    source_id=source.id,
                    title=source.title,
                    url=source.url,
                    source_type=str(source.source_type),
                    official_confidence=str(
                        source.metadata.get("official_confidence") or "unknown"
                    ),
                )
                for source_id in related_source_ids
                if (source := sources.get(source_id)) is not None
            ],
            related_verified_evidence=[
                ResearchWorkerEvidenceContext(
                    evidence_id=item.id,
                    source_id=item.source_id,
                    dimension=normalize_dimension(item.dimension),
                    fact=item.normalized_fact[:240],
                )
                for item in evidence[:12]
            ],
            previous_queries=state.attempted_queries[-5:],
            previous_query_count=len(state.attempted_queries),
            visited_urls=state.visited_urls[-5:],
            visited_url_count=len(state.visited_urls),
            remaining_gaps=[
                ResearchWorkerGapContext(
                    gap_id=item.id,
                    dimension=normalize_dimension(item.dimension),
                    missing_information=item.missing_information,
                    insufficiency_reason=(
                        item.why_existing_evidence_is_insufficient
                    ),
                )
                for item in gaps
            ],
            budget_state=budget_state,
            conversation_history_shared=False,
            metadata={
                "context_builder": "research_mission_context_v1",
                "mission_state_version": state.version,
            },
        )
        existing = [
            ResearchWorkerContext(**raw)
            for raw in self.store.load_many(task_id, "research_worker_contexts")
        ]
        self.store.save_many(
            task_id,
            "research_worker_contexts",
            [item for item in existing if item.id != context.id] + [context],
        )
        return context

    def _budget_state(
        self,
        *,
        task_id: str,
        mission: ResearchMission,
        research_task: ResearchTask,
        worker_budget: ResearchAgentBudget,
    ) -> ResearchMissionBudgetState:
        plans = self.store.load_many(task_id, "research_plans")
        plan = ResearchPlan(**plans[-1]) if plans else None
        coordinator_runs = self.store.load_many(
            task_id, "research_agent_coordinator_runs"
        )
        coordinator = coordinator_runs[-1] if coordinator_runs else {}
        max_rounds = (
            plan.budget.max_collection_rounds if plan else 3
        )
        completed_units = len(
            {
                str(raw.get("research_task_id") or "")
                for raw in self.store.load_many(task_id, "research_agent_runs")
                if raw.get("mission_id") == mission.id and raw.get("outcome")
            }
        )
        return ResearchMissionBudgetState(
            collection_round=research_task.collection_round,
            max_collection_rounds=max_rounds,
            completed_units=completed_units,
            max_units=max(1, len(mission.information_need_ids) * max_rounds),
            sources_used=len(self.store.load_many(task_id, "sources")),
            max_total_sources=(
                plan.budget.max_total_sources if plan else 40
            ),
            actions_used=len(
                self.store.load_many(task_id, "research_agent_actions")
            ),
            max_actions=int(coordinator.get("max_actions") or 100),
            worker_max_steps=worker_budget.max_steps,
            worker_max_searches=worker_budget.max_searches,
            worker_max_sources=worker_budget.max_sources,
        )

    def _need(self, task_id: str, need_id: str) -> InformationNeed | None:
        return next(
            (
                InformationNeed(**raw)
                for raw in self.store.load_many(
                    task_id, "research_information_needs"
                )
                if raw.get("id") == need_id
            ),
            None,
        )

    def _research_tasks(self, task_id: str) -> list[ResearchTask]:
        return [
            ResearchTask(**raw)
            for raw in self.store.load_many(task_id, "research_tasks")
        ]

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in values if item))
