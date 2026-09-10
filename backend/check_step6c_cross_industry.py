from __future__ import annotations

import json
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.llm.client import LLMClient
from app.llm.config import LLMConfig
from app.llm.structured import parse_competitive_analysis_portfolio_v2
from app.schemas import (
    AgentRole,
    AnalysisTask,
    ContextBundle,
    LLMMode,
    LLMProvider,
    ProductCard,
    SourceDocument,
    SourceEvidence,
)


TASK_ID = "step6c_cross_industry_consumer_goods"
FORBIDDEN_DOMAIN_TERMS = {
    "在线教育",
    "在线课堂",
    "教师",
    "学生",
    "课程",
    "SaaS",
    "PaaS",
    "API",
    "SDK",
    "LMS",
    "部署",
}


def build_fixture():
    task = AnalysisTask(
        id=TASK_ID,
        task_id=TASK_ID,
        query="为通勤人群选择下一代保温杯产品定位与渠道方案",
        industry="实体消费品与饮具",
        competitors=["暖行杯", "山川杯"],
        focus_areas=["目标客户", "保温性能", "材料与设计", "价格", "渠道与售后"],
    )
    sources = [
        SourceDocument(
            id="src_warm_official",
            task_id=TASK_ID,
            title="暖行杯产品说明",
            url="https://fixture.invalid/warm",
            source_type="official_site",
            competitor="暖行杯",
            content_excerpt="面向城市通勤，强调轻量杯身、保温性能与线下售后。",
            reliability_score=0.9,
        ),
        SourceDocument(
            id="src_mountain_official",
            task_id=TASK_ID,
            title="山川杯产品说明",
            url="https://fixture.invalid/mountain",
            source_type="official_site",
            competitor="山川杯",
            content_excerpt="面向户外和长途使用，强调耐用材料、大容量与电商销售。",
            reliability_score=0.9,
        ),
    ]
    evidence = [
        SourceEvidence(
            id="ev_warm_positioning",
            task_id=TASK_ID,
            source_id="src_warm_official",
            competitor="暖行杯",
            dimension="positioning",
            snippet="面向城市通勤人群，强调轻量与便携。",
            normalized_fact="暖行杯公开定位面向城市通勤人群。",
            confidence=0.9,
        ),
        SourceEvidence(
            id="ev_warm_feature",
            task_id=TASK_ID,
            source_id="src_warm_official",
            competitor="暖行杯",
            dimension="feature",
            snippet="杯身采用轻量材料并提供六小时保温说明。",
            normalized_fact="暖行杯公开说明轻量材料与六小时保温能力。",
            confidence=0.85,
        ),
        SourceEvidence(
            id="ev_warm_pricing",
            task_id=TASK_ID,
            source_id="src_warm_official",
            competitor="暖行杯",
            dimension="pricing",
            snippet="公开建议零售价为一百九十九元。",
            normalized_fact="暖行杯公开建议零售价为一百九十九元。",
            confidence=0.9,
        ),
        SourceEvidence(
            id="ev_mountain_positioning",
            task_id=TASK_ID,
            source_id="src_mountain_official",
            competitor="山川杯",
            dimension="positioning",
            snippet="面向户外与长途使用人群，强调容量与耐用性。",
            normalized_fact="山川杯公开定位面向户外与长途使用人群。",
            confidence=0.9,
        ),
        SourceEvidence(
            id="ev_mountain_feature",
            task_id=TASK_ID,
            source_id="src_mountain_official",
            competitor="山川杯",
            dimension="feature",
            snippet="采用耐磨涂层并提供大容量杯型。",
            normalized_fact="山川杯公开说明采用耐磨涂层并提供大容量杯型。",
            confidence=0.85,
        ),
        SourceEvidence(
            id="ev_mountain_pricing",
            task_id=TASK_ID,
            source_id="src_mountain_official",
            competitor="山川杯",
            dimension="pricing",
            snippet="公开建议零售价为二百三十九元。",
            normalized_fact="山川杯公开建议零售价为二百三十九元。",
            confidence=0.9,
        ),
    ]
    cards = [
        ProductCard(
            id="prod_warm",
            task_id=TASK_ID,
            name="暖行杯",
            positioning="面向城市通勤人群的轻量便携保温杯。",
            target_users=["城市通勤人群"],
            pricing_summary="公开建议零售价为一百九十九元。",
            core_features=["轻量杯身", "六小时保温"],
            strengths=["便携定位明确"],
            weaknesses=["实际保温效果仍需独立验证"],
            source_ids=["src_warm_official"],
            evidence_ids=[
                "ev_warm_positioning",
                "ev_warm_feature",
                "ev_warm_pricing",
            ],
            confidence=0.85,
        ),
        ProductCard(
            id="prod_mountain",
            task_id=TASK_ID,
            name="山川杯",
            positioning="面向户外与长途场景的耐用大容量保温杯。",
            target_users=["户外与长途使用人群"],
            pricing_summary="公开建议零售价为二百三十九元。",
            core_features=["耐磨涂层", "大容量杯型"],
            strengths=["户外场景定位明确"],
            weaknesses=["实际耐用性仍需独立验证"],
            source_ids=["src_mountain_official"],
            evidence_ids=[
                "ev_mountain_positioning",
                "ev_mountain_feature",
                "ev_mountain_pricing",
            ],
            confidence=0.85,
        ),
    ]
    return task, sources, evidence, cards


def main() -> None:
    task, sources, evidence, cards = build_fixture()
    artifacts = {
        "analysis_task": [task.model_dump(mode="json")],
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    artifact_root = (
        Path(__file__).resolve().parent / "app" / "data" / "contract_tests"
    )
    store = ArtifactStore(root_dir=artifact_root)
    store.save_many(TASK_ID, "llm_calls", [])
    store.save_many(TASK_ID, "llm_outputs", [])
    client = LLMClient(
        config=LLMConfig(
            provider=LLMProvider.MOCK,
            model="mock-structured-v1",
            mode=LLMMode.LLM_WITH_FALLBACK,
            api_style="mock",
            enable_real_calls=False,
        ),
        store=store,
    )
    raw, call, output = client.generate_structured(
        task_id=TASK_ID,
        agent_role=AgentRole.ANALYST,
        agent_run_id="run_cross_industry",
        node_id="build_claims",
        context_bundle=ContextBundle(
            task_id=TASK_ID,
            agent_role=AgentRole.ANALYST,
            node_id="build_claims",
        ),
        output_schema="CompetitiveAnalysisPortfolioV2",
        prompt_id="competitive_analyst",
        prompt_version="2.2.1-candidate",
        prompt_hash="fixture-hash",
        prompt_summary="使用简体中文执行通用竞品分析契约测试。",
        artifacts=artifacts,
    )
    portfolio = parse_competitive_analysis_portfolio_v2(raw)

    business_text = json.dumps(raw, ensure_ascii=False)
    leaked_terms = sorted(term for term in FORBIDDEN_DOMAIN_TERMS if term in business_text)
    if leaked_terms:
        raise AssertionError("跨行业输出泄漏历史领域模板: " + "、".join(leaked_terms))
    if portfolio.brief_assessment.industry != "实体消费品与饮具":
        raise AssertionError("行业识别没有保留实体消费品任务信息")
    if not {"暖行杯", "山川杯"} <= {
        profile.name for profile in portfolio.competitor_profiles
    }:
        raise AssertionError("竞品画像没有覆盖两个实体消费品对象")
    if call.used_fallback or output.validation_status != "passed":
        raise AssertionError("跨行业 Mock 结构化输出不应回退或校验失败")

    print("STEP6C_CROSS_INDUSTRY_CHECK_PASS")
    print(f"industry={portfolio.brief_assessment.industry}")
    print(f"competitor_profiles={len(portfolio.competitor_profiles)}")
    print(f"claims_v2={len(portfolio.items)}")
    print(f"research_gaps={len(portfolio.research_gaps)}")
    print("domain_template_leak_count=0")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
