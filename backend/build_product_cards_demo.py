from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import ProductCard, SourceDocument, SourceEvidence


DEFAULT_TASK_ID = "snapshot_online_education_demo"

COMPANY_BY_COMPETITOR = {
    "ClassIn": "EEO",
    "腾讯云实时互动 / TRTC 教育方案": "Tencent Cloud",
    "BigBlueButton": "BigBlueButton project",
}

PRODUCT_ID_BY_COMPETITOR = {
    "ClassIn": "prod_classin",
    "腾讯云实时互动 / TRTC 教育方案": "prod_tencent_lcic",
    "BigBlueButton": "prod_bigbluebutton",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic ProductCard artifacts from saved local "
            "SourceDocument/SourceEvidence artifacts."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def load_sources_and_evidence(
    store: ArtifactStore,
    task_id: str,
) -> tuple[list[SourceDocument], list[SourceEvidence]]:
    sources = [SourceDocument(**item) for item in store.load_many(task_id, "sources")]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    if not sources:
        raise ValueError(f"No sources found for task_id={task_id}")
    if not evidence:
        raise ValueError(f"No evidence found for task_id={task_id}")
    validate_source_evidence_links(sources, evidence)
    return sources, evidence


def validate_source_evidence_links(
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> None:
    source_ids = {source.id for source in sources}
    missing_refs = sorted(
        {item.source_id for item in evidence if item.source_id not in source_ids}
    )
    if missing_refs:
        raise ValueError(
            "Evidence references missing SourceDocument.id values: "
            + ", ".join(missing_refs)
        )


def validate_product_card_links(
    product_cards: list[ProductCard],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> None:
    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    for card in product_cards:
        missing_sources = sorted(set(card.source_ids) - source_ids)
        missing_evidence = sorted(set(card.evidence_ids) - evidence_ids)
        if missing_sources:
            raise ValueError(
                f"{card.id} references missing source_ids: "
                + ", ".join(missing_sources)
            )
        if missing_evidence:
            raise ValueError(
                f"{card.id} references missing evidence_ids: "
                + ", ".join(missing_evidence)
            )
        if not card.evidence_ids:
            raise ValueError(f"{card.id} must reference at least one evidence item")


def build_product_cards(
    task_id: str,
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> list[ProductCard]:
    sources_by_competitor: dict[str, list[SourceDocument]] = defaultdict(list)
    evidence_by_competitor: dict[str, list[SourceEvidence]] = defaultdict(list)

    for source in sources:
        sources_by_competitor[source.competitor].append(source)
    for item in evidence:
        evidence_by_competitor[item.competitor].append(item)

    cards: list[ProductCard] = []
    for competitor in sorted(evidence_by_competitor):
        competitor_evidence = evidence_by_competitor[competitor]
        competitor_sources = sources_by_competitor.get(competitor, [])
        evidence_ids = [item.id for item in competitor_evidence]
        source_ids = sorted({item.source_id for item in competitor_evidence})
        source_ids.extend(
            source.id
            for source in competitor_sources
            if source.id not in source_ids
        )

        card = ProductCard(
            id=PRODUCT_ID_BY_COMPETITOR.get(
                competitor,
                "prod_" + competitor.lower().replace(" ", "_"),
            ),
            task_id=task_id,
            name=competitor,
            company=COMPANY_BY_COMPETITOR.get(competitor, ""),
            positioning=join_facts(competitor_evidence, "positioning")
            or fallback_positioning(competitor_evidence),
            target_users=select_facts(competitor_evidence, "customer", limit=4),
            pricing_summary=join_facts(competitor_evidence, "pricing")
            or fallback_pricing(competitor_evidence),
            core_features=select_facts(competitor_evidence, "feature", limit=6),
            strengths=build_strengths(competitor_evidence),
            weaknesses=build_weaknesses(competitor_evidence),
            source_ids=source_ids,
            evidence_ids=evidence_ids,
            confidence=average_confidence(competitor_evidence),
            metadata={
                "builder": "build_product_cards_demo.py",
                "rule": "aggregate_by_competitor_and_dimension",
            },
        )
        cards.append(card)

    validate_product_card_links(cards, sources, evidence)
    return cards


def select_facts(
    evidence: list[SourceEvidence],
    dimension: str,
    *,
    limit: int,
) -> list[str]:
    facts = [
        item.normalized_fact
        for item in evidence
        if item.dimension == dimension
    ]
    return facts[:limit]


def join_facts(evidence: list[SourceEvidence], dimension: str) -> str:
    facts = select_facts(evidence, dimension, limit=3)
    return " ".join(facts)


def fallback_positioning(evidence: list[SourceEvidence]) -> str:
    for dimension in ("ecosystem", "customer", "feature"):
        text = join_facts(evidence, dimension)
        if text:
            return text
    return ""


def fallback_pricing(evidence: list[SourceEvidence]) -> str:
    positioning_facts = select_facts(evidence, "positioning", limit=3)
    for fact in positioning_facts:
        if "open-source" in fact.lower() or "self-host" in fact.lower():
            return (
                "当前资料没有明确的付费价格；已有资料将该产品描述为开源、自托管方案。"
            )
    return "当前资料没有明确的价格信息。"


def build_strengths(evidence: list[SourceEvidence]) -> list[str]:
    strengths: list[str] = []
    feature_facts = select_facts(evidence, "feature", limit=2)
    ecosystem_facts = select_facts(evidence, "ecosystem", limit=2)
    customer_facts = select_facts(evidence, "customer", limit=1)

    if feature_facts:
        strengths.append(feature_facts[0])
    if len(feature_facts) > 1:
        strengths.append(feature_facts[1])
    if ecosystem_facts:
        strengths.append(ecosystem_facts[0])
    if customer_facts:
        strengths.append(customer_facts[0])
    return strengths[:4]


def build_weaknesses(evidence: list[SourceEvidence]) -> list[str]:
    risk_facts = select_facts(evidence, "risk", limit=3)
    if risk_facts:
        return risk_facts

    pricing_facts = select_facts(evidence, "pricing", limit=3)
    for fact in pricing_facts:
        if "API" in fact or "quoted" in fact or "customer manager" in fact:
            return [
                "Pricing and integration capabilities vary by package, so "
                "deployment assumptions should be checked against the selected tier."
            ]
    return []


def average_confidence(evidence: list[SourceEvidence]) -> float:
    if not evidence:
        return 0.0
    return round(sum(item.confidence for item in evidence) / len(evidence), 2)


def summarize_by_competitor(cards: list[ProductCard]) -> str:
    return ", ".join(
        f"{card.name}=sources:{len(card.source_ids)}/evidence:{len(card.evidence_ids)}"
        for card in cards
    )


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    sources, evidence = load_sources_and_evidence(store, args.task_id)
    product_cards = build_product_cards(args.task_id, sources, evidence)
    store.save_many(args.task_id, "product_cards", product_cards)

    print("ProductCard demo passed")
    print(f"task_id={args.task_id}")
    print(f"artifact_dir={store.root_dir / args.task_id}")
    print(f"product_cards={len(product_cards)} ({summarize_by_competitor(product_cards)})")


if __name__ == "__main__":
    main()
