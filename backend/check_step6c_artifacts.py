from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.llm.structured import (
    portfolio_v2_to_legacy_claims,
    validate_portfolio_v2_refs,
)
from app.prompts import PromptRegistry
from app.schemas import (
    AnalysisClaim,
    AnalysisClaimV2,
    BriefAssessment,
    ComparabilityNote,
    CompetitiveAnalysisPortfolioV2,
    CompetitorProfile,
    EvidenceCoverage,
    InformationNeed,
    KeyIntelligenceQuestion,
    LLMCall,
    LLMOutput,
    ResearchGap,
    SourceDocument,
    SourceEvidence,
)
from run_step6c_professional_workflow_demo import DEFAULT_STEP6C_TASK_ID


ARTIFACT_MODELS = {
    "analysis_portfolios": CompetitiveAnalysisPortfolioV2,
    "brief_assessments": BriefAssessment,
    "competitor_profiles": CompetitorProfile,
    "intelligence_questions": KeyIntelligenceQuestion,
    "information_needs": InformationNeed,
    "evidence_coverage": EvidenceCoverage,
    "comparability_notes": ComparabilityNote,
    "claims_v2": AnalysisClaimV2,
    "research_gaps": ResearchGap,
    "claims": AnalysisClaim,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Step6C professional-analysis artifacts and references."
    )
    parser.add_argument("--task-id", default=DEFAULT_STEP6C_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_typed(store: ArtifactStore, task_id: str, artifact_type: str, model):
    raw = store.load_many(task_id, artifact_type)
    return [model(**item) for item in raw]


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    sources = load_typed(store, args.task_id, "sources", SourceDocument)
    evidence = load_typed(store, args.task_id, "evidence", SourceEvidence)
    typed = {
        name: load_typed(store, args.task_id, name, model)
        for name, model in ARTIFACT_MODELS.items()
    }
    calls = load_typed(store, args.task_id, "llm_calls", LLMCall)
    outputs = load_typed(store, args.task_id, "llm_outputs", LLMOutput)

    require(sources, "sources 为空")
    require(evidence, "evidence 为空")
    for name, items in typed.items():
        require(items, f"{name} 为空")
    require(len(typed["analysis_portfolios"]) == 1, "应只有一个 V2 分析组合")

    portfolio = typed["analysis_portfolios"][0]
    registry_prompt = PromptRegistry().load(
        "competitive_analyst",
        allow_candidate=True,
    )
    require(portfolio.prompt_id == registry_prompt.prompt_id, "portfolio prompt_id 不一致")
    require(
        portfolio.prompt_version == registry_prompt.version,
        "portfolio prompt_version 不一致",
    )
    require(
        portfolio.metadata.get("prompt_hash") == registry_prompt.content_hash,
        "portfolio prompt_hash 不一致",
    )
    validate_portfolio_v2_refs(
        portfolio,
        known_source_ids={item.id for item in sources},
        known_evidence_ids={item.id for item in evidence},
        known_competitors={item.name for item in portfolio.competitor_profiles},
        evidence_competitors={item.id: item.competitor for item in evidence},
    )

    component_checks = {
        "brief_assessments": [portfolio.brief_assessment],
        "competitor_profiles": portfolio.competitor_profiles,
        "intelligence_questions": portfolio.key_intelligence_questions,
        "information_needs": portfolio.information_needs,
        "evidence_coverage": portfolio.evidence_coverage,
        "comparability_notes": portfolio.comparability_notes,
        "claims_v2": portfolio.items,
        "research_gaps": portfolio.research_gaps,
    }
    for name, expected in component_checks.items():
        require(
            [item.model_dump(mode="json") for item in typed[name]]
            == [item.model_dump(mode="json") for item in expected],
            f"{name} 与 analysis_portfolios 中的对应内容不一致",
        )

    legacy_expected = portfolio_v2_to_legacy_claims(portfolio)
    legacy_actual = typed["claims"]
    require(
        [item.id for item in legacy_actual] == [item.id for item in legacy_expected],
        "V2 到旧 AnalysisClaim 的编号映射不一致",
    )
    require(
        all(item.metadata.get("source_schema") == "AnalysisClaimV2" for item in legacy_actual),
        "旧 claims 缺少 V2 来源标记",
    )

    analyst_calls = [
        call for call in calls if call.output_schema == "CompetitiveAnalysisPortfolioV2"
    ]
    require(len(analyst_calls) == 1, "应只有一次 V2 Analyst LLMCall")
    analyst_call = analyst_calls[0]
    require(analyst_call.prompt_id == registry_prompt.prompt_id, "LLMCall prompt_id 不一致")
    require(analyst_call.prompt_version == registry_prompt.version, "LLMCall prompt_version 不一致")
    require(analyst_call.prompt_hash == registry_prompt.content_hash, "LLMCall prompt_hash 不一致")
    require(not analyst_call.used_fallback, "Mock V2 Analyst 不应触发回退")
    require(
        any(output.llm_call_id == analyst_call.id for output in outputs),
        "V2 Analyst LLMCall 缺少 LLMOutput",
    )

    print("STEP6C_ARTIFACT_CHECK_PASS")
    print(f"task_id={args.task_id}")
    print(f"prompt={registry_prompt.prompt_id}@{registry_prompt.version}")
    print(f"prompt_hash={registry_prompt.content_hash}")
    print(f"competitor_profiles={len(portfolio.competitor_profiles)}")
    print(f"intelligence_questions={len(portfolio.key_intelligence_questions)}")
    print(f"evidence_coverage={len(portfolio.evidence_coverage)}")
    print(f"claims_v2={len(portfolio.items)}")
    print(f"research_gaps={len(portfolio.research_gaps)}")
    print(f"legacy_claims={len(legacy_actual)}")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
