from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Any

from app.execution.research_agent_coordinator import (
    ResearchAgentCoordinatorEvent,
    ResearchAgentCoordinatorRun,
)
from app.harness.artifacts import ArtifactStore
from app.harness.pipeline import ResearchPipelineHarness
from app.schemas import SchemaModel


class StubArtifact(SchemaModel):
    id: str


def save_stub(store: ArtifactStore, task_id: str, artifact_type: str) -> None:
    store.save_many(
        task_id,
        artifact_type,
        [StubArtifact(id=f"{artifact_type}_1")],
    )


class FakePlanningService:
    def __init__(self, store: ArtifactStore, calls: list[str]):
        self.store = store
        self.calls = calls

    def build(self, task_id: str) -> dict[str, Any]:
        self.calls.append("planning")
        for artifact_type in (
            "analysis_tasks",
            "research_plans",
            "research_kiqs",
            "research_information_needs",
            "research_tasks",
            "task_board",
        ):
            save_stub(self.store, task_id, artifact_type)
        return {"task_id": task_id}


class FakeResearchCoordinator:
    def __init__(
        self,
        store: ArtifactStore,
        calls: list[str],
        *,
        wait_for_stop: bool = False,
    ):
        self.store = store
        self.calls = calls
        self.wait_for_stop = wait_for_stop
        self.started = threading.Event()
        self.run: ResearchAgentCoordinatorRun | None = None
        self.events: list[ResearchAgentCoordinatorEvent] = []

    def submit(self, task_id: str, **_: Any) -> ResearchAgentCoordinatorRun:
        self.calls.append("research")
        self.run = ResearchAgentCoordinatorRun(
            task_id=task_id,
            status="running" if self.wait_for_stop else "completed",
            total_tasks=1,
            completed_tasks=0 if self.wait_for_stop else 1,
            progress_percent=25 if self.wait_for_stop else 100,
            message="fake research",
        )
        self.events = [
            ResearchAgentCoordinatorEvent(
                task_id=task_id,
                coordinator_run_id=self.run.id,
                sequence=1,
                event_type="started",
                message="fake research started",
            ),
            ResearchAgentCoordinatorEvent(
                task_id=task_id,
                coordinator_run_id=self.run.id,
                sequence=2,
                event_type="evidence_added",
                message="fake evidence verified",
                data={
                    "id": "evidence_1",
                    "title": "Fake evidence",
                    "url": "https://example.com/evidence",
                    "status": "verified",
                },
            ),
        ]
        if not self.wait_for_stop:
            for artifact_type in (
                "sources",
                "evidence",
                "product_cards",
                "evidence_coverage",
                "research_gaps",
                "research_agent_runs",
                "research_worker_results",
            ):
                save_stub(self.store, task_id, artifact_type)
            self.events.append(
                ResearchAgentCoordinatorEvent(
                    task_id=task_id,
                    coordinator_run_id=self.run.id,
                    sequence=3,
                    event_type="completed",
                    message="fake research completed",
                )
            )
        self.started.set()
        return self.run

    def request_stop(self, task_id: str) -> ResearchAgentCoordinatorRun:
        assert self.run is not None
        self.run = self.run.model_copy(
            update={
                "status": "stopped",
                "stop_reason": "user_requested",
                "message": "fake research stopped",
            }
        )
        self.events.append(
            ResearchAgentCoordinatorEvent(
                task_id=task_id,
                coordinator_run_id=self.run.id,
                sequence=len(self.events) + 1,
                event_type="stopped",
                message="fake research stopped",
            )
        )
        return self.run

    def reconcile_interrupted(
        self,
        _task_id: str,
    ) -> ResearchAgentCoordinatorRun | None:
        return self.run

    def get_latest_run(
        self,
        _task_id: str,
    ) -> ResearchAgentCoordinatorRun | None:
        return self.run

    def sync_evidence_events(self, _task_id: str) -> list[Any]:
        return []

    def get_events(
        self,
        _task_id: str,
        *,
        after: int = 0,
    ) -> list[ResearchAgentCoordinatorEvent]:
        return [item for item in self.events if item.sequence > after]


class FakeAnalysisService:
    def __init__(
        self,
        store: ArtifactStore,
        calls: list[str],
        *,
        fail_once: bool = False,
    ):
        self.store = store
        self.calls = calls
        self.fail_once = fail_once
        self.failed = False

    def get_payload(self, task_id: str) -> dict[str, Any]:
        return {
            "completed": bool(
                self.store.load_many(task_id, "analysis_portfolios")
            )
        }

    def run_once(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append("analysis")
        if self.fail_once and not self.failed:
            self.failed = True
            raise RuntimeError("intentional analyst failure")
        for artifact_type in (
            "analysis_portfolios",
            "brief_assessments",
            "competitor_profiles",
            "claims_v2",
            "claims",
            "analysis_evidence_coverage",
            "analysis_research_gaps",
            "analysis_assessments",
            "citation_checks",
        ):
            save_stub(self.store, task_id, artifact_type)
        return {"completed": True}


class FakeReportingService:
    def __init__(self, store: ArtifactStore, calls: list[str]):
        self.store = store
        self.calls = calls

    def get_payload(self, task_id: str) -> dict[str, Any]:
        completed = bool(self.store.load_many(task_id, "quality_gates"))
        return {
            "completed": completed,
            "stage": "completed" if completed else "awaiting_writer",
            "quality_gate": {"status": "passed"} if completed else None,
        }

    def run(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append("reporting")
        for artifact_type in (
            "reports",
            "report_statements",
            "review_feedback",
            "quality_gates",
            "feedback_tasks",
        ):
            save_stub(self.store, task_id, artifact_type)
        return self.get_payload(task_id)


def build_harness(
    root: Path,
    calls: list[str],
    *,
    wait_for_stop: bool = False,
    fail_analysis_once: bool = False,
) -> tuple[ResearchPipelineHarness, FakeResearchCoordinator]:
    store = ArtifactStore(root)
    research = FakeResearchCoordinator(
        store,
        calls,
        wait_for_stop=wait_for_stop,
    )
    analysis = FakeAnalysisService(
        store,
        calls,
        fail_once=fail_analysis_once,
    )
    reporting = FakeReportingService(store, calls)
    harness = ResearchPipelineHarness(
        store=store,
        poll_interval_seconds=0.01,
        planning_service_factory=lambda current: FakePlanningService(
            current,
            calls,
        ),
        research_coordinator_factory=lambda _current: research,
        analysis_service_factory=lambda _current: analysis,
        reporting_service_factory=lambda _current: reporting,
    )
    return harness, research


def check_automatic_pipeline(root: Path) -> None:
    calls: list[str] = []
    harness, research = build_harness(root, calls)
    run = harness.submit(
        "task_automatic",
        mode="deepseek",
        acknowledge_real_llm_call=True,
    )
    final = harness.wait("task_automatic", timeout=5)
    assert final.id == run.id
    assert str(final.status) == "completed"
    assert calls == ["planning", "research", "analysis", "reporting"]
    assert final.completed_stages == [
        "planning",
        "researching",
        "analyzing",
        "reporting",
    ]
    handoffs = harness.get_handoffs("task_automatic")
    assert [(item.sender, item.recipient) for item in handoffs] == [
        ("research_planner_agent", "research_agent_coordinator"),
        ("research_evidence_agent", "professional_research_analyst_agent"),
        ("professional_research_analyst_agent", "citation_agent"),
        ("citation_agent", "professional_writer_agent"),
        ("professional_writer_agent", "reviewer_agent"),
        ("reviewer_agent", "quality_gate_orchestrator"),
    ]
    assert all(item.protocol_version == "1.0" for item in handoffs)
    assert all(item.artifact_refs for item in handoffs)
    research_to_analyst = handoffs[1]
    assert any(
        item.artifact_type == "research_tasks"
        for item in research_to_analyst.artifact_refs
    )
    analyst_to_citation = handoffs[2]
    assert any(
        item.artifact_type == "analysis_assessments" and item.count == 1
        for item in analyst_to_citation.artifact_refs
    )
    checkpoints = harness.get_checkpoints("task_automatic")
    assert len(checkpoints) == 4
    analyzing_checkpoint = next(
        item for item in checkpoints if str(item.stage) == "analyzing"
    )
    assert any(
        item.artifact_type == "analysis_assessments" and item.count == 1
        for item in analyzing_checkpoint.artifact_refs
    )
    assert any(
        item.event_type == "evidence_added"
        for item in harness.get_events("task_automatic")
    )

    from app.api import main as api_main

    original_harness_factory = api_main.get_research_pipeline_harness
    original_coordinator_factory = api_main.get_research_agent_coordinator
    try:
        api_main.get_research_pipeline_harness = lambda: harness
        api_main.get_research_agent_coordinator = lambda: research
        payload = api_main.research_pipeline_status_payload("task_automatic")
    finally:
        api_main.get_research_pipeline_harness = original_harness_factory
        api_main.get_research_agent_coordinator = original_coordinator_factory
    assert payload["terminal"] is True
    assert payload["pipeline_run"]["status"] == "completed"
    assert payload["research_agent_coordinator_run"]["status"] == "completed"
    assert payload["research_agent_coordinator_run"]["current_stage"] == "completed"
    route_paths = {route.path for route in api_main.app.routes}
    assert "/api/analysis-tasks/{task_id}/pipeline/run" in route_paths
    assert (
        "/api/analysis-tasks/{task_id}/pipeline/run/events/stream"
        in route_paths
    )


def check_checkpoint_resume(root: Path) -> None:
    calls: list[str] = []
    harness, _ = build_harness(root, calls, fail_analysis_once=True)
    first = harness.submit(
        "task_resume",
        mode="deepseek",
        acknowledge_real_llm_call=True,
    )
    failed = harness.wait("task_resume", timeout=5)
    assert str(failed.status) == "failed"
    assert failed.completed_stages == ["planning", "researching"]

    resumed = harness.submit(
        "task_resume",
        mode="deepseek",
        acknowledge_real_llm_call=True,
    )
    final = harness.wait("task_resume", timeout=5)
    assert resumed.id == first.id == final.id
    assert str(final.status) == "completed"
    assert final.resume_count == 1
    assert calls == [
        "planning",
        "research",
        "analysis",
        "analysis",
        "reporting",
    ]
    assert final.stage_attempts["planning"] == 1
    assert final.stage_attempts["researching"] == 1
    assert final.stage_attempts["analyzing"] == 2
    assert len(harness.get_checkpoints("task_resume")) == 4
    assert any(
        item.event_type == "stage_resumed"
        for item in harness.get_events("task_resume")
    )


def check_stop_blocks_downstream_agents(root: Path) -> None:
    calls: list[str] = []
    harness, research = build_harness(root, calls, wait_for_stop=True)
    harness.submit(
        "task_stop",
        mode="deepseek",
        acknowledge_real_llm_call=True,
    )
    assert research.started.wait(timeout=2)
    harness.request_stop("task_stop")
    final = harness.wait("task_stop", timeout=5)
    assert str(final.status) == "stopped"
    assert calls == ["planning", "research"]
    assert "analyzing" not in final.completed_stages
    assert any(
        item.event_type == "pipeline_stopped"
        for item in harness.get_events("task_stop")
    )

    previous_coordinator_run_id = final.research_coordinator_run_id
    research.wait_for_stop = False
    harness.submit(
        "task_stop",
        mode="deepseek",
        acknowledge_real_llm_call=True,
    )
    resumed = harness.wait("task_stop", timeout=5)
    assert str(resumed.status) == "completed", resumed.model_dump(mode="json")
    assert resumed.research_coordinator_run_id != previous_coordinator_run_id
    assert calls == [
        "planning",
        "research",
        "research",
        "analysis",
        "reporting",
    ]
    assert sum(
        item.event_type == "research_started"
        for item in harness.get_events("task_stop")
    ) == 2


def main() -> None:
    checks_root = Path(__file__).resolve().parent / "app" / "data" / "checks"
    checks_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="pipeline_harness_check_",
        dir=checks_root,
    ) as root:
        root_path = Path(root)
        check_automatic_pipeline(root_path / "automatic")
        check_checkpoint_resume(root_path / "resume")
        check_stop_blocks_downstream_agents(root_path / "stop")
    print("unified pipeline harness checks passed")


if __name__ == "__main__":
    main()
