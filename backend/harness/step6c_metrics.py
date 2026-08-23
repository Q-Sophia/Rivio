from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from app.llm.language import ZH_CN, validate_structured_output_language
from app.schemas import (
    CompetitiveAnalysisPortfolioV2,
    SourceDocument,
    SourceEvidence,
)


ROLE_VALUES = {"direct", "indirect", "substitute", "benchmark", "emerging"}
UNCERTAINTY_TYPES = {"inference", "risk", "opportunity", "recommendation"}
COMPARISON_TYPES = {"comparison", "baseline"}
COMPARABILITY_DIMENSIONS = {"pricing", "feature", "risk", "market"}


def _rate(valid: int, total: int, *, empty: float = 1.0) -> float:
    if total <= 0:
        return empty
    return round(valid / total, 4)


def _metric(value: float | int | bool, passed: bool, details: str) -> dict[str, Any]:
    return {"value": value, "passed": passed, "details": details}


def _normalize_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def evaluate_portfolio_core(
    *,
    portfolio: CompetitiveAnalysisPortfolioV2,
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    allowed_dimensions: set[str] | None = None,
    forbidden_terms: set[str] | None = None,
    injection_markers: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    source_ids = {item.id for item in sources}
    evidence_ids = {item.id for item in evidence}
    evidence_competitor = {item.id: item.competitor for item in evidence}
    known_competitors = {item.name for item in portfolio.competitor_profiles}
    raw_output = {"item": portfolio.model_dump(mode="json")}
    output_text = json.dumps(raw_output, ensure_ascii=False)

    referenced_evidence = [
        evidence_id
        for profile in portfolio.competitor_profiles
        for evidence_id in profile.evidence_ids
    ]
    referenced_evidence.extend(
        evidence_id
        for coverage in portfolio.evidence_coverage
        for evidence_id in coverage.evidence_ids
    )
    referenced_evidence.extend(
        evidence_id
        for claim in portfolio.items
        for evidence_id in [*claim.evidence_ids, *claim.counter_evidence_ids]
    )
    referenced_evidence.extend(
        evidence_id
        for gap in portfolio.research_gaps
        for evidence_id in gap.related_evidence_ids
    )
    known_ref_count = sum(1 for item in referenced_evidence if item in evidence_ids)

    claim_refs_valid = [claim for claim in portfolio.items if claim.evidence_ids]
    comparison_claims = [
        claim for claim in portfolio.items if claim.claim_type in COMPARISON_TYPES
    ]
    balanced_comparisons = []
    for claim in comparison_claims:
        covered = {
            evidence_competitor.get(evidence_id, "")
            for evidence_id in claim.evidence_ids
        }
        if all(competitor in covered for competitor in claim.competitors):
            balanced_comparisons.append(claim)
    multi_competitor_claims = [
        claim for claim in portfolio.items if len(claim.competitors) >= 2
    ]
    balanced_multi_competitor_claims = []
    for claim in multi_competitor_claims:
        covered = {
            evidence_competitor.get(evidence_id, "")
            for evidence_id in claim.evidence_ids
        }
        if all(competitor in covered for competitor in claim.competitors):
            balanced_multi_competitor_claims.append(claim)

    valid_roles = [
        profile
        for profile in portfolio.competitor_profiles
        if profile.role in ROLE_VALUES
        and profile.selection_reason.strip()
        and profile.represented_path.strip()
    ]
    notes_by_dimension = {
        note.dimension: note for note in portfolio.comparability_notes
    }
    gated_claims = [
        claim
        for claim in portfolio.items
        if claim.dimension not in COMPARABILITY_DIMENSIONS
        or claim.dimension in notes_by_dimension
    ]

    unsupported_product_facts = sum(
        1
        for claim in portfolio.items
        if not claim.evidence_ids
        or any(item not in evidence_ids for item in claim.evidence_ids)
        or any(item not in known_competitors for item in claim.competitors)
    )
    language_errors = validate_structured_output_language(
        "CompetitiveAnalysisPortfolioV2",
        raw_output,
        output_language=ZH_CN,
    )

    allowed = allowed_dimensions or {str(item.dimension) for item in evidence}
    selected = set(portfolio.brief_assessment.selected_dimensions)
    relevant_dimensions = selected & allowed

    rationale_profiles = []
    for profile in portfolio.competitor_profiles:
        has_note = any(
            profile.name in note.competitors for note in portfolio.comparability_notes
        )
        if (
            profile.role in ROLE_VALUES
            and profile.selection_reason.strip()
            and profile.represented_path.strip()
            and has_note
        ):
            rationale_profiles.append(profile)

    valid_kiqs = [
        item
        for item in portfolio.key_intelligence_questions
        if item.question.strip()
        and item.decision_link.strip()
        and item.dimensions
        and len(item.question.strip()) >= 12
        and len(item.decision_link.strip()) >= 6
        and bool(
            set(item.dimensions)
            & set(portfolio.brief_assessment.selected_dimensions)
        )
    ]
    non_fact_claims = [item for item in portfolio.items if item.claim_type != "fact"]
    cross_comparisons = [
        item
        for item in non_fact_claims
        if item.claim_type in COMPARISON_TYPES and len(item.competitors) >= 2
    ]
    decision_impacts = [
        item
        for item in non_fact_claims
        if len(item.decision_impact.strip()) >= 8
        and item.decision_impact.strip() not in {"有助于决策", "供决策参考"}
    ]
    uncertain_claims = [
        item for item in portfolio.items if item.claim_type in UNCERTAINTY_TYPES
    ]
    uncertainty_disclosed = [
        item for item in uncertain_claims if len(item.uncertainty.strip()) >= 6
    ]

    redundant_pairs = 0
    total_pairs = 0
    for index, left in enumerate(portfolio.items):
        for right in portfolio.items[index + 1 :]:
            total_pairs += 1
            if left.dimension != right.dimension:
                continue
            if set(left.competitors) != set(right.competitors):
                continue
            union = set(left.evidence_ids) | set(right.evidence_ids)
            overlap = _rate(
                len(set(left.evidence_ids) & set(right.evidence_ids)),
                len(union),
                empty=0.0,
            )
            similarity = SequenceMatcher(
                None,
                _normalize_text(left.claim_text),
                _normalize_text(right.claim_text),
            ).ratio()
            if overlap >= 0.8 and similarity >= 0.85:
                redundant_pairs += 1

    forbidden = forbidden_terms or set()
    leaked_terms = sorted(item for item in forbidden if item in output_text)
    markers = injection_markers or set()
    followed_markers = sorted(item for item in markers if item in output_text)

    known_evidence_rate = _rate(known_ref_count, len(referenced_evidence))
    non_empty_rate = _rate(len(claim_refs_valid), len(portfolio.items))
    comparison_coverage = _rate(len(balanced_comparisons), len(comparison_claims))
    multi_competitor_balance = _rate(
        len(balanced_multi_competitor_claims),
        len(multi_competitor_claims),
    )
    role_rate = _rate(len(valid_roles), len(portfolio.competitor_profiles))
    gate_rate = _rate(len(gated_claims), len(portfolio.items))
    dimension_rate = _rate(len(relevant_dimensions), len(selected))
    rationale_rate = _rate(len(rationale_profiles), len(portfolio.competitor_profiles))
    kiq_rate = _rate(len(valid_kiqs), len(portfolio.key_intelligence_questions))
    cross_rate = _rate(len(cross_comparisons), len(non_fact_claims))
    decision_rate = _rate(len(decision_impacts), len(non_fact_claims))
    uncertainty_rate = _rate(len(uncertainty_disclosed), len(uncertain_claims))
    redundancy_rate = _rate(redundant_pairs, total_pairs, empty=0.0)

    return {
        "schema_validation_rate": _metric(1.0, True, "portfolio 已通过 Pydantic Schema 校验"),
        "known_evidence_ref_rate": _metric(
            known_evidence_rate,
            known_evidence_rate == 1.0,
            f"known={known_ref_count}; total={len(referenced_evidence)}",
        ),
        "claim_evidence_non_empty_rate": _metric(
            non_empty_rate,
            non_empty_rate == 1.0,
            f"non_empty={len(claim_refs_valid)}; total={len(portfolio.items)}",
        ),
        "comparison_party_coverage_rate": _metric(
            comparison_coverage,
            comparison_coverage == 1.0,
            f"balanced={len(balanced_comparisons)}; total={len(comparison_claims)}",
        ),
        "competitor_role_valid_rate": _metric(
            role_rate,
            role_rate == 1.0,
            f"valid={len(valid_roles)}; total={len(portfolio.competitor_profiles)}",
        ),
        "comparability_gate_coverage_rate": _metric(
            gate_rate,
            gate_rate == 1.0,
            f"gated={len(gated_claims)}; total={len(portfolio.items)}",
        ),
        "unsupported_product_fact_count": _metric(
            unsupported_product_facts,
            unsupported_product_facts == 0,
            "启发式检查未知竞品、空证据或未知 evidence_id",
        ),
        "prompt_injection_follow_count": _metric(
            len(followed_markers),
            not followed_markers,
            "followed=" + ",".join(followed_markers),
        ),
        "zh_cn_language_rate": _metric(
            1.0 if not language_errors else 0.0,
            not language_errors,
            "；".join(language_errors) if language_errors else "全部业务文本通过中文检查",
        ),
        "domain_template_leak_count": _metric(
            len(leaked_terms),
            not leaked_terms,
            "leaked=" + ",".join(leaked_terms),
        ),
        "dimension_selection_accuracy": _metric(
            dimension_rate,
            dimension_rate == 1.0,
            f"selected={sorted(selected)}; allowed={sorted(allowed)}",
        ),
        "competitive_set_rationale_coverage": _metric(
            rationale_rate,
            rationale_rate == 1.0,
            f"covered={len(rationale_profiles)}; total={len(portfolio.competitor_profiles)}",
        ),
        "key_intelligence_question_relevance": _metric(
            kiq_rate,
            kiq_rate >= 0.8,
            f"valid={len(valid_kiqs)}; total={len(portfolio.key_intelligence_questions)}",
        ),
        "cross_competitor_comparison_rate": _metric(
            cross_rate,
            cross_rate >= 0.4 or not non_fact_claims,
            f"cross={len(cross_comparisons)}; non_fact={len(non_fact_claims)}",
        ),
        "evidence_balanced_comparison_rate": _metric(
            comparison_coverage,
            comparison_coverage == 1.0,
            f"balanced={len(balanced_comparisons)}; total={len(comparison_claims)}",
        ),
        "multi_competitor_evidence_balance_rate": _metric(
            multi_competitor_balance,
            multi_competitor_balance == 1.0,
            (
                f"balanced={len(balanced_multi_competitor_claims)}; "
                f"total={len(multi_competitor_claims)}"
            ),
        ),
        "decision_impact_coverage": _metric(
            decision_rate,
            decision_rate >= 0.9,
            f"specific={len(decision_impacts)}; non_fact={len(non_fact_claims)}",
        ),
        "uncertainty_disclosure_rate": _metric(
            uncertainty_rate,
            uncertainty_rate == 1.0,
            f"disclosed={len(uncertainty_disclosed)}; total={len(uncertain_claims)}",
        ),
        "claim_redundancy_rate": _metric(
            redundancy_rate,
            redundancy_rate <= 0.15,
            f"redundant_pairs={redundant_pairs}; total_pairs={total_pairs}",
        ),
        "claim_type_distribution": _metric(
            dict(Counter(str(item.claim_type) for item in portfolio.items)),
            True,
            "仅记录分布，不以类型越多作为通过条件",
        ),
    }


def evaluate_fixture_expectations(
    *,
    fixture,
    portfolio: CompetitiveAnalysisPortfolioV2,
) -> dict[str, dict[str, Any]]:
    predicted_gap_dimensions = {item.dimension for item in portfolio.research_gaps}
    expected_gap_dimensions = set(fixture.expected_gap_dimensions)
    matched = predicted_gap_dimensions & expected_gap_dimensions
    if not expected_gap_dimensions and not predicted_gap_dimensions:
        precision = recall = 1.0
    else:
        precision = _rate(len(matched), len(predicted_gap_dimensions))
        recall = _rate(len(matched), len(expected_gap_dimensions))

    actual_statuses = {str(item.status) for item in portfolio.evidence_coverage}
    expected_statuses = set(fixture.expected_coverage_statuses)
    coverage_status_passed = expected_statuses <= actual_statuses

    actual_roles = {item.name: str(item.role) for item in portfolio.competitor_profiles}
    role_match = actual_roles == fixture.expected_roles
    comparison_claims = [
        item
        for item in portfolio.items
        if item.claim_type in COMPARISON_TYPES and len(item.competitors) >= 2
    ]
    no_cross_comparison_passed = (
        not fixture.require_no_cross_comparison or not comparison_claims
    )
    has_recommendation = any(
        item.claim_type == "recommendation" for item in portfolio.items
    )
    recommendation_passed = (
        not fixture.require_recommendation_candidate or has_recommendation
    )
    actual_claim_types = {str(item.claim_type) for item in portfolio.items}
    claim_types_passed = fixture.expected_claim_types <= actual_claim_types

    combined_path_text = " ".join(
        [
            item.represented_path + " " + item.delivery_model
            for item in portfolio.competitor_profiles
        ]
        + [item.claim_text + " " + item.decision_impact for item in portfolio.items]
    )
    tradeoff_checks = {
        "workflow_control": (
            ("完整工作流" in combined_path_text or "完整成品" in combined_path_text)
            and ("定制控制" in combined_path_text or "控制权" in combined_path_text)
        ),
        "integration_responsibility": (
            "集成" in combined_path_text
            and ("自研责任" in combined_path_text or "客户承担" in combined_path_text)
        ),
        "total_cost": "总体拥有成本" in combined_path_text,
        "operations": "运维" in combined_path_text,
        "ecosystem_migration": (
            "生态依赖" in combined_path_text and "迁移控制" in combined_path_text
        ),
    }
    tradeoff_count = sum(tradeoff_checks.values())
    tradeoff_passed = not fixture.require_path_tradeoffs or tradeoff_count >= 3

    behavior_passed = all(
        [
            coverage_status_passed,
            role_match,
            no_cross_comparison_passed,
            recommendation_passed,
            claim_types_passed,
            tradeoff_passed,
        ]
    )
    return {
        "research_gap_precision": _metric(
            precision,
            precision >= 0.8,
            f"predicted={sorted(predicted_gap_dimensions)}; expected={sorted(expected_gap_dimensions)}",
        ),
        "research_gap_recall": _metric(
            recall,
            recall >= 0.8,
            f"matched={sorted(matched)}; expected={sorted(expected_gap_dimensions)}",
        ),
        "fixture_expected_coverage_status": _metric(
            coverage_status_passed,
            coverage_status_passed,
            f"actual={sorted(actual_statuses)}; expected={sorted(expected_statuses)}",
        ),
        "fixture_competitor_role_match": _metric(
            role_match,
            role_match,
            f"actual={actual_roles}; expected={fixture.expected_roles}",
        ),
        "fixture_no_forced_comparison": _metric(
            no_cross_comparison_passed,
            no_cross_comparison_passed,
            f"cross_comparisons={len(comparison_claims)}",
        ),
        "fixture_recommendation_candidate": _metric(
            recommendation_passed,
            recommendation_passed,
            f"has_recommendation={has_recommendation}",
        ),
        "fixture_expected_claim_types": _metric(
            claim_types_passed,
            claim_types_passed,
            f"actual={sorted(actual_claim_types)}; expected={sorted(fixture.expected_claim_types)}",
        ),
        "path_tradeoff_coverage": _metric(
            tradeoff_count,
            tradeoff_passed,
            json.dumps(tradeoff_checks, ensure_ascii=False, sort_keys=True),
        ),
        "fixture_behavior_passed": _metric(
            behavior_passed,
            behavior_passed,
            "关键预期行为的合取结果",
        ),
    }
