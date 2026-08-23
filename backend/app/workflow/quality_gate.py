from __future__ import annotations

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    IssueSeverity,
    QualityGateDecision,
    ReviewFeedback,
    ReviewIssue,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from app.workflow.taskboard import TaskBoardStore, status_value


QUALITY_GATES_ARTIFACT = "quality_gates"
FEEDBACK_TASKS_ARTIFACT = "feedback_tasks"
BLOCKING_CITATION_STATUSES = {
    "unsupported",
    "missing_evidence",
    "invalid_evidence",
}
WARNING_CITATION_STATUSES = {"weak"}
FEEDBACK_TASK_TYPES = {
    TaskType.SUPPLEMENT_COLLECTION.value,
    TaskType.SUPPLEMENT_ANALYSIS.value,
    TaskType.REVISE_REPORT.value,
}


def run_review_quality_gate(
    *,
    task_id: str,
    store: ArtifactStore,
    task_board_store: TaskBoardStore,
) -> QualityGateDecision:
    """Create feedback tasks from citation/review issues.

    The current version is deterministic. It does not execute supplement tasks
    because WebCollector and LLM agents are not available yet. Non-blocking weak
    citations become skipped feedback tasks; blocking invalid/missing evidence
    becomes blocked tasks that prevent finalization in later milestones.
    """

    claims = [AnalysisClaim(**item) for item in store.load_many(task_id, "claims")]
    citation_checks = [
        CitationCheck(**item)
        for item in store.load_many(task_id, "citation_checks")
    ]
    reports = [
        CompetitiveReport(**item)
        for item in store.load_many(task_id, "reports")
    ]
    reviews = [
        ReviewFeedback(**item)
        for item in store.load_many(task_id, "review_feedback")
    ]
    if not citation_checks:
        raise ValueError(f"No citation_checks found for task_id={task_id}")
    if not reviews:
        raise ValueError(f"No review_feedback found for task_id={task_id}")

    latest_review = reviews[-1]
    issue_by_target = _issues_by_target(latest_review.issues)
    created_tasks = _build_feedback_tasks(
        task_id=task_id,
        claims=claims,
        citation_checks=citation_checks,
        review=latest_review,
        issue_by_target=issue_by_target,
    )
    board = task_board_store.require_board(task_id)
    existing_task_keys = {record.task_key for record in board.tasks}
    new_tasks = [
        task for task in created_tasks if task.task_key not in existing_task_keys
    ]
    for task in new_tasks:
        task_board_store.upsert_record(task_id, task)

    blocking = any(
        status_value(task.status) in {TaskStatus.BLOCKED.value, TaskStatus.REQUIRES_HUMAN.value}
        for task in created_tasks
    )
    weak_count = sum(
        1 for check in citation_checks if status_value(check.status) in WARNING_CITATION_STATUSES
    )
    blocking_count = sum(
        1
        for check in citation_checks
        if status_value(check.status) in BLOCKING_CITATION_STATUSES
    )
    if blocking:
        gate_status = "blocked"
        passed = False
        severity = IssueSeverity.HIGH
    elif weak_count:
        gate_status = "passed_with_warnings"
        passed = True
        severity = IssueSeverity.MEDIUM
    else:
        gate_status = "passed"
        passed = True
        severity = IssueSeverity.LOW

    decision = QualityGateDecision(
        id="gate_review_citation_feedback",
        task_id=task_id,
        gate_name="review_citation_feedback_gate",
        status=gate_status,
        passed=passed,
        blocking=blocking,
        severity=severity,
        issue_ids=[issue.id for issue in latest_review.issues],
        citation_check_ids=[check.id for check in citation_checks],
        created_task_ids=[task.id for task in created_tasks],
        message=_build_gate_message(
            latest_review=latest_review,
            weak_count=weak_count,
            blocking_count=blocking_count,
            feedback_task_count=len(created_tasks),
        ),
        metadata={
            "report_id": reports[-1].id if reports else "",
            "review_feedback_id": latest_review.id,
            "rule": "citation_and_review_issues_to_taskboard_feedback_tasks",
            "feedback_task_keys": [task.task_key for task in created_tasks],
        },
    )
    store.save_many(task_id, QUALITY_GATES_ARTIFACT, [decision])
    store.save_many(task_id, FEEDBACK_TASKS_ARTIFACT, created_tasks)
    return decision


def _build_feedback_tasks(
    *,
    task_id: str,
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    review: ReviewFeedback,
    issue_by_target: dict[str, list[ReviewIssue]],
) -> list[TaskRecord]:
    claim_ids = {claim.id for claim in claims}
    tasks: list[TaskRecord] = []
    now = utc_now()

    for check in citation_checks:
        status = status_value(check.status)
        related_issues = issue_by_target.get(check.claim_id, [])
        issue_ids = [issue.id for issue in related_issues]
        if status in WARNING_CITATION_STATUSES:
            tasks.append(
                TaskRecord(
                    id=f"tb_feedback_analyze_{check.claim_id}",
                    task_id=task_id,
                    task_key=f"supplement_analysis_{check.claim_id}",
                    parent_task_id="review_report",
                    task_type=TaskType.SUPPLEMENT_ANALYSIS,
                    target_agent_role=AgentRole.ANALYST,
                    status=TaskStatus.SKIPPED,
                    priority=TaskPriority.MEDIUM,
                    depends_on=["review_report"],
                    input_refs=["claims", "citation_checks", "evidence"],
                    output_refs=["claims"],
                    reason=(
                        "Weak citation is explained by ReviewFeedback. "
                        "LLM/WebCollector supplement analysis is deferred in rule-only mode."
                    ),
                    created_by_agent_run_id=review.reviewer_run_id,
                    completed_at=now,
                    metadata={
                        "feedback_reason": "weak_citation",
                        "claim_id": check.claim_id,
                        "citation_check_id": check.id,
                        "review_issue_ids": issue_ids,
                        "execution_policy": "skipped_until_llm_or_webcollector",
                    },
                )
            )
            continue

        if status not in BLOCKING_CITATION_STATUSES:
            continue

        blocked_by = issue_ids or [f"issue_{check.claim_id}_citation_blocking"]
        collect_task_key = f"supplement_collection_{check.claim_id}"
        tasks.append(
            TaskRecord(
                id=f"tb_feedback_collect_{check.claim_id}",
                task_id=task_id,
                task_key=collect_task_key,
                parent_task_id="review_report",
                task_type=TaskType.SUPPLEMENT_COLLECTION,
                target_agent_role=AgentRole.COLLECTOR,
                status=TaskStatus.BLOCKED,
                priority=TaskPriority.HIGH,
                depends_on=["review_report"],
                blocked_by=blocked_by,
                input_refs=["sources", "evidence", "claims", "citation_checks"],
                output_refs=["sources", "evidence"],
                reason=(
                    f"Claim {check.claim_id} has citation status={status}. "
                    "Supplement collection is required before final report."
                ),
                created_by_agent_run_id=review.reviewer_run_id,
                metadata={
                    "feedback_reason": status,
                    "claim_id": check.claim_id,
                    "citation_check_id": check.id,
                    "review_issue_ids": issue_ids,
                    "requires_tool": "webcollector_or_manual_source_input",
                },
            )
        )
        tasks.append(
            TaskRecord(
                id=f"tb_feedback_reanalyze_{check.claim_id}",
                task_id=task_id,
                task_key=f"supplement_analysis_{check.claim_id}",
                parent_task_id=collect_task_key,
                task_type=TaskType.SUPPLEMENT_ANALYSIS,
                target_agent_role=AgentRole.ANALYST,
                status=TaskStatus.PENDING,
                priority=TaskPriority.HIGH,
                depends_on=[collect_task_key],
                input_refs=["product_cards", "evidence", "claims"],
                output_refs=["claims"],
                reason=(
                    f"Re-analyze claim {check.claim_id} after supplement evidence exists."
                ),
                created_by_agent_run_id=review.reviewer_run_id,
                metadata={
                    "feedback_reason": status,
                    "claim_id": check.claim_id,
                    "citation_check_id": check.id,
                    "depends_on_claim_exists": check.claim_id in claim_ids,
                },
            )
        )

    if not review.approved:
        high_issue_ids = [
            issue.id for issue in review.issues if status_value(issue.severity) == IssueSeverity.HIGH.value
        ]
        tasks.append(
            TaskRecord(
                id="tb_feedback_revise_report",
                task_id=task_id,
                task_key="revise_report_after_review",
                parent_task_id="review_report",
                task_type=TaskType.REVISE_REPORT,
                target_agent_role=AgentRole.WRITER,
                status=TaskStatus.BLOCKED,
                priority=TaskPriority.HIGH,
                depends_on=["review_report"],
                blocked_by=high_issue_ids or [f"issue_{review.id}_not_approved"],
                input_refs=["reports", "claims", "citation_checks", "review_feedback"],
                output_refs=["reports"],
                reason="ReviewFeedback is not approved; report revision is required.",
                created_by_agent_run_id=review.reviewer_run_id,
                metadata={
                    "feedback_reason": "review_not_approved",
                    "review_feedback_id": review.id,
                    "high_issue_ids": high_issue_ids,
                },
            )
        )

    return _dedupe_tasks(tasks)


def _issues_by_target(issues: list[ReviewIssue]) -> dict[str, list[ReviewIssue]]:
    grouped: dict[str, list[ReviewIssue]] = {}
    for issue in issues:
        if not issue.target_id:
            continue
        grouped.setdefault(issue.target_id, []).append(issue)
    return grouped


def _dedupe_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
    seen: set[str] = set()
    deduped: list[TaskRecord] = []
    for task in tasks:
        if task.task_key in seen:
            continue
        seen.add(task.task_key)
        deduped.append(task)
    return deduped


def _build_gate_message(
    *,
    latest_review: ReviewFeedback,
    weak_count: int,
    blocking_count: int,
    feedback_task_count: int,
) -> str:
    return (
        f"Review approved={latest_review.approved}; "
        f"weak_citations={weak_count}; "
        f"blocking_citations={blocking_count}; "
        f"feedback_tasks={feedback_task_count}."
    )
