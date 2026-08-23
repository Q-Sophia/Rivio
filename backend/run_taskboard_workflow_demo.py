from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.workflow.snapshot_pipeline import run_snapshot_taskboard_workflow
from app.workflow.taskboard import TaskBoardStore
from build_product_cards_demo import DEFAULT_TASK_ID
from check_snapshot import DEFAULT_SNAPSHOT_ID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local snapshot workflow through TaskBoard-driven dynamic DAG "
            "scheduling."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def print_summary(summary, board) -> None:
    for key in [
        "sources_count",
        "evidence_count",
        "product_cards_count",
        "claims_count",
        "citation_checks_count",
        "reports_count",
        "review_feedback_count",
        "dag_nodes_count",
        "agent_runs_count",
        "tool_calls_count",
        "supported_count",
        "weak_count",
        "approved",
        "review_score",
        "pipeline_status",
    ]:
        print(f"{key}={getattr(summary, key)}")
    print(f"task_board_status={board.status}")
    print(f"task_records_count={len(board.tasks)}")
    completed = sum(1 for record in board.tasks if record.status == "completed")
    skipped = sum(1 for record in board.tasks if record.status == "skipped")
    blocked = sum(1 for record in board.tasks if record.status == "blocked")
    print(f"task_records_completed={completed}")
    print(f"task_records_skipped={skipped}")
    print(f"task_records_blocked={blocked}")
    print(f"quality_gate_status={summary.metadata.get('quality_gate_status', 'not_run')}")
    print(f"quality_gate_passed={summary.metadata.get('quality_gate_passed', False)}")
    print(f"feedback_tasks_count={summary.metadata.get('feedback_tasks_count', 0)}")
    print(f"working_memory_count={summary.metadata.get('working_memory_count', 0)}")
    print(f"memory_items_count={summary.metadata.get('memory_items_count', 0)}")
    print(f"context_bundles_count={summary.metadata.get('context_bundles_count', 0)}")
    print(f"guardrail_checks_count={summary.metadata.get('guardrail_checks_count', 0)}")
    print(f"guardrail_failed_count={summary.metadata.get('guardrail_failed_count', 0)}")
    print(f"guardrail_warning_count={summary.metadata.get('guardrail_warning_count', 0)}")


def print_failed_nodes(recorder) -> None:
    failed_nodes = [node for node in recorder.dag_nodes if node.status == "failed"]
    for node in failed_nodes:
        print(
            f"failed_node={node.id} error={node.metadata.get('error', '')}",
            file=sys.stderr,
        )


def main() -> None:
    args = parse_args()
    try:
        summary, recorder = run_snapshot_taskboard_workflow(
            task_id=args.task_id,
            snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root,
            artifact_root=args.artifact_root,
        )
    except Exception as exc:
        print(
            f"taskboard_workflow_failed error={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    store = ArtifactStore(root_dir=args.artifact_root)
    board = TaskBoardStore(store=store).require_board(args.task_id)
    print_summary(summary, board)
    if summary.pipeline_status != "completed" or board.status != "completed":
        print_failed_nodes(recorder)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
