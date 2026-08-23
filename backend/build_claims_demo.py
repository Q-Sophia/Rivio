from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import AnalysisClaim, EvidenceDimension, ProductCard, SourceEvidence
from build_product_cards_demo import DEFAULT_TASK_ID


PRODUCED_BY_AGENT_RUN_ID = "rule_milestone2_claim_builder"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic rule-based AnalysisClaim artifacts from "
            "saved ProductCard and SourceEvidence artifacts."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def load_product_cards_and_evidence(
    store: ArtifactStore,
    task_id: str,
) -> tuple[list[ProductCard], list[SourceEvidence]]:
    product_cards = [
        ProductCard(**item)
        for item in store.load_many(task_id, "product_cards")
    ]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    if not product_cards:
        raise ValueError(
            f"No product_cards found for task_id={task_id}; run build_product_cards_demo.py first"
        )
    if not evidence:
        raise ValueError(f"No evidence found for task_id={task_id}")
    validate_product_card_evidence_links(product_cards, evidence)
    return product_cards, evidence


def validate_product_card_evidence_links(
    product_cards: list[ProductCard],
    evidence: list[SourceEvidence],
) -> None:
    evidence_ids = {item.id for item in evidence}
    for card in product_cards:
        if not card.evidence_ids:
            raise ValueError(f"{card.id} must reference at least one evidence item")
        missing = sorted(set(card.evidence_ids) - evidence_ids)
        if missing:
            raise ValueError(
                f"{card.id} references missing evidence_ids: " + ", ".join(missing)
            )


def build_claims(
    task_id: str,
    product_cards: list[ProductCard],
    evidence: list[SourceEvidence],
) -> list[AnalysisClaim]:
    competitors = [card.name for card in product_cards]
    evidence_by_id = {item.id: item for item in evidence}

    claim_specs = [
        {
            "id": "cl_online_education_positioning",
            "dimension": EvidenceDimension.POSITIONING,
            "claim_text": (
                "The three competitors have distinct product positions: ClassIn "
                "is a client-based online live teaching product, Tencent Cloud "
                "LCIC is a low-code education real-time interaction product, and "
                "BigBlueButton is an open-source self-hosted virtual classroom."
            ),
            "evidence_ids": [
                "ev_classin_client_positioning",
                "ev_tencent_lcic_low_code",
                "ev_bbb_open_source_self_hosted",
            ],
        },
        {
            "id": "cl_online_education_feature_baseline",
            "dimension": EvidenceDimension.FEATURE,
            "claim_text": (
                "All three products cover real-time classroom interaction basics, "
                "including audio/video interaction, whiteboard or shared teaching "
                "surfaces, course content support, and collaboration tools."
            ),
            "evidence_ids": [
                "ev_classin_classroom_toolkit",
                "ev_classin_courseware_support",
                "ev_tencent_core_features",
                "ev_tencent_courseware_recording",
                "ev_bbb_feature_set",
                "ev_bbb_collaboration_tools",
            ],
        },
        {
            "id": "cl_online_education_pricing_model",
            "dimension": EvidenceDimension.PRICING,
            "claim_text": (
                "The commercial models differ: ClassIn uses edition-based annual "
                "packages, Tencent Cloud LCIC uses monthly cloud package tiers "
                "with usage quotas, and BigBlueButton's captured evidence points "
                "to open-source self-hosting rather than a packaged SaaS price."
            ),
            "evidence_ids": [
                "ev_classin_basic_price_capacity",
                "ev_classin_business_api",
                "ev_tencent_pricing_tiers",
                "ev_tencent_pricing_capacity_api",
                "ev_bbb_open_source_self_hosted",
            ],
        },
        {
            "id": "cl_online_education_ecosystem",
            "dimension": EvidenceDimension.ECOSYSTEM,
            "claim_text": (
                "The ecosystem routes are different: ClassIn extends into AI LMS "
                "and hardware, Tencent Cloud emphasizes Open API and multi-platform "
                "client integration, and BigBlueButton can connect with LMS systems "
                "such as Moodle and Canvas according to the captured third-party evidence."
            ),
            "evidence_ids": [
                "ev_classin_ai_lms",
                "ev_classin_hardware_ecosystem",
                "ev_tencent_cross_platform",
                "ev_tencent_open_api",
                "ev_bbb_lms_integration",
            ],
        },
        {
            "id": "cl_online_education_risk_profile",
            "dimension": EvidenceDimension.RISK,
            "claim_text": (
                "The risk profile is not uniform: BigBlueButton implies self-hosting "
                "and operations responsibility, Tencent Cloud introduces a cloud/API "
                "integration dependency with built-in safety controls, and ClassIn "
                "ties stronger API capability to paid or premium tiers in the captured evidence."
            ),
            "evidence_ids": [
                "ev_bbb_deployment_risk",
                "ev_tencent_open_api",
                "ev_tencent_moderation_ai_noise",
                "ev_classin_business_api",
                "ev_classin_premium_prepaid_api",
            ],
        },
    ]

    claims = [
        build_claim_from_spec(task_id, competitors, evidence_by_id, spec)
        for spec in claim_specs
    ]
    validate_claim_links(claims, evidence)
    return claims


def build_claim_from_spec(
    task_id: str,
    competitors: list[str],
    evidence_by_id: dict[str, SourceEvidence],
    spec: dict,
) -> AnalysisClaim:
    evidence_ids = list(spec["evidence_ids"])
    if not evidence_ids:
        raise ValueError(f"{spec['id']} must bind at least one evidence item")

    missing = sorted(set(evidence_ids) - set(evidence_by_id))
    if missing:
        raise ValueError(
            f"{spec['id']} references missing evidence_ids: " + ", ".join(missing)
        )

    evidence_items = [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
    confidence = round(
        sum(item.confidence for item in evidence_items) / len(evidence_items),
        2,
    )

    return AnalysisClaim(
        id=spec["id"],
        task_id=task_id,
        dimension=spec["dimension"],
        claim_text=spec["claim_text"],
        competitors=competitors,
        evidence_ids=evidence_ids,
        confidence=confidence,
        produced_by_agent_run_id=PRODUCED_BY_AGENT_RUN_ID,
        metadata={
            "builder": "build_claims_demo.py",
            "rule": "fixed_snapshot_claims_with_required_evidence_ids",
        },
    )


def validate_claim_links(
    claims: list[AnalysisClaim],
    evidence: list[SourceEvidence],
) -> None:
    evidence_ids = {item.id for item in evidence}
    dimensions = {claim.dimension for claim in claims}
    required_dimensions = {
        "positioning",
        "feature",
        "pricing",
        "ecosystem",
        "risk",
    }
    missing_dimensions = sorted(required_dimensions - dimensions)
    if missing_dimensions:
        raise ValueError(
            "Missing required claim dimensions: " + ", ".join(missing_dimensions)
        )

    for claim in claims:
        if not claim.evidence_ids:
            raise ValueError(f"{claim.id} is an empty-evidence claim")
        missing = sorted(set(claim.evidence_ids) - evidence_ids)
        if missing:
            raise ValueError(
                f"{claim.id} references missing evidence_ids: " + ", ".join(missing)
            )


def summarize_by_dimension(claims: list[AnalysisClaim]) -> str:
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim.dimension] = counts.get(claim.dimension, 0) + 1
    return ", ".join(f"{dimension}={count}" for dimension, count in counts.items())


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    product_cards, evidence = load_product_cards_and_evidence(store, args.task_id)
    claims = build_claims(args.task_id, product_cards, evidence)
    store.save_many(args.task_id, "claims", claims)

    print("AnalysisClaim demo passed")
    print(f"task_id={args.task_id}")
    print(f"artifact_dir={store.root_dir / args.task_id}")
    print(f"claims={len(claims)} ({summarize_by_dimension(claims)})")


if __name__ == "__main__":
    main()
