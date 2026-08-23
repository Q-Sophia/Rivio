from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

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
from build_claims_demo import (
    build_claims,
    load_product_cards_and_evidence,
)
from build_product_cards_demo import (
    DEFAULT_TASK_ID,
    build_product_cards,
    load_sources_and_evidence,
)
from build_report_demo import (
    build_report,
    load_artifacts as load_report_inputs,
)
from check_snapshot import DEFAULT_SNAPSHOT_ID, run_snapshot_check
from run_citation_check_demo import (
    load_artifacts as load_citation_inputs,
    run_citation_checks,
)
from run_review_demo import (
    build_review_feedback,
    load_artifacts as load_review_inputs,
)


T = TypeVar("T")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full deterministic local snapshot pipeline: collect, "
            "product cards, claims, citation checks, report, and review."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def run_step(name: str, fn: Callable[[], T]) -> T:
    print(f"[pipeline] start: {name}")
    try:
        result = fn()
    except Exception as exc:
        print(f"[pipeline] failed: {name}", file=sys.stderr)
        print(f"[pipeline] error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"[pipeline] done: {name}")
    return result


def run_pipeline(
    *,
    snapshot_id: str,
    task_id: str,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
) -> dict[str, object]:
    store_holder: dict[str, ArtifactStore] = {}

    def collect_snapshot() -> tuple[list[SourceDocument], list[SourceEvidence]]:
        _task, sources, evidence, store = run_snapshot_check(
            snapshot_id=snapshot_id,
            task_id=task_id,
            snapshot_root=snapshot_root,
            artifact_root=artifact_root,
        )
        store_holder["store"] = store
        return sources, evidence

    sources, evidence = run_step("collect snapshot", collect_snapshot)
    store = store_holder["store"]

    def build_product_card_artifacts() -> list[ProductCard]:
        saved_sources, saved_evidence = load_sources_and_evidence(store, task_id)
        product_cards = build_product_cards(task_id, saved_sources, saved_evidence)
        store.save_many(task_id, "product_cards", product_cards)
        return product_cards

    product_cards = run_step("build product_cards", build_product_card_artifacts)

    def build_claim_artifacts() -> list[AnalysisClaim]:
        saved_product_cards, saved_evidence = load_product_cards_and_evidence(
            store,
            task_id,
        )
        claims = build_claims(task_id, saved_product_cards, saved_evidence)
        store.save_many(task_id, "claims", claims)
        return claims

    claims = run_step("build claims", build_claim_artifacts)

    def run_citation_check_artifacts() -> list[CitationCheck]:
        saved_sources, saved_evidence, saved_claims = load_citation_inputs(
            store,
            task_id,
        )
        updated_claims, citation_checks = run_citation_checks(
            task_id,
            saved_sources,
            saved_evidence,
            saved_claims,
        )
        store.save_many(task_id, "claims", updated_claims)
        store.save_many(task_id, "citation_checks", citation_checks)
        return citation_checks

    citation_checks = run_step("citation check", run_citation_check_artifacts)

    def build_report_artifacts() -> list[CompetitiveReport]:
        report_inputs = load_report_inputs(store, task_id)
        report = build_report(task_id, *report_inputs)
        store.save_many(task_id, "reports", [report])
        return [report]

    reports = run_step("build report", build_report_artifacts)

    def build_review_artifacts() -> list[ReviewFeedback]:
        report, saved_claims, saved_citation_checks, saved_product_cards = (
            load_review_inputs(store, task_id)
        )
        review = build_review_feedback(
            task_id,
            report,
            saved_claims,
            saved_citation_checks,
            saved_product_cards,
        )
        store.save_many(task_id, "review_feedback", [review])
        return [review]

    review_feedback = run_step("review", build_review_artifacts)

    return build_summary(
        sources=sources,
        evidence=evidence,
        product_cards=product_cards,
        claims=claims,
        citation_checks=citation_checks,
        reports=reports,
        review_feedback=review_feedback,
    )


def build_summary(
    *,
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    product_cards: list[ProductCard],
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    reports: list[CompetitiveReport],
    review_feedback: list[ReviewFeedback],
) -> dict[str, object]:
    supported_count = sum(1 for check in citation_checks if check.status == "supported")
    weak_count = sum(1 for check in citation_checks if check.status == "weak")
    latest_review = review_feedback[-1] if review_feedback else None
    return {
        "sources_count": len(sources),
        "evidence_count": len(evidence),
        "product_cards_count": len(product_cards),
        "claims_count": len(claims),
        "citation_checks_count": len(citation_checks),
        "reports_count": len(reports),
        "review_feedback_count": len(review_feedback),
        "supported_count": supported_count,
        "weak_count": weak_count,
        "approved": latest_review.approved if latest_review else False,
        "review_score": latest_review.overall_score if latest_review else 0.0,
    }


def print_summary(summary: dict[str, object]) -> None:
    print("[pipeline] summary")
    for key in [
        "sources_count",
        "evidence_count",
        "product_cards_count",
        "claims_count",
        "citation_checks_count",
        "reports_count",
        "review_feedback_count",
        "supported_count",
        "weak_count",
        "approved",
        "review_score",
    ]:
        print(f"{key}={summary[key]}")


def main() -> None:
    args = parse_args()
    summary = run_pipeline(
        snapshot_id=args.snapshot_id,
        task_id=args.task_id,
        snapshot_root=args.snapshot_root,
        artifact_root=args.artifact_root,
    )
    print_summary(summary)


if __name__ == "__main__":
    main()
