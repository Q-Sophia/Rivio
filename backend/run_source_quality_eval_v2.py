from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_TASKS = [
    "task_user_46c24a3ccd54",
    "task_user_4143cb765d5e",
    "task_user_8666280bf195",
]


def load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("items", "records", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [raw]
    return []


def norm(value: Any) -> str:
    return str(value or "").strip()


def lower(value: Any) -> str:
    return norm(value).lower()


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def avg(values: list[float | None]) -> float | None:
    clean = [x for x in values if x is not None]
    return round(statistics.mean(clean), 4) if clean else None


def median(values: list[float | None]) -> float | None:
    clean = [x for x in values if x is not None]
    return round(statistics.median(clean), 4) if clean else None


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def dist(values) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def evaluate_task(task_dir: Path) -> dict[str, Any]:
    selections = load_items(task_dir / "source_selection_runs.json")
    search_results = load_items(task_dir / "web_search_results.json")
    sources = load_items(task_dir / "sources.json")

    search_by_id = {
        norm(x.get("id")): x for x in search_results if norm(x.get("id"))
    }

    joined = []
    for sel in selections:
        rid = norm(sel.get("search_result_id"))
        result = search_by_id.get(rid, {})
        role = lower(sel.get("source_role")) or lower(
            (result.get("metadata") or {}).get("source_level")
            if isinstance(result.get("metadata"), dict)
            else result.get("source_level")
        ) or "unknown"
        official = lower(sel.get("official_confidence")) or lower(
            (result.get("metadata") or {}).get("official_confidence")
            if isinstance(result.get("metadata"), dict)
            else result.get("official_confidence")
        ) or "unknown"

        joined.append({
            "search_result_id": rid or None,
            "url": norm(sel.get("url")) or norm(result.get("url")) or None,
            "domain": norm(sel.get("domain")) or norm(result.get("domain")) or None,
            "source_role": role,
            "official_confidence": official,
            "selected": bool(sel.get("selected")),
            "selection_reason": norm(sel.get("selection_reason")) or None,
            "quality_rank": as_float(sel.get("quality_rank")),
            "final_score": as_float(sel.get("final_score")),
            "freshness_score": as_float(sel.get("freshness_score")),
            "relevance_score": as_float(sel.get("relevance_score")),
            "dimension_fit_score": as_float(sel.get("dimension_fit_score")),
            "authority_score": as_float(sel.get("authority_score")),
            "penalty_score": as_float(sel.get("penalty_score")),
        })

    selected = [x for x in joined if x["selected"]]
    unselected = [x for x in joined if not x["selected"]]
    low_all = [x for x in joined if x["source_role"] == "low_quality"]
    low_selected = [x for x in low_all if x["selected"]]

    good_roles = {
        "primary",
        "authoritative_secondary",
        "authoritative_third_party",
        "first_party",
    }
    selected_good = [x for x in selected if x["source_role"] in good_roles]

    # Final collected SourceDocument distribution: current runtime output, not Gold.
    final_source_levels = []
    for source in sources:
        meta = source.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
        level = (
            lower(source.get("source_level"))
            or lower(meta.get("source_level"))
            or "unknown"
        )
        final_source_levels.append(level)

    final_low_quality_count = sum(
        1 for x in final_source_levels if x == "low_quality"
    )

    return {
        "task_id": task_dir.name,
        "ranked_candidate_count": len(joined),
        "selected_candidate_count": len(selected),
        "selection_rate": rate(len(selected), len(joined)),
        "selected_role_distribution": dist(x["source_role"] for x in selected),
        "all_role_distribution": dist(x["source_role"] for x in joined),
        "selected_official_confidence_distribution": dist(
            x["official_confidence"] for x in selected
        ),
        "high_authority_selected_count": len(selected_good),
        "high_authority_selected_share": rate(len(selected_good), len(selected)),
        "low_quality_candidate_count": len(low_all),
        "low_quality_selected_count": len(low_selected),
        "low_quality_rejection_rate": rate(
            len(low_all) - len(low_selected), len(low_all)
        ),
        "selected_avg_final_score": avg([x["final_score"] for x in selected]),
        "unselected_avg_final_score": avg([x["final_score"] for x in unselected]),
        "selected_median_quality_rank": median(
            [x["quality_rank"] for x in selected]
        ),
        "unselected_median_quality_rank": median(
            [x["quality_rank"] for x in unselected]
        ),
        "selected_avg_freshness_score": avg(
            [x["freshness_score"] for x in selected]
        ),
        "selection_reason_distribution": dist(
            x["selection_reason"] or "none" for x in joined
        ),
        "final_source_count": len(sources),
        "final_source_level_distribution": dist(final_source_levels),
        "final_low_quality_source_count": final_low_quality_count,
        "joined_rows": joined,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    total_ranked = sum(x["ranked_candidate_count"] for x in results)
    total_selected = sum(x["selected_candidate_count"] for x in results)
    total_low = sum(x["low_quality_candidate_count"] for x in results)
    total_low_selected = sum(x["low_quality_selected_count"] for x in results)
    total_good_selected = sum(x["high_authority_selected_count"] for x in results)
    total_sources = sum(x["final_source_count"] for x in results)
    total_final_low = sum(x["final_low_quality_source_count"] for x in results)

    role_counter = Counter()
    official_counter = Counter()
    final_source_counter = Counter()
    reason_counter = Counter()
    selected_scores = []
    unselected_scores = []
    selected_ranks = []
    unselected_ranks = []

    for result in results:
        role_counter.update(result["selected_role_distribution"])
        official_counter.update(result["selected_official_confidence_distribution"])
        final_source_counter.update(result["final_source_level_distribution"])
        reason_counter.update(result["selection_reason_distribution"])
        for row in result["joined_rows"]:
            if row["selected"]:
                if row["final_score"] is not None:
                    selected_scores.append(row["final_score"])
                if row["quality_rank"] is not None:
                    selected_ranks.append(row["quality_rank"])
            else:
                if row["final_score"] is not None:
                    unselected_scores.append(row["final_score"])
                if row["quality_rank"] is not None:
                    unselected_ranks.append(row["quality_rank"])

    return {
        "task_count": len(results),
        "task_ids": [x["task_id"] for x in results],
        "ranked_candidate_count": total_ranked,
        "selected_candidate_count": total_selected,
        "selection_rate": rate(total_selected, total_ranked),
        "selected_role_distribution": dict(sorted(role_counter.items())),
        "selected_official_confidence_distribution": dict(
            sorted(official_counter.items())
        ),
        "high_authority_selected_share": rate(
            total_good_selected, total_selected
        ),
        "low_quality_candidate_count": total_low,
        "low_quality_selected_count": total_low_selected,
        "low_quality_rejection_rate": rate(
            total_low - total_low_selected, total_low
        ),
        "selected_avg_final_score": avg(selected_scores),
        "unselected_avg_final_score": avg(unselected_scores),
        "selected_median_quality_rank": median(selected_ranks),
        "unselected_median_quality_rank": median(unselected_ranks),
        "selection_reason_distribution": dict(sorted(reason_counter.items())),
        "final_source_count": total_sources,
        "final_source_level_distribution": dict(sorted(final_source_counter.items())),
        "final_low_quality_source_count": total_final_low,
        "warning": (
            "Source role / official confidence are runtime ranker outputs, not human Gold. "
            "These metrics evaluate deterministic selection/filter behavior, not classification accuracy."
        ),
    }


def write_report(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# Source Quality Eval V2 — Current Tasks",
        "",
        "本评测直接读取当前版本 `source_selection_runs.json`，并按 `search_result_id` "
        "关联 `web_search_results.json`。不调用 LLM/Search/MCP。",
        "",
        "## Overall",
        "",
        f"- tasks: **{summary['task_count']}**",
        f"- ranked candidates: **{summary['ranked_candidate_count']}**",
        f"- selected candidates: **{summary['selected_candidate_count']}** "
        f"({pct(summary['selection_rate'])})",
        f"- selected role distribution: `{summary['selected_role_distribution']}`",
        f"- high-authority selected share: **{pct(summary['high_authority_selected_share'])}**",
        f"- low-quality candidates: **{summary['low_quality_candidate_count']}**",
        f"- low-quality selected: **{summary['low_quality_selected_count']}**",
        f"- low-quality rejection rate: **{pct(summary['low_quality_rejection_rate'])}**",
        f"- selected avg final score: **{summary['selected_avg_final_score']}**",
        f"- unselected avg final score: **{summary['unselected_avg_final_score']}**",
        f"- selected median quality rank: **{summary['selected_median_quality_rank']}**",
        f"- unselected median quality rank: **{summary['unselected_median_quality_rank']}**",
        f"- final collected sources: **{summary['final_source_count']}**",
        f"- final source level distribution: `{summary['final_source_level_distribution']}`",
        f"- final low-quality sources: **{summary['final_low_quality_source_count']}**",
        "",
        "## Per task",
        "",
    ]

    for r in results:
        lines.extend([
            f"### {r['task_id']}",
            "",
            f"- ranked / selected: {r['ranked_candidate_count']} / {r['selected_candidate_count']} "
            f"({pct(r['selection_rate'])})",
            f"- selected roles: `{r['selected_role_distribution']}`",
            f"- low-quality: {r['low_quality_selected_count']} selected / "
            f"{r['low_quality_candidate_count']} candidates; rejection "
            f"{pct(r['low_quality_rejection_rate'])}",
            f"- final sources: {r['final_source_count']}; "
            f"low-quality={r['final_low_quality_source_count']}",
            "",
        ])

    lines.extend([
        "## Interpretation",
        "",
        "- `source_role`、`official_confidence` 是系统运行时 Ranker 的输出，不是人工 Gold。",
        "- 因此不能把本报告写成“官方来源识别准确率 X%”。",
        "- 可以用于证明：Ranker 对候选进行了确定性排序与选择、低质量角色被过滤到什么程度、最终采集来源的运行时角色分布。",
        "- 若要计算分类 Precision/Recall/F1，需要单独的人工作标 Golden labels。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_self_test() -> None:
    root = Path(tempfile.mkdtemp())
    td = root / "task_demo"
    td.mkdir()

    (td / "source_selection_runs.json").write_text(
        json.dumps([
            {
                "search_result_id": "r1",
                "source_role": "primary",
                "official_confidence": "confirmed",
                "selected": True,
                "quality_rank": 1,
                "final_score": 90,
                "freshness_score": 10,
                "selection_reason": "selected",
            },
            {
                "search_result_id": "r2",
                "source_role": "low_quality",
                "official_confidence": "unknown",
                "selected": False,
                "quality_rank": 2,
                "final_score": 5,
                "freshness_score": 0,
                "selection_reason": "quality_below_minimum",
            },
        ]),
        encoding="utf-8",
    )
    (td / "web_search_results.json").write_text(
        json.dumps([
            {"id": "r1", "url": "https://official.test"},
            {"id": "r2", "url": "https://bad.test"},
        ]),
        encoding="utf-8",
    )
    (td / "sources.json").write_text(
        json.dumps([
            {"id": "s1", "source_level": "first_party"}
        ]),
        encoding="utf-8",
    )

    result = evaluate_task(td)
    assert result["ranked_candidate_count"] == 2
    assert result["selected_candidate_count"] == 1
    assert result["low_quality_rejection_rate"] == 1.0
    assert result["final_low_quality_source_count"] == 0
    print("SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Current-version Source Quality Eval using SourceSelectionRun."
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
        default=backend / "eval_outputs" / "source_quality_v2",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return 0

    runs_dir = args.runs_dir.resolve()
    task_ids = args.task_ids or DEFAULT_TASKS
    results = []

    for task_id in task_ids:
        td = runs_dir / task_id
        if not td.exists():
            raise SystemExit(f"Task not found: {task_id}")
        results.append(evaluate_task(td))

    summary = aggregate(results)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Remove verbose joined rows from public result file.
    compact_results = []
    for result in results:
        item = dict(result)
        item.pop("joined_rows", None)
        compact_results.append(item)

    (out / "source_quality_current_eval.json").write_text(
        json.dumps(compact_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "source_quality_current_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(out / "SOURCE_QUALITY_CURRENT_EVAL.md", summary, results)

    print(f"Tasks: {summary['task_count']}")
    print(f"Ranked candidates: {summary['ranked_candidate_count']}")
    print(
        f"Selected: {summary['selected_candidate_count']} "
        f"({pct(summary['selection_rate'])})"
    )
    print("Selected roles:", summary["selected_role_distribution"])
    print(
        f"Low-quality selected: {summary['low_quality_selected_count']}/"
        f"{summary['low_quality_candidate_count']} | "
        f"rejection={pct(summary['low_quality_rejection_rate'])}"
    )
    print(
        f"Avg final score selected/unselected: "
        f"{summary['selected_avg_final_score']} / "
        f"{summary['unselected_avg_final_score']}"
    )
    print(
        f"Median quality rank selected/unselected: "
        f"{summary['selected_median_quality_rank']} / "
        f"{summary['unselected_median_quality_rank']}"
    )
    print(
        f"Final collected sources: {summary['final_source_count']} | "
        f"runtime levels={summary['final_source_level_distribution']} | "
        f"low_quality={summary['final_low_quality_source_count']}"
    )
    print(f"Output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
