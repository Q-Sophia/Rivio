from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.llm import LLMConfig
from app.schemas import LLMMode, LLMProvider
from app.workflow.snapshot_pipeline import run_step6c_writer_real_pilot_workflow
from app.workflow.taskboard import TaskBoardStore
from check_snapshot import DEFAULT_SNAPSHOT_ID


DEFAULT_TASK_ID = "snapshot_step6c2d_writer_deepseek_v4_pilot_v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Step6C.2D with mock upstream agents and exactly one real "
            "DeepSeek Writer call."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    upstream_config = LLMConfig(
        provider=LLMProvider.MOCK,
        model="mock-structured-v1",
        mode=LLMMode.LLM_WITH_FALLBACK,
        api_style="mock",
        structured_output_mode="json_schema",
        thinking_mode="provider_default",
        output_language="zh-CN",
    )
    writer_config = LLMConfig(
        provider=LLMProvider.COMPATIBLE,
        model=args.model,
        mode=LLMMode.LLM,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        timeout_seconds=args.timeout_seconds,
        max_tokens=args.max_tokens,
        temperature=0.2,
        output_language="zh-CN",
        max_retries=2,
        retry_base_seconds=1.0,
        enable_real_calls=True,
        api_style="chat_completions",
        structured_output_mode="json_object",
        thinking_mode="disabled",
    )
    upstream_config.validate()
    writer_config.validate()
    readiness_errors = writer_config.real_call_readiness_errors()
    if readiness_errors:
        for error in readiness_errors:
            print(f"writer_readiness_error={error}", file=sys.stderr)
        raise SystemExit(2)

    try:
        summary, recorder = run_step6c_writer_real_pilot_workflow(
            task_id=args.task_id,
            snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root,
            artifact_root=args.artifact_root,
            upstream_llm_config=upstream_config,
            writer_llm_config=writer_config,
        )
    except Exception as exc:
        print(
            f"step6c2d_writer_pilot_failed error={type(exc).__name__}: {exc}",
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
        "writer_only_real": metadata.get("writer_only_real", False),
        "writer_llm_provider": metadata.get("writer_llm_provider", ""),
        "writer_llm_model": metadata.get("writer_llm_model", ""),
        "upstream_llm_provider": metadata.get("upstream_llm_provider", ""),
        "real_llm_calls_count": metadata.get("real_llm_calls_count", 0),
        "real_writer_calls_count": metadata.get("real_writer_calls_count", 0),
        "mock_llm_calls_count": metadata.get("mock_llm_calls_count", 0),
        "llm_fallback_count": metadata.get("llm_fallback_count", 0),
        "report_title": latest_report.get("title", ""),
        "writer_prompt_version": (latest_report.get("sections") or {}).get(
            "report_version", ""
        ),
        "report_statements_count": metadata.get("report_statements_count", 0),
    }
    for key, value in fields.items():
        print(f"{key}={value}")

    failed_nodes = [node for node in recorder.dag_nodes if node.status == "failed"]
    if summary.pipeline_status != "completed" or str(board.status) != "completed" or failed_nodes:
        for node in failed_nodes:
            print(
                f"failed_node={node.id} error={node.metadata.get('error', '')}",
                file=sys.stderr,
            )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
