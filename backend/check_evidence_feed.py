from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.execution.evidence_feed import build_evidence_feed
from app.execution.research_agent_coordinator import (
    ResearchAgentCoordinator,
    ResearchAgentCoordinatorRun,
)
from app.schemas import (
    AnalysisTask,
    CollectionAttempt,
    ResearchSourceCandidate,
    SourceDocument,
    SourceEvidence,
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
        existing = self.load_many(task_id, artifact_type)
        existing.extend(
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item)
            for item in items
        )
        self.data[(task_id, artifact_type)] = existing

    def load_many(
        self,
        task_id: str,
        artifact_type: str,
    ) -> list[dict[str, Any]]:
        return list(self.data.get((task_id, artifact_type), []))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    task_id = "task_evidence_feed"
    store = MemoryArtifactStore()
    store.save_many(
        task_id,
        "analysis_tasks",
        [
            AnalysisTask(
                id=task_id,
                query="分析豆包用户评价",
                competitors=["豆包"],
            )
        ],
    )
    candidates = [
        ResearchSourceCandidate(
            id="candidate_tavily",
            task_id=task_id,
            research_task_id="research_tavily",
            query="豆包 用户评价",
            source_tool="web_search",
            provider="tavily",
            title="豆包用户使用报告",
            url="https://example.com/doubao-review",
            source_type="general_third_party",
            selected_for_collection=True,
        ),
        ResearchSourceCandidate(
            id="candidate_zhihu",
            task_id=task_id,
            research_task_id="research_zhihu",
            query="豆包 好用吗",
            source_tool="zhihu_search",
            provider="zhihu_mcp",
            title="豆包真实使用体验",
            url="https://www.zhihu.com/question/123",
            source_type="community",
            selected_for_collection=True,
        ),
        ResearchSourceCandidate(
            id="candidate_official",
            task_id=task_id,
            research_task_id="research_official",
            query="豆包 官方网站",
            source_tool="web_search",
            provider="tavily",
            title="豆包官方网站",
            url="https://www.doubao.com/",
            source_type="official_site",
            selected_for_collection=True,
            metadata={"selection_state": "selected", "final_score": 42.0},
        ),
    ]
    store.save_many(task_id, "research_source_candidates", candidates)

    coordinator = ResearchAgentCoordinator.__new__(ResearchAgentCoordinator)
    coordinator.store = store
    coordinator._lock = threading.RLock()
    run = ResearchAgentCoordinatorRun(
        id="researchagentcoord_feed",
        task_id=task_id,
        status="running",
    )

    discovered = build_evidence_feed(store, task_id)
    require(
        {item["status"] for item in discovered} == {"discovered"},
        "selected candidates were not projected as discovered",
    )
    first_events = coordinator.sync_evidence_events(task_id, run=run)
    require(
        len(first_events) == 3
        and all(item.event_type == "evidence_added" for item in first_events),
        "discovered evidence_added events were not emitted",
    )

    store.save_many(
        task_id,
        "collection_attempts",
        [
            CollectionAttempt(
                task_id=task_id,
                research_task_id="research_official",
                requested_url="https://www.doubao.com/?utm_source=agent",
                status="failed",
                error="RuntimeError: robots.txt rejected automated collection",
            )
        ],
    )
    store.save_many(
        task_id,
        "research_agent_observations",
        [
            {
                "id": "observation_zhihu_fetch_failed",
                "task_id": task_id,
                "research_task_id": "research_zhihu",
                "action_id": "action_zhihu_fetch",
                "action": "FETCH",
                "status": "failed",
                "summary": "ValueError: Zhihu MCP 候选没有可验证正文",
                "payload": {
                    "failed_url": "https://www.zhihu.com/question/123?utm_medium=mcp",
                    "error": "ValueError: Zhihu MCP 候选没有可验证正文",
                },
            }
        ],
    )
    failed_feed = build_evidence_feed(store, task_id)
    failed_by_id = {item["id"]: item for item in failed_feed}
    require(
        failed_by_id["candidate_official"]["status"] == "collection_failed",
        "failed CollectionAttempt was not projected",
    )
    require(
        failed_by_id["candidate_official"]["failure_reason"]
        == "站点 robots.txt 不允许自动采集",
        "raw collection error was not mapped to a concise reason",
    )
    require(
        failed_by_id["candidate_official"]["candidate_quality_score"] == 42.0,
        "candidate quality score was not kept separate from source reliability",
    )
    require(
        failed_by_id["candidate_zhihu"]["status"] == "collection_failed",
        "failed Research Agent FETCH observation was not projected",
    )
    failure_events = coordinator.sync_evidence_events(task_id, run=run)
    require(
        len(failure_events) == 2
        and all(item.data["status"] == "collection_failed" for item in failure_events),
        "collection_failed transitions were not emitted through evidence_added",
    )

    tavily_source = SourceDocument(
        id="source_tavily",
        task_id=task_id,
        title="豆包用户使用报告",
        url="https://example.com/doubao-review",
        source_type="other",
        competitor="豆包",
        reliability_score=0.72,
        metadata={
            "provider": "tavily",
            "discovered_url": "https://example.com/doubao-review",
        },
    )
    store.save_many(task_id, "sources", [tavily_source])
    collected_events = coordinator.sync_evidence_events(task_id, run=run)
    require(
        len(collected_events) == 1
        and collected_events[0].data["status"] == "collected",
        "source persistence was not emitted as collected",
    )

    zhihu_source = SourceDocument(
        id="source_zhihu",
        task_id=task_id,
        title="豆包真实使用体验",
        url="https://www.zhihu.com/question/123",
        source_type="social",
        competitor="豆包",
        reliability_score=0.6,
        metadata={
            "source_tool": "zhihu_search",
            "provider": "zhihu_mcp",
        },
    )
    store.save_many(task_id, "sources", [tavily_source, zhihu_source])
    store.save_many(
        task_id,
        "evidence",
        [
            SourceEvidence(
                id="evidence_zhihu",
                task_id=task_id,
                source_id=zhihu_source.id,
                competitor="豆包",
                snippet="用户描述了连续使用后的真实体验。",
                normalized_fact="用户提供了豆包的实际使用反馈。",
                confidence=0.8,
            )
        ],
    )
    verified_events = coordinator.sync_evidence_events(task_id, run=run)
    require(
        [item.data["status"] for item in verified_events]
        == ["collected", "verified"],
        "source/evidence transitions were not emitted in order",
    )
    require(
        set(verified_events[-1].data) == {
            "title",
            "url",
            "source_tool",
            "status",
            "reliability_score",
        },
        "evidence_added payload contract changed",
    )

    feed = build_evidence_feed(store, task_id)
    by_id = {item["id"]: item for item in feed}
    require(by_id["candidate_tavily"]["status"] == "collected", "FETCH projection failed")
    require(by_id["candidate_tavily"]["source_tool"] == "tavily", "Tavily provenance lost")
    require(by_id["candidate_zhihu"]["status"] == "verified", "Evidence projection failed")
    require(by_id["candidate_zhihu"]["source_tool"] == "zhihu_search", "Zhihu provenance lost")
    require(
        by_id["candidate_official"]["status"] == "collection_failed",
        "unresolved collection failure was hidden",
    )

    completed_run = run.model_copy(update={"status": "completed"})
    store.save_many(
        task_id,
        "research_agent_coordinator_runs",
        [completed_run],
    )

    original_get_store = api_main.get_store
    original_get_coordinator = api_main.get_research_agent_coordinator
    api_main.get_store = lambda: store
    api_main.get_research_agent_coordinator = lambda: coordinator
    try:
        client = TestClient(api_main.app)
        response = client.get(f"/tasks/{task_id}/evidence-feed")
        require(response.status_code == 200, "evidence-feed endpoint failed")
        require(len(response.json()["items"]) == 3, "evidence-feed endpoint lost items")

        stream = client.get(
            f"/api/analysis-tasks/{task_id}/research-agent/run/events/stream"
        )
        require(stream.status_code == 200, "SSE endpoint failed")
        require(
            '"event_type": "evidence_added"' in stream.text,
            "evidence_added was not delivered through the existing SSE stream",
        )
    finally:
        api_main.get_store = original_get_store
        api_main.get_research_agent_coordinator = original_get_coordinator

    root = Path(__file__).resolve().parents[1]
    frontend = (root / "frontend" / "src" / "app.js").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    require('id="evidence-library"' in html, "Evidence Library sidebar missing")
    require("evidence_added" in frontend, "frontend does not consume evidence events")
    require("collection_failed" in frontend, "frontend does not render collection failures")
    require("setInterval" not in frontend, "frontend introduced polling")

    print("check_evidence_feed: PASS")
    print("statuses=discovered,collection_failed,collected,verified")
    print("sources=tavily,zhihu_search,official_site")
    print("sse_event=evidence_added")
    print("frontend_polling=false")


if __name__ == "__main__":
    main()
