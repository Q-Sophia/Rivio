from __future__ import annotations

from app.analysis_assessment import (
    completion_criterion_id,
    materialize_analysis_assessment,
    resolve_framework_assessment_binding,
    validate_analyst_assessment_stage,
)
from app.frameworks import load_framework
from app.schemas import (
    AnalysisAssessmentStatus,
    AnalysisTask,
    AnalystAssessmentStage,
    AnalystDimensionAssessmentDraft,
    AnalystResearchGapDraft,
    CompletionCriterionEvaluation,
    CompletionCriterionStatus,
    DimensionAssessmentStatus,
    ResearchGap,
    ResearchGapImpact,
    ResearchGapOrigin,
    ResearchGapType,
    ResearchTask,
    SourceEvidence,
)


TASK_ID = "task_professional_assessment_fixture"
COMPETITOR = "豆包"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fixture():
    framework = load_framework("competitive_intelligence", "1.0.0")
    dimension = next(
        item
        for item in framework.dimensions
        if item.dimension_id == "customer_experience"
    )
    task = AnalysisTask(
        id=TASK_ID,
        query="评估豆包的真实用户体验是否支持产品选型",
        competitors=[COMPETITOR],
        industry="AI 助手",
        focus_areas=["customer"],
    )
    research_task = ResearchTask(
        schema_version="v2",
        id="researchtask_customer_experience",
        task_id=TASK_ID,
        information_need_id="need_customer_experience",
        title="豆包客户与使用体验",
        objective="核实豆包的真实客户反馈与使用体验。",
        competitor=COMPETITOR,
        dimension=dimension.evidence_dimension,
        research_intent=dimension.research_intent,
        stop_condition="；".join(dimension.completion_criteria),
        framework_id=framework.framework_id,
        framework_version=framework.version,
        framework_dimension_id=dimension.dimension_id,
        framework_content_hash=framework.content_hash,
    )
    binding = resolve_framework_assessment_binding(
        task=task,
        research_tasks=[research_task],
    )
    customer_evidence = SourceEvidence(
        id="evidence_customer_experience",
        task_id=TASK_ID,
        source_id="source_zhihu_customer_experience",
        competitor=COMPETITOR,
        dimension="customer",
        snippet="用户描述了实际使用场景、版本、优点和问题。",
        normalized_fact=(
            "真实用户在具体场景中描述了产品版本、正面体验、负面问题和冲突反馈。"
        ),
        confidence=0.82,
    )
    wrong_dimension_evidence = SourceEvidence(
        id="evidence_market_positioning",
        task_id=TASK_ID,
        source_id="source_official_positioning",
        competitor=COMPETITOR,
        dimension="positioning",
        snippet="官方介绍产品定位。",
        normalized_fact="豆包是一款 AI 助手产品。",
        confidence=0.9,
    )
    return (
        framework,
        dimension,
        task,
        research_task,
        binding,
        customer_evidence,
        wrong_dimension_evidence,
    )


def make_assessment(
    *,
    stage: AnalystAssessmentStage,
    binding,
    evidence: list[SourceEvidence],
    task: AnalysisTask,
    round_number: int,
):
    return materialize_analysis_assessment(
        stage=stage,
        binding=binding,
        evidence=evidence,
        task=task,
        pipeline_id="pipeline_professional_assessment_fixture",
        assessment_round=round_number,
        analyst_agent_run_id="agentrun_professional_assessment_fixture",
    )


def check_complete_evidence_is_sufficient() -> None:
    _, dimension, task, _, binding, evidence, _ = fixture()
    stage = AnalystAssessmentStage(
        task_id=TASK_ID,
        dimension_assessments=[
            AnalystDimensionAssessmentDraft(
                dimension_id=dimension.dimension_id,
                competitor=COMPETITOR,
                status=DimensionAssessmentStatus.COVERED,
                covered_facts=dimension.required_facts,
                missing_facts=[],
                evidence_ids=[evidence.id],
                completion_criteria_met=dimension.completion_criteria,
                completion_criteria_unmet=[],
                reasoning="现有可验证证据覆盖框架要求的全部体验事实与边界。",
                decision_impact="当前信息足以支持该维度的阶段性选型判断。",
            )
        ],
    )
    assessment = make_assessment(
        stage=stage,
        binding=binding,
        evidence=[evidence],
        task=task,
        round_number=1,
    )
    require(
        assessment.overall_status == AnalysisAssessmentStatus.SUFFICIENT.value,
        "完整 Evidence 应输出 SUFFICIENT",
    )
    require(assessment.coverage_score == 1.0, "完整事实覆盖率应为 1.0")
    require(not assessment.research_gaps, "完整 Evidence 不应生成 ResearchGap")


def check_missing_user_feedback_is_partial() -> None:
    _, dimension, task, _, binding, evidence, _ = fixture()
    covered = dimension.required_facts[:1]
    missing = dimension.required_facts[1:]
    stage = AnalystAssessmentStage(
        task_id=TASK_ID,
        dimension_assessments=[
            AnalystDimensionAssessmentDraft(
                dimension_id=dimension.dimension_id,
                competitor=COMPETITOR,
                status=DimensionAssessmentStatus.PARTIAL,
                covered_facts=covered,
                missing_facts=missing,
                evidence_ids=[evidence.id],
                completion_criteria_met=dimension.completion_criteria[:1],
                completion_criteria_unmet=dimension.completion_criteria[1:],
                reasoning="当前只有一条体验描述，缺少用户角色和正负反馈结构。",
                decision_impact="无法判断该体验是否适用于目标用户群。",
            )
        ],
        research_gaps=[
            AnalystResearchGapDraft(
                dimension_id=dimension.dimension_id,
                competitors=[COMPETITOR],
                gap_type=ResearchGapType.INSUFFICIENT_EVIDENCE,
                impact=ResearchGapImpact.HIGH,
                missing_facts=missing,
                missing_information="缺少用户角色、使用版本和正负反馈结构。",
                why_existing_evidence_is_insufficient="单条体验证据不足以支持用户群判断。",
                suggested_queries=["豆包 真实用户 使用体验 优缺点"],
                preferred_source_types=["social", "blog"],
                blocks_decision=True,
                decision_blocked="目标用户适配性判断被阻塞。",
                stop_condition="获得不同用户角色的可追溯体验证据。",
            )
        ],
    )
    assessment = make_assessment(
        stage=stage,
        binding=binding,
        evidence=[evidence],
        task=task,
        round_number=2,
    )
    require(
        assessment.overall_status == AnalysisAssessmentStatus.PARTIAL.value,
        "缺少用户反馈结构时应输出 PARTIAL",
    )
    require(len(assessment.research_gaps) == 1, "PARTIAL 应生成 ResearchGap")
    gap = assessment.research_gaps[0]
    require(
        gap.origin == ResearchGapOrigin.PROFESSIONAL_ANALYST.value,
        "ResearchGap 必须标记 Professional Analyst 来源",
    )
    require(
        gap.framework_dimension_id == dimension.dimension_id
        and gap.assessment_id == assessment.id,
        "ResearchGap 必须保留 Framework 与 Assessment provenance",
    )


def check_wrong_framework_evidence_is_insufficient() -> None:
    framework, dimension, task, _, binding, _, wrong_evidence = fixture()
    stage = AnalystAssessmentStage(
        task_id=TASK_ID,
        dimension_assessments=[
            AnalystDimensionAssessmentDraft(
                dimension_id=dimension.dimension_id,
                competitor=COMPETITOR,
                status=DimensionAssessmentStatus.MISSING,
                covered_facts=[],
                missing_facts=dimension.required_facts,
                evidence_ids=[],
                completion_criteria_met=[],
                completion_criteria_unmet=dimension.completion_criteria,
                reasoning="现有材料只覆盖产品定位，未覆盖真实用户体验。",
                decision_impact="客户体验维度无法形成可靠判断。",
            )
        ],
    )
    assessment = make_assessment(
        stage=stage,
        binding=binding,
        evidence=[wrong_evidence],
        task=task,
        round_number=3,
    )
    require(
        assessment.overall_status == AnalysisAssessmentStatus.INSUFFICIENT.value,
        "不满足 Framework 维度的 Evidence 应输出 INSUFFICIENT",
    )
    require(
        assessment.research_gaps
        and assessment.research_gaps[0].gap_type
        == ResearchGapType.MISSING_FACT.value,
        "缺失全部 required_facts 时应合成 missing_fact ResearchGap",
    )

    invalid = stage.model_copy(deep=True)
    invalid.dimension_assessments[0].evidence_ids = [wrong_evidence.id]
    try:
        validate_analyst_assessment_stage(
            stage=invalid,
            framework=framework,
            scope=binding.scope,
            evidence=[wrong_evidence],
        )
    except ValueError as exc:
        require("dimension" in str(exc), "错误维度 Evidence 应由 allowlist gate 拒绝")
    else:
        raise AssertionError("Analyst 不得引用其他 Framework 维度的 Evidence")


def check_stable_criterion_ids_and_not_applicable() -> None:
    framework = load_framework("competitive_intelligence", "1.0.0")
    dimension = next(
        item
        for item in framework.dimensions
        if item.dimension_id == "commercial_strategy"
    )
    task = AnalysisTask(
        id=TASK_ID,
        query="比较豆包当前公开价格",
        competitors=[COMPETITOR],
        focus_areas=["pricing"],
    )
    research_task = ResearchTask(
        schema_version="v2",
        id="researchtask_commercial_strategy",
        task_id=TASK_ID,
        information_need_id="need_commercial_strategy",
        title="豆包商业策略",
        objective="核实豆包公开价格。",
        competitor=COMPETITOR,
        dimension=dimension.evidence_dimension,
        stop_condition="；".join(dimension.completion_criteria),
        framework_id=framework.framework_id,
        framework_version=framework.version,
        framework_dimension_id=dimension.dimension_id,
        framework_content_hash=framework.content_hash,
    )
    binding = resolve_framework_assessment_binding(
        task=task,
        research_tasks=[research_task],
    )
    refs = binding.scope[0]["completion_criteria_refs"]
    require(
        [item["criterion_id"] for item in refs]
        == [
            completion_criterion_id(dimension.dimension_id, criterion)
            for criterion in dimension.completion_criteria
        ],
        "criterion ID 必须从维度和原始 criterion 稳定派生",
    )
    evidence = SourceEvidence(
        id="evidence_public_price",
        task_id=TASK_ID,
        source_id="source_official_price",
        competitor=COMPETITOR,
        dimension="pricing",
        snippet="专业版公开价格为每月 20 元。",
        normalized_fact="豆包专业版公开价格为每月 20 元。",
        confidence=0.9,
    )
    draft = AnalystDimensionAssessmentDraft(
        dimension_id=dimension.dimension_id,
        competitor=COMPETITOR,
        status=DimensionAssessmentStatus.PARTIAL,
        covered_facts=dimension.required_facts[:1],
        missing_facts=dimension.required_facts[1:],
        evidence_ids=[evidence.id],
        completion_criteria_evaluations=[
            CompletionCriterionEvaluation(
                criterion_id=refs[0]["criterion_id"],
                status=CompletionCriterionStatus.MET,
            ),
            CompletionCriterionEvaluation(
                criterion_id=refs[1]["criterion_id"],
                status=CompletionCriterionStatus.NOT_APPLICABLE,
            ),
        ],
        completion_criteria_met=["已获得公开价格（模型自由复述）"],
        reasoning="已有公开价格，因此询价边界条件不适用。",
        decision_impact="仍需补充套餐和地域口径。",
    )
    stage = AnalystAssessmentStage(
        task_id=TASK_ID,
        dimension_assessments=[draft],
    )
    validate_analyst_assessment_stage(
        stage=stage,
        framework=framework,
        scope=binding.scope,
        evidence=[evidence],
    )
    normalized = stage.dimension_assessments[0]
    require(
        normalized.completion_criteria_met == dimension.completion_criteria[:1]
        and normalized.completion_criteria_unmet == []
        and normalized.completion_criteria_not_applicable
        == dimension.completion_criteria[1:],
        "稳定 ID 必须投影回 Framework 原文且保留 not_applicable",
    )
    assessment = make_assessment(
        stage=stage,
        binding=binding,
        evidence=[evidence],
        task=task,
        round_number=4,
    )
    require(
        assessment.dimension_assessments[0].completion_criteria_evaluations,
        "持久化 Assessment 必须保留 criterion ID 与三态结果",
    )

    invalid = stage.model_copy(deep=True)
    invalid.dimension_assessments[0].completion_criteria_evaluations.pop()
    try:
        validate_analyst_assessment_stage(
            stage=invalid,
            framework=framework,
            scope=binding.scope,
            evidence=[evidence],
        )
    except ValueError as exc:
        require("criterion ID coverage" in str(exc), "缺失 criterion ID 应被拒绝")
    else:
        raise AssertionError("criterion ID 覆盖仍必须严格完整")


def check_legacy_research_gap_is_backward_compatible() -> None:
    legacy = ResearchGap(
        task_id=TASK_ID,
        competitors=[COMPETITOR],
        dimension="customer",
        missing_information="缺少真实用户反馈。",
        decision_blocked="无法形成体验判断。",
        why_existing_evidence_is_insufficient="当前只有官方介绍。",
        stop_condition="获得一条真实体验证据。",
    )
    require(
        legacy.gap_type == ResearchGapType.MISSING_FACT.value
        and legacy.origin == ResearchGapOrigin.DETERMINISTIC_COVERAGE.value,
        "旧 ResearchGap 必须用默认字段继续解析",
    )
    require(
        {item.value for item in ResearchGapType}
        == {
            "missing_fact",
            "insufficient_evidence",
            "missing_source_type",
            "missing_comparison",
            "decision_blocking",
        },
        "ResearchGapType 必须覆盖反馈控制器约定的五类缺口",
    )


def main() -> None:
    check_complete_evidence_is_sufficient()
    check_missing_user_feedback_is_partial()
    check_wrong_framework_evidence_is_insufficient()
    check_stable_criterion_ids_and_not_applicable()
    check_legacy_research_gap_is_backward_compatible()
    print("PROFESSIONAL_ANALYSIS_ASSESSMENT_CHECK_PASS")
    print("complete_evidence=SUFFICIENT")
    print("missing_user_feedback=PARTIAL+ResearchGap")
    print("wrong_dimension_evidence=INSUFFICIENT")
    print("legacy_research_gap=compatible")
    print("criterion_id_partition=met+unmet+not_applicable")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
