from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> tuple[Any, str | None]:
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"{path}: {type(exc).__name__}: {exc}"


def norm(value: Any) -> str:
    return str(value or "").strip()


def lower(value: Any) -> str:
    return norm(value).lower()


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def looks_like_candidate(item: dict[str, Any]) -> bool:
    if "selected" not in item:
        return False
    keys = {
        "url",
        "source_level",
        "official_confidence",
        "freshness_score",
        "rejection_reason",
        "first_party",
    }
    return bool(keys & set(item))


def walk_candidates(value: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if looks_like_candidate(value):
            out.append(value)
        for child in value.values():
            walk_candidates(child, out)
    elif isinstance(value, list):
        for child in value:
            walk_candidates(child, out)


def candidate_key(task_id: str, item: dict[str, Any], index: int) -> str:
    cid = norm(item.get("id"))
    if cid:
        return f"{task_id}|id|{cid}"
    url = norm(item.get("url"))
    title = norm(item.get("title"))
    if url:
        return f"{task_id}|url|{url}|{title}"
    return f"{task_id}|idx|{index}"


def scan_runs(runs_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for task_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        raw, err = load_json(task_dir / "research_agent_observations.json")
        if err:
            errors.append(err)
            continue
        if raw is None:
            continue

        found: list[dict[str, Any]] = []
        walk_candidates(raw, found)

        deduped: dict[str, dict[str, Any]] = {}
        for idx, item in enumerate(found):
            deduped[candidate_key(task_dir.name, item, idx)] = item

        for item in deduped.values():
            selected = bool(item.get("selected"))
            source_level = lower(item.get("source_level")) or "unknown"
            official_confidence = lower(item.get("official_confidence")) or "unknown"
            rejection_reason = norm(item.get("rejection_reason"))
            freshness = as_float(item.get("freshness_score"))
            rows.append(
                {
                    "task_id": task_dir.name,
                    "candidate_id": norm(item.get("id")) or None,
                    "title": norm(item.get("title")) or None,
                    "url": norm(item.get("url")) or None,
                    "domain": norm(item.get("domain")) or None,
                    "selected": selected,
                    "first_party_runtime": item.get("first_party"),
                    "source_level_runtime": source_level,
                    "official_confidence_runtime": official_confidence,
                    "freshness_score": freshness,
                    "rejection_reason": rejection_reason or None,
                    "rejected_quality_below_minimum": rejection_reason.startswith(
                        "quality_below_minimum:"
                    ),
                    "rejected_source_budget": rejection_reason == "source_budget_reached",
                }
            )
    return rows, errors


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def mean(values: list[float | None]) -> float | None:
    clean = [x for x in values if x is not None]
    return round(statistics.mean(clean), 4) if clean else None


def median(values: list[float | None]) -> float | None:
    clean = [x for x in values if x is not None]
    return round(statistics.median(clean), 4) if clean else None


def counter_dict(values) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_summary(rows: list[dict[str, Any]], errors: list[str]) -> dict[str, Any]:
    selected = [r for r in rows if r["selected"]]
    rejected = [r for r in rows if not r["selected"]]
    low_quality = [r for r in rows if r["source_level_runtime"] == "low_quality"]
    selected_low = [r for r in low_quality if r["selected"]]

    selected_first_party_true = [
        r for r in selected if r["first_party_runtime"] is True
    ]
    selected_first_party_known = [
        r for r in selected if isinstance(r["first_party_runtime"], bool)
    ]

    freshness_all = [r["freshness_score"] for r in rows]
    freshness_selected = [r["freshness_score"] for r in selected]

    tasks = sorted({r["task_id"] for r in rows})

    return {
        "task_count": len(tasks),
        "task_ids": tasks,
        "candidate_count": len(rows),
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "selection_rate": rate(len(selected), len(rows)),
        "selected_source_level_distribution": counter_dict(
            r["source_level_runtime"] for r in selected
        ),
        "all_source_level_distribution": counter_dict(
            r["source_level_runtime"] for r in rows
        ),
        "selected_official_confidence_distribution": counter_dict(
            r["official_confidence_runtime"] for r in selected
        ),
        "low_quality_candidate_count": len(low_quality),
        "low_quality_selected_count": len(selected_low),
        "low_quality_selected_rate": rate(len(selected_low), len(low_quality)),
        "quality_below_minimum_rejection_count": sum(
            1 for r in rows if r["rejected_quality_below_minimum"]
        ),
        "source_budget_rejection_count": sum(
            1 for r in rows if r["rejected_source_budget"]
        ),
        "selected_runtime_first_party_rate": rate(
            len(selected_first_party_true),
            len(selected_first_party_known),
        ),
        "freshness": {
            "all_mean": mean(freshness_all),
            "all_median": median(freshness_all),
            "selected_mean": mean(freshness_selected),
            "selected_median": median(freshness_selected),
            "checkable_all": sum(x is not None for x in freshness_all),
            "checkable_selected": sum(x is not None for x in freshness_selected),
        },
        "scan_error_count": len(errors),
        "scan_errors": errors[:100],
        "warning": (
            "source_level / first_party / official_confidence are runtime classifier "
            "outputs, not human Gold labels. Do not interpret them as source-role accuracy."
        ),
    }


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Source Quality Eval V1",
        "",
        "纯本地历史候选来源统计，不调用 LLM/Search/MCP。",
        "",
        "## Overall",
        "",
        f"- tasks with candidate observations: **{summary['task_count']}**",
        f"- candidates: **{summary['candidate_count']}**",
        f"- selected: **{summary['selected_count']}**",
        f"- rejected: **{summary['rejected_count']}**",
        f"- selection rate: **{pct(summary['selection_rate'])}**",
        "",
        "## Selected source distribution",
        "",
        f"- source_level(runtime): `{summary['selected_source_level_distribution']}`",
        f"- official_confidence(runtime): `{summary['selected_official_confidence_distribution']}`",
        f"- runtime first_party selected rate: **{pct(summary['selected_runtime_first_party_rate'])}**",
        "",
        "## Rejection / filtering",
        "",
        f"- low_quality candidates: **{summary['low_quality_candidate_count']}**",
        f"- low_quality selected: **{summary['low_quality_selected_count']}**",
        f"- low_quality selected rate: **{pct(summary['low_quality_selected_rate'])}**",
        f"- quality_below_minimum rejected: **{summary['quality_below_minimum_rejection_count']}**",
        f"- source_budget_reached rejected: **{summary['source_budget_rejection_count']}**",
        "",
        "## Freshness",
        "",
        f"- selected freshness mean / median: "
        f"**{summary['freshness']['selected_mean']} / {summary['freshness']['selected_median']}**",
        f"- all freshness mean / median: "
        f"**{summary['freshness']['all_mean']} / {summary['freshness']['all_median']}**",
        "",
        "## Important limitation",
        "",
        "- `source_level / first_party / official_confidence` 是系统运行时分类结果，不是人工 Gold。",
        "- 因此本报告不能声称“官方来源识别准确率”或“source role classification accuracy”。",
        "- 当前可以可靠用于简历/工程报告的是：候选筛选量、低质量来源淘汰、来源层级分布、budget/filter 行为。",
        "- 如果后续找到人工 Golden labels，再单独计算 precision/recall/F1 或 pairwise ranking accuracy。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_self_test() -> None:
    root = Path(tempfile.mkdtemp())
    task = root / "task_demo"
    task.mkdir()
    data = [
        {
            "candidates": [
                {
                    "id": "a",
                    "url": "https://official.test",
                    "selected": True,
                    "first_party": True,
                    "source_level": "first_party",
                    "official_confidence": "high",
                    "freshness_score": 0.8,
                    "rejection_reason": "",
                },
                {
                    "id": "b",
                    "url": "https://bad.test",
                    "selected": False,
                    "first_party": False,
                    "source_level": "low_quality",
                    "official_confidence": "unknown",
                    "freshness_score": 0.2,
                    "rejection_reason": "quality_below_minimum:10<20",
                },
            ]
        }
    ]
    (task / "research_agent_observations.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    rows, errors = scan_runs(root)
    assert not errors
    summary = build_summary(rows, errors)
    assert summary["candidate_count"] == 2
    assert summary["selected_count"] == 1
    assert summary["low_quality_selected_rate"] == 0.0
    assert summary["quality_below_minimum_rejection_count"] == 1
    print("SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Source Quality Eval from historical research observations."
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
        "--output-dir",
        type=Path,
        default=backend / "eval_outputs" / "source_quality",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return 0

    rows, errors = scan_runs(args.runs_dir.resolve())
    if not rows:
        raise SystemExit("No source candidate records found.")

    summary = build_summary(rows, errors)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "source_quality_candidates.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "source_quality_eval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(output_dir / "SOURCE_QUALITY_EVAL.md", summary)

    print(f"Tasks: {summary['task_count']}")
    print(f"Candidates: {summary['candidate_count']}")
    print(f"Selected: {summary['selected_count']} ({pct(summary['selection_rate'])})")
    print(
        "Selected source levels:",
        summary["selected_source_level_distribution"],
    )
    print(
        f"Low-quality selected: {summary['low_quality_selected_count']}/"
        f"{summary['low_quality_candidate_count']} "
        f"({pct(summary['low_quality_selected_rate'])})"
    )
    print(
        "Quality-below-minimum rejected:",
        summary["quality_below_minimum_rejection_count"],
    )
    print(
        "Source-budget rejected:",
        summary["source_budget_rejection_count"],
    )
    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
