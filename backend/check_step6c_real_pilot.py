from __future__ import annotations

import argparse
import json
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_AB_SUMMARY = (
    BACKEND_DIR
    / "app"
    / "data"
    / "ab_tests"
    / "step6c_deepseek_v4_pilot_retry1_summary.json"
)
DEFAULT_CANDIDATE_SUMMARY = (
    BACKEND_DIR
    / "app"
    / "data"
    / "ab_tests"
    / "step6c_deepseek_v4_candidate_2_2_1_pilot_summary.json"
)
DEFAULT_FILTERED_CANDIDATE_SUMMARY = (
    BACKEND_DIR
    / "app"
    / "data"
    / "ab_tests"
    / "step6c_deepseek_v4_candidate_2_2_2_pilot_summary.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit saved Step6C real-provider pilot results without a network call."
    )
    parser.add_argument("--ab-summary", type=Path, default=DEFAULT_AB_SUMMARY)
    parser.add_argument(
        "--candidate-summary",
        type=Path,
        default=DEFAULT_CANDIDATE_SUMMARY,
    )
    parser.add_argument(
        "--filtered-candidate-summary",
        type=Path,
        default=DEFAULT_FILTERED_CANDIDATE_SUMMARY,
    )
    return parser.parse_args()


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def load_summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    errors: list[str] = []
    ab = load_summary(args.ab_summary)
    candidate = load_summary(args.candidate_summary)
    filtered_candidate = load_summary(args.filtered_candidate_summary)

    for label, summary in [
        ("ab", ab),
        ("candidate", candidate),
        ("filtered_candidate", filtered_candidate),
    ]:
        require(summary.get("real_llm_called") is True, f"{label}: 未记录真实调用", errors)
        require(summary.get("provider") == "compatible", f"{label}: provider 不正确", errors)
        require(
            summary.get("model") == "deepseek-v4-flash",
            f"{label}: model 不正确",
            errors,
        )

    ab_results = {item["variant"]: item for item in ab.get("results", [])}
    baseline = ab_results.get("v1_baseline", {})
    ab_candidate = ab_results.get("v2_candidate", {})
    require(baseline.get("status") == "failed", "基线应被结构化闸门拒绝", errors)
    require(
        "缺少 evidence_ids" in baseline.get("error", ""),
        "基线失败原因应为结论缺少证据编号",
        errors,
    )
    require(
        ab_candidate.get("status") == "quality_gate_failed",
        "2.2.0 候选应被多竞品证据闸门标记",
        errors,
    )
    require(
        "multi_competitor_evidence_balance_rate"
        in ab_candidate.get("quality_gate_failures", []),
        "2.2.0 候选缺少多竞品证据失败记录",
        errors,
    )

    candidate_results = candidate.get("results", [])
    current = candidate_results[0] if candidate_results else {}
    require(
        current.get("prompt_version") == "2.2.1-candidate",
        "当前真实候选版本不正确",
        errors,
    )
    require(
        current.get("status") == "quality_gate_failed",
        "2.2.1 候选应保留真实质量闸门失败",
        errors,
    )
    require(current.get("used_fallback") is False, "真实候选不得使用回退", errors)
    require(current.get("input_tokens", 0) > 0, "真实候选缺少输入 Token", errors)
    require(current.get("output_tokens", 0) > 0, "真实候选缺少输出 Token", errors)
    require(
        current.get("metrics", {})
        .get("multi_competitor_evidence_balance_rate", {})
        .get("value")
        == 0.875,
        "2.2.1 真实失败样本的证据均衡率应为 0.875",
        errors,
    )

    filtered_results = filtered_candidate.get("results", [])
    filtered = filtered_results[0] if filtered_results else {}
    require(
        filtered.get("prompt_version") == "2.2.2-candidate",
        "过滤候选 Prompt 版本不正确",
        errors,
    )
    require(
        filtered.get("status") == "completed_with_rejections",
        "2.2.2 候选应在透明剔除不合格结论后完成",
        errors,
    )
    require(filtered.get("used_fallback") is False, "2.2.2 不得使用回退", errors)
    require(filtered.get("input_tokens", 0) > 0, "2.2.2 缺少输入 Token", errors)
    require(filtered.get("output_tokens", 0) > 0, "2.2.2 缺少输出 Token", errors)
    require(filtered.get("metric_pass_rate") == 1.0, "2.2.2 指标未全部通过", errors)
    require(
        filtered.get("quality_gate_failures") == [],
        "2.2.2 过滤后仍存在质量失败",
        errors,
    )
    require(
        filtered.get("rejected_claims_count") == 1,
        "2.2.2 应透明记录 1 条剔除结论",
        errors,
    )
    rejected_ids = {
        item.get("claim_id") for item in filtered.get("rejected_claims", [])
    }
    require(rejected_ids == {"claim_006"}, "剔除结论编号不正确", errors)
    require(
        filtered.get("metrics", {})
        .get("multi_competitor_evidence_balance_rate", {})
        .get("value")
        == 1.0,
        "2.2.2 过滤后多竞品证据均衡率应为 1.0",
        errors,
    )

    if errors:
        print("STEP6C_REAL_PILOT_AUDIT_FAIL")
        print(f"errors={len(errors)}")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        raise SystemExit(1)

    print("STEP6C_REAL_PILOT_AUDIT_PASS")
    print("real_calls_recorded=6")
    print("ab_status=failed_as_expected")
    print("baseline_failure=missing_evidence_ids")
    print("candidate_2_2_0_quality_gate=failed")
    print("candidate_2_2_1_quality_gate=failed")
    print("candidate_2_2_1_multi_competitor_balance=0.875")
    print("candidate_2_2_2_status=completed_with_rejections")
    print("candidate_2_2_2_rejected_claims=1")
    print("candidate_2_2_2_multi_competitor_balance=1.0")
    print("candidate_2_2_2_metric_pass_rate=1.0")
    print("mock_fallback_used=false")
    print("network_used=false")


if __name__ == "__main__":
    main()
