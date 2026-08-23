from __future__ import annotations

import argparse
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    CitationStatus,
    CompetitiveReport,
    IssueSeverity,
    ProductCard,
    ReviewFeedback,
    ReviewIssue,
)
from build_product_cards_demo import DEFAULT_TASK_ID


REVIEWER_RUN_ID = "rule_milestone3_reviewer"

REQUIRED_RD_KEYWORDS = [
    "产品研发",
    "功能基线",
    "技术接入",
    "商业模式",
    "风险",
    "研发建议",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic ReviewFeedback checks over CompetitiveReport, "
            "AnalysisClaim, CitationCheck, and ProductCard artifacts."
        )
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def load_artifacts(
    store: ArtifactStore,
    task_id: str,
) -> tuple[CompetitiveReport, list[AnalysisClaim], list[CitationCheck], list[ProductCard]]:
    reports = [
        CompetitiveReport(**item)
        for item in store.load_many(task_id, "reports")
    ]
    claims = [AnalysisClaim(**item) for item in store.load_many(task_id, "claims")]
    citation_checks = [
        CitationCheck(**item)
        for item in store.load_many(task_id, "citation_checks")
    ]
    product_cards = [
        ProductCard(**item)
        for item in store.load_many(task_id, "product_cards")
    ]

    if not reports:
        raise ValueError(
            f"No reports found for task_id={task_id}; run build_report_demo.py first"
        )
    if not claims:
        raise ValueError(f"No claims found for task_id={task_id}")
    if not citation_checks:
        raise ValueError(f"No citation_checks found for task_id={task_id}")
    if not product_cards:
        raise ValueError(f"No product_cards found for task_id={task_id}")

    return reports[-1], claims, citation_checks, product_cards


def build_review_feedback(
    task_id: str,
    report: CompetitiveReport,
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    product_cards: list[ProductCard],
) -> ReviewFeedback:
    issues: list[ReviewIssue] = []
    claim_by_id = {claim.id: claim for claim in claims}
    checks_by_claim_id = {check.claim_id: check for check in citation_checks}

    if not report.markdown.strip():
        issues.append(
            make_issue(
                task_id,
                "issue_report_empty_markdown",
                IssueSeverity.HIGH,
                "report",
                report.id,
                "report.markdown is empty.",
            )
        )

    if not report.claim_ids:
        issues.append(
            make_issue(
                task_id,
                "issue_report_empty_claim_ids",
                IssueSeverity.HIGH,
                "report",
                report.id,
                "report.claim_ids is empty.",
            )
        )

    missing_report_claims = sorted(
        claim_id for claim_id in report.claim_ids if claim_id not in claim_by_id
    )
    if missing_report_claims:
        issues.append(
            make_issue(
                task_id,
                "issue_report_missing_claim_refs",
                IssueSeverity.HIGH,
                "report",
                report.id,
                "report.claim_ids contains ids not found in claims: "
                + ", ".join(missing_report_claims),
            )
        )

    for claim_id in report.claim_ids:
        check = checks_by_claim_id.get(claim_id)
        if check is None:
            issues.append(
                make_issue(
                    task_id,
                    f"issue_{claim_id}_missing_citation_check",
                    IssueSeverity.HIGH,
                    "claim",
                    claim_id,
                    "Report claim has no CitationCheck artifact.",
                )
            )
            continue

        if check.status in {
            CitationStatus.MISSING_EVIDENCE,
            CitationStatus.INVALID_EVIDENCE,
        }:
            issues.append(
                make_issue(
                    task_id,
                    f"issue_{claim_id}_invalid_citation",
                    IssueSeverity.HIGH,
                    "claim",
                    claim_id,
                    f"Claim citation status is {check.status}: {check.message}",
                )
            )
        elif check.status == CitationStatus.WEAK:
            issues.append(
                make_issue(
                    task_id,
                    f"issue_{claim_id}_weak_citation",
                    IssueSeverity.MEDIUM,
                    "claim",
                    claim_id,
                    f"Claim has weak citation support: {check.message}",
                )
            )

    missing_keywords = [
        keyword for keyword in REQUIRED_RD_KEYWORDS if keyword not in report.markdown
    ]
    if missing_keywords:
        issues.append(
            make_issue(
                task_id,
                "issue_report_missing_rd_keywords",
                IssueSeverity.MEDIUM,
                "report",
                report.id,
                "Report is missing product R&D perspective keywords: "
                + ", ".join(missing_keywords),
            )
        )

    if len(product_cards) < 3:
        issues.append(
            make_issue(
                task_id,
                "issue_insufficient_product_cards",
                IssueSeverity.HIGH,
                "task",
                task_id,
                f"Expected at least 3 ProductCard artifacts, found {len(product_cards)}.",
            )
        )

    overall_score = calculate_score(issues)
    approved = not any(issue.severity == "high" for issue in issues)
    suggestions = build_suggestions(issues)

    return ReviewFeedback(
        id="review_online_education_report",
        task_id=task_id,
        reviewer_run_id=REVIEWER_RUN_ID,
        overall_score=overall_score,
        approved=approved,
        issues=issues,
        suggestions=suggestions,
        metadata={
            "reviewer": "run_review_demo.py",
            "rule": "deterministic_report_claim_citation_product_card_checks",
        },
    )


def make_issue(
    task_id: str,
    issue_id: str,
    severity: IssueSeverity,
    target_type: str,
    target_id: str,
    message: str,
) -> ReviewIssue:
    return ReviewIssue(
        id=issue_id,
        task_id=task_id,
        severity=severity,
        target_type=target_type,
        target_id=target_id,
        message=message,
    )


def calculate_score(issues: list[ReviewIssue]) -> float:
    score = 10.0
    for issue in issues:
        if issue.severity == "high":
            score -= 2.0
        elif issue.severity == "medium":
            score -= 1.0
        else:
            score -= 0.5
    return max(round(score, 1), 0.0)


def build_suggestions(issues: list[ReviewIssue]) -> list[str]:
    suggestions: list[str] = []
    if any(issue.severity == "high" for issue in issues):
        suggestions.append(
            "Fix high severity evidence or report linkage issues before using the report."
        )
    if any(issue.severity == "medium" for issue in issues):
        suggestions.append(
            "Review medium severity issues, especially weak citation support, before presentation."
        )
    if not suggestions:
        suggestions.append("No blocking review issues found in the local artifact chain.")
    return suggestions


def summarize_issues(issues: list[ReviewIssue]) -> str:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    if not counts:
        return "none"
    return ", ".join(f"{severity}={count}" for severity, count in counts.items())


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    report, claims, citation_checks, product_cards = load_artifacts(
        store,
        args.task_id,
    )
    review = build_review_feedback(
        args.task_id,
        report,
        claims,
        citation_checks,
        product_cards,
    )
    store.save_many(args.task_id, "review_feedback", [review])

    print("ReviewFeedback demo passed")
    print(f"task_id={args.task_id}")
    print(f"artifact_dir={store.root_dir / args.task_id}")
    print(
        "review_feedback=1 "
        f"approved={review.approved} "
        f"score={review.overall_score} "
        f"issues={summarize_issues(review.issues)}"
    )


if __name__ == "__main__":
    main()
