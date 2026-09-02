from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.execution.research_agent_coordinator import ResearchAgentCoordinator
from app.execution.research_mission import ResearchMissionService
from app.execution.research_mission_context import ResearchMissionContextBuilder
from app.execution.research_mission_supervisor import LLMMissionSupervisor
from app.harness.artifacts import ArtifactStore
from app.llm.client import LLMClient
from app.llm.config import LLMConfig
from app.schemas import (
    EvidenceCoverage,
    InformationNeed,
    ObservedResearchTerm,
    ResearchAgentRun,
    ResearchBudget,
    ResearchGap,
    ResearchMissionDecision,
    ResearchMissionBudgetState,
    ResearchMissionState,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    SourceDocument,
    SourceEvidence,
    utc_now,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


ARTIFACTS = (
    "research_plans",
    "research_information_needs",
    "research_tasks",
    "research_missions",
    "research_mission_states",
    "research_worker_contexts",
    "research_worker_results",
    "research_mission_decisions",
    "research_agent_runs",
    "research_agent_actions",
    "research_agent_observations",
    "research_agent_coordinator_runs",
    "research_agent_coordinator_events",
    "sources",
    "evidence",
    "source_task_associations",
    "official_domain_contexts",
    "evidence_coverage",
    "research_gaps",
    "product_cards",
    "llm_calls",
    "llm_outputs",
    "search_attempts",
    "web_search_results",
)


def reset(store: ArtifactStore, task_id: str) -> None:
    for artifact_type in ARTIFACTS:
        store.save_many(task_id, artifact_type, [])


def needs(task_id: str) -> list[InformationNeed]:
    return [
        InformationNeed(
            id="need_feature",
            task_id=task_id,
            question_id="kiq_product",
            dimension="feature",
            required_facts=["current product features"],
            comparability_basis="same product scope",
            decision_link="product decision",
        ),
        InformationNeed(
            id="need_pricing",
            task_id=task_id,
            question_id="kiq_product",
            dimension="pricing",
            required_facts=["current official pricing"],
            comparability_basis="same billing period",
            decision_link="product decision",
        ),
    ]


def seed_plan(store: ArtifactStore, task_id: str) -> list[ResearchTask]:
    initial = ResearchTask(
        id="unit_feature_round_1",
        task_id=task_id,
        information_need_id="need_feature",
        title="Acme feature research",
        objective="Verify current Acme features",
        competitor="Acme",
        dimension="feature",
        status="waiting_for_collector",
        stop_condition="verified feature evidence",
        collection_round=1,
    )
    deferred = ResearchTask(
        id="unit_pricing_deferred",
        task_id=task_id,
        information_need_id="need_pricing",
        title="Acme pricing candidate",
        objective="Research current Acme pricing when selected",
        competitor="Acme",
        dimension="pricing",
        status="mission_deferred",
        stop_condition="verified pricing evidence",
        collection_round=1,
    )
    store.save_many(task_id, "research_information_needs", needs(task_id))
    store.save_many(task_id, "research_tasks", [initial, deferred])
    store.save_many(
        task_id,
        "research_plans",
        [
            ResearchPlan(
                id=f"plan_{task_id}",
                task_id=task_id,
                decision_question="Which Acme capabilities support the decision?",
                status=ResearchPlanStatus.NEEDS_COLLECTION,
                information_need_ids=["need_feature", "need_pricing"],
                research_task_ids=[initial.id, deferred.id],
                covered_competitors=["Acme"],
                covered_dimensions=["feature", "pricing"],
                budget=ResearchBudget(
                    max_collection_rounds=3,
                    max_sources_per_task=3,
                    max_total_sources=10,
                ),
            )
        ],
    )
    return [initial, deferred]


def check_context_filtering(root: Path) -> None:
    task_id = "task_kp_r2_context_filter"
    store = ArtifactStore(root / "context_filter")
    reset(store, task_id)
    tasks = seed_plan(store, task_id)
    mission_service = ResearchMissionService(store=store)
    mission = mission_service.ensure_missions(task_id)[0]
    require(
        set(mission.information_need_ids) == {"need_feature", "need_pricing"},
        "Mission 未持有多个真实 InformationNeed",
    )
    acme_source = SourceDocument(
        id="source_acme",
        task_id=task_id,
        title="Acme docs",
        url="https://docs.acme.example/product",
        source_type="docs",
        competitor="Acme",
        metadata={"official_confidence": "confirmed"},
    )
    rival_source = SourceDocument(
        id="source_rival",
        task_id=task_id,
        title="Rival docs",
        url="https://docs.rival.example/product",
        source_type="docs",
        competitor="Rival",
    )
    acme_evidence = SourceEvidence(
        id="ev_acme",
        task_id=task_id,
        source_id=acme_source.id,
        competitor="Acme",
        dimension="feature",
        snippet="Acme supports teams.",
        normalized_fact="Acme supports teams.",
    )
    rival_evidence = SourceEvidence(
        id="ev_rival",
        task_id=task_id,
        source_id=rival_source.id,
        competitor="Rival",
        dimension="feature",
        snippet="Rival supports teams.",
        normalized_fact="Rival supports teams.",
    )
    store.save_many(task_id, "sources", [acme_source, rival_source])
    store.save_many(task_id, "evidence", [acme_evidence, rival_evidence])
    store.save_many(
        task_id,
        "research_gaps",
        [
            ResearchGap(
                id="gap_acme_feature",
                task_id=task_id,
                competitors=["Acme"],
                dimension="feature",
                missing_information="official current feature detail",
                decision_blocked="feature decision",
                why_existing_evidence_is_insufficient="partial coverage",
                stop_condition="official evidence found",
            ),
            ResearchGap(
                id="gap_rival_feature",
                task_id=task_id,
                competitors=["Rival"],
                dimension="feature",
                missing_information="rival detail",
                decision_blocked="other decision",
                why_existing_evidence_is_insufficient="missing",
                stop_condition="evidence found",
            ),
        ],
    )
    raw_state = store.load_many(task_id, "research_mission_states")[0]
    state = ResearchMissionState(**raw_state).model_copy(
        update={
            "attempted_queries": [f"Acme query {index}" for index in range(8)],
            "visited_urls": [f"https://acme.example/{index}" for index in range(8)],
            "source_ids": [acme_source.id, rival_source.id],
            "verified_evidence_ids": [acme_evidence.id, rival_evidence.id],
            "observed_terms": ["Acme Workspaces", "Rival Secret"],
        }
    )
    store.save_many(task_id, "research_mission_states", [state])
    store.save_many(
        task_id,
        "research_agent_observations",
        [],
    )
    context = ResearchMissionContextBuilder(store=store).build(
        task_id=task_id,
        mission=mission,
        state=state,
        research_task=tasks[0],
    )
    payload = context.model_dump(mode="json")
    require(context.mission_goal and context.current_need.id == "need_feature", "Context goal/current need 缺失")
    require([item.source_id for item in context.related_sources] == ["source_acme"], "Context 未过滤无关 competitor Source")
    require([item.evidence_id for item in context.related_verified_evidence] == ["ev_acme"], "Context 未过滤无关 Evidence")
    require([item.gap_id for item in context.remaining_gaps] == ["gap_acme_feature"], "Context 未过滤无关 ResearchGap")
    require(len(context.previous_queries) == 5 and context.previous_query_count == 8, "Context query history 未压缩")
    require(
        not set(payload).intersection(
            {"messages", "observations", "recent_observations", "action_ids", "worker_run_ids"}
        )
        and context.conversation_history_shared is False,
        "ContextBuilder 泄漏了 Worker 完整历史",
    )
    mock_decision = LLMMissionSupervisor(
        llm_client=LLMClient(config=LLMConfig(), store=store),
        store=store,
    ).decide(
        task_id=task_id,
        mission=mission,
        state=state,
        worker_result=None,
        coverage=[],
        gaps=[],
        budget_state=ResearchMissionBudgetState(max_units=6),
    )
    require(
        mock_decision.action == "FINISH"
        and mock_decision.mission_id == mission.id,
        "Mock LLM Supervisor 未通过结构化 decision boundary",
    )
    require(
        len(store.load_many(task_id, "llm_calls")) == 1,
        "Mock Supervisor decision 未记录 LLM trace",
    )


class FakeResearchService:
    def __init__(self, store: ArtifactStore):
        self.store = store
        self.calls: list[str] = []
        self.contexts: dict[str, dict] = {}

    def run_once(self, task_id: str, *, research_task_id: str, **_kwargs):
        task = next(
            ResearchTask(**raw)
            for raw in self.store.load_many(task_id, "research_tasks")
            if raw.get("id") == research_task_id
        )
        contexts = [
            raw
            for raw in self.store.load_many(task_id, "research_worker_contexts")
            if raw.get("current_need", {}).get("id") == task.information_need_id
        ]
        self.contexts[research_task_id] = contexts[-1]
        mission = ResearchMissionService(store=self.store).mission_for_task(
            task_id, research_task_id
        )
        if not self.calls:
            source = SourceDocument(
                id="source_acme_shared",
                task_id=task_id,
                title="Acme official product docs",
                url="https://docs.acme.example/product",
                source_type="docs",
                competitor="Acme",
                metadata={
                    "research_task_id": research_task_id,
                    "official_confidence": "confirmed",
                },
            )
            self.store.save_many(task_id, "sources", [source])
        self.calls.append(research_task_id)
        run = ResearchAgentRun(
            id=f"run_{research_task_id}",
            task_id=task_id,
            research_task_id=research_task_id,
            mission_id=mission.id if mission else "",
            status="completed",
            outcome="PARTIAL" if len(self.calls) == 1 else "COMPLETE",
            attempted_queries=[f"query_{len(self.calls)}"],
            visited_urls=[f"https://docs.acme.example/{len(self.calls)}"],
            observed_terms=[
                ObservedResearchTerm(
                    task_id=task_id,
                    research_task_id=research_task_id,
                    term="Acme Workspace",
                    discovered_from="offline fixture",
                    provenance_id=f"observation_{len(self.calls)}",
                )
            ],
            remaining_need=("pricing still missing" if len(self.calls) == 1 else ""),
            step_count=2,
            search_count=1,
            source_count=1 if len(self.calls) == 1 else 0,
            completed_at=utc_now(),
        )
        runs = [
            ResearchAgentRun(**raw)
            for raw in self.store.load_many(task_id, "research_agent_runs")
        ]
        self.store.save_many(task_id, "research_agent_runs", [*runs, run])
        tasks = [
            ResearchTask(**raw)
            for raw in self.store.load_many(task_id, "research_tasks")
        ]
        self.store.save_many(
            task_id,
            "research_tasks",
            [
                item.model_copy(update={"status": "evidence_extracted"})
                if item.id == research_task_id
                else item
                for item in tasks
            ],
        )
        return {"runs": [run.model_dump(mode="json")], "status": "completed"}


class FakeCoverageService:
    def __init__(self, store: ArtifactStore):
        self.store = store
        self.calls = 0

    def refresh(self, task_id: str):
        self.calls += 1
        sufficient = self.calls >= 2
        self.store.save_many(
            task_id,
            "evidence_coverage",
            [
                EvidenceCoverage(
                    task_id=task_id,
                    competitor="Acme",
                    dimension=dimension,
                    status=("sufficient" if sufficient else "partial"),
                    limitations=("" if sufficient else "more evidence needed"),
                )
                for dimension in ("feature", "pricing")
            ],
        )
        self.store.save_many(
            task_id,
            "research_gaps",
            []
            if sufficient
            else [
                ResearchGap(
                    id="gap_pricing",
                    task_id=task_id,
                    competitors=["Acme"],
                    dimension="pricing",
                    missing_information="current official pricing",
                    decision_blocked="pricing decision",
                    why_existing_evidence_is_insufficient="not researched yet",
                    stop_condition="pricing evidence found",
                )
            ],
        )
        return {"sufficient": sufficient}


class MockMissionSupervisor:
    def __init__(self):
        self.calls: list[dict] = []

    def decide(self, *, task_id, mission, state, worker_result, **_kwargs):
        self.calls.append(
            {
                "state_version": state.version,
                "worker_result_id": worker_result.id if worker_result else "",
            }
        )
        if len(self.calls) == 1:
            return ResearchMissionDecision(
                id="decision_create_pricing",
                task_id=task_id,
                mission_id=mission.id,
                action="CREATE_RESEARCH_UNIT",
                target_need="need_pricing",
                research_goal="Verify current Acme pricing from official sources",
                reason="Pricing need remains uncovered.",
            )
        return ResearchMissionDecision(
            id="decision_finish",
            task_id=task_id,
            mission_id=mission.id,
            action="FINISH",
            reason="All Mission needs are sufficiently covered.",
        )


def check_mission_loop(root: Path) -> None:
    task_id = "task_kp_r2_mission_loop"
    store = ArtifactStore(root / "mission_loop")
    reset(store, task_id)
    seed_plan(store, task_id)
    worker = FakeResearchService(store)
    coverage = FakeCoverageService(store)
    supervisor = MockMissionSupervisor()
    coordinator = ResearchAgentCoordinator(
        store=store,
        research_service_factory=lambda _store: worker,
        coverage_service_factory=lambda _store: coverage,
        mission_supervisor_factory=lambda _store: supervisor,
    )
    coordinator.submit(task_id, acknowledge_real_llm_call=True)
    final = coordinator.wait(task_id, timeout=20)
    require(final.status == "completed", final.error or final.message)
    require(len(supervisor.calls) == 2, "Supervisor 未形成 decide→research→decide 闭环")
    decisions = store.load_many(task_id, "research_mission_decisions")
    require([item["action"] for item in decisions] == ["CREATE_RESEARCH_UNIT", "FINISH"], "Supervisor 结构化 decision 顺序错误")
    units = [
        ResearchTask(**raw)
        for raw in store.load_many(task_id, "research_tasks")
        if raw.get("metadata", {}).get("source") == "r2_mission_supervisor"
    ]
    require(len(units) == 1 and units[0].information_need_id == "need_pricing", "Supervisor 未为真实 target_need 创建下一 Research Unit")
    require(worker.calls == ["unit_feature_round_1", units[0].id], "Mission loop 未执行 Supervisor 创建的 Research Unit")
    worker2_context = worker.contexts[units[0].id]
    require(worker2_context["previous_queries"] == ["query_1"], "Worker2 未读取 Worker1 更新后的 MissionState")
    require(worker2_context["related_sources"][0]["source_id"] == "source_acme_shared", "Worker2 未复用 Worker1 Source")
    require(worker2_context["conversation_history_shared"] is False, "Worker2 继承了完整对话历史")
    state = ResearchMissionState(**store.load_many(task_id, "research_mission_states")[0])
    require(len(state.worker_result_ids) == 2 and len(state.supervisor_decision_ids) == 2, "MissionState 未保存 WorkerResult/Supervisor decision")
    require(
        len(
            [
                item
                for item in store.load_many(
                    task_id, "research_agent_coordinator_events"
                )
                if item["event_type"] == "mission_supervisor_decision"
            ]
        )
        == 2,
        "Supervisor decision 未进入现有 Coordinator trace",
    )
    require(not store.load_many(task_id, "llm_calls"), "专项测试调用了真实 LLM")
    require(not store.load_many(task_id, "search_attempts"), "专项测试调用了 Tavily")


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "kp_r2_mission_supervisor"
    )
    check_context_filtering(root)
    check_mission_loop(root)
    print("check_kp_r2_mission_supervisor: PASS")
    print("mock_supervisor_decision=true")
    print("context_filtering=true")
    print("mission_loop=true")
    print("deepseek_calls=0")
    print("tavily_calls=0")


if __name__ == "__main__":
    main()
