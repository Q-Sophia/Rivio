from __future__ import annotations

from collections.abc import Callable

from app.agents.runtime import AgentRuntime
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentContext,
    AnalysisTask,
    DAGNode,
    RunStatus,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    utc_now,
)
from app.workflow.dag import StepSpec
from app.workflow.taskboard import TaskBoardStore, status_value
from app.workflow.trace import TraceRecorder


class TaskBoardDrivenDAGExecutor:
    """Execute ready TaskRecord items while preserving DAG trace artifacts.

    This is the first dynamic-DAG bridge: the workflow is still the six known
    snapshot steps, but scheduling now comes from TaskBoard readiness and
    dependency state instead of a hard-coded for-loop.
    """

    def __init__(
        self,
        *,
        task_id: str,
        task: AnalysisTask,
        store: ArtifactStore,
        recorder: TraceRecorder,
        task_board_store: TaskBoardStore,
        steps: list[StepSpec],
        progress_callback: Callable[[dict], None] | None = None,
    ):
        self.task_id = task_id
        self.task = task
        self.store = store
        self.recorder = recorder
        self.task_board_store = task_board_store
        self.steps_by_key = {step.id: step for step in steps}
        self.runtime = AgentRuntime(store=store, recorder=recorder)
        self.progress_callback = progress_callback
        self.nodes_by_id: dict[str, DAGNode] = {}

    def execute(self) -> TraceRecorder:
        board = self.task_board_store.require_board(self.task_id)
        self._materialize_dag_nodes(board)

        while True:
            board = self.task_board_store.mark_ready_tasks(self.task_id)
            if self._is_finished(board):
                break

            ready_record = self._next_ready_record(board)
            if ready_record is None:
                self._mark_board_blocked(board)
                break

            if ready_record.task_key not in self.steps_by_key:
                self._fail_unsupported_task(ready_record)
                break

            self._run_record(ready_record)

        self.recorder.save_trace()
        return self.recorder

    def _materialize_dag_nodes(self, board: TaskBoard) -> None:
        nodes: list[DAGNode] = []
        updated_records: list[TaskRecord] = []
        for record in board.tasks:
            if record.task_key not in self.steps_by_key:
                updated_records.append(record)
                continue

            node_id = record.node_id or record.task_key
            record.node_id = node_id
            step = self.steps_by_key[record.task_key]
            nodes.append(
                DAGNode(
                    id=node_id,
                    task_id=self.task_id,
                    label=record.task_key,
                    agent_role=step.agent.role,
                    status=self._to_run_status(record.status),
                    depends_on=list(record.depends_on),
                    input_refs=record.input_refs or step.input_refs,
                    output_refs=[] if status_value(record.status) != TaskStatus.COMPLETED.value else list(record.output_refs),
                    started_at=record.started_at,
                    completed_at=record.completed_at,
                    metadata={
                        "task_record_id": record.id,
                        "task_key": record.task_key,
                    },
                )
            )
            updated_records.append(record)

        board.tasks = updated_records
        board.updated_at = utc_now()
        self.task_board_store.save_board(board)
        self.nodes_by_id = {node.id: node for node in nodes}
        self.recorder.set_dag_nodes(nodes)

    def _run_record(self, record: TaskRecord) -> None:
        step = self.steps_by_key[record.task_key]
        node = self.nodes_by_id[record.node_id or record.task_key]

        self.task_board_store.update_status(
            self.task_id,
            record.task_key,
            TaskStatus.CLAIMED,
            claimed_by_agent=step.agent.name,
            node_id=node.id,
        )
        running_board = self.task_board_store.update_status(
            self.task_id,
            record.task_key,
            TaskStatus.RUNNING,
            claimed_by_agent=step.agent.name,
            node_id=node.id,
        )
        record = self._record_from_board(running_board, record.task_key)
        record.attempts += 1
        self.task_board_store.upsert_record(self.task_id, record)
        self._emit_progress(record.task_key, "running", step.agent.name)

        node.status = RunStatus.RUNNING
        node.started_at = node.started_at or utc_now()
        self.recorder.save_dag_nodes()

        context = AgentContext(
            task_id=self.task_id,
            task=self.task,
            node_id=node.id,
            input_refs=record.input_refs or step.input_refs,
            artifacts={"expected_output_refs": step.expected_output_refs},
            metadata={
                "task_record_id": record.id,
                "task_key": record.task_key,
                "scheduler": "task_board",
            },
        )
        result = self.runtime.run(agent=step.agent, context=context, node=node)

        node.completed_at = result.agent_run.completed_at
        if self._status_value(result.status) == RunStatus.FAILED.value:
            node.status = RunStatus.FAILED
            node.metadata = {**node.metadata, "error": result.error}
            self.recorder.save_trace()
            self.task_board_store.update_status(
                self.task_id,
                record.task_key,
                TaskStatus.FAILED,
                output_refs=node.output_refs,
                error=result.error,
                claimed_by_agent=step.agent.name,
                node_id=node.id,
            )
            self._emit_progress(record.task_key, "failed", result.error or "执行失败")
            return

        node.status = RunStatus.COMPLETED
        if not node.output_refs:
            node.output_refs = list(step.expected_output_refs)
        self.recorder.save_trace()
        self.task_board_store.update_status(
            self.task_id,
            record.task_key,
            TaskStatus.COMPLETED,
            output_refs=node.output_refs,
            error="",
            claimed_by_agent=step.agent.name,
            node_id=node.id,
        )
        self._emit_progress(record.task_key, "completed", result.output_summary)

    def _emit_progress(self, step_key: str, status: str, message: str) -> None:
        if self.progress_callback is None:
            return
        completed = sum(
            1 for node in self.nodes_by_id.values() if node.status == RunStatus.COMPLETED
        )
        total = max(len(self.steps_by_key), 1)
        progress = int(completed / total * 100)
        if status == "running":
            progress = max(progress, int((completed + 0.25) / total * 100))
        try:
            self.progress_callback(
                {
                    "step_key": step_key,
                    "status": status,
                    "message": message,
                    "progress_percent": min(progress, 100),
                }
            )
        except Exception:
            # Observability must never change workflow semantics.
            return

    def _next_ready_record(self, board: TaskBoard) -> TaskRecord | None:
        for record in board.tasks:
            if status_value(record.status) == TaskStatus.READY.value:
                return record
        return None

    def _is_finished(self, board: TaskBoard) -> bool:
        statuses = {status_value(record.status) for record in board.tasks}
        if not statuses:
            return True
        if TaskStatus.FAILED.value in statuses:
            return True
        if TaskStatus.REQUIRES_HUMAN.value in statuses:
            return True
        return statuses <= {TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value}

    def _mark_board_blocked(self, board: TaskBoard) -> None:
        pending = [
            record.task_key
            for record in board.tasks
            if status_value(record.status)
            in {TaskStatus.PENDING.value, TaskStatus.BLOCKED.value}
        ]
        if not pending:
            return
        board.status = TaskStatus.BLOCKED
        board.metadata = {
            **board.metadata,
            "blocked_reason": "No ready TaskRecord could be scheduled.",
            "blocked_tasks": pending,
        }
        board.updated_at = utc_now()
        self.task_board_store.save_board(board)

    def _fail_unsupported_task(self, record: TaskRecord) -> None:
        error = f"No StepSpec registered for TaskRecord task_key={record.task_key}"
        self.task_board_store.update_status(
            self.task_id,
            record.task_key,
            TaskStatus.FAILED,
            error=error,
        )

    def _record_from_board(self, board: TaskBoard, ref: str) -> TaskRecord:
        for record in board.tasks:
            if record.id == ref or record.task_key == ref:
                return record
        raise KeyError(f"TaskRecord not found after status update: {ref}")

    @staticmethod
    def _to_run_status(status) -> RunStatus:
        value = status_value(status)
        if value == TaskStatus.RUNNING.value:
            return RunStatus.RUNNING
        if value == TaskStatus.COMPLETED.value:
            return RunStatus.COMPLETED
        if value == TaskStatus.FAILED.value:
            return RunStatus.FAILED
        if value == TaskStatus.SKIPPED.value:
            return RunStatus.SKIPPED
        return RunStatus.PENDING

    @staticmethod
    def _status_value(status) -> str:
        if hasattr(status, "value"):
            return str(status.value)
        return str(status)
