from __future__ import annotations

import csv
import json
import shutil
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.eval.adapters import CurrentResearchAgentAdapter, LegacyResearchAdapter
from app.eval.cases import EvalCase, load_eval_cases
from app.eval.frozen import FrozenInputManager
from app.eval.runner import EvaluationRunner
from app.frameworks import DEFAULT_FRAMEWORK_ID, DEFAULT_FRAMEWORK_VERSION, get_framework_registry
from app.harness.artifacts import ArtifactStore
from app.intake.step6e4 import Step6E4RefreshService, _refresh_bounded_gap_state
from app.schemas import (
    AgentRole,
    AnalysisTask,
    CollectionAttempt,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    InformationNeed,
    KeyIntelligenceQuestion,
    LLMCall,
    ResearchActionType,
    ResearchAgentAction,
    ResearchBudget,
    ResearchLoopRun,
    ResearchLoopRunStatus,
    ResearchLoopStopReason,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    RunStatus,
    SearchAttempt,
    SourceDocument,
    SourceEvidence,
    SourceType,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
    ToolCall,
    WebSearchResult,
    utc_now,
)
from app.execution.research_agent_coordinator import (
    ResearchAgentCoordinatorEvent,
    ResearchAgentCoordinatorRun,
)


@contextmanager
def writable_workspace(parent: Path):
    """Windows-safe temporary workspace without tempfile's restrictive mode."""
    path = parent / f"rivio_eval_r1_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakePrepareAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def prepare(self, case: EvalCase, store: ArtifactStore) -> dict[str, Any]:
        self.calls += 1
        framework = get_framework_registry().load_framework(
            DEFAULT_FRAMEWORK_ID,
            DEFAULT_FRAMEWORK_VERSION,
        )
        dimension = framework.dimensions[0]
        task = AnalysisTask(
            id=f"frozen_{case.id}",
            query=case.request,
            competitors=["Alpha", "Beta"],
            industry="离线评测",
            focus_areas=[dimension.label],
            metadata={
                "research_brief": {
                    "research_mode": "comparison",
                    "comparison_targets": ["Alpha", "Beta"],
                    "research_gap_tracking": True,
                }
            },
        )
        kiq = KeyIntelligenceQuestion(
            task_id=task.id,
            question="Alpha 与 Beta 的定位有何差异？",
            decision_link=task.query,
            dimensions=[str(dimension.evidence_dimension)],
        )
        need = InformationNeed(
            task_id=task.id,
            question_id=kiq.id,
            dimension=str(dimension.evidence_dimension),
            research_intent=dimension.research_intent,
            required_facts=list(dimension.required_facts),
            preferred_source_types=[str(item) for item in dimension.preferred_source_types],
            comparability_basis=dimension.comparability_basis,
            decision_link=task.query,
        )
        tasks = [
            ResearchTask(
                schema_version="v2",
                id=f"research_{case.id}_{competitor.casefold()}",
                task_id=task.id,
                information_need_id=need.id,
                title=f"核实 {competitor} 产品定位",
                objective=f"核实 {competitor} 产品定位与目标用户",
                competitor=competitor,
                dimension=str(dimension.evidence_dimension),
                research_intent=dimension.research_intent,
                query_hints=[f"{competitor} official positioning"],
                preferred_source_types=["official_site", "docs"],
                stop_condition="取得可验证直接证据",
                framework_id=framework.framework_id,
                framework_version=framework.version,
                framework_dimension_id=dimension.dimension_id,
                framework_content_hash=framework.content_hash,
            )
            for competitor in task.competitors
        ]
        plan = ResearchPlan(
            task_id=task.id,
            decision_question=task.query,
            status=ResearchPlanStatus.NEEDS_COLLECTION,
            kiq_ids=[kiq.id],
            information_need_ids=[need.id],
            research_task_ids=[item.id for item in tasks],
            missing_competitors=list(task.competitors),
            missing_dimensions=[str(dimension.evidence_dimension)],
            budget=ResearchBudget(
                max_collection_rounds=2,
                max_sources_per_task=2,
                max_total_sources=8,
                max_real_llm_calls=20,
            ),
            planner_provider="fake_planner_no_api",
        )
        records = [
            TaskRecord(
                id=f"queue_{item.id}",
                task_id=task.id,
                task_key=item.id,
                task_type=TaskType.SUPPLEMENT_COLLECTION,
                target_agent_role=AgentRole.COLLECTOR,
                status=TaskStatus.READY,
                reason=item.objective,
            )
            for item in tasks
        ]
        board = TaskBoard(task_id=task.id, status=TaskStatus.READY, tasks=records)
        return {
            "request": {"case": case.id, "request": case.request},
            "intent_draft": {
                "id": f"draft_{case.id}",
                "request_text": case.request,
                "competitors": task.competitors,
                "ready_for_confirmation": True,
            },
            "analysis_task": task.model_dump(mode="json"),
            "research_brief": task.metadata["research_brief"],
            "research_plan": plan.model_dump(mode="json"),
            "kiqs": [kiq.model_dump(mode="json")],
            "information_needs": [need.model_dump(mode="json")],
            "research_tasks": [item.model_dump(mode="json") for item in tasks],
            "framework_definition": framework.model_dump(mode="json"),
            "task_board": board.model_dump(mode="json"),
            "prepare_trace": {
                "intent_runs": 1,
                "planner_runs": 1,
                "provider": "fake_prepare_no_api",
                "source_task_id": task.id,
            },
        }


class FakeSearchProvider:
    name = "fake_eval_provider"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, query: str) -> list[dict[str, str]]:
        self.calls.append(query)
        return [
            {
                "title": f"{query} source",
                "url": f"https://example.com/{len(self.calls)}",
                "snippet": f"Verified text for {query}",
            }
        ]


def _save_common_research_artifacts(
    store: ArtifactStore,
    task_id: str,
    *,
    provider: FakeSearchProvider,
    agent_actions: bool,
) -> tuple[list[SourceDocument], list[SourceEvidence]]:
    tasks = store.load_many(task_id, "research_tasks")
    sources: list[SourceDocument] = []
    evidence: list[SourceEvidence] = []
    attempts: list[SearchAttempt] = []
    results: list[WebSearchResult] = []
    collections: list[CollectionAttempt] = []
    tool_calls: list[ToolCall] = []
    actions: list[ResearchAgentAction] = []
    for index, task in enumerate(tasks[:2], start=1):
        query = str(task["query_hints"][0])
        hit = provider.search(query)[0]
        attempt = SearchAttempt(
            task_id=task_id,
            research_task_id=task["id"],
            provider=provider.name,
            query=query,
            status="completed",
            result_count=1,
        )
        result = WebSearchResult(
            task_id=task_id,
            research_task_id=task["id"],
            search_attempt_id=attempt.id,
            provider=provider.name,
            query=query,
            rank=1,
            title=hit["title"],
            url=hit["url"],
            snippet=hit["snippet"],
            selected_for_collection=True,
        )
        source = SourceDocument(
            id=f"src_{index}_{task_id}",
            task_id=task_id,
            title=hit["title"],
            url=hit["url"],
            source_type=SourceType.OFFICIAL_SITE,
            competitor=task["competitor"],
            content_excerpt=hit["snippet"],
            metadata={
                "first_party": index == 1,
                "official_confidence": "confirmed" if index == 1 else "rejected",
            },
        )
        item = SourceEvidence(
            id=f"ev_{index}_{task_id}",
            task_id=task_id,
            source_id=source.id,
            competitor=task["competitor"],
            dimension=task["dimension"],
            snippet=hit["snippet"],
            normalized_fact=hit["snippet"],
            confidence=0.9,
            extraction_method="fake_provider_exact_quote",
            metadata={"quote_verified": True, "research_task_id": task["id"]},
        )
        collection = CollectionAttempt(
            task_id=task_id,
            research_task_id=task["id"],
            requested_url=source.url,
            final_url=source.url,
            status="completed",
            http_status=200,
            source_document_id=source.id,
        )
        call = ToolCall(
            task_id=task_id,
            agent_run_id=f"fake_run_{task['id']}",
            tool_name="web_search",
            input={
                "query": query,
                "research_task_id": task["id"],
                "transport": "fake_provider",
            },
            output_summary="one deterministic fake result",
        )
        if agent_actions:
            for action in ResearchActionType:
                actions.append(
                    ResearchAgentAction(
                        task_id=task_id,
                        research_task_id=task["id"],
                        action=action,
                        rationale="deterministic offline test",
                        query=query if action == ResearchActionType.SEARCH else "",
                        url=source.url if action == ResearchActionType.FETCH else "",
                        source_id=source.id if action == ResearchActionType.READ else "",
                        chunk_id="chunk_fake" if action == ResearchActionType.SUBMIT_EVIDENCE else "",
                        exact_quote=item.snippet if action == ResearchActionType.SUBMIT_EVIDENCE else "",
                        supports=item.normalized_fact if action == ResearchActionType.SUBMIT_EVIDENCE else "",
                        finish_status="COMPLETE" if action == ResearchActionType.FINISH else "",
                    )
                )
        attempts.append(attempt)
        results.append(result)
        sources.append(source)
        evidence.append(item)
        collections.append(collection)
        tool_calls.append(call)
    store.save_many(task_id, "search_attempts", attempts)
    store.save_many(task_id, "web_search_results", results)
    store.save_many(task_id, "collection_attempts", collections)
    store.save_many(task_id, "sources", sources)
    store.save_many(task_id, "evidence", evidence)
    store.save_many(task_id, "tool_calls", tool_calls)
    if agent_actions:
        store.save_many(task_id, "research_agent_actions", actions)
    return sources, evidence


class FakeLegacyAdapter:
    def __init__(self, provider: FakeSearchProvider) -> None:
        self.provider = provider
        self.calls = 0

    def run(self, *, store: ArtifactStore, task_id: str) -> dict[str, Any]:
        self.calls += 1
        _sources, evidence = _save_common_research_artifacts(
            store,
            task_id,
            provider=self.provider,
            agent_actions=False,
        )
        tasks = store.load_many(task_id, "research_tasks")
        coverage = [
            EvidenceCoverage(
                task_id=task_id,
                competitor=task["competitor"],
                dimension=task["dimension"],
                status=EvidenceCoverageStatus.SUFFICIENT,
                evidence_ids=[evidence[index].id],
            )
            for index, task in enumerate(tasks[:2])
        ]
        store.save_many(task_id, "evidence_coverage", coverage)
        run = ResearchLoopRun(
            task_id=task_id,
            research_plan_id=store.load_many(task_id, "research_plans")[-1]["id"],
            status=ResearchLoopRunStatus.COMPLETED,
            stop_reason=ResearchLoopStopReason.COVERAGE_SUFFICIENT,
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        store.save_many(task_id, "research_loop_runs", [run])
        return {"final": run.model_dump(mode="json"), "provider": self.provider.name}


class FakeAgentAdapter:
    def __init__(self, provider: FakeSearchProvider) -> None:
        self.provider = provider
        self.calls: list[bool] = []

    def run(
        self,
        *,
        store: ArtifactStore,
        task_id: str,
        supplement_enabled: bool,
    ) -> dict[str, Any]:
        self.calls.append(supplement_enabled)
        _sources, evidence = _save_common_research_artifacts(
            store,
            task_id,
            provider=self.provider,
            agent_actions=True,
        )
        initial_tasks = [
            ResearchTask(**item).model_copy(update={"status": "research_complete"})
            for item in store.load_many(task_id, "research_tasks")
        ]
        store.save_many(task_id, "research_tasks", initial_tasks)
        initial_coverage = [
            EvidenceCoverage(
                task_id=task_id,
                competitor=item.competitor,
                dimension=item.dimension,
                status=(
                    EvidenceCoverageStatus.SUFFICIENT
                    if index == 0
                    else EvidenceCoverageStatus.PARTIAL
                ),
                evidence_ids=[evidence[index].id],
                limitations="fake bounded gap" if index else "",
            )
            for index, item in enumerate(initial_tasks)
        ]
        store.save_many(task_id, "evidence_coverage", initial_coverage)
        queue = Step6E4RefreshService(store=store)
        initial_summary = _refresh_bounded_gap_state(
            store=store,
            task_id=task_id,
            coverage=initial_coverage,
            total_source_count=2,
            publish_research_tasks=queue._publish_research_tasks,
            supplement_enabled=supplement_enabled,
        )
        started_at = utc_now()
        first_event_time = started_at + timedelta(milliseconds=5)
        events = [
            ResearchAgentCoordinatorEvent(
                task_id=task_id,
                coordinator_run_id=f"coord_{task_id}",
                sequence=1,
                event_type="coverage_refreshed",
                message="offline initial coverage",
                data={"coverage_summary": {**initial_summary, "projection": {"verified_evidence_count": 2}}},
                created_at=first_event_time,
            )
        ]
        llm_calls = [
            LLMCall(
                task_id=task_id,
                agent_role=AgentRole.RESEARCHER,
                metadata={"input_tokens": 10, "output_tokens": 5},
                created_at=started_at,
            )
        ]
        final_summary = initial_summary
        if supplement_enabled:
            final_coverage = [
                item.model_copy(update={"status": EvidenceCoverageStatus.SUFFICIENT})
                for item in initial_coverage
            ]
            store.save_many(task_id, "evidence_coverage", final_coverage)
            final_summary = _refresh_bounded_gap_state(
                store=store,
                task_id=task_id,
                coverage=final_coverage,
                total_source_count=2,
                publish_research_tasks=queue._publish_research_tasks,
                supplement_enabled=True,
            )
            events.append(
                ResearchAgentCoordinatorEvent(
                    task_id=task_id,
                    coordinator_run_id=f"coord_{task_id}",
                    sequence=2,
                    event_type="coverage_refreshed",
                    message="offline final coverage",
                    data={"coverage_summary": {**final_summary, "projection": {"verified_evidence_count": 2}}},
                    created_at=first_event_time + timedelta(milliseconds=5),
                )
            )
            llm_calls.append(
                LLMCall(
                    task_id=task_id,
                    agent_role=AgentRole.RESEARCHER,
                    metadata={"input_tokens": 6, "output_tokens": 3},
                    created_at=first_event_time + timedelta(milliseconds=2),
                )
            )
            extra_tool = ToolCall(
                task_id=task_id,
                agent_run_id="fake_supplement",
                tool_name="web_search",
                input={"query": "fake supplement", "transport": "fake_provider"},
                created_at=first_event_time + timedelta(milliseconds=2),
            )
            store.save_many(
                task_id,
                "tool_calls",
                [
                    ToolCall(**item)
                    for item in store.load_many(task_id, "tool_calls")
                ]
                + [extra_tool],
            )
        store.save_many(task_id, "llm_calls", llm_calls)
        final_time = events[-1].created_at + timedelta(milliseconds=2)
        run = ResearchAgentCoordinatorRun(
            id=f"coord_{task_id}",
            task_id=task_id,
            status="completed",
            result_status="COMPLETE",
            supplement_enabled=supplement_enabled,
            started_at=started_at,
            completed_at=final_time,
            stop_reason="coverage_sufficient" if supplement_enabled else "no_runnable_supplement",
        )
        store.save_many(task_id, "research_agent_coordinator_events", events)
        store.save_many(task_id, "research_agent_coordinator_runs", [run])
        return {
            "final": run.model_dump(mode="json"),
            "provider": self.provider.name,
            "initial_summary": initial_summary,
            "final_summary": final_summary,
        }


class FakeModel:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return self.payload


class FakeLegacyRunner:
    def submit(self, task_id: str) -> FakeModel:
        return FakeModel({"task_id": task_id, "status": "queued"})

    def wait(self, task_id: str, timeout: float) -> FakeModel:
        return FakeModel({"task_id": task_id, "status": "completed"})


class FakeCoordinator(FakeLegacyRunner):
    def submit(self, task_id: str, **_kwargs) -> FakeModel:
        return super().submit(task_id)


def main() -> None:
    cases = load_eval_cases()
    require(len(cases) == 5, "eval_cases load test failed")
    require(cases[4].id == "case_05" and "企业大模型" in cases[4].request, "case manifest incorrect")

    # Keep the smoke workspace outside app/data.  Some Windows development
    # setups protect that tree while a local API process is serving it.
    checks_root = Path(__file__).resolve().parents[1] / ".eval_checks"
    checks_root.mkdir(parents=True, exist_ok=True)
    with writable_workspace(checks_root) as temp:
        workspace = Path(temp)
        prepare_adapter = FakePrepareAdapter()
        frozen = FrozenInputManager(
            workspace_root=workspace,
            prepare_adapter=prepare_adapter,
        )
        first = frozen.prepare_case(cases[0])
        second = frozen.prepare_case(cases[0])
        require(prepare_adapter.calls == 1, "prepare idempotency failed")
        require(not first["reused"] and second["reused"], "prepare reuse flag failed")
        loaded = frozen.load_case(cases[0].id)
        require(loaded["research_tasks"] and loaded["information_needs"], "frozen save/load failed")
        require(first["frozen_input_hash"] == second["frozen_input_hash"], "frozen hash changed")

        legacy_contract = LegacyResearchAdapter(
            runner_factory=lambda _store: FakeLegacyRunner()
        ).run(store=ArtifactStore(workspace / "legacy_contract"), task_id="legacy_contract")
        require(legacy_contract["final"]["status"] == "completed", "Legacy adapter fake failed")
        agent_contract = CurrentResearchAgentAdapter(
            coordinator_factory=lambda _store, _enabled: FakeCoordinator()
        ).run(
            store=ArtifactStore(workspace / "agent_contract"),
            task_id="agent_contract",
            supplement_enabled=False,
        )
        require(agent_contract["final"]["status"] == "completed", "Agent adapter fake failed")

        provider = FakeSearchProvider()
        legacy = FakeLegacyAdapter(provider)
        agent = FakeAgentAdapter(provider)
        runner = EvaluationRunner(
            workspace_root=workspace,
            frozen_manager=frozen,
            legacy_adapter=legacy,
            agent_adapter=agent,
        )
        run_dir = runner.run_all(case_id="case_01")
        require(legacy.calls == 1, "E2 Legacy adapter was not called")
        require(agent.calls == [True, False, True], "Agent variants not isolated or ordered")
        off = json.loads((run_dir / "e3/case_01/supplement_off/metrics.json").read_text(encoding="utf-8"))
        on = json.loads((run_dir / "e3/case_01/supplement_on/metrics.json").read_text(encoding="utf-8"))
        require(off["supplement_task_count"] == 0, "supplement OFF created task")
        require(on["supplement_task_count"] > 0, "supplement ON skipped production path")
        require(on["supplement_provenance_valid"], str(on["supplement_provenance_errors"]))
        require(on["extra_tool_calls"] == 1 and on["extra_tokens"] == 9, "E3 aggregation failed")
        require(provider.calls, "Fake Provider end-to-end smoke did not search")

        for name in (
            "e2_case_results.json",
            "e3_case_results.json",
            "e2_summary.csv",
            "e3_summary.csv",
            "eval_summary.md",
            "e2_architecture_difference.md",
        ):
            require((run_dir / name).is_file(), f"missing generated output: {name}")
        e2_json = json.loads((run_dir / "e2_case_results.json").read_text(encoding="utf-8"))
        e3_json = json.loads((run_dir / "e3_case_results.json").read_text(encoding="utf-8"))
        require(len(e2_json) == 2 and len(e3_json) == 2, "JSON generation failed")
        with (run_dir / "e2_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
            require(len(list(csv.DictReader(handle))) == 2, "CSV generation failed")
        summary = (run_dir / "eval_summary.md").read_text(encoding="utf-8")
        require("不生成实验结论" in summary, "partial run generated conclusions")
        require(
            "# E2 Aggregate by Variant" in summary
            and "# E3 Aggregate by Variant" in summary,
            "variant aggregate sections missing",
        )

    print("check_rivio_eval_r1: PASS")
    print("eval_cases_load=true")
    print("frozen_input_save_load=true")
    print("prepare_idempotency=true")
    print("legacy_adapter_fake=true")
    print("agent_adapter_fake=true")
    print("supplement_off_zero=true")
    print("supplement_on_production_path=true")
    print("metrics_csv_json_summary=true")
    print("fake_provider_e2e=true")
    print("real_llm_calls=0")
    print("real_search_calls=0")


if __name__ == "__main__":
    main()
