from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas import AgentContext, AgentResult, AgentRole, AgentRun, RunStatus


class BaseAgent(ABC):
    """Lightweight business-agent interface.

    Adapted from the reference BaseAgent structure, but intentionally keeps
    runtime concerns out of the agent: no LLM client, no timing, no trace writes.
    """

    name: str
    role: AgentRole
    input_artifacts: list[str]
    output_artifacts: list[str]

    def __init__(
        self,
        *,
        name: str,
        role: AgentRole,
        input_artifacts: list[str] | None = None,
        output_artifacts: list[str] | None = None,
    ):
        self.name = name
        self.role = role
        self.input_artifacts = input_artifacts or []
        self.output_artifacts = output_artifacts or []

    @abstractmethod
    def execute(self, context: AgentContext) -> AgentResult:
        """Run agent-specific deterministic business logic."""

    def make_result(
        self,
        context: AgentContext,
        *,
        output_summary: str,
        output_artifacts: dict[str, list[str]] | None = None,
        status: RunStatus = RunStatus.COMPLETED,
        error: str = "",
    ) -> AgentResult:
        agent_run = AgentRun(
            id=context.metadata.get("agent_run_id", f"run_{context.node_id}"),
            task_id=context.task_id,
            node_id=context.node_id,
            agent_role=self.role,
            status=status,
        )
        return AgentResult(
            task_id=context.task_id,
            agent_run=agent_run,
            status=status,
            output_summary=output_summary,
            output_artifacts=output_artifacts or {},
            error=error,
        )
