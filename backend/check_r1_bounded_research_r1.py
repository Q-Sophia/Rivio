from __future__ import annotations

from pathlib import Path
from typing import Any

from app.execution.research_agent_coordinator import ResearchAgentCoordinator
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisTask,
    EvidenceCoverageStatus,
    ResearchAgentAction,
    ResearchAgentRun,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    ResearchTaskOutcome,
    RunStatus,
    SourceDocument,
    SourceEvidence,
    SourceType,
    TaskBoard,
    TaskStatus,
)
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeR1Service:
    """Offline R1 fixture: persists only R1 Source/Evidence/Run artifacts."""

    def __init__(
        self,
        store: ArtifactStore,
        evidence_plan: dict[tuple[str, str, int], tuple[int, bool]],
    ):
        self.store = store
        self.evidence_plan = evidence_plan
        self.calls: list[dict[str, Any]] = []

    def run_once(
        self,
        task_id: str,
        *,
        research_task_id: str,
        mode,
        acknowledge_real_llm_call: bool,
        budget,
    ) -> dict[str, Any]:
        task = next(
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
            if item.get("id") == research_task_id
        )
        self.calls.append(
            {
                "research_task_id": task.id,
                "competitor": task.competitor,
                "dimension": task.dimension,
                "collection_round": task.collection_round,
                "mode": str(mode),
                "budget": budget.model_dump(mode="json"),
            }
        )
        evidence_count, weak = self.evidence_plan.get(
            (task.competitor, task.dimension, task.collection_round),
            (0, False),
        )
        sources = [
            SourceDocument(**item)
            for item in self.store.load_many(task_id, "sources")
        ]
        evidence = [
            SourceEvidence(**item)
            for item in self.store.load_many(task_id, "evidence")
        ]
        verified_ids: list[str] = []
        for index in range(evidence_count):
            suffix = (
                f"{task.competitor}_{task.dimension}_"
                f"r{task.collection_round}_{index}"
            ).replace(" ", "_")
            source = SourceDocument(
                id=f"src_{suffix}",
                task_id=task_id,
                title=f"{task.competitor} {task.dimension} evidence",
                url=f"https://offline.test/{suffix}",
                source_type=(
                    SourceType.SOCIAL if weak else SourceType.OFFICIAL_SITE
                ),
                competitor=task.competitor,
                content_excerpt="offline bounded research fixture",
            )
            item = SourceEvidence(
                id=f"ev_{suffix}",
                task_id=task_id,
                source_id=source.id,
                competitor=task.competitor,
                dimension=task.dimension,
                snippet=f"verified evidence {suffix}",
                normalized_fact=f"verified fact {suffix}",
                confidence=0.2 if weak else 0.9,
            )
            sources.append(source)
            evidence.append(item)
            verified_ids.append(item.id)

        self.store.save_many(task_id, "sources", sources)
        self.store.save_many(task_id, "evidence", evidence)
        outcome = (
            ResearchTaskOutcome.PARTIAL
            if verified_ids
            else ResearchTaskOutcome.EXHAUSTED
        )
        run = ResearchAgentRun(
            task_id=task_id,
            research_task_id=task.id,
            status=RunStatus.COMPLETED,
            outcome=outcome,
            verified_evidence_ids=verified_ids,
        )
        runs = [
            ResearchAgentRun(**item)
            for item in self.store.load_many(task_id, "research_agent_runs")
        ]
        self.store.save_many(task_id, "research_agent_runs", [*runs, run])
        tasks = [
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
        ]
        self.store.save_many(
            task_id,
            "research_tasks",
            [
                item.model_copy(
                    update={
                        "status": (
                            "evidence_extracted"
                            if verified_ids
                            else "evidence_exhausted"
                        )
                    }
                )
                if item.id == task.id
                else item
                for item in tasks
            ],
        )
        return {"runs": [run.model_dump(mode="json")]}


def seed_case(
    store: ArtifactStore,
    *,
    task_id: str,
    competitors: list[str],
    dimensions: list[str],
    max_rounds: int,
    max_total_sources: int = 40,
) -> None:
    for artifact_type in (
        "analysis_tasks",
        "research_plans",
        "research_tasks",
        "task_board",
        "task_records",
        "sources",
        "evidence",
        "product_cards",
        "evidence_coverage",
        "research_gaps",
        "research_agent_runs",
        "research_agent_actions",
        "research_agent_observations",
        "research_agent_coordinator_runs",
        "research_agent_coordinator_events",
        "llm_calls",
        "search_attempts",
        "web_search_results",
    ):
        store.save_many(task_id, artifact_type, [])
    analysis_task = AnalysisTask(
        id=task_id,
        query="bounded research fixture",
        competitors=competitors,
        focus_areas=dimensions,
    )
    research_tasks = [
        ResearchTask(
            id=f"researchtask_{competitor}_{dimension}_round_1",
            task_id=task_id,
            information_need_id=f"need_{competitor}_{dimension}",
            title=f"Research {competitor} {dimension}",
            objective=f"Verify {competitor} {dimension}",
            competitor=competitor,
            dimension=dimension,
            status="waiting_for_collector",
            stop_condition="Reach sufficient basic coverage or bounded stop.",
            collection_round=1,
        )
        for competitor in competitors
        for dimension in dimensions
    ]
    plan = ResearchPlan(
        id=f"researchplan_{task_id}",
        task_id=task_id,
        decision_question="Is basic evidence coverage sufficient?",
        status=ResearchPlanStatus.NEEDS_COLLECTION,
        research_task_ids=[item.id for item in research_tasks],
        missing_competitors=competitors,
        missing_dimensions=dimensions,
        budget=ResearchBudget(
            max_collection_rounds=max_rounds,
            max_sources_per_task=5,
            max_total_sources=max_total_sources,
        ),
    )
    store.save_many(task_id, "analysis_tasks", [analysis_task])
    store.save_many(task_id, "research_plans", [plan])
    store.save_many(task_id, "research_tasks", research_tasks)
    TaskBoardStore(store).save_board(
        TaskBoard(task_id=task_id, status=TaskStatus.READY, tasks=[])
    )


def run_case(
    root: Path,
    *,
    name: str,
    competitors: list[str],
    dimensions: list[str],
    max_rounds: int,
    evidence_plan: dict[tuple[str, str, int], tuple[int, bool]],
):
    task_id = f"task_r1_bounded_{name}"
    store = ArtifactStore(root / name)
    seed_case(
        store,
        task_id=task_id,
        competitors=competitors,
        dimensions=dimensions,
        max_rounds=max_rounds,
    )
    fake = FakeR1Service(store, evidence_plan)
    coordinator = ResearchAgentCoordinator(
        store=store,
        research_service_factory=lambda _store: fake,
    )
    coordinator.submit(
        task_id,
        acknowledge_real_llm_call=True,
    )
    completed = coordinator.wait(task_id, timeout=20)
    require(completed.status == "completed", completed.error or completed.message)
    require(not store.load_many(task_id, "llm_calls"), "专项测试调用了 DeepSeek")
    require(
        not store.load_many(task_id, "search_attempts")
        and not store.load_many(task_id, "web_search_results"),
        "专项测试调用了 Tavily/search provider",
    )
    return task_id, store, fake, completed


def check_partial_then_sufficient(root: Path) -> None:
    task_id, store, fake, completed = run_case(
        root,
        name="partial_then_sufficient",
        competitors=["A"],
        dimensions=["pricing"],
        max_rounds=3,
        evidence_plan={
            ("A", "pricing", 1): (1, False),
            ("A", "pricing", 2): (1, False),
        },
    )
    require(
        [item["collection_round"] for item in fake.calls] == [1, 2],
        "Round1 PARTIAL 未触发 Round2 supplement",
    )
    require(
        completed.stop_reason == "coverage_sufficient",
        "Round2 SUFFICIENT 后未停止",
    )
    coverage = store.load_many(task_id, "evidence_coverage")
    require(
        coverage[0]["status"] == EvidenceCoverageStatus.SUFFICIENT.value,
        "Round2 后 Coverage 未重新计算为 SUFFICIENT",
    )
    supplement = next(
        ResearchTask(**item)
        for item in store.load_many(task_id, "research_tasks")
        if int(item.get("collection_round", 0)) == 2
    )
    require(
        supplement.assigned_role == AgentRole.RESEARCHER.value
        and supplement.research_gap_id
        and supplement.metadata.get("trigger_competitor") == "A"
        and supplement.metadata.get("trigger_dimension") == "pricing"
        and supplement.metadata.get("trigger_coverage_status") == "partial"
        and supplement.metadata.get("collection_round") == 2,
        "补采 ResearchTask 缺少 coverage/gap/round 追溯或未指派 R1",
    )


def check_max_round_stop(root: Path) -> None:
    _task_id, _store, fake, completed = run_case(
        root,
        name="max_round_stop",
        competitors=["A"],
        dimensions=["feature"],
        max_rounds=3,
        evidence_plan={
            ("A", "feature", 1): (1, True),
            ("A", "feature", 2): (1, True),
            ("A", "feature", 3): (1, True),
        },
    )
    require(
        [item["collection_round"] for item in fake.calls] == [1, 2, 3],
        "一直不足时执行轮次不正确",
    )
    require(
        completed.stop_reason == "max_collection_rounds",
        "一直不足时未按 max_collection_rounds 停止",
    )


def check_sufficient_not_supplemented(root: Path) -> None:
    _task_id, _store, fake, completed = run_case(
        root,
        name="sufficient_no_supplement",
        competitors=["A"],
        dimensions=["pricing"],
        max_rounds=3,
        evidence_plan={("A", "pricing", 1): (2, False)},
    )
    require(len(fake.calls) == 1, "SUFFICIENT 维度被重复补采")
    require(completed.stop_reason == "coverage_sufficient", "SUFFICIENT 未停止")


def check_multi_scope_only_insufficient(root: Path) -> None:
    task_id, store, fake, completed = run_case(
        root,
        name="multi_scope",
        competitors=["A", "B"],
        dimensions=["pricing", "feature"],
        max_rounds=3,
        evidence_plan={
            ("A", "pricing", 1): (2, False),
            ("A", "feature", 1): (1, False),
            ("B", "pricing", 1): (2, False),
            ("B", "feature", 1): (1, False),
            ("A", "feature", 2): (1, False),
            ("B", "feature", 2): (1, False),
        },
    )
    round_two_pairs = {
        (item["competitor"], item["dimension"])
        for item in fake.calls
        if item["collection_round"] == 2
    }
    require(
        round_two_pairs == {("A", "feature"), ("B", "feature")},
        f"多对象补采范围错误：{round_two_pairs}",
    )
    require(completed.stop_reason == "coverage_sufficient", "多对象未正常关闭")
    supplement_records = [
        item
        for item in store.load_many(task_id, "task_records")
        if item.get("metadata", {}).get("source")
        == "R1 bounded coverage supplement"
    ]
    require(
        supplement_records
        and all(
            item["target_agent_role"] == AgentRole.RESEARCHER.value
            and item["metadata"].get("coverage_id")
            and item["metadata"].get("research_gap_id")
            for item in supplement_records
        ),
        "补采 TaskBoard 记录未指向 R1 或缺少触发链",
    )


def check_source_and_action_budget_stops(root: Path) -> None:
    for budget_type in ("source", "action"):
        task_id = f"task_r1_bounded_{budget_type}_budget"
        store = ArtifactStore(root / f"{budget_type}_budget")
        seed_case(
            store,
            task_id=task_id,
            competitors=["A"],
            dimensions=["pricing"],
            max_rounds=3,
            max_total_sources=1,
        )
        if budget_type == "source":
            store.save_many(
                task_id,
                "sources",
                [
                    SourceDocument(
                        id="src_budget",
                        task_id=task_id,
                        title="budget seed",
                        url="https://offline.test/budget",
                        competitor="A",
                    )
                ],
            )
        else:
            store.save_many(
                task_id,
                "research_agent_actions",
                [
                    ResearchAgentAction(
                        id=f"action_{index}",
                        task_id=task_id,
                        research_task_id="budget_fixture",
                        action="SEARCH",
                        rationale="pre-existing action budget fixture",
                        query=f"offline budget query {index}",
                    )
                    for index in range(20)
                ],
            )
        fake = FakeR1Service(store, {})
        coordinator = ResearchAgentCoordinator(
            store=store,
            research_service_factory=lambda _store: fake,
        )
        coordinator.submit(task_id, acknowledge_real_llm_call=True)
        completed = coordinator.wait(task_id, timeout=20)
        expected = f"{budget_type}_budget_exhausted"
        require(completed.stop_reason == expected, f"{budget_type} budget 未停止")
        require(not fake.calls, f"{budget_type} budget 耗尽后仍调用 R1")


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_bounded_research_r1"
    )
    check_partial_then_sufficient(root)
    check_max_round_stop(root)
    check_sufficient_not_supplemented(root)
    check_multi_scope_only_insufficient(root)
    check_source_and_action_budget_stops(root)
    print("check_r1_bounded_research_r1: PASS")
    print("round1_partial_round2_supplement=true")
    print("round2_sufficient_stop=true")
    print("max_collection_rounds_stop=true")
    print("sufficient_not_supplemented=true")
    print("multi_scope_only_insufficient_supplemented=true")
    print("source_action_budget_stop=true")
    print("supplement_traceability=true")
    print("collector_extractor_formal_path=false")
    print("deepseek_calls=0")
    print("tavily_calls=0")


if __name__ == "__main__":
    main()
