from __future__ import annotations

import argparse
from pathlib import Path

from app.context import build_context_memory_artifacts, run_guardrail_checks
from app.harness.artifacts import ArtifactStore
from build_product_cards_demo import DEFAULT_TASK_ID


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic ContextBundle, WorkingMemory, MemoryItem, and "
            "GuardrailCheck artifacts from an existing snapshot workflow run."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    result = build_context_memory_artifacts(task_id=args.task_id, store=store)
    guardrail_checks = run_guardrail_checks(task_id=args.task_id, store=store)
    failed = [check for check in guardrail_checks if check.status == "failed"]
    warnings = [check for check in guardrail_checks if check.status == "warning"]

    print("Context and memory demo passed")
    print(f"task_id={args.task_id}")
    print(f"artifact_dir={store.root_dir / args.task_id}")
    print("working_memory=1")
    print(f"memory_items_count={len(result['memory_items'])}")
    print(f"context_bundles_count={len(result['context_bundles'])}")
    print(f"guardrail_checks_count={len(guardrail_checks)}")
    print(f"guardrail_failed_count={len(failed)}")
    print(f"guardrail_warning_count={len(warnings)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
