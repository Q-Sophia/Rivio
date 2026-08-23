from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.llm import LLMConfig
from app.schemas import LLMMode, LLMProvider
from app.workflow.snapshot_pipeline import run_step6c_dual_real_pilot_workflow
from app.workflow.taskboard import TaskBoardStore
from check_snapshot import DEFAULT_SNAPSHOT_ID


DEFAULT_TASK_ID = "snapshot_step6c3_analyst_writer_deepseek_v4_pilot"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Step6C.3 with a mock Extractor and exactly one real "
            "DeepSeek call for Analyst and Writer respectively."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    return parser.parse_args()


def build_real_config(
    args: argparse.Namespace,
    *,
    max_tokens: int,
) -> LLMConfig:
    return LLMConfig(
        provider=LLMProvider.COMPATIBLE,
        model=args.model,
        mode=LLMMode.LLM,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        timeout_seconds=args.timeout_seconds,
        max_tokens=max_tokens,
        temperature=0.2,
        output_language="zh-CN",
        max_retries=2,
        retry_base_seconds=1.0,
        enable_real_calls=True,
        api_style="chat_completions",
        structured_output_mode="json_object",
        thinking_mode="disabled",
    )


def main() -> None:
    args = parse_args()
    extractor_config = LLMConfig(
        provider=LLMProvider.MOCK,
        model="mock-structured-v1",
        mode=LLMMode.LLM_WITH_FALLBACK,
        api_style="mock",
        structured_output_mode="json_schema",
        thinking_mode="provider_default",
        output_language="zh-CN",
    )
    analyst_config = build_real_config(args, max_tokens=16000)
    writer_config = build_real_config(args, max_tokens=12000)
    for config in [extractor_config, analyst_config, writer_config]:
        config.validate()
    readiness_errors = sorted(
        set(
            analyst_config.real_call_readiness_errors()
            + writer_config.real_call_readiness_errors()
        )
    )
    if readiness_errors:
        for error in readiness_errors:
            print(f"real_call_readiness_error={error}", file=sys.stderr)
        raise SystemExit(2)

    try:
        summary, recorder = run_step6c_dual_real_pilot_workflow(
            task_id=args.task_id,
            snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root,
            artifact_root=args.artifact_root,
            extractor_llm_config=extractor_config,
            analyst_llm_config=analyst_config,
            writer_llm_config=writer_config,
        )
    except Exception as exc:
        print(
            f"step6c3_dual_real_pilot_failed error={type(exc).__name__}: {exc}",
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
        "analyst_writer_real": metadata.get("analyst_writer_real", False),
        "extractor_llm_provider": metadata.get("upstream_llm_provider", ""),
        "analyst_llm_model": metadata.get("analyst_llm_model", ""),
        "writer_llm_model": metadata.get("writer_llm_model", ""),
        "real_llm_calls_count": metadata.get("real_llm_calls_count", 0),
        "real_analyst_calls_count": metadata.get("real_analyst_calls_count", 0),
        "real_writer_calls_count": metadata.get("real_writer_calls_count", 0),
        "mock_llm_calls_count": metadata.get("mock_llm_calls_count", 0),
        "llm_fallback_count": metadata.get("llm_fallback_count", 0),
        "analyst_rejected_claims_count": metadata.get(
            "analyst_rejected_claims_count", 0
        ),
        "claims_v2_count": metadata.get("claims_v2_count", 0),
        "research_gaps_count": metadata.get("research_gaps_count", 0),
        "report_title": latest_report.get("title", ""),
        "analyst_prompt_version": metadata.get("analyst_prompt_version", ""),
        "writer_prompt_version": metadata.get("writer_prompt_version", ""),
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
