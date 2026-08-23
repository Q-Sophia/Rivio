from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import DAGNode, TaskBoard, TaskRecord, TaskStatus
from app.workflow.taskboard import (
    FIXED_SNAPSHOT_TASKS,
    TaskBoardStore,
    build_fixed_snapshot_task_records,
    status_value,
)
from build_product_cards_demo import DEFAULT_TASK_ID


RUN_TO_TASK_STATUS = {
    "pending": TaskStatus.PENDING,
    "running": TaskStatus.RUNNING,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "skipped": TaskStatus.SKIPPED,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build TaskBoard and TaskRecord artifacts for the existing local "
            "snapshot workflow without changing business artifacts."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def load_dag_nodes(store: ArtifactStore, task_id: str) -> dict[str, DAGNode]:
    raw_nodes = store.load_many(task_id, "dag_nodes")
    nodes = [DAGNode(**item) for item in raw_nodes]
    return {node.id: node for node in nodes}


def records_from_dag_nodes(store: ArtifactStore, task_id: str) -> list[TaskRecord]:
    node_by_id = load_dag_nodes(store, task_id)
    records = build_fixed_snapshot_task_records(task_id, completed=False)
    task_types = {step["task_key"]: step["task_type"] for step in FIXED_SNAPSHOT_TASKS}
    target_roles = {step["task_key"]: step["target_agent_role"] for step in FIXED_SNAPSHOT_TASKS}

    updated_records: list[TaskRecord] = []
    for record in records:
        node = node_by_id.get(record.task_key)
        if node is None:
            updated_records.append(record)
            continue
        updated_records.append(
            TaskRecord(
                id=record.id,
                task_id=task_id,
                task_key=record.task_key,
                parent_task_id=record.parent_task_id,
                task_type=task_types[record.task_key],
                target_agent_role=target_roles[record.task_key],
                status=RUN_TO_TASK_STATUS.get(status_value(node.status), TaskStatus.PENDING),
                priority=record.priority,
                depends_on=record.depends_on,
                blocked_by=record.blocked_by,
                input_refs=list(node.input_refs or record.input_refs),
                output_refs=list(node.output_refs or record.output_refs),
                reason="Mapped from existing fixed snapshot DAGNode.",
                created_by_agent_run_id=record.created_by_agent_run_id,
                claimed_by_agent=record.claimed_by_agent,
                node_id=node.id,
                attempts=record.attempts,
                max_attempts=record.max_attempts,
                created_at=record.created_at,
                updated_at=node.completed_at or node.started_at or record.updated_at,
                started_at=node.started_at,
                completed_at=node.completed_at,
                error=str(node.metadata.get("error", "")),
                metadata={
                    "source_dag_node_id": node.id,
                    "source_dag_node_status": status_value(node.status),
                },
            )
        )
    return updated_records


def build_and_save_taskboard(*, task_id: str, artifact_root: Path | None = None) -> TaskBoard:
    artifact_store = ArtifactStore(root_dir=artifact_root)
    taskboard_store = TaskBoardStore(artifact_store)
    records = records_from_dag_nodes(artifact_store, task_id)
    board = TaskBoard(
        task_id=task_id,
        status=TaskBoardStore._infer_board_status(records),
        tasks=records,
        max_review_rounds=3,
        current_review_round=0,
        metadata={
            "workflow_shape": "fixed_snapshot_steps_as_task_board",
            "generated_by": "build_taskboard_demo.py",
            "source_artifact": "dag_nodes.json",
        },
    )
    return taskboard_store.save_board(board)


def main() -> None:
    args = parse_args()
    board = build_and_save_taskboard(
        task_id=args.task_id,
        artifact_root=args.artifact_root,
    )
    print(f"task_id={board.task_id}")
    print(f"task_board_status={board.status}")
    print(f"task_records_count={len(board.tasks)}")
    print("artifact_type=task_board")
    print("artifact_type=task_records")


if __name__ == "__main__":
    main()
