from __future__ import annotations

import time

from app.agents.base import BaseAgent
from app.harness.artifacts import ArtifactStore
from app.schemas import AgentContext, AgentResult, AgentRun, DAGNode, RunStatus, utc_now
from app.workflow.trace import TraceRecorder


class AgentRuntime:
    """Runs one BaseAgent and owns lifecycle trace.

    This is the local, no-LLM version of the runtime layer: it creates and
    persists AgentRun records, catches errors, and writes DAG output refs.
    """

    def __init__(self, *, store: ArtifactStore, recorder: TraceRecorder):
        self.store = store
        self.recorder = recorder

    def run(
        self,
        *,
        agent: BaseAgent,
        context: AgentContext,
        node: DAGNode,
    ) -> AgentResult:
        started_at = utc_now()
        start = time.perf_counter()
        agent_run = AgentRun(
            id=f"run_{node.id}",
            task_id=context.task_id,
            node_id=node.id,
            agent_role=agent.role,
            status=RunStatus.RUNNING,
            input_summary=self._input_summary(context),
            started_at=started_at,
            created_at=started_at,
        )
        self.recorder.agent_runs.append(agent_run)
        self.recorder.save_agent_runs()

        runtime_context = context.model_copy(
            update={
                "metadata": {
                    **context.metadata,
                    "agent_run_id": agent_run.id,
                    "agent_name": agent.name,
                }
            }
        )

        try:
            result = agent.execute(runtime_context)
        except Exception as exc:
            completed_at = utc_now()
            duration_ms = int((time.perf_counter() - start) * 1000)
            error = f"{type(exc).__name__}: {exc}"
            agent_run.status = RunStatus.FAILED
            agent_run.completed_at = completed_at
            agent_run.duration_ms = duration_ms
            agent_run.error = error
            self.recorder.save_agent_runs()
            return AgentResult(
                task_id=context.task_id,
                agent_run=agent_run,
                status=RunStatus.FAILED,
                output_summary="",
                error=error,
            )

        completed_at = utc_now()
        duration_ms = int((time.perf_counter() - start) * 1000)
        agent_run.status = result.status
        agent_run.completed_at = completed_at
        agent_run.duration_ms = duration_ms
        agent_run.output_summary = result.output_summary
        agent_run.error = result.error
        node.output_refs = list(result.output_artifacts.keys())
        self.recorder.save_agent_runs()
        return result.model_copy(update={"agent_run": agent_run})

    @staticmethod
    def _input_summary(context: AgentContext) -> str:
        if not context.input_refs:
            return "No input artifacts."
        return "Input artifacts: " + ", ".join(context.input_refs)
