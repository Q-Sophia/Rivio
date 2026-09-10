from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

CORE_FILES = {
    "analysis_tasks",
    "research_tasks",
    "sources",
    "evidence",
    "reports",
    "report_statements",
    "task_records",
}

COUNT_ARTIFACTS = {
    "research_tasks": "research_task_count",
    "sources": "source_count",
    "source_chunks": "chunk_count",
    "evidence": "verified_evidence_count",
    "citation_checks": "citation_check_count",
    "reports": "report_count",
    "report_statements": "report_statement_count",
}

STATUS_FAILED = {"failed", "error", "aborted", "cancelled", "canceled"}
STATUS_PARTIAL = {"partial", "requires_human", "waiting_for_human"}
STATUS_COMPLETE = {"completed", "complete", "passed", "success", "succeeded", "finished"}


def load_json_items(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{path.name}: {type(exc).__name__}: {exc}"

    if raw is None:
        return [], None
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)], None
    if isinstance(raw, dict):
        for key in ("items", "records", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)], None
        return [raw], None
    return [], f"{path.name}: unsupported JSON root type {type(raw).__name__}"


def latest(items: list[dict[str, Any]]) -> dict[str, Any]:
    return items[-1] if items else {}


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def nested_metadata(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata")
    return value if isinstance(value, dict) else {}


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    text = str(value).strip()
    return text if text else None


def bool_from_any(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "passed", "pass"}:
            return True
        if v in {"false", "0", "no", "failed", "fail", "blocked"}:
            return False
    return None


def artifact_path(task_dir: Path, artifact_type: str) -> Path:
    return task_dir / f"{artifact_type}.json"


def read_artifact(task_dir: Path, artifact_type: str, errors: list[str]) -> list[dict[str, Any]]:
    items, err = load_json_items(artifact_path(task_dir, artifact_type))
    if err:
        errors.append(err)
    return items


def find_pipeline_status(task_dir: Path, errors: list[str]) -> str | None:
    preferred = (
        "pipeline_runs",
        "pipeline_states",
        "pipeline_state",
        "unified_pipeline_runs",
        "pipeline_checkpoints",
    )
    checked: set[Path] = set()

    for name in preferred:
        path = artifact_path(task_dir, name)
        checked.add(path)
        items, err = load_json_items(path)
        if err:
            errors.append(err)
        if items:
            item = latest(items)
            value = first_nonempty(
                item.get("status"),
                item.get("pipeline_status"),
                item.get("state"),
            )
            if value is not None:
                return normalize_status(value)

    for path in sorted(task_dir.glob("pipeline*.json")):
        if path in checked:
            continue
        items, err = load_json_items(path)
        if err:
            errors.append(err)
        if not items:
            continue
        item = latest(items)
        value = first_nonempty(
            item.get("status"),
            item.get("pipeline_status"),
            item.get("state"),
        )
        if value is not None:
            return normalize_status(value)
    return None


def extract_framework(
    analysis_task: dict[str, Any],
    research_tasks: list[dict[str, Any]],
    research_plans: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    candidates = [analysis_task, *research_tasks, *research_plans]
    framework_id = None
    framework_version = None

    for item in candidates:
        meta = nested_metadata(item)
        framework_id = first_nonempty(
            framework_id,
            item.get("framework_id"),
            meta.get("framework_id"),
        )
        framework_version = first_nonempty(
            framework_version,
            item.get("framework_version"),
            meta.get("framework_version"),
        )
        if framework_id and framework_version:
            break

    return (
        str(framework_id) if framework_id is not None else None,
        str(framework_version) if framework_version is not None else None,
    )


def detect_supplement(research_tasks: list[dict[str, Any]]) -> bool:
    for item in research_tasks:
        meta = nested_metadata(item)
        if first_nonempty(
            item.get("parent_research_task_id"),
            item.get("gap_id"),
            item.get("research_gap_id"),
        ):
            return True
        if int(item.get("collection_round") or 0) > 1:
            return True
        source = str(meta.get("source") or "").lower()
        if source in {"r2_mission_supervisor", "supplement", "supplement_task"}:
            return True
        if bool(meta.get("is_supplement")) or bool(meta.get("supplemental")):
            return True
    return False


def research_flags(
    research_tasks: list[dict[str, Any]],
    agent_runs: list[dict[str, Any]],
    loop_runs: list[dict[str, Any]],
) -> tuple[bool, bool]:
    partial = False
    failed = False

    values: list[str] = []
    for item in [*research_tasks, *agent_runs, *loop_runs]:
        for key in ("status", "outcome", "stop_reason"):
            value = normalize_status(item.get(key))
            if value:
                values.append(value.lower())

    for value in values:
        if value in STATUS_PARTIAL or "partial" in value:
            partial = True
        if value in STATUS_FAILED or "fail" in value or "exhausted" in value:
            failed = True

    return partial, failed


def completion_class(
    pipeline_status: str | None,
    quality_gate_status: str | None,
    quality_gate_passed: bool | None,
    has_report: bool,
    has_partial_research: bool,
    has_failed_research: bool,
) -> str:
    p = (pipeline_status or "").lower()
    q = (quality_gate_status or "").lower()

    if p in STATUS_FAILED or "fail" in p or "error" in p or "abort" in p:
        return "failed"
    if quality_gate_passed is False and q in {"failed", "blocked", "rejected"}:
        return "failed"
    if has_failed_research and not has_report:
        return "failed"
    if quality_gate_passed is True:
        return "complete"
    if p in STATUS_COMPLETE:
        return "complete"
    if has_partial_research:
        return "partial"
    if has_report:
        return "partial"
    return "unknown"


def task_size(research_task_count: int) -> str:
    if research_task_count <= 5:
        return "small"
    if research_task_count <= 10:
        return "medium"
    return "large"


def scan_task(task_dir: Path) -> dict[str, Any]:
    errors: list[str] = []

    analysis_tasks = read_artifact(task_dir, "analysis_tasks", errors)
    research_tasks = read_artifact(task_dir, "research_tasks", errors)
    research_plans = read_artifact(task_dir, "research_plans", errors)
    agent_runs = read_artifact(task_dir, "research_agent_runs", errors)
    loop_runs = read_artifact(task_dir, "research_loop_runs", errors)
    sources = read_artifact(task_dir, "sources", errors)
    chunks = read_artifact(task_dir, "source_chunks", errors)
    evidence = read_artifact(task_dir, "evidence", errors)
    gaps_old = read_artifact(task_dir, "research_gaps", errors)
    gaps_new = read_artifact(task_dir, "analysis_research_gaps", errors)
    claims_v2 = read_artifact(task_dir, "claims_v2", errors)
    claims_legacy = read_artifact(task_dir, "claims", errors)
    citations = read_artifact(task_dir, "citation_checks", errors)
    reports = read_artifact(task_dir, "reports", errors)
    statements = read_artifact(task_dir, "report_statements", errors)
    gates = read_artifact(task_dir, "quality_gates", errors)

    analysis_task = latest(analysis_tasks)
    research_plan = latest(research_plans)
    gate = latest(gates)

    query = first_nonempty(
        analysis_task.get("query"),
        research_plan.get("decision_question"),
        analysis_task.get("research_question"),
    )

    competitors = analysis_task.get("competitors")
    if not isinstance(competitors, list):
        competitors = research_plan.get("covered_competitors")
    if not isinstance(competitors, list):
        competitors = []
    competitors = [str(x) for x in competitors if str(x).strip()]

    framework_id, framework_version = extract_framework(
        analysis_task, research_tasks, research_plans
    )

    pipeline_status = find_pipeline_status(task_dir, errors)
    quality_gate_status = normalize_status(
        first_nonempty(gate.get("status"), gate.get("decision"))
    )
    quality_gate_passed = bool_from_any(
        first_nonempty(gate.get("passed"), gate.get("approved"))
    )

    if claims_v2:
        claims = claims_v2
        claim_artifact = "claims_v2"
    else:
        claims = claims_legacy
        claim_artifact = "claims" if claims_legacy else None

    gap_ids: set[str] = set()
    fallback_gap_count = 0
    for item in [*gaps_old, *gaps_new]:
        gid = str(item.get("id") or "").strip()
        if gid:
            gap_ids.add(gid)
        else:
            fallback_gap_count += 1
    research_gap_count = len(gap_ids) + fallback_gap_count

    if agent_runs:
        research_run_count = len(agent_runs)
        research_run_artifact = "research_agent_runs"
    else:
        research_run_count = len(loop_runs)
        research_run_artifact = "research_loop_runs" if loop_runs else None

    has_partial_research, has_failed_research = research_flags(
        research_tasks, agent_runs, loop_runs
    )

    has_report = bool(reports)
    has_quality_gate = bool(gates)
    trace_prereq = (
        bool(sources)
        and bool(evidence)
        and bool(claims)
        and bool(reports or statements)
    )

    created_at = first_nonempty(
        analysis_task.get("created_at"),
        analysis_task.get("createdAt"),
        research_plan.get("created_at"),
    )

    row = {
        "task_id": task_dir.name,
        "created_at": created_at,
        "query": query,
        "competitors": competitors,
        "competitor_count": len(competitors),
        "framework_id": framework_id,
        "framework_version": framework_version,
        "pipeline_status": pipeline_status,
        "quality_gate_status": quality_gate_status,
        "quality_gate_passed": quality_gate_passed,
        "research_task_count": len(research_tasks),
        "research_run_count": research_run_count,
        "research_run_artifact": research_run_artifact,
        "source_count": len(sources),
        "chunk_count": len(chunks),
        "verified_evidence_count": len(evidence),
        "research_gap_count": research_gap_count,
        "claim_count": len(claims),
        "claim_artifact": claim_artifact,
        "citation_check_count": len(citations),
        "report_count": len(reports),
        "report_statement_count": len(statements),
        "has_report": has_report,
        "has_quality_gate": has_quality_gate,
        "has_supplement_task": detect_supplement(research_tasks),
        "has_partial_research": has_partial_research,
        "has_failed_research": has_failed_research,
        "has_traceability_prerequisites": trace_prereq,
        "completion_class": None,
        "task_size": task_size(len(research_tasks)),
        "scan_status": "ok" if not errors else "partial",
        "scan_error": "; ".join(errors) if errors else None,
    }

    row["completion_class"] = completion_class(
        pipeline_status=pipeline_status,
        quality_gate_status=quality_gate_status,
        quality_gate_passed=quality_gate_passed,
        has_report=has_report,
        has_partial_research=has_partial_research,
        has_failed_research=has_failed_research,
    )
    return row


def is_task_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    names = {p.stem for p in path.glob("*.json")}
    return bool(names & CORE_FILES)


def numeric_summary(rows: list[dict[str, Any]], field: str) -> dict[str, int | float | None]:
    values = [int(row.get(field) or 0) for row in rows]
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def count(field: str, value: Any = True) -> int:
        return sum(1 for row in rows if row.get(field) == value)

    return {
        "total_tasks": len(rows),
        "scan_success": count("scan_status", "ok"),
        "scan_partial": count("scan_status", "partial"),
        "scan_failed": count("scan_status", "failed"),
        "completion": {
            "complete": count("completion_class", "complete"),
            "partial": count("completion_class", "partial"),
            "failed": count("completion_class", "failed"),
            "unknown": count("completion_class", "unknown"),
        },
        "task_size": {
            "small": count("task_size", "small"),
            "medium": count("task_size", "medium"),
            "large": count("task_size", "large"),
        },
        "has_report": count("has_report"),
        "has_quality_gate": count("has_quality_gate"),
        "has_traceability_prerequisites": count("has_traceability_prerequisites"),
        "has_supplement_task": count("has_supplement_task"),
        "has_partial_research": count("has_partial_research"),
        "has_failed_research": count("has_failed_research"),
        "counts": {
            field: numeric_summary(rows, field)
            for field in (
                "research_task_count",
                "source_count",
                "verified_evidence_count",
                "claim_count",
                "report_statement_count",
            )
        },
    }


def shortlist(rows: list[dict[str, Any]], limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    def pick(predicate):
        matched = [row for row in rows if predicate(row)]
        matched.sort(
            key=lambda r: (
                -int(r.get("verified_evidence_count") or 0),
                -int(r.get("claim_count") or 0),
                -int(r.get("report_statement_count") or 0),
                str(r.get("task_id") or ""),
            )
        )
        fields = (
            "task_id",
            "completion_class",
            "research_task_count",
            "source_count",
            "verified_evidence_count",
            "claim_count",
            "report_statement_count",
            "has_traceability_prerequisites",
            "has_supplement_task",
        )
        return [{k: row.get(k) for k in fields} for row in matched[:limit]]

    return {
        "complete_with_traceability_prerequisites": pick(
            lambda r: r["completion_class"] == "complete"
            and r["has_traceability_prerequisites"]
        ),
        "partial": pick(lambda r: r["completion_class"] == "partial"),
        "failed": pick(lambda r: r["completion_class"] == "failed"),
        "with_supplement": pick(lambda r: r["has_supplement_task"]),
        "large": pick(lambda r: r["task_size"] == "large"),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["competitors"] = json.dumps(
                out["competitors"], ensure_ascii=False
            )
            writer.writerow(out)


def write_report(
    path: Path,
    runs_dir: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    candidates: dict[str, list[dict[str, Any]]],
) -> None:
    lines = [
        "# EVAL Task Inventory Lite",
        "",
        f"- Artifact root: `{runs_dir}`",
        f"- Total tasks: **{summary['total_tasks']}**",
        f"- Scan OK / Partial / Failed: **{summary['scan_success']} / {summary['scan_partial']} / {summary['scan_failed']}**",
        "",
        "## Distribution",
        "",
        f"- completion: {summary['completion']}",
        f"- task_size: {summary['task_size']}",
        f"- has_report: {summary['has_report']}",
        f"- has_quality_gate: {summary['has_quality_gate']}",
        f"- has_traceability_prerequisites: {summary['has_traceability_prerequisites']}",
        f"- has_supplement_task: {summary['has_supplement_task']}",
        f"- has_partial_research: {summary['has_partial_research']}",
        f"- has_failed_research: {summary['has_failed_research']}",
        "",
        "## Count ranges",
        "",
    ]
    for field, values in summary["counts"].items():
        lines.append(
            f"- {field}: min={values['min']}, median={values['median']}, max={values['max']}"
        )

    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- `has_traceability_prerequisites` 仅表示 Source + Evidence + Claim + Report/ReportStatement 同时存在；本轮不做逐 ID 全链验证。",
            "- `claim_count` 优先使用 `claims_v2`，不存在时回退 `claims`。",
            "- `research_run_count` 优先使用 `research_agent_runs`，不存在时回退 `research_loop_runs`。",
            "- supplement：存在 `parent_research_task_id` / gap provenance、`collection_round > 1`，或 metadata.source=`r2_mission_supervisor` 时判定。",
            "- task_size：<=5 small，6~10 medium，>10 large。",
            "- 无法低成本确定的字段保留 null/unknown，不做语义推断。",
            "",
            "## Candidate shortlist",
            "",
        ]
    )
    for group, items in candidates.items():
        lines.append(f"### {group}")
        if not items:
            lines.append("- none")
        else:
            for item in items:
                lines.append(
                    f"- `{item['task_id']}` | rt={item['research_task_count']} | "
                    f"src={item['source_count']} | ev={item['verified_evidence_count']} | "
                    f"claim={item['claim_count']} | stmt={item['report_statement_count']}"
                )
        lines.append("")

    partial_rows = [r for r in rows if r["scan_status"] != "ok"]
    if partial_rows:
        lines.extend(["## Scan warnings", ""])
        for row in partial_rows[:30]:
            lines.append(f"- `{row['task_id']}`: {row['scan_error']}")
        if len(partial_rows) > 30:
            lines.append(f"- ... {len(partial_rows) - 30} more")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build lightweight local Eval task inventory.")
    script_path = Path(__file__).resolve()
    default_backend = script_path.parents[1] if script_path.parent.name == "scripts" else Path.cwd()
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=default_backend / "app" / "data" / "runs",
        help="ArtifactStore task root. Default: backend/app/data/runs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_backend / "eval_outputs" / "task_inventory",
        help="Output directory.",
    )
    args = parser.parse_args()

    runs_dir = args.runs_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not runs_dir.exists():
        raise SystemExit(f"Artifact root does not exist: {runs_dir}")

    task_dirs = sorted(
        [p for p in runs_dir.iterdir() if is_task_dir(p)],
        key=lambda p: p.name,
    )

    rows: list[dict[str, Any]] = []
    for task_dir in task_dirs:
        try:
            rows.append(scan_task(task_dir))
        except Exception as exc:
            rows.append(
                {
                    "task_id": task_dir.name,
                    "created_at": None,
                    "query": None,
                    "competitors": [],
                    "competitor_count": 0,
                    "framework_id": None,
                    "framework_version": None,
                    "pipeline_status": None,
                    "quality_gate_status": None,
                    "quality_gate_passed": None,
                    "research_task_count": 0,
                    "research_run_count": 0,
                    "research_run_artifact": None,
                    "source_count": 0,
                    "chunk_count": 0,
                    "verified_evidence_count": 0,
                    "research_gap_count": 0,
                    "claim_count": 0,
                    "claim_artifact": None,
                    "citation_check_count": 0,
                    "report_count": 0,
                    "report_statement_count": 0,
                    "has_report": False,
                    "has_quality_gate": False,
                    "has_supplement_task": False,
                    "has_partial_research": False,
                    "has_failed_research": False,
                    "has_traceability_prerequisites": False,
                    "completion_class": "unknown",
                    "task_size": "small",
                    "scan_status": "failed",
                    "scan_error": f"{type(exc).__name__}: {exc}",
                }
            )

    summary = build_summary(rows)
    candidates = shortlist(rows)

    output_dir.mkdir(parents=True, exist_ok=True)

    inventory_json = output_dir / "eval_task_inventory.json"
    inventory_csv = output_dir / "eval_task_inventory.csv"
    summary_json = output_dir / "eval_task_inventory_summary.json"
    shortlist_json = output_dir / "eval_candidate_shortlist.json"
    report_md = output_dir / "EVAL_TASK_INVENTORY_REPORT.md"

    inventory_json.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(inventory_csv, rows)
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shortlist_json.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(report_md, runs_dir, rows, summary, candidates)

    print(f"Artifact root: {runs_dir}")
    print(f"Tasks found: {summary['total_tasks']}")
    print(
        "Completion: "
        f"complete={summary['completion']['complete']} "
        f"partial={summary['completion']['partial']} "
        f"failed={summary['completion']['failed']} "
        f"unknown={summary['completion']['unknown']}"
    )
    print(
        "Replay prerequisites: "
        f"{summary['has_traceability_prerequisites']}"
    )
    print(f"Supplement tasks: {summary['has_supplement_task']}")
    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
