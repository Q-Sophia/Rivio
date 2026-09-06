from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.execution.research_agent_coordinator import (
    ResearchAgentCoordinator,
    ResearchAgentCoordinatorRun,
)


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def save_many(
        self,
        task_id: str,
        artifact_type: str,
        items: Iterable[Any],
    ) -> None:
        self.data[(task_id, artifact_type)] = [
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item)
            for item in items
        ]

    def append_many(
        self,
        task_id: str,
        artifact_type: str,
        items: Iterable[Any],
    ) -> None:
        values = self.load_many(task_id, artifact_type)
        values.extend(
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item)
            for item in items
        )
        self.data[(task_id, artifact_type)] = values

    def load_many(
        self,
        task_id: str,
        artifact_type: str,
    ) -> list[dict[str, Any]]:
        return list(self.data.get((task_id, artifact_type), []))


class RunningFuture:
    def cancel(self) -> bool:
        return False

    def done(self) -> bool:
        return False


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    task_id = "task_stop_research_agent"
    store = MemoryArtifactStore()
    coordinator = ResearchAgentCoordinator.__new__(ResearchAgentCoordinator)
    coordinator.store = store
    coordinator._lock = threading.RLock()
    coordinator._stop_requested = set()
    coordinator._futures = {task_id: RunningFuture()}

    running = ResearchAgentCoordinatorRun(
        id="researchagentcoord_stop",
        task_id=task_id,
        status="running",
        progress_percent=37,
        total_tasks=8,
        completed_tasks=3,
    )
    store.save_many(
        task_id,
        "research_agent_coordinator_runs",
        [running],
    )

    stopping = coordinator.request_stop(task_id)
    require(stopping.status == "stopping", "running task was not marked stopping")
    require(
        coordinator.get_events(task_id)[-1].event_type == "stop_requested",
        "stop_requested event missing",
    )

    stopped = coordinator._finish(
        running,
        reason="would_have_completed",
        message="normal completion",
    )
    require(stopped.status == "stopped", "stop did not win the completion race")
    require(stopped.result_status == "STOPPED", "stopped result status missing")
    require(stopped.progress_percent == 37, "stop incorrectly forced progress to 100")
    require(
        coordinator.get_events(task_id)[-1].event_type == "stopped",
        "stopped event missing",
    )

    original_get_coordinator = api_main.get_research_agent_coordinator
    api_main.get_research_agent_coordinator = lambda: coordinator
    try:
        response = TestClient(api_main.app).post(
            f"/api/analysis-tasks/{task_id}/research-agent/run/stop"
        )
        require(response.status_code == 200, "stop endpoint failed")
        require(response.json()["terminal"] is True, "stopped run is not terminal")
    finally:
        api_main.get_research_agent_coordinator = original_get_coordinator

    root = Path(__file__).resolve().parents[1]
    frontend = (root / "frontend" / "src" / "app.js").read_text(
        encoding="utf-8"
    )
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    require('id="stop-research-loop-btn"' in html, "stop button missing")
    require("stopResearchLoop" in frontend, "stop endpoint is not wired")
    require(
        'event.event_type !== "evidence_added"' in frontend,
        "evidence events still enter the research timeline",
    )
    layout_start = html.index('class="research-runtime-layout"')
    layout_end = html.index("</div>", html.index('id="evidence-library"'))
    require(
        layout_start < html.index('id="research-loop-runtime"') < layout_end
        and layout_start < html.index('id="evidence-library"') < layout_end,
        "research activity and evidence library are not in the same split layout",
    )

    print("check_research_agent_stop: PASS")
    print("cooperative_stop=true")
    print("evidence_timeline_filtered=true")
    print("evidence_sidebar_split_layout=true")


if __name__ == "__main__":
    main()
