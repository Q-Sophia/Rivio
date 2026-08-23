from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.prompts import PromptRegistry
from app.schemas import AnalysisTask


BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent
REGISTRY_PATH = BACKEND_DIR / "app" / "prompts" / "registry.json"
PROMPT_PATH = (
    BACKEND_DIR / "app" / "prompts" / "competitive_analyst" / "v2.yaml"
)
SPEC_PATH = REPO_DIR / "docs" / "competitive_analysis_spec_v1.md"
GOVERNANCE_PATH = (
    REPO_DIR / "docs" / "competitive_analysis_agent_governance_v1.md"
)
EVAL_PATH = REPO_DIR / "docs" / "step6c_prompt_evaluation_plan.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    prompt = yaml.safe_load(PROMPT_PATH.read_text(encoding="utf-8"))
    spec = SPEC_PATH.read_text(encoding="utf-8")
    governance = GOVERNANCE_PATH.read_text(encoding="utf-8")
    evaluation = EVAL_PATH.read_text(encoding="utf-8")

    entries = registry.get("prompts", [])
    candidate_entries = [
        item
        for item in entries
        if item.get("id") == prompt["prompt_id"]
    ]
    require(
        len(candidate_entries) == 1,
        "Prompt registry must contain exactly one Step6C candidate entry",
    )
    require(
        all(
            item.get("status") in {
                "candidate_not_runtime_enabled",
                "evaluation_only",
            }
            for item in entries
        ),
        "Prompt registry contains an implicitly enabled Step6C prompt",
    )
    entry = candidate_entries[0]
    require(entry["id"] == prompt["prompt_id"], "prompt_id mismatch")
    require(entry["version"] == prompt["prompt_version"], "version mismatch")
    require(
        entry["status"] == "candidate_not_runtime_enabled",
        "Candidate prompt must not be runtime enabled",
    )
    require(
        prompt["compatibility"]["current_runtime_compatible"] is True,
        "Step6C prompt must declare experimental runtime compatibility",
    )
    require(
        prompt["compatibility"]["runtime_stage"]
        in {"experimental_mock", "experimental_real_pilot"},
        "Step6C prompt runtime_stage must remain experimental",
    )
    require(
        prompt["compatibility"]["default_runtime_enabled"] is False,
        "Candidate prompt must not become the default runtime implicitly",
    )

    required_claim_fields = {
        "claim_type",
        "counter_evidence_ids",
        "reasoning_summary",
        "uncertainty",
        "decision_impact",
        "evidence_ids",
    }
    actual_claim_fields = set(
        prompt["output_contract"]["claim_required_fields"]
    )
    require(
        required_claim_fields <= actual_claim_fields,
        "AnalysisClaimV2 contract is missing professional-analysis fields",
    )

    claim_types = set(prompt["output_contract"]["claim_type_enum"])
    require(
        {
            "fact",
            "comparison",
            "baseline",
            "inference",
            "risk",
            "opportunity",
            "recommendation",
        }
        == claim_types,
        "claim_type taxonomy mismatch",
    )
    require(
        len(prompt["analysis_method"]) == 9,
        "The nine-step competitive-analysis method is incomplete",
    )
    require(
        len(prompt["few_shot_examples"]) >= 4,
        "At least four boundary examples are required",
    )
    alignment = prompt.get("claim_evidence_alignment", {})
    require(
        len(alignment.get("audit_algorithm", [])) >= 6,
        "Claim/evidence alignment audit algorithm is incomplete",
    )
    runtime_prompt = PromptRegistry().load(
        "competitive_analyst",
        allow_candidate=True,
    ).build_runtime_prompt(
        AnalysisTask(
            task_id="prompt_contract_check",
            query="检查竞品证据规则",
            competitors=["A", "B"],
            focus_areas=["risk"],
        )
    )
    for required_runtime_text in [
        "evidence_id -> competitor",
        "缺证据对象必须退出结论并进入研究缺口",
        "claim_evidence_invariant",
        "缺证据对象只能进入 ResearchGap",
    ]:
        require(
            required_runtime_text in runtime_prompt,
            f"Runtime prompt missing evidence alignment rule: {required_runtime_text}",
        )

    combined = "\n".join(
        [
            prompt["system_prompt"],
            prompt["task_prompt_template"],
            prompt["quality_self_check"],
            "\n".join(prompt["negative_constraints"]),
        ]
    )
    for required_text in [
        "不得依靠模型记忆补充",
        "ResearchGap",
        "不得把资料未提及解释为竞品没有该能力",
        "不能假设当前任务属于软件或在线教育行业",
        "不输出隐藏的逐步思维过程",
    ]:
        require(required_text in combined, f"Missing policy text: {required_text}")

    for heading in [
        "研究简报",
        "建立竞争集合",
        "设计关键情报问题",
        "建立信息需求矩阵",
        "做同口径比较",
        "从发现形成洞察",
        "研究缺口与停止规则",
        "报告交付结构",
        "动态选择行业维度",
        "通用性与领域边界",
    ]:
        require(heading in spec, f"Specification section missing: {heading}")
    for engineering_term in [
        "PromptRegistry",
        "ArtifactStore",
        "Harness（运行框架）映射",
        "candidate_not_runtime_enabled",
    ]:
        require(
            engineering_term not in spec,
            f"Pure method specification contains engineering term: {engineering_term}",
        )

    for heading in [
        "证据主链",
        "Prompt Registry（提示词注册表）",
        "Agent 职责边界",
        "Guardrails（安全护栏）",
        "Quality Gate（质量闸门）",
        "Harness 五层映射",
    ]:
        require(heading in governance, f"Governance section missing: {heading}")

    for metric in [
        "known_evidence_ref_rate",
        "comparison_party_coverage_rate",
        "competitor_role_valid_rate",
        "comparability_gate_coverage_rate",
        "Competitive Set Rationale Coverage",
        "Key Intelligence Question Relevance",
        "Path Trade-off Coverage",
        "Dimension Selection Accuracy",
        "domain_template_leak_count",
        "Research Gap Precision / Recall",
        "Claim Redundancy Rate",
        "Claim Copy Rate",
        "Multi-claim Synthesis Rate",
        "Recommendation Support Rate",
    ]:
        require(metric in evaluation, f"Evaluation metric missing: {metric}")

    for case_id in [
        "normal_full",
        "different_solution_paths",
        "missing_pricing",
        "conflicting_price",
        "weak_social_only",
        "single_competitor",
        "missing_not_absent",
        "noisy_content",
        "goal_underspecified",
        "not_comparable_tiers",
        "cross_industry_consumer_goods",
        "cross_industry_professional_service",
    ]:
        require(case_id in evaluation, f"Evaluation case missing: {case_id}")

    require(
        "不继续进行无上限的 DeepSeek 调用" in evaluation,
        "Real-provider pilots must remain explicitly bounded",
    )
    require(
        "仍未 approved" in evaluation,
        "Failed real-provider candidate must not be approved",
    )

    print("STEP6C_PROMPT_DESIGN_CHECK_PASS")
    print(f"prompt_id={prompt['prompt_id']}")
    print(f"prompt_version={prompt['prompt_version']}")
    print(f"status={entry['status']}")
    print(f"method_steps={len(prompt['analysis_method'])}")
    print(f"few_shot_examples={len(prompt['few_shot_examples'])}")
    print(f"claim_types={len(claim_types)}")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
