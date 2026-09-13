from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from app.eval.__main__ import build_parser
from app.eval.adapters import ContextGovernanceResearchAgentAdapter
from app.eval.frozen import write_json
from app.eval.runner import EvaluationRunner
from app.harness.artifacts import ArtifactStore


CASE_ID = "case_01"
FROZEN_HASH = "frozen_case_01_same_hash"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_fake_scenario(
    root: Path,
    name: str,
    outcomes: dict[bool, str],
) -> tuple[Path, FakeFrozenInputManager, FakeContextAgentAdapter]:
    frozen = FakeFrozenInputManager()
    adapter = FakeContextAgentAdapter(outcomes)
    runner = EvaluationRunner(
        workspace_root=root / name,
        frozen_manager=frozen,
        context_agent_adapter=adapter,
    )
    run_dir = runner.run_context_governance_ab(case_id=CASE_ID)
    return run_dir, frozen, adapter


class FakeFrozenInputManager:
    def __init__(self, case_id: str = CASE_ID):
        self.case_id = case_id
        self.materializations: list[dict] = []

    def validate_case(self, case_id: str):
        require(case_id == self.case_id, "Fake frozen manager case 不匹配")
        return {"case": case_id, "frozen_input_hash": FROZEN_HASH}

    def load_case(self, case_id: str):
        self.validate_case(case_id)
        return {
            "manifest": {
                "case": case_id,
                "frozen_input_hash": FROZEN_HASH,
            },
            "research_plan": {
                "budget": {
                    "max_total_sources": 40,
                    "max_sources_per_task": 5,
                    "max_collection_rounds": 3,
                }
            },
        }

    def materialize(
        self,
        case_id: str,
        *,
        store,
        task_id: str,
        variant: str,
    ):
        self.validate_case(case_id)
        self.materializations.append(
            {
                "case": case_id,
                "root": str(store.root_dir),
                "task_id": task_id,
                "variant": variant,
                "frozen_input_hash": FROZEN_HASH,
            }
        )
        return {
            "task_id": task_id,
            "frozen_input_hash": FROZEN_HASH,
            "research_task_count": 2,
            "information_need_count": 1,
        }


class FakeContextAgentAdapter:
    def __init__(self, outcomes: dict[bool, str] | None = None):
        self.calls: list[dict] = []
        self.outcomes = outcomes or {}

    def run(
        self,
        *,
        store,
        task_id: str,
        supplement_enabled: bool,
        context_governance_enabled: bool,
    ):
        self.calls.append(
            {
                "root": str(store.root_dir),
                "task_id": task_id,
                "supplement_enabled": supplement_enabled,
                "context_governance_enabled": (
                    context_governance_enabled
                ),
            }
        )
        mode = "governed" if context_governance_enabled else "legacy"
        outcome = self.outcomes.get(context_governance_enabled, "success")
        input_tokens = [50, 80] if context_governance_enabled else [100, 200]
        latencies = [6, 9] if context_governance_enabled else [10, 20]
        sections = (
            [
                {
                    "research_task": 200,
                    "information_need": 180,
                    "research_state": 900,
                    "observed_terms": 100,
                    "recent_observations": 250,
                    "mission_context": 700,
                },
                {
                    "research_task": 200,
                    "information_need": 180,
                    "research_state": 1050,
                    "observed_terms": 120,
                    "recent_observations": 300,
                    "mission_context": 700,
                },
            ]
            if context_governance_enabled
            else [
                {
                    "research_task": 200,
                    "information_need": 180,
                    "research_state": 2000,
                    "observed_terms": 1000,
                    "recent_observations": 400,
                    "mission_context": 700,
                },
                {
                    "research_task": 200,
                    "information_need": 180,
                    "research_state": 2500,
                    "observed_terms": 1200,
                    "recent_observations": 600,
                    "mission_context": 700,
                },
            ]
        )
        task_root = store.root_dir / task_id
        provider_error = (
            "LLMProviderResponseError: 模型接口 HTTP 402，"
            "detail={'message': 'Insufficient Balance'}"
        )
        failed = outcome == "http_402"
        usage_missing = outcome == "usage_missing"
        stop_reason = (
            "action_budget_exhausted"
            if outcome == "budget_stop"
            else "failed"
            if failed
            else "coverage_sufficient"
        )
        trace_count = 1 if failed else 2
        write_json(
            task_root / "research_agent_actions.json",
            [] if failed else [
                {"id": "action_1", "action": "SEARCH"},
                {"id": "action_2", "action": "FINISH"},
            ],
        )
        write_json(
            task_root / "research_action_context_traces.json",
            [
                {
                    "action_index": index + 1,
                    "context_mode": mode,
                    "input_tokens": (
                        None
                        if failed or usage_missing
                        else input_tokens[index]
                    ),
                    "llm_latency_ms": latencies[index],
                    "metadata": {
                        "node_id": f"research_step_{index + 1}",
                        "usage_available": not failed and not usage_missing,
                    },
                    "section_chars": section,
                    "section_estimated_tokens": {
                        key: value // 4 for key, value in section.items()
                    },
                }
                for index, section in enumerate(sections[:trace_count])
            ],
        )
        write_json(
            task_root / "llm_calls.json",
            [
                {
                    "node_id": f"research_step_{index + 1}",
                    "status": "failed" if failed else "completed",
                    "duration_ms": latencies[index],
                    "error": provider_error if failed else "",
                    "metadata": {
                        "attempts": 0 if failed else 1,
                        "input_tokens": (
                            0
                            if failed or usage_missing
                            else input_tokens[index]
                        ),
                        "output_tokens": (
                            0 if failed or usage_missing else 10
                        ),
                        "usage_available": (
                            not failed and not usage_missing
                        ),
                    },
                }
                for index in range(trace_count)
            ],
        )
        write_json(
            task_root / "evidence.json",
            [] if failed else [
                {
                    "id": "evidence_1",
                    "metadata": {"quote_verified": True},
                }
            ],
        )
        write_json(
            task_root / "research_information_needs.json",
            [{"id": "need_1", "dimension": "feature"}],
        )
        write_json(
            task_root / "evidence_coverage.json",
            [
                {
                    "dimension": "feature",
                    "status": "missing" if failed else "sufficient",
                }
            ],
        )
        write_json(
            task_root / "research_gaps.json",
            [{"id": "gap_1"}] if failed else [],
        )
        write_json(
            task_root / "research_task_failures.json",
            (
                [
                    {
                        "research_task_id": "research_task_1",
                        "error_type": "ValueError",
                        "error_message": provider_error,
                    }
                ]
                if failed
                else []
            ),
        )
        write_json(
            task_root / "research_agent_coordinator_runs.json",
            [
                {
                    "status": "failed" if failed else "completed",
                    "result_status": "FAILED" if failed else "COMPLETE",
                    "stop_reason": stop_reason,
                    "error": provider_error if failed else "",
                    "failed_tasks": 1 if failed else 0,
                    "research_gap_count": 1 if failed else 0,
                    "context_governance_enabled": (
                        context_governance_enabled
                    ),
                }
            ],
        )
        write_json(task_root / "search_attempts.json", [])
        write_json(task_root / "web_search_results.json", [])
        write_json(task_root / "tool_calls.json", [])
        return {
            "final": {
                "status": "failed" if failed else "completed",
                "result_status": "FAILED" if failed else "COMPLETE",
                "error": provider_error if failed else "",
                "stop_reason": stop_reason,
                "context_governance_enabled": (
                    context_governance_enabled
                ),
            }
        }


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "research_context_governance_ab"
    )
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    probe_store = ArtifactStore(root / "adapter_probe")
    probe_coordinator = (
        ContextGovernanceResearchAgentAdapter._production_factory(
            probe_store,
            True,
            True,
        )
    )
    probe_service = probe_coordinator._research_service_factory(probe_store)
    require(
        probe_coordinator.supplement_enabled is True
        and probe_coordinator.context_governance_enabled is True
        and probe_service.store is probe_store
        and probe_service.context_governance_enabled is True,
        "Production Context A/B adapter 未隔离 store 或传递控制变量",
    )
    probe_coordinator._pool.shutdown(wait=False)
    frozen = FakeFrozenInputManager()
    adapter = FakeContextAgentAdapter()
    runner = EvaluationRunner(
        workspace_root=root,
        frozen_manager=frozen,
        context_agent_adapter=adapter,
    )
    try:
        run_dir = runner.run_context_governance_ab(case_id=CASE_ID)
        require(len(adapter.calls) == 2, "A/B 必须执行两个隔离 variant")
        require(
            [item["context_governance_enabled"] for item in adapter.calls]
            == [False, True],
            "A/B 执行顺序或治理开关错误",
        )
        require(
            all(item["supplement_enabled"] is True for item in adapter.calls),
            "A/B supplement 配置不一致",
        )
        require(
            len({item["root"] for item in adapter.calls}) == 2
            and len({item["task_id"] for item in adapter.calls}) == 2,
            "A/B 必须使用独立 ArtifactStore 和 Task Identity",
        )
        require(
            len(frozen.materializations) == 2
            and {
                item["frozen_input_hash"]
                for item in frozen.materializations
            }
            == {FROZEN_HASH},
            "A/B 没有引用同一个 frozen input",
        )

        variant_root = run_dir / "context_governance" / CASE_ID
        legacy = json.loads(
            (variant_root / "context_legacy" / "metrics.json").read_text(
                encoding="utf-8"
            )
        )
        governed = json.loads(
            (
                variant_root
                / "context_governed"
                / "metrics.json"
            ).read_text(encoding="utf-8")
        )
        require(
            legacy["total_input_tokens"] == 300
            and legacy["avg_input_tokens_per_action"] == 150
            and legacy["p50_input_tokens"] == 150
            and legacy["p95_input_tokens"] == 195
            and legacy["max_input_tokens"] == 200,
            "A 组 token 分布指标错误",
        )
        require(
            governed["total_input_tokens"] == 130
            and governed["llm_total_latency_ms"] == 15,
            "B 组 token/latency 指标错误",
        )
        require(
            all(
                item["action_count"] == 2
                and item["evidence_count"] == 1
                and item["valid_evidence_count"] == 1
                and item["coverage"] == 1
                and item["final_gap_count"] == 0
                and item["stop_reason"] == "coverage_sufficient"
                and item["context_configuration_match"] is True
                for item in (legacy, governed)
            ),
            "研究质量、终态或 context 配置投影错误",
        )
        legacy_config = json.loads(
            (variant_root / "context_legacy" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        governed_config = json.loads(
            (
                variant_root
                / "context_governed"
                / "config.json"
            ).read_text(encoding="utf-8")
        )
        require(
            legacy_config["control_config_hash"]
            == governed_config["control_config_hash"]
            and legacy_config["frozen_input_hash"]
            == governed_config["frozen_input_hash"]
            == FROZEN_HASH,
            "A/B 控制配置或 frozen hash 不一致",
        )

        comparison = json.loads(
            (run_dir / "context_governance_comparison.json").read_text(
                encoding="utf-8"
            )
        )
        require(
            comparison["comparison_valid"] is True
            and comparison["invalid_reasons"] == [],
            "成功 A/B 应通过 comparison validity gate",
        )
        observed_change = comparison["context_sections"][
            "observed_terms"
        ]["changes"]["avg_chars_per_action"]
        mission_change = comparison["context_sections"][
            "mission_context"
        ]["changes"]["avg_chars_per_action"]
        require(
            observed_change["control"] == 1100
            and observed_change["governed"] == 110
            and observed_change["reduction_ratio"] == 0.9,
            "observed_terms 分项变化错误",
        )
        require(
            mission_change["control"] == 700
            and mission_change["governed"] == 700
            and mission_change["reduction_ratio"] == 0,
            "Mission Context 应保持不变",
        )
        with (run_dir / "context_governance_summary.csv").open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            csv_rows = list(csv.DictReader(handle))
        require(len(csv_rows) == 2, "Context A/B CSV 必须包含两组")
        require(
            "observed_terms" in (
                run_dir / "context_governance_summary.md"
            ).read_text(encoding="utf-8"),
            "Context A/B Markdown 未输出分项变化",
        )
        summary_result = runner.summary()
        require(
            summary_result["comparison_complete"] is True,
            "summary 未识别 context-ab run",
        )
        require(
            summary_result["comparison_valid"] is True,
            "成功 A/B summary 应标记为有效",
        )

        one_402_dir, _one_402_frozen, _one_402_adapter = (
            run_fake_scenario(
                root,
                "one_402",
                {False: "success", True: "http_402"},
            )
        )
        one_402_comparison = json.loads(
            (
                one_402_dir / "context_governance_comparison.json"
            ).read_text(encoding="utf-8")
        )
        require(
            one_402_comparison["comparison_valid"] is False
            and any(
                "context_governed:provider_failure" == reason
                for reason in one_402_comparison["invalid_reasons"]
            ),
            "单侧 HTTP 402 必须使 comparison 无效",
        )
        require(
            one_402_comparison["metrics"]["total_input_tokens"][
                "control"
            ]
            == 300
            and one_402_comparison["metrics"]["total_input_tokens"][
                "governed"
            ]
            is None
            and one_402_comparison["metrics"]["total_input_tokens"][
                "reduction_ratio"
            ]
            is None,
            "无效 comparison 应保留原始 metrics 且不得计算 reduction",
        )
        one_402_execution = json.loads(
            next(
                (
                    one_402_dir
                    / "context_governance"
                    / CASE_ID
                    / "context_governed"
                ).glob("execution.json")
            ).read_text(encoding="utf-8")
        )
        require(
            one_402_execution["harness_status"] == "completed"
            and one_402_execution["status"] == "failed"
            and one_402_execution["result_status"] == "FAILED"
            and "Insufficient Balance"
            in one_402_execution["failure_reason"],
            "Coordinator 失败终态必须覆盖 Harness 正常返回语义",
        )
        require(
            "Not generated because the A/B comparison is invalid."
            in (
                one_402_dir / "context_governance_summary.md"
            ).read_text(encoding="utf-8"),
            "无效 comparison 不得生成 Resume-safe conclusion",
        )

        both_402_dir, _both_402_frozen, _both_402_adapter = (
            run_fake_scenario(
                root,
                "both_402",
                {False: "http_402", True: "http_402"},
            )
        )
        both_402_comparison = json.loads(
            (
                both_402_dir / "context_governance_comparison.json"
            ).read_text(encoding="utf-8")
        )
        require(
            both_402_comparison["comparison_valid"] is False
            and sum(
                reason.endswith(":provider_failure")
                for reason in both_402_comparison["invalid_reasons"]
            )
            == 2,
            "双侧 HTTP 402 必须分别记录无效原因",
        )

        usage_missing_dir, _usage_frozen, _usage_adapter = (
            run_fake_scenario(
                root,
                "usage_missing",
                {False: "usage_missing", True: "usage_missing"},
            )
        )
        usage_comparison = json.loads(
            (
                usage_missing_dir
                / "context_governance_comparison.json"
            ).read_text(encoding="utf-8")
        )
        usage_metrics = json.loads(
            (
                usage_missing_dir
                / "context_governance"
                / CASE_ID
                / "context_legacy"
                / "metrics.json"
            ).read_text(encoding="utf-8")
        )
        require(
            usage_metrics["total_input_tokens"] is None
            and usage_metrics["actual_input_tokens_available"] is False
            and usage_metrics["input_token_usage_missing_count"] == 2
            and usage_comparison["comparison_valid"] is False,
            "Provider usage 缺失必须投影为 N/A 并阻止 comparison",
        )

        budget_dir, _budget_frozen, _budget_adapter = run_fake_scenario(
            root,
            "budget_stop",
            {False: "budget_stop", True: "budget_stop"},
        )
        budget_comparison = json.loads(
            (
                budget_dir / "context_governance_comparison.json"
            ).read_text(encoding="utf-8")
        )
        require(
            budget_comparison["comparison_valid"] is True
            and budget_comparison["invalid_reasons"] == []
            and budget_comparison["stop_reason"]
            == {
                "context_legacy": "action_budget_exhausted",
                "context_governed": "action_budget_exhausted",
            }
            and budget_comparison["metrics"]["total_input_tokens"][
                "reduction_ratio"
            ]
            is not None,
            "action_budget_exhausted 应作为有效受控终态",
        )
        parsed = build_parser().parse_args(
            ["context-ab", "--case", CASE_ID]
        )
        require(
            parsed.command == "context-ab" and parsed.case == CASE_ID,
            "context-ab CLI 参数解析错误",
        )
        parsed_case_05 = build_parser().parse_args(
            ["context-ab", "--case", "case_05"]
        )
        require(
            parsed_case_05.case == "case_05",
            "context-ab CLI 应接受 case_05 参数",
        )
        case_05_frozen = FakeFrozenInputManager(case_id="case_05")
        case_05_adapter = FakeContextAgentAdapter()
        case_05_runner = EvaluationRunner(
            workspace_root=root / "case_05_supported",
            frozen_manager=case_05_frozen,
            context_agent_adapter=case_05_adapter,
        )
        case_05_run = case_05_runner.run_context_governance_ab(
            case_id="case_05"
        )
        case_05_comparison = json.loads(
            (case_05_run / "context_governance_comparison.json").read_text(
                encoding="utf-8"
            )
        )
        require(
            case_05_comparison["case"] == "case_05"
            and len(case_05_adapter.calls) == 2,
            "context-ab 应完整执行 case_05 A/B 并正确标记报告",
        )
        try:
            runner.run_context_governance_ab(case_id="case_02")
        except ValueError as exc:
            require(
                "仅支持 case_01 / case_05" in str(exc),
                "错误 case 提示不明确",
            )
        else:
            raise AssertionError("R1 Runner 不应允许 case_02")
    finally:
        if root.exists():
            shutil.rmtree(root)

    print("Research Context Governance A/B Runner offline checks passed.")
    print("real_llm_calls=0")
    print("real_search_calls=0")


if __name__ == "__main__":
    main()
