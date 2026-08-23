from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.schemas import AgentRun, DAGNode, ToolCall
from app.workflow.snapshot_pipeline import FIXED_WORKFLOW_STEPS
from app.workflow.trace import PipelineSummary
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")

REQUIRED_TRACE_ARTIFACTS = [
    "dag_nodes",
    "agent_runs",
    "tool_calls",
    "pipeline_summary",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate DAGNode, AgentRun, ToolCall, and pipeline summary trace "
            "artifacts for the local snapshot agent workflow."
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
        return value.value
    return str(value)


def validate_workflow_trace(store: ArtifactStore, task_id: str) -> list[str]:
    errors: list[str] = []
    for artifact_type in REQUIRED_TRACE_ARTIFACTS:
        path = artifact_path(store, task_id, artifact_type)
        if not path.exists():
            errors.append(f"Missing required trace artifact: {path}")

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

    validate_dag_nodes(dag_nodes, errors)
    validate_agent_runs(dag_nodes, agent_runs, errors)
    validate_tool_calls(agent_runs, tool_calls, errors)
    validate_failure_ordering(dag_nodes, errors)
    validate_pipeline_summary(summaries, dag_nodes, agent_runs, tool_calls, errors)
    return errors


def validate_dag_nodes(dag_nodes: list[DAGNode], errors: list[str]) -> None:
    if len(dag_nodes) != len(FIXED_WORKFLOW_STEPS):
        errors.append(
            f"Expected {len(FIXED_WORKFLOW_STEPS)} DAG nodes, found {len(dag_nodes)}"
        )

    actual_order = [node.id for node in dag_nodes]
    if actual_order != FIXED_WORKFLOW_STEPS:
        errors.append(
            "DAG node order mismatch: "
            f"expected={FIXED_WORKFLOW_STEPS}; actual={actual_order}"
        )

    for node in dag_nodes:
        if status_value(node.status) == "completed":
            if not node.started_at:
                errors.append(f"Completed DAGNode {node.id} is missing started_at")
            if not node.completed_at:
                errors.append(f"Completed DAGNode {node.id} is missing completed_at")
            if not node.output_refs:
                errors.append(f"Completed DAGNode {node.id} has empty output_refs")
        if status_value(node.status) == "failed" and not node.metadata.get("error"):
            errors.append(f"Failed DAGNode {node.id} is missing metadata.error")


def validate_agent_runs(
    dag_nodes: list[DAGNode],
    agent_runs: list[AgentRun],
    errors: list[str],
) -> None:
    node_ids = {node.id for node in dag_nodes}
    completed_node_ids = {
        node.id for node in dag_nodes if status_value(node.status) == "completed"
    }
    run_node_ids = {run.node_id for run in agent_runs}

    for node_id in completed_node_ids:
        if node_id not in run_node_ids:
            errors.append(f"Completed DAGNode {node_id} has no AgentRun")

    for run in agent_runs:
        if run.node_id not in node_ids:
            errors.append(
                f"AgentRun {run.id} references missing node_id={run.node_id}"
            )
        if status_value(run.status) != "completed":
            errors.append(
                f"AgentRun {run.id} status is {status_value(run.status)}, expected completed"
            )
        if not run.started_at:
            errors.append(f"AgentRun {run.id} is missing started_at")
        if not run.completed_at:
            errors.append(f"AgentRun {run.id} is missing completed_at")
        if run.duration_ms < 0:
            errors.append(f"AgentRun {run.id} has negative duration_ms")

    executed_node_ids = {
        node.id
        for node in dag_nodes
        if status_value(node.status) in {"running", "completed", "failed"}
    }
    if len(agent_runs) != len(executed_node_ids):
        errors.append(
            "AgentRun count does not match executed DAG node count: "
            f"agent_runs={len(agent_runs)} executed_nodes={len(executed_node_ids)}"
        )


def validate_tool_calls(
    agent_runs: list[AgentRun],
    tool_calls: list[ToolCall],
    errors: list[str],
) -> None:
    agent_run_ids = {run.id for run in agent_runs}
    runs_with_tools = {call.agent_run_id for call in tool_calls}

    for call in tool_calls:
        if call.agent_run_id not in agent_run_ids:
            errors.append(
                f"ToolCall {call.id} references missing agent_run_id={call.agent_run_id}"
            )
        if status_value(call.status) != "completed":
            errors.append(
                f"ToolCall {call.id} status is {status_value(call.status)}, expected completed"
            )
        if not call.tool_name:
            errors.append(f"ToolCall {call.id} has empty tool_name")

    for run in agent_runs:
        if run.id not in runs_with_tools:
            errors.append(f"AgentRun {run.id} has no ToolCall records")


def validate_failure_ordering(dag_nodes: list[DAGNode], errors: list[str]) -> None:
    failed_indexes = [
        index
        for index, node in enumerate(dag_nodes)
        if status_value(node.status) == "failed"
    ]
    if not failed_indexes:
        if dag_nodes and status_value(dag_nodes[-1].status) != "completed":
            errors.append(
                "Pipeline has no failed nodes, but final DAGNode is not completed"
            )
        return

    first_failed = min(failed_indexes)
    for node in dag_nodes[first_failed + 1 :]:
        if status_value(node.status) == "completed":
            errors.append(
                f"DAGNode {node.id} is completed after failed node "
                f"{dag_nodes[first_failed].id}"
            )


def validate_pipeline_summary(
    summaries: list[PipelineSummary],
    dag_nodes: list[DAGNode],
    agent_runs: list[AgentRun],
    tool_calls: list[ToolCall],
    errors: list[str],
) -> None:
    if not summaries:
        return

    summary = summaries[-1]
    if summary.dag_nodes_count != len(dag_nodes):
        errors.append(
            "pipeline_summary dag_nodes_count mismatch: "
            f"{summary.dag_nodes_count} != {len(dag_nodes)}"
        )
    if summary.agent_runs_count != len(agent_runs):
        errors.append(
            "pipeline_summary agent_runs_count mismatch: "
            f"{summary.agent_runs_count} != {len(agent_runs)}"
        )
    if summary.tool_calls_count != len(tool_calls):
        errors.append(
            "pipeline_summary tool_calls_count mismatch: "
            f"{summary.tool_calls_count} != {len(tool_calls)}"
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
    errors = validate_workflow_trace(store, args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
