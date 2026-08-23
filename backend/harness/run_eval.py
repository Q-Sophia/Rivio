from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.harness.artifacts import ArtifactStore
from build_product_cards_demo import DEFAULT_TASK_ID
from metrics import evaluate_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local artifact and workflow trace evaluation metrics."
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    summary = evaluate_run(store=store, task_id=args.task_id)
    output_path = store.root_dir / args.task_id / "eval_summary.json"
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"eval_passed={summary['passed']}")
    print(f"task_id={summary['task_id']}")
    print(f"artifact_dir={summary['artifact_dir']}")
    print(f"eval_summary={output_path}")
    for name, metric in summary["metrics"].items():
        print(f"{name}={metric['value']} passed={metric['passed']}")

    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
