from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.workflow.snapshot_pipeline import run_snapshot_llm_agent_workflow
from app.workflow.taskboard import TaskBoardStore
from build_product_cards_demo import DEFAULT_TASK_ID
from check_snapshot import DEFAULT_SNAPSHOT_ID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local snapshot workflow with mock LLM Extractor/Analyst/Writer "
            "agents and rule fallback."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--llm-mode", default=None)
    return parser.parse_args()


def print_summary(summary, board) -> None:
    keys = [
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
    ]
    for key in keys:
        print(f"{key}={getattr(summary, key)}")
    print(f"task_board_status={board.status}")
    print(f"task_records_count={len(board.tasks)}")
    print(f"llm_provider={summary.metadata.get('llm_provider', '')}")
    print(f"llm_model={summary.metadata.get('llm_model', '')}")
    print(f"llm_mode={summary.metadata.get('llm_mode', '')}")
    print(f"output_language={summary.metadata.get('output_language', '')}")
    print(f"real_calls_enabled={summary.metadata.get('real_calls_enabled', False)}")
    print(f"llm_api_surface={summary.metadata.get('llm_api_surface', '')}")
    print(f"structured_output_mode={summary.metadata.get('structured_output_mode', '')}")
    print(f"thinking_mode={summary.metadata.get('thinking_mode', 'provider_default')}")
    print(f"llm_max_retries={summary.metadata.get('llm_max_retries', 0)}")
    print(f"llm_calls_count={summary.metadata.get('llm_calls_count', 0)}")
    print(f"llm_outputs_count={summary.metadata.get('llm_outputs_count', 0)}")
    print(f"llm_fallback_count={summary.metadata.get('llm_fallback_count', 0)}")
    print(f"quality_gate_status={summary.metadata.get('quality_gate_status', 'not_run')}")
    print(f"quality_gate_passed={summary.metadata.get('quality_gate_passed', False)}")
    print(f"context_bundles_count={summary.metadata.get('context_bundles_count', 0)}")
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
        summary, recorder = run_snapshot_llm_agent_workflow(
            task_id=args.task_id,
            snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root,
            artifact_root=args.artifact_root,
            llm_mode=args.llm_mode,
        )
    except Exception as exc:
        print(
            f"llm_agent_workflow_failed error={type(exc).__name__}: {exc}",
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
