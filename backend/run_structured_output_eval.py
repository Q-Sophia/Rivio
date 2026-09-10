from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

OLD_SCHEMAS = {"CompetitiveAnalysisPortfolioV2"}
STAGED_SCHEMAS = {
    "AnalystBriefProfilesStage",
    "AnalystAssessmentStage",
    "AnalystClaimsStage",
}
ANALYST_SCHEMAS = OLD_SCHEMAS | STAGED_SCHEMAS


def load_json(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{path}: {type(exc).__name__}: {exc}"
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
    return [], f"{path}: unsupported JSON root {type(raw).__name__}"


def meta(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata")
    return value if isinstance(value, dict) else {}


def norm(value: Any) -> str:
    return str(value or "").strip()


def lower(value: Any) -> str:
    return norm(value).lower()


def as_num(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def is_completed(call: dict[str, Any]) -> bool:
    return lower(call.get("status")) in {"completed", "complete", "success", "succeeded"}


def is_failed(call: dict[str, Any]) -> bool:
    return lower(call.get("status")) in {"failed", "error"}


def finish_reason(call: dict[str, Any]) -> str:
    return lower(meta(call).get("finish_reason")) or "unknown"


def call_schema(call: dict[str, Any]) -> str:
    return norm(call.get("output_schema"))


def call_id(call: dict[str, Any]) -> str:
    return norm(call.get("id"))


def output_validation(output: dict[str, Any]) -> str:
    return lower(output.get("validation_status"))


def is_validation_pass(output: dict[str, Any]) -> bool | None:
    status = output_validation(output)
    if not status:
        return None
    if status in {"passed", "pass", "valid", "success", "completed"}:
        return True
    if status in {"failed", "fail", "invalid", "error"}:
        return False
    return None


def execution_kind(call: dict[str, Any]) -> str:
    m = meta(call)
    mock = m.get("mock_provider")
    real_enabled = m.get("real_calls_enabled")

    provider = lower(call.get("provider"))
    model = lower(call.get("model"))

    if mock is True or provider == "mock" or "mock" in model:
        return "mock"
    if real_enabled is True or mock is False:
        return "historical_real"
    return "unknown"


def architecture(schema: str) -> str | None:
    if schema in OLD_SCHEMAS:
        return "one_shot"
    if schema in STAGED_SCHEMAS:
        return "staged"
    return None


def infer_retry(call: dict[str, Any]) -> bool:
    node = lower(call.get("node_id"))
    return "attempt_2" in node or "retry" in node


def task_final_artifact_status(task_dir: Path, arch: str) -> dict[str, Any]:
    if arch == "one_shot":
        candidates = ["analysis_portfolios"]
    else:
        candidates = [
            "analysis_portfolios",
            "claims_v2",
            "analysis_assessments",
            "brief_assessments",
        ]
    counts = {}
    total = 0
    for name in candidates:
        items, _ = load_json(task_dir / f"{name}.json")
        counts[name] = len(items)
        total += len(items)
    return {
        "has_final_artifact": total > 0,
        "artifact_counts": counts,
    }


def scan_runs(runs_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []

    if not runs_dir.exists():
        return [], [f"runs dir not found: {runs_dir}"]

    for task_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        calls, err = load_json(task_dir / "llm_calls.json")
        if err:
            errors.append(err)
        outputs, err = load_json(task_dir / "llm_outputs.json")
        if err:
            errors.append(err)

        outputs_by_call: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for output in outputs:
            cid = norm(output.get("llm_call_id"))
            if cid:
                outputs_by_call[cid].append(output)

        for call in calls:
            schema = call_schema(call)
            arch = architecture(schema)
            if arch is None:
                continue

            linked_outputs = outputs_by_call.get(call_id(call), [])
            validation_values = [
                is_validation_pass(output)
                for output in linked_outputs
                if is_validation_pass(output) is not None
            ]
            if not linked_outputs:
                validation_pass = None
            elif validation_values:
                validation_pass = all(validation_values)
            else:
                validation_pass = None

            m = meta(call)
            final = task_final_artifact_status(task_dir, arch)

            records.append({
                "task_id": task_dir.name,
                "call_id": call_id(call),
                "node_id": norm(call.get("node_id")),
                "schema": schema,
                "architecture": arch,
                "execution_kind": execution_kind(call),
                "status": lower(call.get("status")) or "unknown",
                "completed": is_completed(call),
                "failed": is_failed(call),
                "finish_reason": finish_reason(call),
                "truncated": finish_reason(call) == "length",
                "retry_call": infer_retry(call),
                "used_fallback": bool(call.get("used_fallback")),
                "validation_pass": validation_pass,
                "linked_output_count": len(linked_outputs),
                "input_tokens": as_num(m.get("input_tokens")),
                "output_tokens": as_num(m.get("output_tokens")),
                "duration_ms": as_num(call.get("duration_ms")),
                "provider_attempts": as_num(m.get("attempts")),
                "final_artifact_present": final["has_final_artifact"],
                "final_artifact_counts": final["artifact_counts"],
            })
    return records, errors


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def avg(values: list[float | None]) -> float | None:
    clean = [x for x in values if x is not None]
    return round(statistics.mean(clean), 2) if clean else None


def median(values: list[float | None]) -> float | None:
    clean = [x for x in values if x is not None]
    return round(statistics.median(clean), 2) if clean else None


def summarize_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    validation_checkable = [r for r in records if r["validation_pass"] is not None]
    tasks = sorted({r["task_id"] for r in records})
    final_tasks = sorted({
        r["task_id"] for r in records if r["final_artifact_present"]
    })

    return {
        "call_count": len(records),
        "task_count": len(tasks),
        "task_ids": tasks,
        "structured_output_success_rate": rate(
            sum(1 for r in records if r["completed"]), len(records)
        ),
        "call_failure_rate": rate(
            sum(1 for r in records if r["failed"]), len(records)
        ),
        "json_truncation_rate": rate(
            sum(1 for r in records if r["truncated"]), len(records)
        ),
        "retry_call_rate": rate(
            sum(1 for r in records if r["retry_call"]), len(records)
        ),
        "fallback_rate": rate(
            sum(1 for r in records if r["used_fallback"]), len(records)
        ),
        "schema_validation_pass_rate": rate(
            sum(1 for r in validation_checkable if r["validation_pass"]),
            len(validation_checkable),
        ),
        "schema_validation_checkable_calls": len(validation_checkable),
        "final_artifact_task_success_rate": rate(len(final_tasks), len(tasks)),
        "final_artifact_task_count": len(final_tasks),
        "avg_input_tokens": avg([r["input_tokens"] for r in records]),
        "avg_output_tokens": avg([r["output_tokens"] for r in records]),
        "median_duration_ms": median([r["duration_ms"] for r in records]),
        "avg_provider_attempts": avg([r["provider_attempts"] for r in records]),
    }


def build_summary(records: list[dict[str, Any]], errors: list[str]) -> dict[str, Any]:
    groups: dict[str, Any] = {}

    for execution in ("historical_real", "mock", "unknown"):
        for arch in ("one_shot", "staged"):
            subset = [
                r for r in records
                if r["execution_kind"] == execution and r["architecture"] == arch
            ]
            groups[f"{execution}:{arch}"] = summarize_group(subset)

    real_one = [
        r for r in records
        if r["execution_kind"] == "historical_real" and r["architecture"] == "one_shot"
    ]
    real_staged = [
        r for r in records
        if r["execution_kind"] == "historical_real" and r["architecture"] == "staged"
    ]

    one_tasks = {r["task_id"] for r in real_one}
    staged_tasks = {r["task_id"] for r in real_staged}
    matched_tasks = sorted(one_tasks & staged_tasks)

    comparison_status = (
        "matched_historical_pair_available"
        if matched_tasks
        else "observational_only_not_causal"
    )

    return {
        "total_analyst_structured_calls": len(records),
        "historical_real_calls": sum(
            1 for r in records if r["execution_kind"] == "historical_real"
        ),
        "mock_calls": sum(1 for r in records if r["execution_kind"] == "mock"),
        "unknown_execution_calls": sum(
            1 for r in records if r["execution_kind"] == "unknown"
        ),
        "groups": groups,
        "real_one_shot_vs_staged": {
            "comparison_status": comparison_status,
            "matched_task_ids": matched_tasks,
            "one_shot": summarize_group(real_one),
            "staged": summarize_group(real_staged),
            "warning": (
                "Without matched same-task historical runs, differences are descriptive only "
                "and must not be presented as causal improvement."
            ),
        },
        "scan_error_count": len(errors),
        "scan_errors": errors[:100],
    }


def run_existing_regression(backend_dir: Path) -> dict[str, Any]:
    script = backend_dir / "check_step6f_analyst_output_stability.py"
    if not script.exists():
        return {
            "status": "not_found",
            "script": str(script),
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "script": str(script),
            "returncode": None,
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }

    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "script": str(script),
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def fmt_num(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def write_report(
    path: Path,
    summary: dict[str, Any],
    regression: dict[str, Any],
) -> None:
    comp = summary["real_one_shot_vs_staged"]
    old = comp["one_shot"]
    new = comp["staged"]

    lines = [
        "# Structured Output Eval V1",
        "",
        "纯本地历史审计 + 确定性回归。不会调用真实 LLM/Search/MCP。",
        "",
        "## 1. Historical inventory",
        "",
        f"- Analyst structured calls: **{summary['total_analyst_structured_calls']}**",
        f"- historical real: **{summary['historical_real_calls']}**",
        f"- mock: **{summary['mock_calls']}**",
        f"- unknown execution kind: **{summary['unknown_execution_calls']}**",
        "",
        "## 2. Historical real: one-shot vs staged",
        "",
        f"- comparison status: **{comp['comparison_status']}**",
        f"- matched task ids: `{comp['matched_task_ids']}`",
        "",
        "| Metric | One-shot | Staged |",
        "|---|---:|---:|",
        f"| Calls | {old['call_count']} | {new['call_count']} |",
        f"| Tasks | {old['task_count']} | {new['task_count']} |",
        f"| Structured output success | {pct(old['structured_output_success_rate'])} | {pct(new['structured_output_success_rate'])} |",
        f"| JSON truncation | {pct(old['json_truncation_rate'])} | {pct(new['json_truncation_rate'])} |",
        f"| Schema validation pass | {pct(old['schema_validation_pass_rate'])} | {pct(new['schema_validation_pass_rate'])} |",
        f"| Final artifact task success | {pct(old['final_artifact_task_success_rate'])} | {pct(new['final_artifact_task_success_rate'])} |",
        f"| Avg input tokens | {fmt_num(old['avg_input_tokens'])} | {fmt_num(new['avg_input_tokens'])} |",
        f"| Avg output tokens | {fmt_num(old['avg_output_tokens'])} | {fmt_num(new['avg_output_tokens'])} |",
        f"| Median latency ms | {fmt_num(old['median_duration_ms'])} | {fmt_num(new['median_duration_ms'])} |",
        "",
        "> 注意：如果没有同一 Task 的 matched before/after，本表只能作为历史描述性证据，不能写成“分阶段机制使成功率提升 X%”的因果结论。",
        "",
        "## 3. Deterministic truncation/retry regression",
        "",
        f"- `check_step6f_analyst_output_stability.py`: **{regression['status'].upper()}**",
        f"- returncode: `{regression['returncode']}`",
        "",
    ]

    if regression.get("stdout_tail"):
        lines.extend([
            "### stdout tail",
            "",
            "```text",
            regression["stdout_tail"].rstrip(),
            "```",
            "",
        ])
    if regression.get("stderr_tail"):
        lines.extend([
            "### stderr tail",
            "",
            "```text",
            regression["stderr_tail"].rstrip(),
            "```",
            "",
        ])

    lines.extend([
        "## 4. Interpretation rules",
        "",
        "- `CompetitiveAnalysisPortfolioV2` 记为旧 one-shot Analyst structured output。",
        "- `AnalystBriefProfilesStage / AnalystAssessmentStage / AnalystClaimsStage` 记为 staged Analyst structured output。",
        "- `finish_reason=length` 记为 JSON truncation。",
        "- `validation_status` 只在 `llm_outputs.json` 中真实存在时纳入 schema pass 分母；缺失不算失败。",
        "- `historical_real` 与 `mock` 必须分开；Mock regression 证明机制正确性，不代表线上模型质量。",
        "- Final artifact success 只表示该历史任务目录存在对应分析产物，不等同于语义质量。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_self_test() -> None:
    root = Path(tempfile.mkdtemp())
    task1 = root / "real_old"
    task1.mkdir()
    (task1 / "llm_calls.json").write_text(json.dumps([
        {
            "id": "c1",
            "output_schema": "CompetitiveAnalysisPortfolioV2",
            "status": "failed",
            "duration_ms": 500,
            "metadata": {
                "mock_provider": False,
                "real_calls_enabled": True,
                "finish_reason": "length",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
        }
    ]), encoding="utf-8")
    (task1 / "llm_outputs.json").write_text(json.dumps([
        {"id": "o1", "llm_call_id": "c1", "validation_status": "failed"}
    ]), encoding="utf-8")

    task2 = root / "real_staged"
    task2.mkdir()
    (task2 / "llm_calls.json").write_text(json.dumps([
        {
            "id": "c2",
            "node_id": "analyst_brief_attempt_1",
            "output_schema": "AnalystBriefProfilesStage",
            "status": "completed",
            "duration_ms": 300,
            "metadata": {
                "mock_provider": False,
                "real_calls_enabled": True,
                "finish_reason": "stop",
                "input_tokens": 600,
                "output_tokens": 200,
            },
        },
        {
            "id": "c3",
            "node_id": "analyst_claims_attempt_2",
            "output_schema": "AnalystClaimsStage",
            "status": "completed",
            "duration_ms": 350,
            "metadata": {
                "mock_provider": False,
                "real_calls_enabled": True,
                "finish_reason": "stop",
                "input_tokens": 700,
                "output_tokens": 250,
            },
        },
    ]), encoding="utf-8")
    (task2 / "llm_outputs.json").write_text(json.dumps([
        {"id": "o2", "llm_call_id": "c2", "validation_status": "passed"},
        {"id": "o3", "llm_call_id": "c3", "validation_status": "passed"},
    ]), encoding="utf-8")
    (task2 / "claims_v2.json").write_text(json.dumps([{"id": "claim1"}]), encoding="utf-8")

    records, errors = scan_runs(root)
    assert not errors
    summary = build_summary(records, errors)
    old = summary["groups"]["historical_real:one_shot"]
    staged = summary["groups"]["historical_real:staged"]
    assert old["json_truncation_rate"] == 1.0
    assert staged["structured_output_success_rate"] == 1.0
    assert staged["retry_call_rate"] == 0.5
    assert staged["schema_validation_pass_rate"] == 1.0
    print("SELF_TEST_PASS")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Structured Output Eval for Analyst historical artifacts."
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
        default=backend / "eval_outputs" / "structured_output",
    )
    parser.add_argument(
        "--skip-regression",
        action="store_true",
        help="Do not run existing deterministic Step6F regression.",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return 0

    runs_dir = args.runs_dir.resolve()
    output_dir = args.output_dir.resolve()

    records, errors = scan_runs(runs_dir)
    summary = build_summary(records, errors)
    regression = (
        {"status": "skipped", "returncode": None, "stdout_tail": "", "stderr_tail": ""}
        if args.skip_regression
        else run_existing_regression(backend)
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "structured_output_calls.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "structured_output_eval_summary.json").write_text(
        json.dumps(
            {"summary": summary, "deterministic_regression": regression},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_report(
        output_dir / "STRUCTURED_OUTPUT_EVAL.md",
        summary,
        regression,
    )

    comp = summary["real_one_shot_vs_staged"]
    old = comp["one_shot"]
    new = comp["staged"]

    print(f"Analyst structured calls: {summary['total_analyst_structured_calls']}")
    print(
        f"Historical real / mock / unknown: "
        f"{summary['historical_real_calls']} / "
        f"{summary['mock_calls']} / "
        f"{summary['unknown_execution_calls']}"
    )
    print(
        "Real one-shot: "
        f"calls={old['call_count']} "
        f"success={pct(old['structured_output_success_rate'])} "
        f"truncation={pct(old['json_truncation_rate'])} "
        f"validation={pct(old['schema_validation_pass_rate'])}"
    )
    print(
        "Real staged: "
        f"calls={new['call_count']} "
        f"success={pct(new['structured_output_success_rate'])} "
        f"truncation={pct(new['json_truncation_rate'])} "
        f"validation={pct(new['schema_validation_pass_rate'])}"
    )
    print(f"Comparison: {comp['comparison_status']}")
    print(f"Deterministic regression: {regression['status']}")
    print(f"Output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
