from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from pydantic import Field

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRun,
    DAGNode,
    RunStatus,
    SchemaModel,
    ToolCall,
    utc_now,
)


class PipelineSummary(SchemaModel):
    id: str = "pipeline_summary_snapshot_agent_workflow"
    task_id: str
    sources_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    product_cards_count: int = Field(default=0, ge=0)
    claims_count: int = Field(default=0, ge=0)
    citation_checks_count: int = Field(default=0, ge=0)
    reports_count: int = Field(default=0, ge=0)
    review_feedback_count: int = Field(default=0, ge=0)
    dag_nodes_count: int = Field(default=0, ge=0)
    agent_runs_count: int = Field(default=0, ge=0)
    tool_calls_count: int = Field(default=0, ge=0)
    supported_count: int = Field(default=0, ge=0)
    weak_count: int = Field(default=0, ge=0)
    approved: bool = False
    review_score: float = Field(default=0.0, ge=0.0, le=10.0)
    pipeline_status: str = "pending"


class TraceRecorder:
    """Keeps workflow trace artifacts in memory and persists snapshots."""

    def __init__(self, *, store: ArtifactStore, task_id: str):
        self.store = store
        self.task_id = task_id
        self.dag_nodes: list[DAGNode] = []
        self.agent_runs: list[AgentRun] = []
        self.tool_calls: list[ToolCall] = []

    def set_dag_nodes(self, nodes: list[DAGNode]) -> None:
        self.dag_nodes = nodes
        self.save_dag_nodes()

    def save_dag_nodes(self) -> None:
        self.store.save_many(self.task_id, "dag_nodes", self.dag_nodes)

    def save_agent_runs(self) -> None:
        self.store.save_many(self.task_id, "agent_runs", self.agent_runs)

    def save_tool_calls(self) -> None:
        self.store.save_many(self.task_id, "tool_calls", self.tool_calls)

    def save_trace(self) -> None:
        self.save_dag_nodes()
        self.save_agent_runs()
        self.save_tool_calls()

    def record_tool_call(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        input_data: dict[str, Any] | None = None,
        output_summary: str = "",
        status: RunStatus = RunStatus.COMPLETED,
        error: str = "",
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
    ) -> ToolCall:
        started = started_at or utc_now()
        start_perf = time.perf_counter()
        completed = completed_at or utc_now()
        if duration_ms is None:
            duration_ms = max(int((time.perf_counter() - start_perf) * 1000), 0)

        tool_call = ToolCall(
            id=f"tool_{agent_run_id}_{len(self.tool_calls) + 1:03d}",
            task_id=self.task_id,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            input=input_data or {},
            output_summary=output_summary,
            status=status,
            duration_ms=duration_ms,
            error=error,
            created_at=started,
            metadata={
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
            },
        )
        self.tool_calls.append(tool_call)
        self.save_tool_calls()
        return tool_call
