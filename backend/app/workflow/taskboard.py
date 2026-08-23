from __future__ import annotations

from collections.abc import Iterable

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    TaskBoard,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)


TASK_BOARD_ARTIFACT = "task_board"
TASK_RECORDS_ARTIFACT = "task_records"
TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.SKIPPED.value,
    TaskStatus.REQUIRES_HUMAN.value,
}
ACTIVE_TASK_STATUSES = {
    TaskStatus.CLAIMED.value,
    TaskStatus.RUNNING.value,
}


FIXED_SNAPSHOT_TASKS = [
    {
        "id": "tb_collect_sources",
        "task_key": "collect_sources",
        "task_type": TaskType.COLLECT_SOURCES,
        "target_agent_role": AgentRole.COLLECTOR,
        "depends_on": [],
        "input_refs": ["snapshot:online_education"],
        "output_refs": ["sources", "evidence"],
    },
    {
        "id": "tb_build_product_cards",
        "task_key": "build_product_cards",
        "task_type": TaskType.BUILD_PRODUCT_CARDS,
        "target_agent_role": AgentRole.EXTRACTOR,
        "depends_on": ["collect_sources"],
        "input_refs": ["sources", "evidence"],
        "output_refs": ["product_cards"],
    },
    {
        "id": "tb_build_claims",
        "task_key": "build_claims",
        "task_type": TaskType.BUILD_CLAIMS,
        "target_agent_role": AgentRole.ANALYST,
        "depends_on": ["build_product_cards"],
        "input_refs": ["product_cards", "evidence"],
        "output_refs": ["claims"],
    },
    {
        "id": "tb_check_citations",
        "task_key": "check_citations",
        "task_type": TaskType.CHECK_CITATIONS,
        "target_agent_role": AgentRole.CITATION,
        "depends_on": ["build_claims"],
        "input_refs": ["sources", "evidence", "claims"],
        "output_refs": ["citation_checks", "claims"],
    },
    {
        "id": "tb_build_report",
        "task_key": "build_report",
        "task_type": TaskType.BUILD_REPORT,
        "target_agent_role": AgentRole.WRITER,
        "depends_on": ["check_citations"],
        "input_refs": ["product_cards", "claims", "citation_checks"],
        "output_refs": ["reports"],
    },
    {
        "id": "tb_review_report",
        "task_key": "review_report",
        "task_type": TaskType.REVIEW_REPORT,
        "target_agent_role": AgentRole.REVIEWER,
        "depends_on": ["build_report"],
        "input_refs": ["reports", "claims", "citation_checks", "product_cards"],
        "output_refs": ["review_feedback"],
    },
]


def status_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


class TaskBoardStore:
    """Persistence boundary for TaskBoard and TaskRecord artifacts.

    TaskBoardStore intentionally sits on top of ArtifactStore. ArtifactStore knows
    how to persist JSON artifacts; TaskBoardStore knows the task-board contract,
    status transitions, dependency readiness, and typed validation.
    """

    def __init__(self, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()

    def save_board(self, board: TaskBoard) -> TaskBoard:
        validated = TaskBoard(**board.model_dump(mode="json"))
        self.store.save_many(validated.task_id, TASK_BOARD_ARTIFACT, [validated])
        self.store.save_many(validated.task_id, TASK_RECORDS_ARTIFACT, validated.tasks)
        return validated

    def create_board(
        self,
        *,
        task_id: str,
        records: Iterable[TaskRecord],
        status: TaskStatus = TaskStatus.PENDING,
        max_review_rounds: int = 3,
        current_review_round: int = 0,
        metadata: dict | None = None,
    ) -> TaskBoard:
        board = TaskBoard(
            task_id=task_id,
            status=status,
            tasks=list(records),
            max_review_rounds=max_review_rounds,
            current_review_round=current_review_round,
            metadata=metadata or {},
        )
        return self.save_board(board)

    def load_board(self, task_id: str) -> TaskBoard | None:
        raw_boards = self.store.load_many(task_id, TASK_BOARD_ARTIFACT)
        if raw_boards:
            return TaskBoard(**raw_boards[-1])

        records = self.load_records(task_id)
        if not records:
            return None
        return TaskBoard(
            task_id=task_id,
            status=self._infer_board_status(records),
            tasks=records,
            metadata={"loaded_from": TASK_RECORDS_ARTIFACT},
        )

    def require_board(self, task_id: str) -> TaskBoard:
        board = self.load_board(task_id)
        if board is None:
            raise FileNotFoundError(f"TaskBoard artifact not found for task_id={task_id}")
        return board

    def load_records(self, task_id: str) -> list[TaskRecord]:
        return [
            TaskRecord(**item)
            for item in self.store.load_many(task_id, TASK_RECORDS_ARTIFACT)
        ]

    def get_record(self, task_id: str, ref: str) -> TaskRecord | None:
        board = self.load_board(task_id)
        if board is None:
            return None
        refs = self._record_refs(board.tasks)
        return refs.get(ref)

    def upsert_record(self, task_id: str, record: TaskRecord) -> TaskBoard:
        board = self.load_board(task_id) or TaskBoard(task_id=task_id, tasks=[])
        if record.task_id != task_id:
            raise ValueError(
                f"TaskRecord {record.id} belongs to task_id={record.task_id}, "
                f"expected {task_id}"
            )

        records = []
        replaced = False
        for existing in board.tasks:
            if existing.id == record.id or existing.task_key == record.task_key:
                records.append(record)
                replaced = True
            else:
                records.append(existing)
        if not replaced:
            records.append(record)

        board.tasks = records
        board.updated_at = utc_now()
        board.status = self._infer_board_status(board.tasks)
        return self.save_board(board)

    def update_status(
        self,
        task_id: str,
        ref: str,
        status: TaskStatus,
        *,
        output_refs: list[str] | None = None,
        blocked_by: list[str] | None = None,
        error: str | None = None,
        claimed_by_agent: str | None = None,
        node_id: str | None = None,
    ) -> TaskBoard:
        board = self.require_board(task_id)
        records = []
        matched = False
        now = utc_now()
        for record in board.tasks:
            if record.id != ref and record.task_key != ref:
                records.append(record)
                continue
            matched = True
            record.status = status
            record.updated_at = now
            if status_value(status) in {TaskStatus.RUNNING.value, TaskStatus.CLAIMED.value}:
                record.started_at = record.started_at or now
            if status_value(status) in TERMINAL_TASK_STATUSES:
                record.completed_at = now
            if output_refs is not None:
                record.output_refs = list(output_refs)
            if blocked_by is not None:
                record.blocked_by = list(blocked_by)
            if error is not None:
                record.error = error
            if claimed_by_agent is not None:
                record.claimed_by_agent = claimed_by_agent
            if node_id is not None:
                record.node_id = node_id
            records.append(record)
        if not matched:
            raise KeyError(f"TaskRecord not found: {ref}")

        board.tasks = records
        board.updated_at = now
        board.status = self._infer_board_status(records)
        return self.save_board(board)

    def mark_ready_tasks(self, task_id: str) -> TaskBoard:
        board = self.require_board(task_id)
        refs = self._record_refs(board.tasks)
        now = utc_now()
        updated_records = []
        for record in board.tasks:
            current = status_value(record.status)
            if current in TERMINAL_TASK_STATUSES or current in ACTIVE_TASK_STATUSES:
                updated_records.append(record)
                continue
            if record.blocked_by:
                if current != TaskStatus.BLOCKED.value:
                    record.status = TaskStatus.BLOCKED
                    record.updated_at = now
                updated_records.append(record)
                continue
            deps_completed = all(
                status_value(refs[dependency].status) == TaskStatus.COMPLETED.value
                for dependency in record.depends_on
            )
            next_status = TaskStatus.READY if deps_completed else TaskStatus.PENDING
            if current != next_status.value:
                record.status = next_status
                record.updated_at = now
            updated_records.append(record)
        board.tasks = updated_records
        board.updated_at = now
        board.status = self._infer_board_status(updated_records)
        return self.save_board(board)

    def ready_records(self, task_id: str) -> list[TaskRecord]:
        board = self.mark_ready_tasks(task_id)
        return [
            record
            for record in board.tasks
            if status_value(record.status) == TaskStatus.READY.value
        ]

    def dependency_edges(self, task_id: str) -> list[dict[str, str]]:
        board = self.require_board(task_id)
        refs = self._record_refs(board.tasks)
        edges = []
        for record in board.tasks:
            for dependency in record.depends_on:
                edges.append(
                    {
                        "from": refs[dependency].task_key,
                        "to": record.task_key,
                    }
                )
        return edges

    @staticmethod
    def _record_refs(records: list[TaskRecord]) -> dict[str, TaskRecord]:
        refs: dict[str, TaskRecord] = {}
        for record in records:
            refs[record.id] = record
            refs[record.task_key] = record
        return refs

    @staticmethod
    def _infer_board_status(records: list[TaskRecord]) -> TaskStatus:
        statuses = {status_value(record.status) for record in records}
        if not records:
            return TaskStatus.PENDING
        if TaskStatus.REQUIRES_HUMAN.value in statuses:
            return TaskStatus.REQUIRES_HUMAN
        if TaskStatus.FAILED.value in statuses:
            return TaskStatus.FAILED
        if statuses and statuses <= {TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value}:
            return TaskStatus.COMPLETED
        if TaskStatus.RUNNING.value in statuses:
            return TaskStatus.RUNNING
        if TaskStatus.CLAIMED.value in statuses:
            return TaskStatus.RUNNING
        if TaskStatus.BLOCKED.value in statuses:
            return TaskStatus.BLOCKED
        if TaskStatus.READY.value in statuses:
            return TaskStatus.READY
        return TaskStatus.PENDING


def build_fixed_snapshot_task_records(task_id: str, *, completed: bool = False) -> list[TaskRecord]:
    status = TaskStatus.COMPLETED if completed else TaskStatus.PENDING
    return [
        TaskRecord(
            task_id=task_id,
            status=status,
            priority=TaskPriority.HIGH if index == 0 else TaskPriority.MEDIUM,
            reason="Fixed snapshot workflow task mapping.",
            **step,
        )
        for index, step in enumerate(FIXED_SNAPSHOT_TASKS)
    ]


def build_fixed_snapshot_task_board(task_id: str, *, completed: bool = False) -> TaskBoard:
    records = build_fixed_snapshot_task_records(task_id, completed=completed)
    return TaskBoard(
        task_id=task_id,
        status=TaskStatus.COMPLETED if completed else TaskStatus.PENDING,
        tasks=records,
        max_review_rounds=3,
        current_review_round=0,
        metadata={
            "workflow_shape": "fixed_snapshot_steps_as_task_board",
            "dynamic_ready": True,
        },
    )
