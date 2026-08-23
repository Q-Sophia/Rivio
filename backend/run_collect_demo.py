from __future__ import annotations

import argparse
from pathlib import Path

from app.schemas import SourceDocument, SourceEvidence
from check_snapshot import (
    DEFAULT_SNAPSHOT_ID,
    load_and_validate_saved_artifacts,
    run_snapshot_check,
)


DEFAULT_DEMO_TASK_ID = "snapshot_online_education_demo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local Snapshot Mode collect path and verify artifacts can "
            "be read back through SourceDocument/SourceEvidence schemas."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_DEMO_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def summarize_by_competitor(items: list[SourceDocument] | list[SourceEvidence]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.competitor] = counts.get(item.competitor, 0) + 1
    return ", ".join(f"{competitor}={count}" for competitor, count in counts.items())


def main() -> None:
    args = parse_args()
    task, collected_sources, collected_evidence, store = run_snapshot_check(
        snapshot_id=args.snapshot_id,
        task_id=args.task_id,
        snapshot_root=args.snapshot_root,
        artifact_root=args.artifact_root,
    )
    saved_sources, saved_evidence = load_and_validate_saved_artifacts(
        store,
        task.task_id,
    )

    if len(saved_sources) != len(collected_sources):
        raise ValueError(
            "Saved source count does not match collected source count: "
            f"{len(saved_sources)} != {len(collected_sources)}"
        )
    if len(saved_evidence) != len(collected_evidence):
        raise ValueError(
            "Saved evidence count does not match collected evidence count: "
            f"{len(saved_evidence)} != {len(collected_evidence)}"
        )

    print("Collect demo passed")
    print(f"task_id={task.task_id}")
    print(f"artifact_dir={store.root_dir / task.task_id}")
    print(f"sources={len(saved_sources)} ({summarize_by_competitor(saved_sources)})")
    print(f"evidence={len(saved_evidence)} ({summarize_by_competitor(saved_evidence)})")


if __name__ == "__main__":
    main()
