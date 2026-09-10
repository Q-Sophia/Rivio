from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_TASKS = [
    "task_user_46c24a3ccd54",
    "task_user_4143cb765d5e",
    "task_user_8666280bf195",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def norm(value: Any) -> str:
    return str(value or "").strip()


def lower(value: Any) -> str:
    return norm(value).lower()


def looks_like_candidate(item: dict[str, Any]) -> bool:
    return "selected" in item and any(
        key in item
        for key in (
            "url",
            "source_level",
            "official_confidence",
            "rejection_reason",
            "first_party",
        )
    )


def walk(value: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if looks_like_candidate(value):
            out.append(value)
        for child in value.values():
            walk(child, out)
    elif isinstance(value, list):
        for child in value:
            walk(child, out)


def key(item: dict[str, Any], i: int) -> str:
    if norm(item.get("id")):
        return f"id:{norm(item.get('id'))}"
    if norm(item.get("url")):
        return f"url:{norm(item.get('url'))}|{norm(item.get('title'))}"
    return f"idx:{i}"


def pct(a: int, b: int) -> str:
    return "N/A" if not b else f"{a / b * 100:.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser()
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=backend / "eval_outputs" / "source_quality_current",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        root = Path(tempfile.mkdtemp())
        td = root / "task"
        td.mkdir()
        data = [{
            "candidates": [
                {
                    "id": "a",
                    "selected": True,
                    "source_level": "first_party",
                    "rejection_reason": "",
                },
                {
                    "id": "b",
                    "selected": False,
                    "source_level": "low_quality",
                    "rejection_reason": "quality_below_minimum:10<20",
                },
            ]
        }]
        (td / "research_agent_observations.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        args.runs_dir = root
        args.task_ids = ["task"]

    task_ids = args.task_ids or DEFAULT_TASKS
    runs_dir = args.runs_dir.resolve()

    per_task = []
    all_rows = []

    for task_id in task_ids:
        path = runs_dir / task_id / "research_agent_observations.json"
        if not path.exists():
            per_task.append({
                "task_id": task_id,
                "status": "no_observations",
                "candidate_count": 0,
                "selected_count": 0,
                "low_quality_selected": 0,
                "quality_rejected": 0,
            })
            continue

        raw = load_json(path)
        found: list[dict[str, Any]] = []
        walk(raw, found)
        dedup = {}
        for i, item in enumerate(found):
            dedup[key(item, i)] = item
        rows = list(dedup.values())
        all_rows.extend((task_id, r) for r in rows)

        selected = [r for r in rows if r.get("selected") is True]
        low_selected = [
            r for r in selected
            if lower(r.get("source_level")) == "low_quality"
        ]
        quality_rejected = [
            r for r in rows
            if norm(r.get("rejection_reason")).startswith("quality_below_minimum:")
        ]

        per_task.append({
            "task_id": task_id,
            "status": "ok",
            "candidate_count": len(rows),
            "selected_count": len(selected),
            "selected_source_levels": dict(sorted(
                Counter(lower(r.get("source_level")) or "unknown" for r in selected).items()
            )),
            "low_quality_selected": len(low_selected),
            "quality_rejected": len(quality_rejected),
        })

    candidates = [r for _, r in all_rows]
    selected = [r for _, r in all_rows if r.get("selected") is True]
    low_selected = [
        r for r in selected if lower(r.get("source_level")) == "low_quality"
    ]
    quality_rejected = [
        r for r in candidates
        if norm(r.get("rejection_reason")).startswith("quality_below_minimum:")
    ]
    selected_levels = Counter(
        lower(r.get("source_level")) or "unknown" for r in selected
    )

    summary = {
        "requested_tasks": task_ids,
        "evaluable_tasks": sum(1 for x in per_task if x["status"] == "ok"),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "selection_rate": None if not candidates else round(len(selected) / len(candidates), 4),
        "selected_source_levels": dict(sorted(selected_levels.items())),
        "low_quality_selected_count": len(low_selected),
        "low_quality_selected_share_of_selected": (
            None if not selected else round(len(low_selected) / len(selected), 4)
        ),
        "quality_below_minimum_rejection_count": len(quality_rejected),
        "per_task": per_task,
        "warning": (
            "Runtime source_level is not human Gold; use this to evaluate current "
            "filter behavior, not source-role classification accuracy."
        ),
    }

    if args.self_test:
        assert summary["candidate_count"] == 2
        assert summary["selected_count"] == 1
        assert summary["low_quality_selected_count"] == 0
        assert summary["quality_below_minimum_rejection_count"] == 1
        print("SELF_TEST_PASS")
        return 0

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "source_quality_current_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Requested tasks: {len(task_ids)}")
    print(f"Evaluable tasks: {summary['evaluable_tasks']}")
    print(f"Candidates: {summary['candidate_count']}")
    print(f"Selected: {summary['selected_count']} ({pct(summary['selected_count'], summary['candidate_count'])})")
    print("Selected source levels:", summary["selected_source_levels"])
    print(
        f"Low-quality selected: {summary['low_quality_selected_count']}/"
        f"{summary['selected_count']} "
        f"({pct(summary['low_quality_selected_count'], summary['selected_count'])})"
    )
    print("Quality-below-minimum rejected:", summary["quality_below_minimum_rejection_count"])
    for item in per_task:
        print(
            f"{item['task_id']}: {item['status']} | "
            f"cand={item['candidate_count']} selected={item['selected_count']} "
            f"low_selected={item['low_quality_selected']} "
            f"quality_rejected={item['quality_rejected']}"
        )
    print(f"Output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
