from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    CitationStatus,
    SourceDocument,
    SourceEvidence,
)
from build_product_cards_demo import DEFAULT_TASK_ID


CHECKED_BY_AGENT_RUN_ID = "rule_milestone2_citation_checker"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate AnalysisClaim evidence references and emit CitationCheck "
            "artifacts for the local snapshot chain."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def load_artifacts(
    store: ArtifactStore,
    task_id: str,
) -> tuple[list[SourceDocument], list[SourceEvidence], list[AnalysisClaim]]:
    sources = [SourceDocument(**item) for item in store.load_many(task_id, "sources")]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    claims = [AnalysisClaim(**item) for item in store.load_many(task_id, "claims")]

    if not sources:
        raise ValueError(f"No sources found for task_id={task_id}")
    if not evidence:
        raise ValueError(f"No evidence found for task_id={task_id}")
    if not claims:
        raise ValueError(
            f"No claims found for task_id={task_id}; run build_claims_demo.py first"
        )

    validate_source_evidence_links(sources, evidence)
    return sources, evidence, claims


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


def run_citation_checks(
    task_id: str,
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    claims: list[AnalysisClaim],
) -> tuple[list[AnalysisClaim], list[CitationCheck]]:
    source_by_id = {source.id: source for source in sources}
    evidence_by_id = {item.id: item for item in evidence}

    updated_claims: list[AnalysisClaim] = []
    checks: list[CitationCheck] = []
    for claim in claims:
        status, message = evaluate_claim_citation(
            claim,
            source_by_id,
            evidence_by_id,
        )
        updated_claim = claim.model_copy(update={"citation_status": status})
        updated_claims.append(updated_claim)
        checks.append(
            CitationCheck(
                id=f"cite_{claim.id}",
                task_id=task_id,
                claim_id=claim.id,
                evidence_ids=claim.evidence_ids,
                status=status,
                message=message,
                checked_by_agent_run_id=CHECKED_BY_AGENT_RUN_ID,
                metadata={
                    "checker": "run_citation_check_demo.py",
                    "rule": "evidence_ids_exist_and_source_ids_resolve",
                },
            )
        )

    return updated_claims, checks


def evaluate_claim_citation(
    claim: AnalysisClaim,
    source_by_id: dict[str, SourceDocument],
    evidence_by_id: dict[str, SourceEvidence],
) -> tuple[CitationStatus, str]:
    if not claim.evidence_ids:
        return (
            CitationStatus.MISSING_EVIDENCE,
            "Claim has no evidence_ids.",
        )

    missing_evidence = sorted(
        evidence_id for evidence_id in claim.evidence_ids if evidence_id not in evidence_by_id
    )
    if missing_evidence:
        return (
            CitationStatus.INVALID_EVIDENCE,
            "Claim references missing evidence_ids: " + ", ".join(missing_evidence),
        )

    evidence_items = [evidence_by_id[evidence_id] for evidence_id in claim.evidence_ids]
    missing_sources = sorted(
        {
            item.source_id
            for item in evidence_items
            if item.source_id not in source_by_id
        }
    )
    if missing_sources:
        return (
            CitationStatus.INVALID_EVIDENCE,
            "Claim evidence references missing source_ids: " + ", ".join(missing_sources),
        )

    weak_evidence = [
        item.id
        for item in evidence_items
        if item.confidence < 0.6
        or source_by_id[item.source_id].reliability_score < 0.6
    ]
    if weak_evidence:
        return (
            CitationStatus.WEAK,
            "All evidence_ids exist, but weak evidence/source reliability is present: "
            + ", ".join(weak_evidence),
        )

    return (
        CitationStatus.SUPPORTED,
        f"All {len(claim.evidence_ids)} evidence_ids exist and resolve to source documents.",
    )


def summarize_status(checks: list[CitationCheck]) -> str:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return ", ".join(f"{status}={count}" for status, count in counts.items())


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    sources, evidence, claims = load_artifacts(store, args.task_id)
    updated_claims, checks = run_citation_checks(
        args.task_id,
        sources,
        evidence,
        claims,
    )
    store.save_many(args.task_id, "claims", updated_claims)
    store.save_many(args.task_id, "citation_checks", checks)

    print("CitationCheck demo passed")
    print(f"task_id={args.task_id}")
    print(f"artifact_dir={store.root_dir / args.task_id}")
    print(f"citation_checks={len(checks)} ({summarize_status(checks)})")


if __name__ == "__main__":
    main()
