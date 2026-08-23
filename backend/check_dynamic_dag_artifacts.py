from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.schemas import AgentRun, DAGNode, TaskBoard, TaskRecord, ToolCall
from app.workflow.taskboard import FIXED_SNAPSHOT_TASKS, TASK_BOARD_ARTIFACT, TASK_RECORDS_ARTIFACT
from app.workflow.trace import PipelineSummary
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")

EXPECTED_TASK_KEYS = [str(step["task_key"]) for step in FIXED_SNAPSHOT_TASKS]
FEEDBACK_TASK_TYPES = {"supplement_collection", "supplement_analysis", "revise_report"}
REQUIRED_ARTIFACTS = [
    TASK_BOARD_ARTIFACT,
    TASK_RECORDS_ARTIFACT,
    "dag_nodes",
    "agent_runs",
    "tool_calls",
    "pipeline_summary",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate TaskBoard-driven dynamic DAG scheduling artifacts for a "
            "local snapshot run."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def artifact_path(store: ArtifactStore, task_id: str, artifact_type: str) -> Path:
    return store.root_dir / task_id / f"{artifact_type}.json"


def load_typed_artifact(
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
            errors.append(
                f"{artifact_type}.json[{index}] failed schema validation: {exc}"
            )
    return items


def status_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def validate_dynamic_dag_artifacts(store: ArtifactStore, task_id: str) -> list[str]:
    errors: list[str] = []
    for artifact_type in REQUIRED_ARTIFACTS:
        path = artifact_path(store, task_id, artifact_type)
        if not path.exists():
            errors.append(f"Missing required scheduling artifact: {path}")

    boards = load_typed_artifact(
        store,
        task_id,
        TASK_BOARD_ARTIFACT,
        TaskBoard,
        errors,
    )
    records = load_typed_artifact(
        store,
        task_id,
        TASK_RECORDS_ARTIFACT,
        TaskRecord,
        errors,
    )
    dag_nodes = load_typed_artifact(store, task_id, "dag_nodes", DAGNode, errors)
    agent_runs = load_typed_artifact(store, task_id, "agent_runs", AgentRun, errors)
    tool_calls = load_typed_artifact(store, task_id, "tool_calls", ToolCall, errors)
    summaries = load_typed_artifact(
        store,
        task_id,
        "pipeline_summary",
        PipelineSummary,
        errors,
    )

    if boards and records:
        validate_task_records(boards[-1], records, errors)
    validate_dag_links(records, dag_nodes, errors)
    validate_agent_and_tool_links(records, dag_nodes, agent_runs, tool_calls, errors)
    validate_summary(summaries, records, dag_nodes, agent_runs, tool_calls, errors)
    return errors


def validate_task_records(
    board: TaskBoard,
    records: list[TaskRecord],
    errors: list[str],
) -> None:
    if status_value(board.status) != "completed":
        errors.append(f"TaskBoard status is {status_value(board.status)}, expected completed")

    board_keys = [record.task_key for record in board.tasks]
    record_keys = [record.task_key for record in records]
    if board_keys != record_keys:
        errors.append("task_board.tasks and task_records.json task_key order mismatch")

    core_records = records[: len(EXPECTED_TASK_KEYS)]
    feedback_records = records[len(EXPECTED_TASK_KEYS) :]
    core_keys = [record.task_key for record in core_records]
    if core_keys != EXPECTED_TASK_KEYS:
        errors.append(
            f"Core TaskRecord order mismatch: expected={EXPECTED_TASK_KEYS}; actual={core_keys}"
        )

    record_refs = {record.id for record in records} | {record.task_key for record in records}
    for record in core_records:
        if status_value(record.status) != "completed":
            errors.append(
                f"Core TaskRecord {record.task_key} status is {status_value(record.status)}, "
                "expected completed"
            )
        if record.attempts <= 0:
            errors.append(f"Core TaskRecord {record.task_key} attempts must be > 0")
        if not record.claimed_by_agent:
            errors.append(f"Core TaskRecord {record.task_key} is missing claimed_by_agent")
        if not record.node_id:
            errors.append(f"Core TaskRecord {record.task_key} is missing node_id")
        if not record.started_at:
            errors.append(f"Core TaskRecord {record.task_key} is missing started_at")
        if not record.completed_at:
            errors.append(f"Core TaskRecord {record.task_key} is missing completed_at")
        _validate_dependencies(record, record_refs, errors)

    for record in feedback_records:
        if record.task_type not in FEEDBACK_TASK_TYPES:
            errors.append(
                f"Unexpected feedback TaskRecord {record.task_key} task_type={record.task_type}"
            )
        if status_value(record.status) not in {"blocked", "pending", "skipped", "requires_human"}:
            errors.append(
                f"Feedback TaskRecord {record.task_key} status={status_value(record.status)} "
                "must be blocked, pending, skipped, or requires_human"
            )
        if not record.parent_task_id:
            errors.append(f"Feedback TaskRecord {record.task_key} is missing parent_task_id")
        if not record.input_refs:
            errors.append(f"Feedback TaskRecord {record.task_key} has empty input_refs")
        if not record.output_refs:
            errors.append(f"Feedback TaskRecord {record.task_key} has empty output_refs")
        _validate_dependencies(record, record_refs, errors)


def _validate_dependencies(record: TaskRecord, refs: set[str], errors: list[str]) -> None:
    for dependency in record.depends_on:
        if dependency not in refs:
            errors.append(
                f"TaskRecord {record.task_key} references missing dependency={dependency}"
            )


def validate_dag_links(
    records: list[TaskRecord],
    dag_nodes: list[DAGNode],
    errors: list[str],
) -> None:
    nodes_by_id = {node.id: node for node in dag_nodes}
    record_by_key = {record.task_key: record for record in records}
    core_records = records[: len(EXPECTED_TASK_KEYS)]

    actual_order = [node.id for node in dag_nodes]
    if actual_order != EXPECTED_TASK_KEYS:
        errors.append(
            f"DAG node order mismatch: expected={EXPECTED_TASK_KEYS}; actual={actual_order}"
        )

    for record in core_records:
        node = nodes_by_id.get(record.node_id)
        if node is None:
            errors.append(
                f"Core TaskRecord {record.task_key} references missing DAGNode {record.node_id}"
            )
            continue
        if node.metadata.get("task_record_id") != record.id:
            errors.append(
                f"DAGNode {node.id} metadata.task_record_id does not match TaskRecord {record.id}"
            )
        if node.metadata.get("task_key") != record.task_key:
            errors.append(
                f"DAGNode {node.id} metadata.task_key does not match TaskRecord {record.task_key}"
            )
        if status_value(node.status) != "completed":
            errors.append(f"DAGNode {node.id} status is {status_value(node.status)}")
        if list(node.depends_on) != list(record.depends_on):
            errors.append(
                f"DAGNode {node.id} depends_on does not match TaskRecord {record.task_key}"
            )

    for node in dag_nodes:
        if node.id not in record_by_key:
            errors.append(f"DAGNode {node.id} has no matching TaskRecord task_key")


def validate_agent_and_tool_links(
    records: list[TaskRecord],
    dag_nodes: list[DAGNode],
    agent_runs: list[AgentRun],
    tool_calls: list[ToolCall],
    errors: list[str],
) -> None:
    node_ids = {node.id for node in dag_nodes}
    record_node_ids = {record.node_id for record in records if record.node_id}
    agent_run_ids = {run.id for run in agent_runs}

    if len(agent_runs) != len(dag_nodes):
        errors.append(
            f"AgentRun count mismatch: expected={len(dag_nodes)}; actual={len(agent_runs)}"
        )

    for run in agent_runs:
        if run.node_id not in node_ids:
            errors.append(f"AgentRun {run.id} references missing DAGNode {run.node_id}")
        if run.node_id not in record_node_ids:
            errors.append(
                f"AgentRun {run.id} node_id={run.node_id} has no matching TaskRecord"
            )
        if status_value(run.status) != "completed":
            errors.append(
                f"AgentRun {run.id} status is {status_value(run.status)}, expected completed"
            )

    for call in tool_calls:
        if call.agent_run_id not in agent_run_ids:
            errors.append(
                f"ToolCall {call.id} references missing AgentRun {call.agent_run_id}"
            )
        if status_value(call.status) != "completed":
            errors.append(
                f"ToolCall {call.id} status is {status_value(call.status)}, expected completed"
            )


def validate_summary(
    summaries: list[PipelineSummary],
    records: list[TaskRecord],
    dag_nodes: list[DAGNode],
    agent_runs: list[AgentRun],
    tool_calls: list[ToolCall],
    errors: list[str],
) -> None:
    if not summaries:
        return

    summary = summaries[-1]
    if summary.pipeline_status != "completed":
        errors.append(f"pipeline_summary status is {summary.pipeline_status}")
    if summary.metadata.get("runtime") != "taskboard_driven_dynamic_dag_v1":
        errors.append("pipeline_summary metadata.runtime is not taskboard_driven_dynamic_dag_v1")
    if summary.dag_nodes_count != len(dag_nodes):
        errors.append("pipeline_summary dag_nodes_count does not match dag_nodes.json")
    if summary.agent_runs_count != len(agent_runs):
        errors.append("pipeline_summary agent_runs_count does not match agent_runs.json")
    if summary.tool_calls_count != len(tool_calls):
        errors.append("pipeline_summary tool_calls_count does not match tool_calls.json")
    if len(records) < len(EXPECTED_TASK_KEYS):
        errors.append("TaskRecord count is smaller than fixed snapshot task count")
    if summary.metadata.get("quality_gate_status") not in {
        "passed",
        "passed_with_warnings",
        "blocked",
        "failed",
        "not_run",
    }:
        errors.append("pipeline_summary metadata.quality_gate_status is invalid")

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
    errors = validate_dynamic_dag_artifacts(store, args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
