from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_TASKS = [
    "task_user_46c24a3ccd54",
    "task_user_4143cb765d5e",
    "task_user_8666280bf195",
]

TARGET_KEYS = {
    "selected",
    "source_level",
    "official_confidence",
    "freshness_score",
    "rejection_reason",
    "quality_score",
    "source_quality",
    "first_party",
}


def walk(value: Any, hits: dict[str, int]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in TARGET_KEYS:
                hits[k] = hits.get(k, 0) + 1
            walk(v, hits)
    elif isinstance(value, list):
        for item in value:
            walk(item, hits)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Locate persisted source-quality fields in current task artifacts."
    )
    script_path = Path(__file__).resolve()
    backend = script_path.parent
    if backend.name in {"scripts", "eval"}:
        backend = backend.parent

    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=backend / "app" / "data" / "runs",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
    )
    args = parser.parse_args()

    runs_dir = args.runs_dir.resolve()
    task_ids = args.task_ids or DEFAULT_TASKS

    total_files = 0
    total_hit_files = 0

    for task_id in task_ids:
        task_dir = runs_dir / task_id
        print(f"\n[{task_id}]")
        if not task_dir.exists():
            print("  task directory missing")
            continue

        files = sorted(task_dir.glob("*.json"))
        print(f"  json files: {len(files)}")
        total_files += len(files)

        found_any = False
        for path in files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  {path.name}: READ_ERROR {type(exc).__name__}: {exc}")
                continue

            hits: dict[str, int] = {}
            walk(raw, hits)
            if hits:
                found_any = True
                total_hit_files += 1
                detail = ", ".join(f"{k}={v}" for k, v in sorted(hits.items()))
                print(f"  {path.name}: {detail}")

        if not found_any:
            print("  NO_SOURCE_QUALITY_FIELDS_FOUND")

    print(f"\nScanned JSON files: {total_files}")
    print(f"Files with source-quality fields: {total_hit_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
