from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.workflow.snapshot_pipeline import run_step6c_professional_workflow
from app.workflow.taskboard import TaskBoardStore
from check_snapshot import DEFAULT_SNAPSHOT_ID


DEFAULT_STEP6C_TASK_ID = "snapshot_step6c_professional_mock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Step6C professional competitive analysis with the versioned "
            "candidate prompt. Defaults to the mock provider."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_STEP6C_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--llm-mode", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        summary, recorder = run_step6c_professional_workflow(
            task_id=args.task_id,
            snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root,
            artifact_root=args.artifact_root,
            llm_mode=args.llm_mode,
        )
    except Exception as exc:
        print(
            f"step6c_workflow_failed error={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    store = ArtifactStore(root_dir=args.artifact_root)
    board = TaskBoardStore(store=store).require_board(args.task_id)
    reports = store.load_many(args.task_id, "reports")
    latest_report = reports[-1] if reports else {}
    metadata = summary.metadata
    fields = {
        "task_id": args.task_id,
        "pipeline_status": summary.pipeline_status,
        "task_board_status": board.status,
        "approved": summary.approved,
        "review_score": summary.review_score,
        "llm_provider": metadata.get("llm_provider", ""),
        "llm_model": metadata.get("llm_model", ""),
        "llm_mode": metadata.get("llm_mode", ""),
        "real_calls_enabled": metadata.get("real_calls_enabled", False),
        "professional_analysis": metadata.get("professional_analysis", False),
        "analysis_portfolios_count": metadata.get("analysis_portfolios_count", 0),
        "competitor_profiles_count": metadata.get("competitor_profiles_count", 0),
        "claims_v2_count": metadata.get("claims_v2_count", 0),
        "research_gaps_count": metadata.get("research_gaps_count", 0),
        "report_title": latest_report.get("title", ""),
        "writer_prompt_id": (latest_report.get("sections") or {}).get(
            "prompt_id", ""
        ),
        "writer_prompt_version": (latest_report.get("sections") or {}).get(
            "report_version", ""
        ),
        "legacy_claims_count": summary.claims_count,
        "citation_checks_count": summary.citation_checks_count,
        "llm_calls_count": metadata.get("llm_calls_count", 0),
        "llm_outputs_count": metadata.get("llm_outputs_count", 0),
        "llm_fallback_count": metadata.get("llm_fallback_count", 0),
    }
    for key, value in fields.items():
        print(f"{key}={value}")

    failed_nodes = [node for node in recorder.dag_nodes if node.status == "failed"]
    if (
        summary.pipeline_status != "completed"
        or str(board.status) != "completed"
        or failed_nodes
    ):
        for node in failed_nodes:
            print(
                f"failed_node={node.id} error={node.metadata.get('error', '')}",
                file=sys.stderr,
            )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
