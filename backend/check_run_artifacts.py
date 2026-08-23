from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    ProductCard,
    ReviewFeedback,
    SourceDocument,
    SourceEvidence,
)
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")

REQUIRED_ARTIFACTS = [
    "sources",
    "evidence",
    "product_cards",
    "claims",
    "citation_checks",
    "reports",
    "review_feedback",
]

PROBLEM_CITATION_STATUSES = {
    "weak",
    "missing_evidence",
    "invalid_evidence",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a completed local snapshot run without generating any "
            "business artifacts."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def artifact_path(store: ArtifactStore, task_id: str, artifact_type: str) -> Path:
    return store.root_dir / task_id / f"{artifact_type}.json"


def load_typed_artifact(
    store: ArtifactStore,
    task_id: str,
    artifact_type: str,
    model: type[T],
    errors: list[str],
) -> list[T]:
    path = artifact_path(store, task_id, artifact_type)
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
    if not raw:
        errors.append(f"{artifact_type}.json must not be empty")
        return []

    items: list[T] = []
    for index, item in enumerate(raw):
        try:
            items.append(model(**item))
        except ValidationError as exc:
            errors.append(
                f"{artifact_type}.json[{index}] failed schema validation: {exc}"
            )
    return items


def validate_run_artifacts(
    *,
    store: ArtifactStore,
    task_id: str,
) -> list[str]:
    errors: list[str] = []
    task_dir = store.root_dir / task_id
    if not task_dir.exists():
        errors.append(f"Task artifact directory does not exist: {task_dir}")

    for artifact_type in REQUIRED_ARTIFACTS:
        path = artifact_path(store, task_id, artifact_type)
        if not path.exists():
            errors.append(f"Missing required artifact: {path}")

    sources = load_typed_artifact(store, task_id, "sources", SourceDocument, errors)
    evidence = load_typed_artifact(store, task_id, "evidence", SourceEvidence, errors)
    product_cards = load_typed_artifact(
        store,
        task_id,
        "product_cards",
        ProductCard,
        errors,
    )
    claims = load_typed_artifact(store, task_id, "claims", AnalysisClaim, errors)
    citation_checks = load_typed_artifact(
        store,
        task_id,
        "citation_checks",
        CitationCheck,
        errors,
    )
    reports = load_typed_artifact(store, task_id, "reports", CompetitiveReport, errors)
    review_feedback = load_typed_artifact(
        store,
        task_id,
        "review_feedback",
        ReviewFeedback,
        errors,
    )

    validate_source_evidence_links(sources, evidence, errors)
    validate_product_card_links(product_cards, sources, evidence, errors)
    validate_claim_links(claims, evidence, errors)
    validate_citation_check_links(citation_checks, claims, evidence, errors)
    validate_report_links(reports, claims, errors)
    validate_review_explains_citation_issues(
        review_feedback,
        citation_checks,
        errors,
    )
    return errors


def validate_source_evidence_links(
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    errors: list[str],
) -> None:
    source_ids = {source.id for source in sources}
    for item in evidence:
        if item.source_id not in source_ids:
            errors.append(
                f"Evidence {item.id} references missing source_id={item.source_id}"
            )


def validate_product_card_links(
    product_cards: list[ProductCard],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    errors: list[str],
) -> None:
    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    for card in product_cards:
        if not card.source_ids:
            errors.append(f"ProductCard {card.id} has empty source_ids")
        if not card.evidence_ids:
            errors.append(f"ProductCard {card.id} has empty evidence_ids")
        for source_id in card.source_ids:
            if source_id not in source_ids:
                errors.append(
                    f"ProductCard {card.id} references missing source_id={source_id}"
                )
        for evidence_id in card.evidence_ids:
            if evidence_id not in evidence_ids:
                errors.append(
                    f"ProductCard {card.id} references missing evidence_id={evidence_id}"
                )


def validate_claim_links(
    claims: list[AnalysisClaim],
    evidence: list[SourceEvidence],
    errors: list[str],
) -> None:
    evidence_ids = {item.id for item in evidence}
    for claim in claims:
        if not claim.evidence_ids:
            errors.append(f"AnalysisClaim {claim.id} has empty evidence_ids")
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_ids:
                errors.append(
                    f"AnalysisClaim {claim.id} references missing evidence_id={evidence_id}"
                )


def validate_citation_check_links(
    citation_checks: list[CitationCheck],
    claims: list[AnalysisClaim],
    evidence: list[SourceEvidence],
    errors: list[str],
) -> None:
    claim_ids = {claim.id for claim in claims}
    evidence_ids = {item.id for item in evidence}
    for check in citation_checks:
        if check.claim_id not in claim_ids:
            errors.append(
                f"CitationCheck {check.id} references missing claim_id={check.claim_id}"
            )
        for evidence_id in check.evidence_ids:
            if evidence_id not in evidence_ids:
                errors.append(
                    f"CitationCheck {check.id} references missing evidence_id={evidence_id}"
                )


def validate_report_links(
    reports: list[CompetitiveReport],
    claims: list[AnalysisClaim],
    errors: list[str],
) -> None:
    claim_ids = {claim.id for claim in claims}
    for report in reports:
        if not report.markdown.strip():
            errors.append(f"CompetitiveReport {report.id} has empty markdown")
        if not report.claim_ids:
            errors.append(f"CompetitiveReport {report.id} has empty claim_ids")
        for claim_id in report.claim_ids:
            if claim_id not in claim_ids:
                errors.append(
                    f"CompetitiveReport {report.id} references missing claim_id={claim_id}"
                )
            if f"[{claim_id}]" not in report.markdown:
                errors.append(
                    f"CompetitiveReport {report.id} markdown missing [{claim_id}]"
                )


def validate_review_explains_citation_issues(
    review_feedback: list[ReviewFeedback],
    citation_checks: list[CitationCheck],
    errors: list[str],
) -> None:
    if not review_feedback:
        return

    latest_review = review_feedback[-1]
    issues = latest_review.issues
    for check in citation_checks:
        if check.status not in PROBLEM_CITATION_STATUSES:
            continue
        matching_issues = [
            issue
            for issue in issues
            if issue.target_type == "claim" and issue.target_id == check.claim_id
        ]
        if not matching_issues:
            errors.append(
                "ReviewFeedback does not explain citation issue "
                f"status={check.status} claim_id={check.claim_id}"
            )


def print_result(errors: list[str]) -> None:
    if errors:
        print("FAIL")
        print(f"errors={len(errors)}")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        return

    print("PASS")
    print("errors=0")


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    errors = validate_run_artifacts(store=store, task_id=args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
