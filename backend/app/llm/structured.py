from __future__ import annotations

from typing import Any

from app.schemas import (
    AnalysisClaim,
    CompetitiveAnalysisPortfolioV2,
    CompetitiveReport,
    ProductCard,
)


def normalize_report_claim_references(
    raw_output: dict,
    known_claim_ids: list[str],
) -> tuple[dict, list[str]]:
    """Ensure a report deterministically references every known input claim.

    The function never invents claim ids. Unknown ids returned by a provider are
    preserved so the normal known-reference validation can reject them.
    """
    raw_item = raw_output.get("item")
    if not isinstance(raw_item, dict):
        return raw_output, []

    normalized_item = dict(raw_item)
    declared_claim_ids = normalized_item.get("claim_ids") or []
    claim_ids: list[str] = []
    for claim_id in [*declared_claim_ids, *known_claim_ids]:
        value = str(claim_id)
        if value and value not in claim_ids:
            claim_ids.append(value)
    normalized_item["claim_ids"] = claim_ids

    known = set(known_claim_ids)
    markdown = str(normalized_item.get("markdown") or "")
    added_claim_ids = [
        claim_id
        for claim_id in claim_ids
        if claim_id in known and f"[{claim_id}]" not in markdown
    ]
    if added_claim_ids:
        index_lines = "\n".join(f"- [{claim_id}]" for claim_id in added_claim_ids)
        markdown = markdown.rstrip() + "\n\n## 结论引用索引\n\n" + index_lines + "\n"
        normalized_item["markdown"] = markdown

    return {**raw_output, "item": normalized_item}, added_claim_ids


def parse_product_cards(raw_output: dict) -> list[ProductCard]:
    return [ProductCard(**item) for item in raw_output.get("items", [])]


def parse_analysis_claims(raw_output: dict) -> list[AnalysisClaim]:
    return [AnalysisClaim(**item) for item in raw_output.get("items", [])]


def parse_competitive_report(raw_output: dict) -> CompetitiveReport:
    return CompetitiveReport(**raw_output.get("item", {}))


def parse_competitive_analysis_portfolio_v2(
    raw_output: dict,
) -> CompetitiveAnalysisPortfolioV2:
    return CompetitiveAnalysisPortfolioV2(**raw_output.get("item", {}))


def portfolio_v2_to_legacy_claims(
    portfolio: CompetitiveAnalysisPortfolioV2,
) -> list[AnalysisClaim]:
    """Keep the evidence-first V1 chain alive while V2 artifacts mature."""
    claims: list[AnalysisClaim] = []
    for item in portfolio.items:
        claims.append(
            AnalysisClaim(
                id=item.id,
                task_id=item.task_id,
                dimension=_legacy_dimension(item.dimension),
                claim_text=item.claim_text,
                competitors=item.competitors,
                evidence_ids=item.evidence_ids,
                confidence=item.confidence,
                produced_by_agent_run_id=item.produced_by_agent_run_id,
                citation_status=item.citation_status,
                metadata={
                    **item.metadata,
                    "claim_type": item.claim_type,
                    "counter_evidence_ids": item.counter_evidence_ids,
                    "reasoning_summary": item.reasoning_summary,
                    "uncertainty": item.uncertainty,
                    "decision_impact": item.decision_impact,
                    "source_schema": "AnalysisClaimV2",
                },
            )
        )
    return claims


def _legacy_dimension(dimension: str) -> str:
    known = {
        "positioning",
        "pricing",
        "feature",
        "ecosystem",
        "market",
        "customer",
        "funding",
        "risk",
        "other",
    }
    return dimension if dimension in known else "other"


def validate_non_empty_evidence_ids(claims: list[AnalysisClaim]) -> None:
    missing = [claim.id for claim in claims if not claim.evidence_ids]
    if missing:
        raise ValueError("AnalysisClaim items missing evidence_ids: " + ", ".join(missing))


def validate_product_card_refs(cards: list[ProductCard]) -> None:
    missing = [
        card.id
        for card in cards
        if not card.source_ids or not card.evidence_ids
    ]
    if missing:
        raise ValueError(
            "ProductCard items missing source_ids or evidence_ids: "
            + ", ".join(missing)
        )


def validate_report_claim_ids(report: CompetitiveReport) -> None:
    if not report.claim_ids:
        raise ValueError("CompetitiveReport.claim_ids is empty")
    missing = [
        claim_id for claim_id in report.claim_ids if f"[{claim_id}]" not in report.markdown
    ]
    if missing:
        raise ValueError(
            "CompetitiveReport.markdown missing claim ids: "
            + ", ".join(missing)
        )


def reject_unaligned_portfolio_v2_claims(
    portfolio: CompetitiveAnalysisPortfolioV2,
    *,
    evidence_competitors: dict[str, str],
    known_competitors: set[str],
) -> tuple[CompetitiveAnalysisPortfolioV2, list[dict[str, Any]]]:
    """Remove whole claims that violate claim/competitor/evidence alignment.

    This governance filter never rewrites a claim or invents evidence. Rejected
    claims remain in audit metadata so the original model failure is visible.
    """

    accepted = []
    rejected: list[dict[str, Any]] = []
    for claim in portfolio.items:
        reasons: list[str] = []
        auditable_text = " ".join(
            [
                claim.claim_text,
                claim.reasoning_summary,
                claim.uncertainty,
                claim.decision_impact,
            ]
        )
        named_competitors = {
            competitor
            for competitor in known_competitors
            if competitor and competitor in auditable_text
        }
        omitted = sorted(named_competitors - set(claim.competitors))
        if omitted:
            reasons.append(
                "文本点名但 competitors 未登记: " + ", ".join(omitted)
            )

        covered = {
            evidence_competitors.get(evidence_id, "")
            for evidence_id in claim.evidence_ids
        }
        uncovered = sorted(
            competitor
            for competitor in claim.competitors
            if competitor not in covered
        )
        if uncovered:
            reasons.append(
                "competitors 缺少对应 evidence_id: " + ", ".join(uncovered)
            )

        if reasons:
            rejected.append(
                {
                    "claim_id": claim.id,
                    "reasons": reasons,
                    "claim": claim.model_dump(mode="json"),
                }
            )
        else:
            accepted.append(claim)

    if not rejected:
        return portfolio, []

    metadata = {
        **portfolio.metadata,
        "claim_alignment_filter": {
            "policy_version": "v1",
            "strategy": "reject_whole_claim_without_rewrite",
            "original_claims_count": len(portfolio.items),
            "accepted_claims_count": len(accepted),
            "rejected_claim_ids": [item["claim_id"] for item in rejected],
        },
    }
    return portfolio.model_copy(update={"items": accepted, "metadata": metadata}), rejected


def validate_portfolio_v2_refs(
    portfolio: CompetitiveAnalysisPortfolioV2,
    *,
    known_source_ids: set[str],
    known_evidence_ids: set[str],
    known_competitors: set[str],
    evidence_competitors: dict[str, str] | None = None,
) -> None:
    if not portfolio.competitor_profiles:
        raise ValueError("CompetitiveAnalysisPortfolioV2.competitor_profiles 为空")
    if not portfolio.key_intelligence_questions:
        raise ValueError("CompetitiveAnalysisPortfolioV2.key_intelligence_questions 为空")
    if not portfolio.items:
        raise ValueError("CompetitiveAnalysisPortfolioV2.items 为空")

    invalid_sources = sorted(
        {
            source_id
            for profile in portfolio.competitor_profiles
            for source_id in profile.source_ids
            if source_id not in known_source_ids
        }
        | {
            source_id
            for coverage in portfolio.evidence_coverage
            for source_id in coverage.source_ids
            if source_id not in known_source_ids
        }
    )
    invalid_evidence = sorted(
        {
            evidence_id
            for profile in portfolio.competitor_profiles
            for evidence_id in profile.evidence_ids
            if evidence_id not in known_evidence_ids
        }
        | {
            evidence_id
            for coverage in portfolio.evidence_coverage
            for evidence_id in coverage.evidence_ids
            if evidence_id not in known_evidence_ids
        }
        | {
            evidence_id
            for claim in portfolio.items
            for evidence_id in [*claim.evidence_ids, *claim.counter_evidence_ids]
            if evidence_id not in known_evidence_ids
        }
        | {
            evidence_id
            for gap in portfolio.research_gaps
            for evidence_id in gap.related_evidence_ids
            if evidence_id not in known_evidence_ids
        }
    )
    invalid_competitors = sorted(
        {
            competitor
            for profile in portfolio.competitor_profiles
            for competitor in [profile.name]
            if known_competitors and competitor not in known_competitors
        }
        | {
            competitor
            for claim in portfolio.items
            for competitor in claim.competitors
            if known_competitors and competitor not in known_competitors
        }
    )
    if invalid_sources:
        raise ValueError("V2 产物引用未知 source_id: " + ", ".join(invalid_sources))
    if invalid_evidence:
        raise ValueError("V2 产物引用未知 evidence_id: " + ", ".join(invalid_evidence))
    if invalid_competitors:
        raise ValueError("V2 产物引用未知竞品: " + ", ".join(invalid_competitors))

    missing_claim_evidence = [item.id for item in portfolio.items if not item.evidence_ids]
    if missing_claim_evidence:
        raise ValueError(
            "AnalysisClaimV2 缺少 evidence_ids: "
            + ", ".join(missing_claim_evidence)
        )

    evidence_competitor = dict(evidence_competitors or {})
    if not evidence_competitor:
        for profile in portfolio.competitor_profiles:
            for evidence_id in profile.evidence_ids:
                evidence_competitor[evidence_id] = profile.name

    omitted_named_competitors: list[str] = []
    for claim in portfolio.items:
        auditable_text = " ".join(
            [
                claim.claim_text,
                claim.reasoning_summary,
                claim.uncertainty,
                claim.decision_impact,
            ]
        )
        named_competitors = {
            competitor
            for competitor in known_competitors
            if competitor and competitor in auditable_text
        }
        omitted = sorted(named_competitors - set(claim.competitors))
        if omitted:
            omitted_named_competitors.append(
                f"{claim.id}({','.join(omitted)})"
            )
    if omitted_named_competitors:
        raise ValueError(
            "结论文本点名竞品但 competitors 未登记: "
            + ", ".join(omitted_named_competitors)
        )

    unbalanced: list[str] = []
    for claim in portfolio.items:
        covered = {
            evidence_competitor.get(evidence_id, "")
            for evidence_id in claim.evidence_ids
        }
        missing_competitors = [
            competitor
            for competitor in claim.competitors
            if competitor not in covered
        ]
        if missing_competitors:
            unbalanced.append(
                f"{claim.id}({','.join(missing_competitors)})"
            )
    if unbalanced:
        raise ValueError(
            "多竞品结论未逐一覆盖所有参与方证据: " + ", ".join(unbalanced)
        )
