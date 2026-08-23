from __future__ import annotations

from pydantic import ValidationError

from app.schemas import (
    AgentRole,
    TaskBoard,
    TaskPriority,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from build_product_cards_demo import DEFAULT_TASK_ID


FIXED_TASKBOARD_STEPS = [
    {
        "id": "tb_collect_sources",
        "task_key": "collect_sources",
        "task_type": TaskType.COLLECT_SOURCES,
        "target_agent_role": AgentRole.COLLECTOR,
        "depends_on": [],
        "input_refs": ["snapshot:online_education"],
        "output_refs": ["sources", "evidence"],
    },
    {
        "id": "tb_build_product_cards",
        "task_key": "build_product_cards",
        "task_type": TaskType.BUILD_PRODUCT_CARDS,
        "target_agent_role": AgentRole.EXTRACTOR,
        "depends_on": ["collect_sources"],
        "input_refs": ["sources", "evidence"],
        "output_refs": ["product_cards"],
    },
    {
        "id": "tb_build_claims",
        "task_key": "build_claims",
        "task_type": TaskType.BUILD_CLAIMS,
        "target_agent_role": AgentRole.ANALYST,
        "depends_on": ["build_product_cards"],
        "input_refs": ["product_cards", "evidence"],
        "output_refs": ["claims"],
    },
    {
        "id": "tb_check_citations",
        "task_key": "check_citations",
        "task_type": TaskType.CHECK_CITATIONS,
        "target_agent_role": AgentRole.CITATION,
        "depends_on": ["build_claims"],
        "input_refs": ["sources", "evidence", "claims"],
        "output_refs": ["citation_checks", "claims"],
    },
    {
        "id": "tb_build_report",
        "task_key": "build_report",
        "task_type": TaskType.BUILD_REPORT,
        "target_agent_role": AgentRole.WRITER,
        "depends_on": ["check_citations"],
        "input_refs": ["product_cards", "claims", "citation_checks"],
        "output_refs": ["reports"],
    },
    {
        "id": "tb_review_report",
        "task_key": "review_report",
        "task_type": TaskType.REVIEW_REPORT,
        "target_agent_role": AgentRole.REVIEWER,
        "depends_on": ["build_report"],
        "input_refs": ["reports", "claims", "citation_checks", "product_cards"],
        "output_refs": ["review_feedback"],
    },
]


def build_snapshot_task_board(task_id: str = DEFAULT_TASK_ID) -> TaskBoard:
    records = [
        TaskRecord(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            priority=TaskPriority.HIGH if index == 0 else TaskPriority.MEDIUM,
            reason="M8 schema smoke test for snapshot workflow task mapping.",
            **step,
        )
        for index, step in enumerate(FIXED_TASKBOARD_STEPS)
    ]
    return TaskBoard(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        tasks=records,
        max_review_rounds=3,
        current_review_round=0,
        metadata={
            "workflow_shape": "fixed_snapshot_steps_as_task_board",
            "generated_by": "check_taskboard_schema.py",
        },
    )


def assert_invalid_dependency_is_rejected(task_id: str = DEFAULT_TASK_ID) -> None:
    bad_task = TaskRecord(
        id="tb_bad_dependency",
        task_id=task_id,
        task_key="bad_dependency",
        task_type=TaskType.ANALYZE_DIMENSION,
        target_agent_role=AgentRole.ANALYST,
        depends_on=["missing_task"],
        reason="This task is intentionally invalid for schema guard testing.",
    )
    try:
        TaskBoard(task_id=task_id, tasks=[bad_task])
    except ValidationError:
        return
    raise AssertionError("TaskBoard accepted a missing dependency")


def assert_cycle_is_rejected(task_id: str = DEFAULT_TASK_ID) -> None:
    first = TaskRecord(
        id="tb_cycle_a",
        task_id=task_id,
        task_key="cycle_a",
        task_type=TaskType.ANALYZE_DIMENSION,
        target_agent_role=AgentRole.ANALYST,
        depends_on=["cycle_b"],
    )
    second = TaskRecord(
        id="tb_cycle_b",
        task_id=task_id,
        task_key="cycle_b",
        task_type=TaskType.SUPPLEMENT_ANALYSIS,
        target_agent_role=AgentRole.ANALYST,
        depends_on=["cycle_a"],
    )
    try:
        TaskBoard(task_id=task_id, tasks=[first, second])
    except ValidationError:
        return
    raise AssertionError("TaskBoard accepted a dependency cycle")


def main() -> None:
    board = build_snapshot_task_board()
    payload = board.model_dump(mode="json")
    roundtrip = TaskBoard(**payload)
    assert len(roundtrip.tasks) == len(FIXED_TASKBOARD_STEPS)
    assert [task.task_key for task in roundtrip.tasks] == [
        step["task_key"] for step in FIXED_TASKBOARD_STEPS
    ]
    assert_invalid_dependency_is_rejected()
    assert_cycle_is_rejected()
    print("PASS")
    print(f"task_board_status={roundtrip.status}")
    print(f"task_records_count={len(roundtrip.tasks)}")
    print("dependency_guard=ok")
    print("cycle_guard=ok")


if __name__ == "__main__":
    main()
