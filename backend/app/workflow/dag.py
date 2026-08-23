from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.base import BaseAgent
from app.agents.runtime import AgentRuntime
from app.harness.artifacts import ArtifactStore
from app.schemas import AgentContext, AnalysisTask, DAGNode, RunStatus, utc_now
from app.workflow.trace import TraceRecorder


@dataclass(frozen=True)
class StepResult:
    output_refs: list[str]
    output_summary: str


@dataclass(frozen=True)
class StepSpec:
    id: str
    label: str
    agent: BaseAgent
    input_refs: list[str]
    expected_output_refs: list[str]
    depends_on: list[str] = field(default_factory=list)

    @property
    def agent_role(self):
        return self.agent.role


class DAGExecutor:
    """Sequential executor for the fixed snapshot workflow DAG."""

    def __init__(
        self,
        *,
        task_id: str,
        task: AnalysisTask,
        store: ArtifactStore,
        recorder: TraceRecorder,
        steps: list[StepSpec],
    ):
        self.task_id = task_id
        self.task = task
        self.store = store
        self.recorder = recorder
        self.steps = steps
        self.runtime = AgentRuntime(store=store, recorder=recorder)
        self.nodes = self._build_nodes()
        self.recorder.set_dag_nodes(self.nodes)

    def execute(self) -> TraceRecorder:
        for index, step in enumerate(self.steps):
            node = self.nodes[index]
            node.status = RunStatus.RUNNING
            node.started_at = utc_now()
            self.recorder.save_dag_nodes()

            context = AgentContext(
                task_id=self.task_id,
                task=self.task,
                node_id=node.id,
                input_refs=step.input_refs,
                artifacts={"expected_output_refs": step.expected_output_refs},
            )
            result = self.runtime.run(agent=step.agent, context=context, node=node)

            node.completed_at = result.agent_run.completed_at
            if self._status_value(result.status) == RunStatus.FAILED.value:
                node.status = RunStatus.FAILED
                node.metadata = {
                    **node.metadata,
                    "error": result.error,
                }
                self.recorder.save_trace()
                break

            node.status = RunStatus.COMPLETED
            if not node.output_refs:
                node.output_refs = step.expected_output_refs
            self.recorder.save_trace()

        return self.recorder

    def _build_nodes(self) -> list[DAGNode]:
        return [
            DAGNode(
                id=step.id,
                task_id=self.task_id,
                label=step.label,
                agent_role=step.agent.role,
                status=RunStatus.PENDING,
                depends_on=step.depends_on,
                input_refs=step.input_refs,
                output_refs=[],
            )
            for step in self.steps
        ]

    @staticmethod
    def _status_value(status) -> str:
        if hasattr(status, "value"):
            return status.value
        return str(status)
