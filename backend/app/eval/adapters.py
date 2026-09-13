from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.execution.research_agent import ResearchEvidenceAgentService
from app.execution.research_agent_coordinator import ResearchAgentCoordinator
from app.execution.research_loop import ResearchLoopRunner
from app.harness.artifacts import ArtifactStore
from app.schemas import ExecutionMode


LegacyRunnerFactory = Callable[[ArtifactStore], Any]
AgentCoordinatorFactory = Callable[[ArtifactStore, bool], Any]
ContextAgentCoordinatorFactory = Callable[[ArtifactStore, bool, bool], Any]


class LegacyResearchAdapter:
    """Thin adapter over the real Collector -> Extractor -> Coverage loop."""

    architecture = "legacy_research_loop"

    def __init__(self, runner_factory: LegacyRunnerFactory | None = None):
        self.runner_factory = runner_factory or (
            lambda store: ResearchLoopRunner(store=store)
        )

    def run(self, *, store: ArtifactStore, task_id: str) -> dict[str, Any]:
        runner = self.runner_factory(store)
        queued = runner.submit(task_id)
        final = runner.wait(task_id, timeout=1800.0)
        return {
            "queued": queued.model_dump(mode="json"),
            "final": final.model_dump(mode="json"),
            "call_chain": [
                "ResearchLoopRunner",
                "CollectorQueueService.run_once",
                "ExtractorQueueService.run_once",
                "Step6E4QueueService.run_once",
            ],
        }


class CurrentResearchAgentAdapter:
    """Thin adapter over the production ResearchAgentCoordinator."""

    architecture = "current_research_agent"

    def __init__(
        self,
        coordinator_factory: AgentCoordinatorFactory | None = None,
    ):
        self.coordinator_factory = coordinator_factory or self._production_factory

    @staticmethod
    def _production_factory(
        store: ArtifactStore,
        supplement_enabled: bool,
    ) -> ResearchAgentCoordinator:
        return ResearchAgentCoordinator(
            store=store,
            supplement_enabled=supplement_enabled,
            research_service_factory=(
                lambda inner_store: ResearchEvidenceAgentService(
                    store=inner_store
                )
            ),
        )

    def run(
        self,
        *,
        store: ArtifactStore,
        task_id: str,
        supplement_enabled: bool,
    ) -> dict[str, Any]:
        coordinator = self.coordinator_factory(store, supplement_enabled)
        queued = coordinator.submit(
            task_id,
            mode=ExecutionMode.DEEPSEEK,
            acknowledge_real_llm_call=True,
        )
        final = coordinator.wait(task_id, timeout=1800.0)
        return {
            "queued": queued.model_dump(mode="json"),
            "final": final.model_dump(mode="json"),
            "supplement_enabled": supplement_enabled,
            "call_chain": [
                "ResearchAgentCoordinator",
                "ResearchEvidenceAgentService.run_once",
                "SEARCH/FETCH/READ/SUBMIT_EVIDENCE/FINISH",
                "ResearchAgentBoundedRefreshService.refresh",
            ],
        }


class ContextGovernanceResearchAgentAdapter:
    """Run the current production Agent with one explicit context switch."""

    architecture = "current_research_agent_context_ab"

    def __init__(
        self,
        coordinator_factory: ContextAgentCoordinatorFactory | None = None,
    ):
        self.coordinator_factory = (
            coordinator_factory or self._production_factory
        )

    @staticmethod
    def _production_factory(
        store: ArtifactStore,
        supplement_enabled: bool,
        context_governance_enabled: bool,
    ) -> ResearchAgentCoordinator:
        return ResearchAgentCoordinator(
            store=store,
            supplement_enabled=supplement_enabled,
            context_governance_enabled=context_governance_enabled,
            research_service_factory=(
                lambda inner_store: ResearchEvidenceAgentService(
                    store=inner_store,
                    context_governance_enabled=(
                        context_governance_enabled
                    ),
                )
            ),
        )

    def run(
        self,
        *,
        store: ArtifactStore,
        task_id: str,
        supplement_enabled: bool,
        context_governance_enabled: bool,
    ) -> dict[str, Any]:
        coordinator = self.coordinator_factory(
            store,
            supplement_enabled,
            context_governance_enabled,
        )
        queued = coordinator.submit(
            task_id,
            mode=ExecutionMode.DEEPSEEK,
            acknowledge_real_llm_call=True,
        )
        final = coordinator.wait(task_id, timeout=1800.0)
        return {
            "queued": queued.model_dump(mode="json"),
            "final": final.model_dump(mode="json"),
            "supplement_enabled": supplement_enabled,
            "context_governance_enabled": (
                context_governance_enabled
            ),
            "call_chain": [
                "ResearchAgentCoordinator",
                "ResearchEvidenceAgentService.run_once",
                "ResearchActionContextViewBuilder",
                "SEARCH/FETCH/READ/SUBMIT_EVIDENCE/FINISH",
                "ResearchAgentBoundedRefreshService.refresh",
            ],
        }
