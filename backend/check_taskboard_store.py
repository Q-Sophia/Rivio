from __future__ import annotations

from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.schemas import AgentRole, TaskPriority, TaskRecord, TaskStatus, TaskType
from app.workflow.taskboard import (
    TaskBoardStore,
    status_value,
    build_fixed_snapshot_task_board,
    build_fixed_snapshot_task_records,
)
from build_product_cards_demo import DEFAULT_TASK_ID


def assert_file_exists(path: Path) -> None:
    if not path.exists():
        raise AssertionError(f"expected file to exist: {path}")


def test_save_and_load_board(root: Path) -> None:
    store = TaskBoardStore(ArtifactStore(root_dir=root))
    board = build_fixed_snapshot_task_board(DEFAULT_TASK_ID, completed=False)
    saved = store.save_board(board)
    loaded = store.require_board(DEFAULT_TASK_ID)
    records = store.load_records(DEFAULT_TASK_ID)

    assert saved.task_id == DEFAULT_TASK_ID
    assert loaded.task_id == DEFAULT_TASK_ID
    assert len(loaded.tasks) == 6
    assert len(records) == 6
    assert [task.task_key for task in loaded.tasks] == [
        "collect_sources",
        "build_product_cards",
        "build_claims",
        "check_citations",
        "build_report",
        "review_report",
    ]
    assert_file_exists(root / DEFAULT_TASK_ID / "task_board.json")
    assert_file_exists(root / DEFAULT_TASK_ID / "task_records.json")


def test_mark_ready_and_status_update(root: Path) -> None:
    store = TaskBoardStore(ArtifactStore(root_dir=root))
    board = build_fixed_snapshot_task_board(DEFAULT_TASK_ID, completed=False)
    store.save_board(board)

    ready = store.ready_records(DEFAULT_TASK_ID)
    assert [record.task_key for record in ready] == ["collect_sources"]

    board = store.update_status(
        DEFAULT_TASK_ID,
        "collect_sources",
        TaskStatus.COMPLETED,
        output_refs=["sources", "evidence"],
    )
    assert status_value(board.status) == TaskStatus.PENDING.value
    ready = store.ready_records(DEFAULT_TASK_ID)
    assert [record.task_key for record in ready] == ["build_product_cards"]

    board = store.update_status(DEFAULT_TASK_ID, "build_product_cards", TaskStatus.RUNNING)
    running = store.get_record(DEFAULT_TASK_ID, "build_product_cards")
    assert running is not None
    assert running.started_at is not None
    assert status_value(board.status) == TaskStatus.RUNNING.value

    board = store.update_status(
        DEFAULT_TASK_ID,
        "build_product_cards",
        TaskStatus.FAILED,
        error="schema mismatch",
    )
    failed = store.get_record(DEFAULT_TASK_ID, "build_product_cards")
    assert failed is not None
    assert failed.completed_at is not None
    assert failed.error == "schema mismatch"
    assert status_value(board.status) == TaskStatus.FAILED.value


def test_upsert_and_dependency_edges(root: Path) -> None:
    store = TaskBoardStore(ArtifactStore(root_dir=root))
    records = build_fixed_snapshot_task_records(DEFAULT_TASK_ID, completed=True)
    store.create_board(
        task_id=DEFAULT_TASK_ID,
        records=records,
        status=TaskStatus.COMPLETED,
        metadata={"test": "upsert"},
    )

    supplement = TaskRecord(
        id="tb_supplement_collection_pricing",
        task_id=DEFAULT_TASK_ID,
        task_key="supplement_collection_pricing",
        parent_task_id="review_report",
        task_type=TaskType.SUPPLEMENT_COLLECTION,
        target_agent_role=AgentRole.COLLECTOR,
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH,
        depends_on=["review_report"],
        input_refs=["review_feedback"],
        output_refs=["sources", "evidence"],
        reason="Reviewer found weak pricing citation.",
    )
    board = store.upsert_record(DEFAULT_TASK_ID, supplement)
    assert len(board.tasks) == 7
    loaded = store.require_board(DEFAULT_TASK_ID)
    assert loaded.tasks[-1].task_key == "supplement_collection_pricing"

    ready = store.ready_records(DEFAULT_TASK_ID)
    assert [record.task_key for record in ready] == ["supplement_collection_pricing"]

    edges = store.dependency_edges(DEFAULT_TASK_ID)
    assert {"from": "review_report", "to": "supplement_collection_pricing"} in edges


def main() -> None:
    root = Path("app/data/taskboard_store_check")
    root.mkdir(parents=True, exist_ok=True)
    test_save_and_load_board(root)
    test_mark_ready_and_status_update(root)
    test_upsert_and_dependency_edges(root)
    print("PASS")
    print(f"task_id={DEFAULT_TASK_ID}")
    print("save_load=ok")
    print("status_update=ok")
    print("ready_records=ok")
    print("upsert=ok")
    print("dependency_edges=ok")


if __name__ == "__main__":
    main()
