from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.schemas import CitationCheck, QualityGateDecision, ReviewFeedback, TaskRecord
from app.workflow.quality_gate import (
    FEEDBACK_TASKS_ARTIFACT,
    QUALITY_GATES_ARTIFACT,
    WARNING_CITATION_STATUSES,
)
from app.workflow.taskboard import TASK_RECORDS_ARTIFACT
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate QualityGateDecision and feedback task artifacts."
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

    items: list[T] = []
    for index, item in enumerate(raw):
        try:
            items.append(model(**item))
        except ValidationError as exc:
            errors.append(
                f"{artifact_type}.json[{index}] failed schema validation: {exc}"
            )
    return items


def status_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def validate_quality_gate_artifacts(store: ArtifactStore, task_id: str) -> list[str]:
    errors: list[str] = []
    gates = load_typed_artifact(
        store,
        task_id,
        QUALITY_GATES_ARTIFACT,
        QualityGateDecision,
        errors,
    )
    feedback_tasks = load_typed_artifact(
        store,
        task_id,
        FEEDBACK_TASKS_ARTIFACT,
        TaskRecord,
        errors,
    )
    task_records = load_typed_artifact(
        store,
        task_id,
        TASK_RECORDS_ARTIFACT,
        TaskRecord,
        errors,
    )
    citation_checks = load_typed_artifact(
        store,
        task_id,
        "citation_checks",
        CitationCheck,
        errors,
    )
    reviews = load_typed_artifact(
        store,
        task_id,
        "review_feedback",
        ReviewFeedback,
        errors,
    )

    if not gates:
        errors.append("quality_gates.json must contain at least one QualityGateDecision")
        return errors

    gate = gates[-1]
    validate_gate(gate, feedback_tasks, task_records, citation_checks, reviews, errors)
    validate_feedback_tasks(feedback_tasks, task_records, errors)
    return errors


def validate_gate(
    gate: QualityGateDecision,
    feedback_tasks: list[TaskRecord],
    task_records: list[TaskRecord],
    citation_checks: list[CitationCheck],
    reviews: list[ReviewFeedback],
    errors: list[str],
) -> None:
    if gate.status not in {"passed", "passed_with_warnings", "blocked", "failed"}:
        errors.append(f"QualityGateDecision status is invalid: {gate.status}")
    if not gate.gate_name:
        errors.append("QualityGateDecision.gate_name is empty")
    if not gate.citation_check_ids:
        errors.append("QualityGateDecision.citation_check_ids is empty")
    if not gate.message:
        errors.append("QualityGateDecision.message is empty")

    citation_check_ids = {check.id for check in citation_checks}
    for check_id in gate.citation_check_ids:
        if check_id not in citation_check_ids:
            errors.append(
                f"QualityGateDecision references missing citation_check_id={check_id}"
            )

    task_record_ids = {task.id for task in task_records}
    feedback_task_ids = {task.id for task in feedback_tasks}
    for task_id in gate.created_task_ids:
        if task_id not in task_record_ids:
            errors.append(
                f"QualityGateDecision created_task_id={task_id} not found in task_records"
            )
        if task_id not in feedback_task_ids:
            errors.append(
                f"QualityGateDecision created_task_id={task_id} not found in feedback_tasks"
            )

    weak_checks = [
        check
        for check in citation_checks
        if status_value(check.status) in WARNING_CITATION_STATUSES
    ]
    if weak_checks and gate.status != "passed_with_warnings":
        errors.append(
            "Weak citations exist, so QualityGateDecision.status should be passed_with_warnings"
        )
    if weak_checks and not gate.created_task_ids:
        errors.append("Weak citations exist, but no feedback tasks were created")

    if reviews and reviews[-1].issues:
        issue_ids = {issue.id for issue in reviews[-1].issues}
        missing_issue_ids = [
            issue_id for issue_id in gate.issue_ids if issue_id not in issue_ids
        ]
        if missing_issue_ids:
            errors.append(
                "QualityGateDecision references review issues not in latest review: "
                + ", ".join(missing_issue_ids)
            )


def validate_feedback_tasks(
    feedback_tasks: list[TaskRecord],
    task_records: list[TaskRecord],
    errors: list[str],
) -> None:
    task_record_ids = {task.id for task in task_records}
    for task in feedback_tasks:
        if task.id not in task_record_ids:
            errors.append(f"Feedback task {task.id} not found in task_records")
        if not task.parent_task_id:
            errors.append(f"Feedback task {task.task_key} is missing parent_task_id")
        if not task.reason:
            errors.append(f"Feedback task {task.task_key} is missing reason")
        if not task.input_refs:
            errors.append(f"Feedback task {task.task_key} has empty input_refs")
        if not task.output_refs:
            errors.append(f"Feedback task {task.task_key} has empty output_refs")
        if status_value(task.status) == "skipped" and not task.completed_at:
            errors.append(f"Skipped feedback task {task.task_key} is missing completed_at")


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
    errors = validate_quality_gate_artifacts(store, args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
