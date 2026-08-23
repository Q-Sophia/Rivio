from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.schemas import DAGNode, TaskBoard, TaskRecord
from app.workflow.taskboard import FIXED_SNAPSHOT_TASKS, TASK_BOARD_ARTIFACT, TASK_RECORDS_ARTIFACT
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")
EXPECTED_TASK_KEYS = [str(step["task_key"]) for step in FIXED_SNAPSHOT_TASKS]
FEEDBACK_TASK_TYPES = {"supplement_collection", "supplement_analysis", "revise_report"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate TaskBoard and TaskRecord artifacts for a local run."
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def artifact_path(store: ArtifactStore, task_id: str, artifact_type: str) -> Path:
    return store.root_dir / task_id / f"{artifact_type}.json"


def load_typed_list(
    store: ArtifactStore,
    task_id: str,
    artifact_type: str,
    model: type[T],
    errors: list[str],
) -> list[T]:
    path = artifact_path(store, task_id, artifact_type)
    if not path.exists():
        errors.append(f"Missing artifact file: {path}")
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{artifact_type}.json is invalid JSON: {exc}")
        return []
    if not isinstance(raw, list):
        errors.append(f"{artifact_type}.json must contain a JSON list")
        return []
    if not raw:
        errors.append(f"{artifact_type}.json must not be empty")
        return []

    items: list[T] = []
    for index, item in enumerate(raw):
        try:
            items.append(model(**item))
        except ValidationError as exc:
            errors.append(f"{artifact_type}.json[{index}] failed schema validation: {exc}")
    return items


def validate_taskboard_artifacts(store: ArtifactStore, task_id: str) -> list[str]:
    errors: list[str] = []
    boards = load_typed_list(store, task_id, TASK_BOARD_ARTIFACT, TaskBoard, errors)
    records = load_typed_list(store, task_id, TASK_RECORDS_ARTIFACT, TaskRecord, errors)
    dag_nodes = load_typed_list(store, task_id, "dag_nodes", DAGNode, errors)

    if not boards or not records:
        return errors

    board = boards[-1]
    validate_board_records_match(board, records, errors)
    validate_fixed_snapshot_shape(records, errors)
    validate_node_links(records, dag_nodes, errors)
    validate_dependency_links(records, errors)
    return errors


def validate_board_records_match(
    board: TaskBoard,
    records: list[TaskRecord],
    errors: list[str],
) -> None:
    board_ids = [task.id for task in board.tasks]
    record_ids = [task.id for task in records]
    if board_ids != record_ids:
        errors.append("task_board.tasks and task_records.json order/id list mismatch")
    if len(board.tasks) != len(records):
        errors.append(
            f"TaskBoard task count mismatch: board={len(board.tasks)} records={len(records)}"
        )


def validate_fixed_snapshot_shape(records: list[TaskRecord], errors: list[str]) -> None:
    actual_keys = [record.task_key for record in records]
    fixed_keys = actual_keys[: len(EXPECTED_TASK_KEYS)]
    if fixed_keys != EXPECTED_TASK_KEYS:
        errors.append(
            f"Core TaskRecord order mismatch: expected={EXPECTED_TASK_KEYS}; actual={fixed_keys}"
        )

    extra_records = records[len(EXPECTED_TASK_KEYS) :]
    for record in extra_records:
        if record.task_type not in FEEDBACK_TASK_TYPES:
            errors.append(
                f"Unexpected extra TaskRecord {record.task_key} task_type={record.task_type}"
            )

    for record in records:
        if not record.input_refs:
            errors.append(f"TaskRecord {record.task_key} has empty input_refs")
        if not record.output_refs:
            errors.append(f"TaskRecord {record.task_key} has empty output_refs")


def validate_node_links(
    records: list[TaskRecord],
    dag_nodes: list[DAGNode],
    errors: list[str],
) -> None:
    node_ids = {node.id for node in dag_nodes}
    core_keys = set(EXPECTED_TASK_KEYS)
    for record in records:
        if record.task_key not in core_keys:
            if record.node_id and record.node_id not in node_ids:
                errors.append(
                    f"Feedback TaskRecord {record.task_key} references missing node_id={record.node_id}"
                )
            continue
        if not record.node_id:
            errors.append(f"TaskRecord {record.task_key} is missing node_id")
            continue
        if record.node_id not in node_ids:
            errors.append(
                f"TaskRecord {record.task_key} references missing node_id={record.node_id}"
            )


def validate_dependency_links(records: list[TaskRecord], errors: list[str]) -> None:
    refs = {record.id for record in records} | {record.task_key for record in records}
    for record in records:
        for dependency in record.depends_on:
            if dependency not in refs:
                errors.append(
                    f"TaskRecord {record.task_key} references missing dependency={dependency}"
                )
        for blocker in record.blocked_by:
            if blocker.startswith("issue_"):
                continue
            if blocker not in refs:
                errors.append(
                    f"TaskRecord {record.task_key} references missing blocker={blocker}"
                )


def print_result(errors: list[str]) -> None:
    if errors:
        print("FAIL")
        print(f"errors={len(errors)}")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        return
    print("PASS")
    print("errors=0")


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    errors = validate_taskboard_artifacts(store, args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
