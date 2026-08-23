from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.workflow.snapshot_pipeline import run_snapshot_agent_workflow
from build_product_cards_demo import DEFAULT_TASK_ID
from check_snapshot import DEFAULT_SNAPSHOT_ID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic local snapshot pipeline with DAGNode, "
            "AgentRun, ToolCall, and pipeline summary trace artifacts."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def print_summary(summary) -> None:
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
        summary, recorder = run_snapshot_agent_workflow(
            task_id=args.task_id,
            snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root,
            artifact_root=args.artifact_root,
        )
    except Exception as exc:
        print(
            f"workflow_failed error={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print_summary(summary)
    if summary.pipeline_status != "completed":
        print_failed_nodes(recorder)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
