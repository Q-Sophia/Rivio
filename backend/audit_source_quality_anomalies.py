from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("source_quality_candidates.json root must be a list")
    return [x for x in raw if isinstance(x, dict)]


def norm(value: Any) -> str:
    return str(value or "").strip()


def group_count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    c = Counter(norm(r.get(key)) or "unknown" for r in rows)
    return dict(sorted(c.items(), key=lambda x: (-x[1], x[0])))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit selected low-quality/unknown historical source candidates."
    )
    script_path = Path(__file__).resolve()
    backend = script_path.parent
    if backend.name in {"scripts", "eval"}:
        backend = backend.parent

    parser.add_argument(
        "--input",
        type=Path,
        default=backend / "eval_outputs" / "source_quality" / "source_quality_candidates.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=backend / "eval_outputs" / "source_quality" / "audit",
    )
    args = parser.parse_args()

    rows = load_json(args.input.resolve())
    selected = [r for r in rows if r.get("selected") is True]
    low = [
        r for r in selected
        if norm(r.get("source_level_runtime")).lower() == "low_quality"
    ]
    unknown = [
        r for r in selected
        if norm(r.get("source_level_runtime")).lower() == "unknown"
    ]

    # Same URL appearing with conflicting runtime labels is useful evidence of
    # schema/policy drift across historical runs.
    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        url = norm(r.get("url"))
        if url:
            by_url[url].append(r)

    conflicts = []
    for url, items in by_url.items():
        labels = sorted({
            norm(x.get("source_level_runtime")).lower() or "unknown"
            for x in items
        })
        first_party_vals = sorted({
            str(x.get("first_party_runtime"))
            for x in items
            if x.get("first_party_runtime") is not None
        })
        if len(labels) > 1 or len(first_party_vals) > 1:
            conflicts.append({
                "url": url,
                "labels": labels,
                "first_party_values": first_party_vals,
                "tasks": sorted({norm(x.get("task_id")) for x in items}),
                "occurrences": len(items),
            })

    summary = {
        "selected_total": len(selected),
        "selected_low_quality": len(low),
        "selected_unknown": len(unknown),
        "selected_low_quality_by_task": group_count(low, "task_id"),
        "selected_low_quality_by_domain": group_count(low, "domain"),
        "selected_unknown_by_task": group_count(unknown, "task_id"),
        "selected_unknown_by_domain": group_count(unknown, "domain"),
        "conflicting_url_label_count": len(conflicts),
    }

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    (out / "source_quality_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "selected_low_quality.json").write_text(
        json.dumps(low, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "selected_unknown.json").write_text(
        json.dumps(unknown, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "conflicting_url_labels.json").write_text(
        json.dumps(conflicts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "task_id", "candidate_id", "title", "url", "domain",
        "source_level_runtime", "first_party_runtime",
        "official_confidence_runtime", "freshness_score", "rejection_reason",
    ]
    with (out / "selected_low_quality.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in low:
            w.writerow({k: row.get(k) for k in fields})

    lines = [
        "# Source Quality Anomaly Audit",
        "",
        f"- selected total: **{len(selected)}**",
        f"- selected low_quality: **{len(low)}**",
        f"- selected unknown: **{len(unknown)}**",
        f"- conflicting URL labels across history: **{len(conflicts)}**",
        "",
        "## Selected low_quality by task",
        "",
    ]
    for k, v in summary["selected_low_quality_by_task"].items():
        lines.append(f"- `{k}`: {v}")

    lines.extend(["", "## Selected low_quality by domain", ""])
    for k, v in summary["selected_low_quality_by_domain"].items():
        lines.append(f"- `{k}`: {v}")

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- 该审计只定位历史异常/版本混杂，不把 runtime source_level 当作人工 Gold。",
        "- 如果 16 条 low_quality selected 集中在少数老任务，说明全历史汇总不适合直接评价当前 Source Quality 策略。",
        "- 如果同一 URL 在历史中出现不同 source_level/first_party，则说明存在分类策略或 Schema 漂移，应按版本/时期分层。",
        "- 如果当前版本任务仍稳定选中 low_quality，再回到生产策略排查；本脚本不修改生产代码。",
        "",
    ])
    (out / "SOURCE_QUALITY_AUDIT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    print(f"Selected total: {len(selected)}")
    print(f"Selected low_quality: {len(low)}")
    print(f"Selected unknown: {len(unknown)}")
    print("Low-quality by task:", summary["selected_low_quality_by_task"])
    print("Low-quality by domain:", summary["selected_low_quality_by_domain"])
    print(f"Conflicting URL labels: {len(conflicts)}")
    print(f"Output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
