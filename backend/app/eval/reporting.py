from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from app.eval.cases import EvalCase
from app.eval.frozen import write_json


E2_CSV_FIELDS = (
    "case",
    "variant",
    "research_success",
    "coverage",
    "evidence_count",
    "valid_evidence_count",
    "unique_sources",
    "official_source_ratio",
    "tool_calls",
    "llm_calls",
    "tokens",
    "elapsed_ms",
)

E3_CSV_FIELDS = (
    "case",
    "variant",
    "initial_coverage",
    "final_coverage",
    "coverage_delta",
    "initial_gap_count",
    "final_gap_count",
    "gap_closure_rate",
    "supplement_rounds",
    "extra_tool_calls",
    "extra_llm_calls",
    "extra_tokens",
    "extra_elapsed_ms",
)


ARCHITECTURE_DIFFERENCE = """# E2 Architecture Difference

## Compared systems

- E2-A reuses `ResearchLoopRunner`: fixed query hints → `CollectorQueueService.run_once()` → `ExtractorQueueService.run_once()` → `Step6E4QueueService.run_once()`.
- E2-B reuses `ResearchAgentCoordinator`: the current Research Agent executes bounded `SEARCH / FETCH / READ / SUBMIT_EVIDENCE / FINISH` actions and then refreshes Coverage/Gap state.

## Fairly comparable metrics

Evidence count, Coverage derived from persisted `EvidenceCoverage`, unique sources, explicitly classified official-source ratio, total ToolCall/LLMCall records, recorded token usage, elapsed time, and terminal success/failure are comparable at the system-output level.

Action-specific counts are not always symmetric. Legacy `search_count` and `fetch_count` map to `SearchAttempt` and `CollectionAttempt`; Legacy `read_count` is N/A because that architecture has no equivalent persisted READ action. Any missing explicit official or quote-verification metadata remains N/A rather than being inferred.

## Supported interpretation

Results may support: “The current Research Agent implementation performed differently from the project's earlier Legacy Research Pipeline on these cases.”

## Unsupported interpretation

E2 is not a single-variable Research Policy ablation. Query generation, loop control, evidence formation, and action models differ, so results must not attribute all changes solely to the dynamic Action Policy. Live Web and LLM nondeterminism also prevent exact replay equivalence.
"""


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: "N/A" if row.get(field) is None else row.get(field)
                    for field in fields
                }
            )


def _load_variant_metrics(run_dir: Path, experiment: str) -> list[dict[str, Any]]:
    root = run_dir / experiment
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("case_*/*/metrics.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in rows if item.get(key) is not None]
    return mean(values) if values else None


def _aggregate_by_variant(
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    aggregates: list[dict[str, Any]] = []
    for variant in sorted({str(item.get("variant") or "") for item in rows}):
        selected = [item for item in rows if item.get("variant") == variant]
        aggregates.append(
            {
                "variant": variant,
                "case_count": len(selected),
                "success_count": sum(
                    item.get("research_success") is True for item in selected
                ),
                "failure_count": sum(
                    item.get("research_success") is not True for item in selected
                ),
                **{key: _average(selected, key) for key in keys},
            }
        )
    return aggregates


def _format(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value * 100:.1f}%" if percent else f"{value:.3f}"
    return str(value)


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str, bool]]) -> str:
    if not rows:
        return "尚无结果。"
    headers = [item[0] for item in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _format(row.get(key), percent=percent)
                for _label, key, percent in columns
            )
            + " |"
        )
    return "\n".join(lines)


def generate_outputs(
    *,
    run_dir: Path,
    cases: list[EvalCase],
) -> dict[str, Any]:
    e2 = _load_variant_metrics(run_dir, "e2")
    e3 = _load_variant_metrics(run_dir, "e3")
    write_json(run_dir / "e2_case_results.json", e2)
    write_json(run_dir / "e3_case_results.json", e3)
    _write_csv(run_dir / "e2_summary.csv", e2, E2_CSV_FIELDS)
    _write_csv(run_dir / "e3_summary.csv", e3, E3_CSV_FIELDS)
    (run_dir / "e2_architecture_difference.md").write_text(
        ARCHITECTURE_DIFFERENCE,
        encoding="utf-8",
    )

    expected_cases = {item.id for item in cases}
    e2_pairs = {(item.get("case"), item.get("variant")) for item in e2}
    e3_pairs = {(item.get("case"), item.get("variant")) for item in e3}
    evaluation_complete = all(
        (case_id, variant) in e2_pairs
        for case_id in expected_cases
        for variant in ("legacy", "agent")
    ) and all(
        (case_id, variant) in e3_pairs
        for case_id in expected_cases
        for variant in ("supplement_off", "supplement_on")
    )
    failures = [
        item
        for item in [*e2, *e3]
        if not item.get("research_success") or item.get("execution_error")
    ]
    manifest_lines = "\n".join(
        f"- `{item.id}`（{item.category}）：{item.request}" for item in cases
    )
    e2_table = _table(
        e2,
        [
            ("Case", "case", False),
            ("Variant", "variant", False),
            ("Success", "research_success", False),
            ("Coverage", "coverage", True),
            ("Evidence", "evidence_count", False),
            ("Tool Calls", "tool_calls", False),
            ("Tokens", "tokens", False),
            ("Elapsed ms", "elapsed_ms", False),
        ],
    )
    e3_table = _table(
        e3,
        [
            ("Case", "case", False),
            ("Variant", "variant", False),
            ("Initial", "initial_coverage", True),
            ("Final", "final_coverage", True),
            ("Delta", "coverage_delta", True),
            ("Gap closure", "gap_closure_rate", True),
            ("Extra tools", "extra_tool_calls", False),
            ("Extra tokens", "extra_tokens", False),
            ("Extra ms", "extra_elapsed_ms", False),
        ],
    )
    e2_aggregate_table = _table(
        _aggregate_by_variant(
            e2,
            ("coverage", "tool_calls", "llm_calls", "tokens", "elapsed_ms"),
        ),
        [
            ("Variant", "variant", False),
            ("Cases", "case_count", False),
            ("Success", "success_count", False),
            ("Failure", "failure_count", False),
            ("Avg Coverage", "coverage", True),
            ("Avg Tools", "tool_calls", False),
            ("Avg LLM", "llm_calls", False),
            ("Avg Tokens", "tokens", False),
            ("Avg ms", "elapsed_ms", False),
        ],
    )
    e3_aggregate_table = _table(
        _aggregate_by_variant(
            e3,
            (
                "initial_coverage",
                "final_coverage",
                "coverage_delta",
                "gap_closure_rate",
                "extra_tool_calls",
                "extra_llm_calls",
                "extra_tokens",
                "extra_elapsed_ms",
            ),
        ),
        [
            ("Variant", "variant", False),
            ("Cases", "case_count", False),
            ("Initial", "initial_coverage", True),
            ("Final", "final_coverage", True),
            ("Delta", "coverage_delta", True),
            ("Gap closure", "gap_closure_rate", True),
            ("Extra tools", "extra_tool_calls", False),
            ("Extra LLM", "extra_llm_calls", False),
            ("Extra tokens", "extra_tokens", False),
            ("Extra ms", "extra_elapsed_ms", False),
        ],
    )
    failure_text = (
        "\n".join(
            f"- `{item.get('case')}/{item.get('variant')}`: "
            f"{item.get('execution_error') or 'research_success=false'}"
            for item in failures
        )
        if failures
        else "当前已记录结果中没有失败 case。"
    )
    supplement_on = [
        item for item in e3 if item.get("variant") == "supplement_on"
    ]
    conclusions = (
        "正式的 5-case E2/E3 结果已齐备。以下汇总仅描述观测值：\n\n"
        f"- Supplement ON 平均 Initial Coverage：{_format(_average(supplement_on, 'initial_coverage'), percent=True)}\n"
        f"- Supplement ON 平均 Final Coverage：{_format(_average(supplement_on, 'final_coverage'), percent=True)}\n"
        f"- Supplement ON 平均 Coverage Delta：{_format(_average(supplement_on, 'coverage_delta'), percent=True)}\n"
        f"- Supplement ON 平均 Gap Closure Rate：{_format(_average(supplement_on, 'gap_closure_rate'), percent=True)}\n"
        f"- Supplement ON 平均 Extra Tool Calls：{_format(_average(supplement_on, 'extra_tool_calls'))}\n"
        f"- Supplement ON 平均 Extra LLM Calls：{_format(_average(supplement_on, 'extra_llm_calls'))}\n"
        f"- Supplement ON 平均 Extra Tokens：{_format(_average(supplement_on, 'extra_tokens'))}\n"
        f"- Supplement ON 平均 Extra Time：{_format(_average(supplement_on, 'extra_elapsed_ms'))} ms\n"
        if evaluation_complete
        else "尚未齐备 5 个 case 的 E2/E3 双侧结果，因此不生成实验结论。"
    )
    summary = f"""# Evaluation Scope

RIVIO-EVAL-R1 固定为 5 个 case，是小规模机制验证，不用于统计显著性结论。结果元数据固定记录 `LIVE_WEB_NONDETERMINISM = true`。

# Case Manifest

{manifest_lines}

# E2 Legacy vs Research Agent

{e2_table}

## E2 Aggregate by Variant

{e2_aggregate_table}

# E2 Interpretation

E2 是系统级对照，不是严格单变量 policy ablation。两条路径的 Query 生成、Research Loop、Evidence formation 和 Action 模型均可能不同；不能把全部变化归因于动态 Action Policy。

# E3 Bounded Supplement Ablation

{e3_table}

## E3 Aggregate by Variant

{e3_aggregate_table}

# Failure Cases

{failure_text}

# Limitations

- 仅有 5 个 case，样本较小。
- Live Web 存在时间与排序非确定性。
- LLM 输出存在非确定性。
- E2 两套架构存在多处实现差异。
- N/A 表示对应架构没有公平映射，或 Artifact 未显式记录该值；不会人为推算。

# Resume-safe Conclusions

{conclusions}
"""
    (run_dir / "eval_summary.md").write_text(summary, encoding="utf-8")
    return {
        "e2_result_count": len(e2),
        "e3_result_count": len(e3),
        "failure_count": len(failures),
        "evaluation_complete": evaluation_complete,
        "summary_path": str(run_dir / "eval_summary.md"),
    }
