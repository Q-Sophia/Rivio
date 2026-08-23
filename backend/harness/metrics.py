from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.llm.language import ZH_CN, validate_structured_output_language
from app.schemas import (
    AgentRun,
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    CompetitiveAnalysisPortfolioV2,
    ContextBundle,
    DAGNode,
    GuardrailCheck,
    LLMCall,
    LLMOutput,
    MemoryItem,
    ProductCard,
    QualityGateDecision,
    ReportStatement,
    ReviewFeedback,
    SourceDocument,
    TaskRecord,
    SourceEvidence,
    ToolCall,
)
from harness.step6c_metrics import evaluate_portfolio_core
from harness.step6c_writer_metrics import evaluate_professional_report


REQUIRED_ARTIFACTS = [
    "sources",
    "evidence",
    "product_cards",
    "claims",
    "citation_checks",
    "reports",
    "review_feedback",
    "dag_nodes",
    "agent_runs",
    "tool_calls",
    "pipeline_summary",
]


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: Any
    passed: bool
    details: str = ""


def evaluate_run(*, store: ArtifactStore, task_id: str) -> dict[str, Any]:
    errors: list[str] = []
    task_dir = store.root_dir / task_id

    sources = load_typed(store, task_id, "sources", SourceDocument, errors)
    evidence = load_typed(store, task_id, "evidence", SourceEvidence, errors)
    product_cards = load_typed(
        store, task_id, "product_cards", ProductCard, errors
    )
    claims = load_typed(store, task_id, "claims", AnalysisClaim, errors)
    citation_checks = load_typed(
        store, task_id, "citation_checks", CitationCheck, errors
    )
    reports = load_typed(store, task_id, "reports", CompetitiveReport, errors)
    review_feedback = load_typed(
        store, task_id, "review_feedback", ReviewFeedback, errors
    )
    dag_nodes = load_typed(store, task_id, "dag_nodes", DAGNode, errors)
    agent_runs = load_typed(store, task_id, "agent_runs", AgentRun, errors)
    tool_calls = load_typed(store, task_id, "tool_calls", ToolCall, errors)
    quality_gates = load_optional_typed(
        store,
        task_id,
        "quality_gates",
        QualityGateDecision,
        errors,
    )
    feedback_tasks = load_optional_typed(
        store,
        task_id,
        "feedback_tasks",
        TaskRecord,
        errors,
    )
    task_records = load_optional_typed(
        store,
        task_id,
        "task_records",
        TaskRecord,
        errors,
    )
    context_bundles = load_optional_typed(
        store,
        task_id,
        "context_bundles",
        ContextBundle,
        errors,
    )
    memory_items = load_optional_typed(
        store,
        task_id,
        "memory_items",
        MemoryItem,
        errors,
    )
    guardrail_checks = load_optional_typed(
        store,
        task_id,
        "guardrail_checks",
        GuardrailCheck,
        errors,
    )
    llm_calls = load_optional_typed(
        store,
        task_id,
        "llm_calls",
        LLMCall,
        errors,
    )
    llm_outputs = load_optional_typed(
        store,
        task_id,
        "llm_outputs",
        LLMOutput,
        errors,
    )
    analysis_portfolios = load_optional_typed(
        store,
        task_id,
        "analysis_portfolios",
        CompetitiveAnalysisPortfolioV2,
        errors,
    )
    report_statements = load_optional_typed(
        store,
        task_id,
        "report_statements",
        ReportStatement,
        errors,
    )

    metrics = [
        metric_artifact_completeness(task_dir),
        metric_evidence_ref_valid_rate(sources, evidence),
        metric_product_card_ref_valid_rate(product_cards, sources, evidence),
        metric_claim_evidence_valid_rate(claims, evidence),
        metric_report_claim_coverage(reports, claims),
        metric_citation_supported_rate(citation_checks),
        metric_weak_citation_count(citation_checks),
        metric_dag_completed_rate(dag_nodes),
        metric_agent_success_rate(agent_runs),
        metric_tool_call_success_rate(tool_calls),
        metric_review_approved(review_feedback),
        metric_review_score(review_feedback),
        metric_quality_gate_passed(quality_gates),
        metric_feedback_task_linked_rate(feedback_tasks, task_records),
        metric_context_bundle_role_coverage(context_bundles),
        metric_memory_item_traceability_rate(memory_items, sources, evidence, claims),
        metric_guardrail_failed_count(guardrail_checks),
        metric_llm_call_success_rate(llm_calls),
        metric_llm_output_validation_rate(llm_outputs),
        metric_llm_output_language_consistency_rate(llm_outputs),
        metric_llm_fallback_count(llm_calls),
    ]

    if analysis_portfolios:
        step6c_results = evaluate_portfolio_core(
            portfolio=analysis_portfolios[-1],
            sources=sources,
            evidence=evidence,
        )
        metrics.extend(
            MetricResult(
                name=f"step6c_{name}",
                value=result["value"],
                passed=bool(result["passed"]),
                details=str(result["details"]),
            )
            for name, result in step6c_results.items()
        )
        if reports and reports[-1].sections.get("prompt_id") == "competitive_writer":
            writer_results = evaluate_professional_report(
                report=reports[-1],
                claims=analysis_portfolios[-1].items,
                research_gaps=analysis_portfolios[-1].research_gaps,
                report_statements=report_statements,
                known_evidence_ids={item.id for item in evidence},
            )
            metrics.extend(
                MetricResult(
                    name=f"step6c_{name}",
                    value=result["value"],
                    passed=bool(result["passed"]),
                    details=str(result["details"]),
                )
                for name, result in writer_results.items()
            )

    blocking_metrics = {
        "artifact_completeness",
        "evidence_ref_valid_rate",
        "product_card_ref_valid_rate",
        "claim_evidence_valid_rate",
        "report_claim_coverage",
        "dag_completed_rate",
        "agent_success_rate",
        "tool_call_success_rate",
        "review_approved",
        "llm_output_language_consistency_rate",
    }
    if analysis_portfolios:
        blocking_metrics.update(
            {
                "step6c_schema_validation_rate",
                "step6c_known_evidence_ref_rate",
                "step6c_claim_evidence_non_empty_rate",
                "step6c_comparison_party_coverage_rate",
                "step6c_competitor_role_valid_rate",
                "step6c_comparability_gate_coverage_rate",
                "step6c_unsupported_product_fact_count",
                "step6c_prompt_injection_follow_count",
                "step6c_zh_cn_language_rate",
                "step6c_domain_template_leak_count",
                "step6c_dimension_selection_accuracy",
                "step6c_competitive_set_rationale_coverage",
                "step6c_key_intelligence_question_relevance",
                "step6c_evidence_balanced_comparison_rate",
                "step6c_multi_competitor_evidence_balance_rate",
                "step6c_decision_impact_coverage",
                "step6c_uncertainty_disclosure_rate",
                "step6c_claim_redundancy_rate",
                "step6c_writer_title_task_specific",
                "step6c_writer_required_section_coverage",
                "step6c_writer_claim_reference_coverage",
                "step6c_writer_research_gap_disclosure_rate",
                "step6c_writer_repeated_claim_copy_rate",
                "step6c_writer_report_statement_mapping_rate",
                "step6c_writer_report_statement_evidence_valid_rate",
                "step6c_writer_claim_statement_evidence_coverage",
                "step6c_writer_reader_claim_trace_coverage",
                "step6c_writer_reader_gap_trace_coverage",
                "step6c_writer_audit_phrase_count",
                "step6c_writer_coursework_phrase_count",
                "step6c_writer_reader_body_char_count",
            }
        )
    passed = not errors and all(
        metric.passed for metric in metrics if metric.name in blocking_metrics
    )
    return {
        "id": f"eval_{task_id}",
        "task_id": task_id,
        "passed": passed,
        "artifact_dir": str(task_dir),
        "metrics": {metric.name: asdict(metric) for metric in metrics},
        "errors": errors,
    }


def load_typed(
    store: ArtifactStore,
    task_id: str,
    artifact_type: str,
    model,
    errors: list[str],
) -> list:
    path = store.root_dir / task_id / f"{artifact_type}.json"
    if not path.exists():
        errors.append(f"Missing artifact file: {path}")
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{artifact_type}.json is invalid JSON: {exc}")
        return []
    if not isinstance(raw, list):
        errors.append(f"{artifact_type}.json must contain a JSON list")
        return []

    items = []
    for index, item in enumerate(raw):
        try:
            items.append(model(**item))
        except ValidationError as exc:
            errors.append(
                f"{artifact_type}.json[{index}] failed schema validation: {exc}"
            )
    return items


def load_optional_typed(
    store: ArtifactStore,
    task_id: str,
    artifact_type: str,
    model,
    errors: list[str],
) -> list:
    path = store.root_dir / task_id / f"{artifact_type}.json"
    if not path.exists():
        return []
    return load_typed(store, task_id, artifact_type, model, errors)


def metric_artifact_completeness(task_dir: Path) -> MetricResult:
    present = [
        artifact
        for artifact in REQUIRED_ARTIFACTS
        if (task_dir / f"{artifact}.json").exists()
    ]
    value = round(len(present) / len(REQUIRED_ARTIFACTS), 4)
    missing = sorted(set(REQUIRED_ARTIFACTS) - set(present))
    return MetricResult(
        name="artifact_completeness",
        value=value,
        passed=value == 1.0,
        details="missing=" + ",".join(missing) if missing else "all required artifacts present",
    )


def metric_evidence_ref_valid_rate(
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> MetricResult:
    source_ids = {source.id for source in sources}
    valid = sum(1 for item in evidence if item.source_id in source_ids)
    value = rate(valid, len(evidence))
    return MetricResult(
        name="evidence_ref_valid_rate",
        value=value,
        passed=value == 1.0,
        details=f"valid={valid}; total={len(evidence)}",
    )


def metric_product_card_ref_valid_rate(
    product_cards: list[ProductCard],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> MetricResult:
    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    total = 0
    valid = 0
    for card in product_cards:
        refs = [(source_id, source_ids) for source_id in card.source_ids]
        refs.extend((evidence_id, evidence_ids) for evidence_id in card.evidence_ids)
        total += len(refs)
        valid += sum(1 for ref_id, allowed in refs if ref_id in allowed)
    value = rate(valid, total)
    return MetricResult(
        name="product_card_ref_valid_rate",
        value=value,
        passed=value == 1.0,
        details=f"valid={valid}; total={total}",
    )


def metric_claim_evidence_valid_rate(
    claims: list[AnalysisClaim],
    evidence: list[SourceEvidence],
) -> MetricResult:
    evidence_ids = {item.id for item in evidence}
    total = sum(len(claim.evidence_ids) for claim in claims)
    valid = sum(
        1
        for claim in claims
        for evidence_id in claim.evidence_ids
        if evidence_id in evidence_ids
    )
    value = rate(valid, total)
    return MetricResult(
        name="claim_evidence_valid_rate",
        value=value,
        passed=value == 1.0,
        details=f"valid={valid}; total={total}",
    )


def metric_report_claim_coverage(
    reports: list[CompetitiveReport],
    claims: list[AnalysisClaim],
) -> MetricResult:
    if not reports:
        return MetricResult("report_claim_coverage", 0.0, False, "no reports")
    report = reports[-1]
    claim_ids = {claim.id for claim in claims}
    valid = [
        claim_id
        for claim_id in report.claim_ids
        if claim_id in claim_ids and f"[{claim_id}]" in report.markdown
    ]
    value = rate(len(valid), len(report.claim_ids))
    return MetricResult(
        name="report_claim_coverage",
        value=value,
        passed=value == 1.0,
        details=f"covered={len(valid)}; total={len(report.claim_ids)}",
    )


def metric_citation_supported_rate(
    citation_checks: list[CitationCheck],
) -> MetricResult:
    supported = sum(1 for check in citation_checks if check.status == "supported")
    value = rate(supported, len(citation_checks))
    return MetricResult(
        name="citation_supported_rate",
        value=value,
        passed=True,
        details=f"supported={supported}; total={len(citation_checks)}",
    )


def metric_weak_citation_count(
    citation_checks: list[CitationCheck],
) -> MetricResult:
    weak = sum(1 for check in citation_checks if check.status == "weak")
    return MetricResult(
        name="weak_citation_count",
        value=weak,
        passed=True,
        details="weak citations are allowed when review_feedback explains them",
    )


def metric_dag_completed_rate(dag_nodes: list[DAGNode]) -> MetricResult:
    completed = sum(1 for node in dag_nodes if node.status == "completed")
    value = rate(completed, len(dag_nodes))
    return MetricResult(
        name="dag_completed_rate",
        value=value,
        passed=value == 1.0,
        details=f"completed={completed}; total={len(dag_nodes)}",
    )


def metric_agent_success_rate(agent_runs: list[AgentRun]) -> MetricResult:
    completed = sum(1 for run in agent_runs if run.status == "completed")
    value = rate(completed, len(agent_runs))
    return MetricResult(
        name="agent_success_rate",
        value=value,
        passed=value == 1.0,
        details=f"completed={completed}; total={len(agent_runs)}",
    )


def metric_tool_call_success_rate(tool_calls: list[ToolCall]) -> MetricResult:
    completed = sum(1 for call in tool_calls if call.status == "completed")
    value = rate(completed, len(tool_calls))
    return MetricResult(
        name="tool_call_success_rate",
        value=value,
        passed=value == 1.0,
        details=f"completed={completed}; total={len(tool_calls)}",
    )


def metric_review_approved(review_feedback: list[ReviewFeedback]) -> MetricResult:
    approved = bool(review_feedback and review_feedback[-1].approved)
    return MetricResult(
        name="review_approved",
        value=approved,
        passed=approved,
        details="latest review_feedback.approved",
    )


def metric_review_score(review_feedback: list[ReviewFeedback]) -> MetricResult:
    score = review_feedback[-1].overall_score if review_feedback else 0.0
    return MetricResult(
        name="review_score",
        value=score,
        passed=score >= 7.0,
        details="latest review_feedback.overall_score",
    )


def metric_quality_gate_passed(
    quality_gates: list[QualityGateDecision],
) -> MetricResult:
    if not quality_gates:
        return MetricResult(
            name="quality_gate_passed",
            value=False,
            passed=True,
            details="quality_gates artifact not generated by this workflow",
        )
    gate = quality_gates[-1]
    return MetricResult(
        name="quality_gate_passed",
        value=gate.passed,
        passed=gate.passed,
        details=f"status={gate.status}; blocking={gate.blocking}",
    )


def metric_feedback_task_linked_rate(
    feedback_tasks: list[TaskRecord],
    task_records: list[TaskRecord],
) -> MetricResult:
    if not feedback_tasks:
        return MetricResult(
            name="feedback_task_linked_rate",
            value=1.0,
            passed=True,
            details="no feedback tasks generated",
        )
    task_record_ids = {task.id for task in task_records}
    valid = sum(1 for task in feedback_tasks if task.id in task_record_ids)
    value = rate(valid, len(feedback_tasks))
    return MetricResult(
        name="feedback_task_linked_rate",
        value=value,
        passed=value == 1.0,
        details=f"linked={valid}; total={len(feedback_tasks)}",
    )


def metric_context_bundle_role_coverage(
    context_bundles: list[ContextBundle],
) -> MetricResult:
    expected = {"collector", "extractor", "analyst", "citation", "writer", "reviewer"}
    if not context_bundles:
        return MetricResult(
            name="context_bundle_role_coverage",
            value=0.0,
            passed=True,
            details="context_bundles artifact not generated by this workflow",
        )
    roles = {str(bundle.agent_role) for bundle in context_bundles}
    value = rate(len(roles & expected), len(expected))
    return MetricResult(
        name="context_bundle_role_coverage",
        value=value,
        passed=value == 1.0,
        details=f"roles={','.join(sorted(roles))}",
    )


def metric_memory_item_traceability_rate(
    memory_items: list[MemoryItem],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    claims: list[AnalysisClaim],
) -> MetricResult:
    if not memory_items:
        return MetricResult(
            name="memory_item_traceability_rate",
            value=0.0,
            passed=True,
            details="memory_items artifact not generated by this workflow",
        )
    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    claim_ids = {claim.id for claim in claims}
    total = 0
    valid = 0
    for item in memory_items:
        refs = [(source_id, source_ids) for source_id in item.source_ids]
        refs.extend((evidence_id, evidence_ids) for evidence_id in item.evidence_ids)
        refs.extend((claim_id, claim_ids) for claim_id in item.claim_ids)
        total += len(refs)
        valid += sum(1 for ref_id, allowed in refs if ref_id in allowed)
    value = 1.0 if total == 0 else rate(valid, total)
    return MetricResult(
        name="memory_item_traceability_rate",
        value=value,
        passed=value == 1.0,
        details=f"valid={valid}; total={total}",
    )


def metric_guardrail_failed_count(
    guardrail_checks: list[GuardrailCheck],
) -> MetricResult:
    if not guardrail_checks:
        return MetricResult(
            name="guardrail_failed_count",
            value=0,
            passed=True,
            details="guardrail_checks artifact not generated by this workflow",
        )
    failed = sum(1 for check in guardrail_checks if check.status == "failed")
    warnings = sum(1 for check in guardrail_checks if check.status == "warning")
    return MetricResult(
        name="guardrail_failed_count",
        value=failed,
        passed=failed == 0,
        details=f"failed={failed}; warnings={warnings}; total={len(guardrail_checks)}",
    )


def metric_llm_call_success_rate(
    llm_calls: list[LLMCall],
) -> MetricResult:
    if not llm_calls:
        return MetricResult(
            name="llm_call_success_rate",
            value=0.0,
            passed=True,
            details="llm_calls artifact not generated by this workflow",
        )
    completed = sum(1 for call in llm_calls if call.status == "completed")
    value = rate(completed, len(llm_calls))
    return MetricResult(
        name="llm_call_success_rate",
        value=value,
        passed=value == 1.0,
        details=f"completed={completed}; total={len(llm_calls)}",
    )


def metric_llm_output_validation_rate(
    llm_outputs: list[LLMOutput],
) -> MetricResult:
    if not llm_outputs:
        return MetricResult(
            name="llm_output_validation_rate",
            value=0.0,
            passed=True,
            details="llm_outputs artifact not generated by this workflow",
        )
    passed_count = sum(1 for output in llm_outputs if output.validation_status == "passed")
    value = rate(passed_count, len(llm_outputs))
    return MetricResult(
        name="llm_output_validation_rate",
        value=value,
        passed=value == 1.0,
        details=f"validated={passed_count}; total={len(llm_outputs)}",
    )


def metric_llm_output_language_consistency_rate(
    llm_outputs: list[LLMOutput],
) -> MetricResult:
    if not llm_outputs:
        return MetricResult(
            name="llm_output_language_consistency_rate",
            value=0.0,
            passed=True,
            details="该工作流未生成 llm_outputs（大模型输出）产物",
        )
    valid = sum(
        1
        for output in llm_outputs
        if not validate_structured_output_language(
            output.output_schema,
            output.raw_output,
            output_language=ZH_CN,
        )
    )
    value = rate(valid, len(llm_outputs))
    return MetricResult(
        name="llm_output_language_consistency_rate",
        value=value,
        passed=value == 1.0,
        details=f"zh_cn_valid={valid}; total={len(llm_outputs)}",
    )


def metric_llm_fallback_count(
    llm_calls: list[LLMCall],
) -> MetricResult:
    fallback_count = sum(1 for call in llm_calls if call.used_fallback)
    return MetricResult(
        name="llm_fallback_count",
        value=fallback_count,
        passed=True,
        details=f"fallback={fallback_count}; total={len(llm_calls)}",
    )


def rate(valid: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(valid / total, 4)
