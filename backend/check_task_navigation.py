from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisTask,
    ResearchLoopRun,
    ResearchLoopRunStatus,
    SchemaModel,
    TaskMode,
)


ROOT = Path(__file__).resolve().parents[1]
CHECK_ROOT = Path(__file__).resolve().parent / "app" / "data" / "tmp" / "task_navigation"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_task(task_id: str, title: str) -> AnalysisTask:
    return AnalysisTask(
        id=task_id,
        query=f"请调研 {title}",
        report_subject=title,
        mode=TaskMode.LIVE,
    )


def seed_navigation_tasks(store: ArtifactStore) -> list[str]:
    task_ids = [
        "task_nav_confirmed",
        "task_nav_queued",
        "task_nav_running",
        "task_nav_analyzed",
        "task_nav_reported",
    ]
    for task_id in task_ids:
        store.save_many(task_id, "analysis_tasks", [make_task(task_id, task_id.removeprefix("task_nav_").title())])

    for task_id, status, progress, current_stage in [
        ("task_nav_queued", ResearchLoopRunStatus.QUEUED, 5, "waiting_for_collector"),
        ("task_nav_running", ResearchLoopRunStatus.RUNNING, 42, "extractor"),
    ]:
        store.save_many(task_id, "research_plans", [SchemaModel(metadata={"fixture": True})])
        store.save_many(
            task_id,
            "research_loop_runs",
            [
                ResearchLoopRun(
                    task_id=task_id,
                    research_plan_id=f"plan_{task_id}",
                    status=status,
                    current_stage=current_stage,
                    progress_percent=progress,
                    message=f"{status} fixture",
                )
            ],
        )

    store.save_many(
        "task_nav_analyzed",
        "claims",
        [SchemaModel(metadata={"fixture": "analyzed"})],
    )
    store.save_many(
        "task_nav_reported",
        "reports",
        [SchemaModel(metadata={"fixture": "reported"})],
    )
    store.save_many(
        "task_nav_reported",
        "research_loop_runs",
        [
            ResearchLoopRun(
                task_id="task_nav_reported",
                research_plan_id="plan_task_nav_reported",
                status=ResearchLoopRunStatus.REQUIRES_HUMAN,
                current_stage="research_loop",
                progress_percent=70,
                message="historical upstream terminal status",
            )
        ],
    )
    return task_ids


def check_backend_navigation_contract() -> None:
    if CHECK_ROOT.exists():
        shutil.rmtree(CHECK_ROOT)
    store = ArtifactStore(CHECK_ROOT)
    task_ids = seed_navigation_tasks(store)
    (CHECK_ROOT / "not_an_analysis_task").mkdir()
    original_get_store = api_main.get_store
    api_main.get_store = lambda: store
    client = TestClient(api_main.app)
    try:
        before = {
            path.relative_to(CHECK_ROOT): path.stat().st_mtime_ns
            for path in CHECK_ROOT.rglob("*.json")
        }
        response = client.get("/api/analysis-tasks?limit=20")
        require(response.status_code == 200, f"task list failed: {response.text}")
        tasks = response.json()["tasks"]
        by_id = {item["task_id"]: item for item in tasks}
        require(set(task_ids) <= by_id.keys(), "task-centric list lost fixture tasks")
        require("not_an_analysis_task" not in by_id, "non-AnalysisTask directory entered navigation")
        require(by_id["task_nav_confirmed"]["status"] == "pending", "confirmed status changed")
        require(by_id["task_nav_queued"]["status"] == "queued", "queued status missing")
        require(by_id["task_nav_queued"]["progress_percent"] == 5, "queued progress missing")
        require(
            by_id["task_nav_queued"]["current_stage"] == "waiting_for_collector",
            "queued current stage missing",
        )
        require(by_id["task_nav_running"]["status"] == "running", "running status missing")
        require(by_id["task_nav_running"]["progress_percent"] == 42, "running progress missing")
        require(by_id["task_nav_analyzed"]["status"] == "analyzed", "analyzed status missing")
        require(by_id["task_nav_analyzed"]["stage"] == "analyzed", "analyzed stage missing")
        require(
            by_id["task_nav_reported"]["status"] == "reported",
            "downstream reported status was overwritten by an old ResearchLoop status",
        )
        require(by_id["task_nav_reported"]["stage"] == "reported", "reported stage missing")
        require(all(item["title"] and item["updated_at"] for item in tasks), "task title/update time missing")
        require(len(client.get("/api/analysis-tasks?limit=3").json()["tasks"]) == 3, "task list limit ignored")
        require(
            client.get("/api/analysis-tasks/task_nav_confirmed/workspace").status_code == 200,
            "valid URL task cannot load workspace",
        )
        require(
            client.get("/api/analysis-tasks/task_nav_missing/workspace").status_code == 404,
            "invalid URL task does not fail cleanly",
        )
        after = {
            path.relative_to(CHECK_ROOT): path.stat().st_mtime_ns
            for path in CHECK_ROOT.rglob("*.json")
        }
        require(before == after, "task navigation GET mutated artifacts")
    finally:
        api_main.get_store = original_get_store
        shutil.rmtree(CHECK_ROOT)


def function_source(frontend: str, start: str, end: str) -> str:
    start_index = frontend.index(start)
    return frontend[start_index:frontend.index(end, start_index)]


def check_frontend_navigation_contract() -> None:
    frontend = (ROOT / "frontend" / "src" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    require('const ACTIVE_TASK_STORAGE_KEY = "lastActiveTaskId"' in frontend, "lastActiveTaskId key missing")
    require('new URLSearchParams(window.location.search).get("task")' in frontend, "URL task parsing missing")
    candidates = function_source(frontend, "function taskRestoreCandidates", "function syncTaskUrl")
    require("[urlTaskId, storedTaskId]" in candidates, "URL task is not first restore candidate")
    restore = function_source(frontend, "async function restoreTaskContext", "function runOptionLabel")
    require("taskRestoreCandidates(urlTaskId, storedTaskId)" in restore, "restore candidates not used")
    require("loadTaskWorkspace(taskId" in restore, "restore does not validate workspace")
    require('historyMode: taskId === urlTaskId ? "none" : "replace"' in restore, "invalid URL fallback is not canonicalized")
    require("loadLegacyDashboard" not in restore, "task restore falls back to legacy Run")

    recent_tasks = function_source(frontend, "function renderRecentTasks", "async function loadRecentTasks")
    require("taskWorkspaceUrl(task.task_id)" in recent_tasks, "recent task lacks native task URL")
    require("loadTaskWorkspace" not in recent_tasks, "recent task click is intercepted by workspace JavaScript")
    require("researchPlan" not in recent_tasks and "researchLoop" not in recent_tasks, "task navigation can execute research")
    require("researchAnalysis" not in recent_tasks and "researchReporting" not in recent_tasks, "task navigation can execute LLM stages")
    refresh = function_source(frontend, "async function loadTask()", "function render()")
    require("restoreTaskContext()" in refresh, "refresh cannot recover task context")
    require("loadLegacyDashboard" not in refresh, "refresh still falls back to legacy Run")
    setup = frontend[frontend.index("function setup()"):]
    require('window.addEventListener("popstate"' in setup, "browser back/forward recovery missing")
    require("restoreTaskContext();" in setup, "startup task recovery missing")
    require("loadRuns({ selectDefaultLegacy: false })" in setup, "startup can auto-select legacy Run")
    require('id="recent-task-list"' in html, "recent task navigation DOM missing")
    require("data-active-task-id" in frontend, "recent task click target missing")
    require("taskWorkspaceUrl(task.task_id)" in frontend, "recent task lacks native URL fallback")
    require("handleRecentTaskClick" not in frontend, "native recent-task navigation is still intercepted")
    require("switchActiveTask" not in frontend, "obsolete JavaScript task switch remains active")


def main() -> None:
    check_backend_navigation_contract()
    check_frontend_navigation_contract()
    print("check_task_navigation: PASS")
    print("url_task_precedence=true")
    print("last_active_task_fallback=true")
    print("invalid_task_fails_without_legacy_fallback=true")
    print("refresh_restores_same_task=true")
    print("task_ab_switch_uses_native_task_url=true")
    print("popstate_recovery=true")
    print("queued_running_analyzed_reported_visible=true")
    print("navigation_get_is_read_only=true")
    print("real_external_calls=0")


if __name__ == "__main__":
    main()
