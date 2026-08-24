from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import call, patch

from app.harness.artifacts import (
    ArtifactStore,
    _WINDOWS_REPLACE_RETRY_DELAYS_SECONDS,
)
from app.schemas import SchemaModel


CHECK_ROOT = Path(__file__).resolve().parent / "app" / "data" / "checks" / "artifact_atomic_write"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def winerror_5(path: Path) -> PermissionError:
    error = PermissionError(13, "simulated transient Windows access denial", str(path))
    error.winerror = 5
    return error


def check_transient_winerror_5_then_success(root: Path) -> None:
    store = ArtifactStore(root / "transient")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(temporary: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise winerror_5(destination)
        return original_replace(temporary, destination)

    with (
        patch.object(Path, "replace", new=flaky_replace),
        patch("app.harness.artifacts.time.sleep") as sleep_mock,
    ):
        store.save_many(
            "task_transient",
            "task_records",
            [SchemaModel(metadata={"case": "transient"})],
        )

    require(attempts == 4, f"expected 4 replace attempts, got {attempts}")
    require(
        sleep_mock.call_args_list == [call(0.02), call(0.05), call(0.1)],
        f"unexpected retry delays: {sleep_mock.call_args_list}",
    )
    require(
        store.load_many("task_transient", "task_records")[0]["metadata"]["case"] == "transient",
        "artifact was not saved after a transient WinError 5",
    )
    require(
        not list((root / "transient" / "task_transient").glob("*.tmp")),
        "successful save left its temporary file behind",
    )


def check_persistent_winerror_5_fails(root: Path) -> None:
    store = ArtifactStore(root / "persistent")
    attempts = 0
    final_error = winerror_5(root / "persistent" / "task_persistent" / "research_loop_runs.json")

    def always_fails(_temporary: Path, _destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        raise final_error

    caught: PermissionError | None = None
    with (
        patch.object(Path, "replace", new=always_fails),
        patch("app.harness.artifacts.time.sleep") as sleep_mock,
    ):
        try:
            store.save_many(
                "task_persistent",
                "research_loop_runs",
                [SchemaModel(metadata={"case": "persistent"})],
            )
        except PermissionError as error:
            caught = error

    require(caught is final_error, "maximum retries did not re-raise the original PermissionError")
    require(
        attempts == len(_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS) + 1,
        f"retry was not bounded: attempts={attempts}",
    )
    require(
        sleep_mock.call_args_list
        == [call(delay) for delay in _WINDOWS_REPLACE_RETRY_DELAYS_SECONDS],
        f"unexpected bounded retry delays: {sleep_mock.call_args_list}",
    )
    task_dir = root / "persistent" / "task_persistent"
    require(not (task_dir / "research_loop_runs.json").exists(), "failed replace changed destination")
    require(len(list(task_dir.glob("*.tmp"))) == 1, "failed replace did not preserve diagnostic tmp")


def check_non_winerror_and_ordinary_save(root: Path) -> None:
    store = ArtifactStore(root / "ordinary")
    store.save_many(
        "task_ordinary",
        "task_records",
        [SchemaModel(metadata={"case": "ordinary"})],
    )
    require(
        store.load_many("task_ordinary", "task_records")[0]["metadata"]["case"] == "ordinary",
        "ordinary ArtifactStore save/load behavior changed",
    )

    attempts = 0
    non_windows_error = PermissionError(13, "non-Windows permission error")

    def non_windows_failure(_temporary: Path, _destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        raise non_windows_error

    with (
        patch.object(Path, "replace", new=non_windows_failure),
        patch("app.harness.artifacts.time.sleep") as sleep_mock,
    ):
        try:
            store.save_many(
                "task_non_windows_error",
                "task_records",
                [SchemaModel(metadata={"case": "non-windows-error"})],
            )
        except PermissionError as error:
            require(error is non_windows_error, "non-WinError 5 was replaced with another exception")
        else:
            raise AssertionError("non-WinError 5 did not propagate")

    require(attempts == 1, "non-WinError 5 was retried")
    sleep_mock.assert_not_called()


def main() -> None:
    if CHECK_ROOT.exists():
        shutil.rmtree(CHECK_ROOT)
    CHECK_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        root = CHECK_ROOT
        check_transient_winerror_5_then_success(root)
        check_persistent_winerror_5_fails(root)
        check_non_winerror_and_ordinary_save(root)
    finally:
        shutil.rmtree(CHECK_ROOT)

    print("check_artifact_atomic_write: PASS")
    print("transient_winerror_5_recovers=true")
    print("persistent_winerror_5_raises=true")
    print("retry_is_bounded=true")
    print("non_winerror_5_not_retried=true")
    print("ordinary_save_unchanged=true")
    print("real_external_calls=0")


if __name__ == "__main__":
    main()
