from __future__ import annotations

from pathlib import Path

from app.execution.research_agent import (
    ProductionResearchTools,
    ResearchEvidenceAgentService,
)
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisTask,
    InformationNeed,
    ResearchAgentAction,
    ResearchTask,
    WebSearchResult,
)
from app.tools.router import resolve_tools
from app.workflow.trace import TraceRecorder


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class SearchThenFinishDecider:
    def __init__(self, competitor: str):
        self.competitor = competitor
        self.calls = 0

    def decide(self, *, task_id, research_task, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return ResearchAgentAction(
                task_id=task_id,
                research_task_id=research_task.id,
                action="SEARCH",
                rationale="Exercise deterministic source routing.",
                query=f"{self.competitor} research evidence",
            )
        return ResearchAgentAction(
            task_id=task_id,
            research_task_id=research_task.id,
            action="FINISH",
            rationale="Routing was observed.",
            finish_status="PARTIAL",
        )


class RecordingCollector:
    def __init__(self, store: ArtifactStore, provider: str):
        self.store = store
        self.provider = provider
        self.calls: list[dict] = []

    def search_agent_query(
        self,
        *,
        task_id,
        research_task,
        query,
        limit,
        source_preference,
    ):
        self.calls.append(
            {
                "task_id": task_id,
                "research_task_id": research_task.id,
                "query": query,
                "limit": limit,
                "source_preference": source_preference,
            }
        )
        existing = [
            WebSearchResult(**raw)
            for raw in self.store.load_many(task_id, "web_search_results")
        ]
        index = len(existing) + 1
        result = WebSearchResult(
            id=f"searchresult_{self.provider}_{index}",
            task_id=task_id,
            research_task_id=research_task.id,
            search_attempt_id=f"searchattempt_{self.provider}_{index}",
            provider=self.provider,
            query=query,
            rank=1,
            title=f"{self.provider} offline result",
            url=f"https://{self.provider}.example/item/{index}",
            snippet="Offline deterministic search result.",
            selected_for_collection=True,
            metadata={
                "source_level": (
                    "community" if self.provider == "zhihu" else "first_party"
                )
            },
        )
        self.store.save_many(task_id, "web_search_results", [*existing, result])
        return research_task, [result.id]

    def close(self) -> None:
        return None


def prepare_store(
    root: Path,
    *,
    task_id: str,
    research_intent: str,
) -> tuple[ArtifactStore, ResearchTask]:
    store = ArtifactStore(root)
    task = ResearchTask(
        id=f"researchtask_{task_id}",
        task_id=task_id,
        information_need_id=f"need_{task_id}",
        title="Acme routing research",
        objective="Research Acme with the source appropriate to this need",
        competitor="Acme",
        dimension="用户评价" if research_intent == "user_feedback" else "核心功能",
        research_intent=research_intent,
        stop_condition="Complete the offline routing check",
    )
    need = InformationNeed(
        id=task.information_need_id,
        task_id=task_id,
        question_id=f"question_{task_id}",
        dimension=task.dimension,
        research_intent=research_intent,
        required_facts=["routing evidence"],
        comparability_basis="same scope",
        decision_link="routing decision",
    )
    store.save_many(
        task_id,
        "analysis_tasks",
        [AnalysisTask(id=task_id, query=task.objective, competitors=["Acme"])],
    )
    store.save_many(task_id, "research_tasks", [task])
    store.save_many(task_id, "research_information_needs", [need])
    for artifact_type in (
        "research_agent_runs",
        "research_agent_actions",
        "research_agent_observations",
        "research_missions",
        "research_mission_states",
        "research_worker_contexts",
        "research_worker_results",
        "research_mission_decisions",
        "sources",
        "evidence",
        "source_task_associations",
        "official_domain_contexts",
        "web_search_results",
        "source_selection_runs",
        "dag_nodes",
        "agent_runs",
        "tool_calls",
    ):
        store.save_many(task_id, artifact_type, [])
    return store, task


def run_agent_case(
    root: Path,
    *,
    task_id: str,
    research_intent: str,
) -> tuple[RecordingCollector, RecordingCollector, list[str]]:
    store, task = prepare_store(
        root,
        task_id=task_id,
        research_intent=research_intent,
    )
    recorder = TraceRecorder(store=store, task_id=task_id)
    tavily = RecordingCollector(store, "tavily")
    zhihu = RecordingCollector(store, "zhihu")
    tools = ProductionResearchTools(
        store=store,
        recorder=recorder,
        collector=tavily,
        community_collector=zhihu,
    )
    try:
        payload = ResearchEvidenceAgentService(store=store).run_once(
            task_id,
            research_task_id=task.id,
            decider=SearchThenFinishDecider(task.competitor),
            tools=tools,
        )
    finally:
        tools.close()
    require(payload["status"] == "completed", f"ResearchAgent failed: {payload}")
    tool_names = [
        str(raw.get("tool_name") or "")
        for raw in store.load_many(task_id, "tool_calls")
    ]
    return tavily, zhihu, tool_names


def main() -> None:
    require(
        resolve_tools({"research_intent": "user_feedback"})
        == ["community_search"],
        "user_feedback should resolve to community_search",
    )
    require(
        resolve_tools({"research_intent": "feature"}) == ["web_search"],
        "feature should resolve to web_search",
    )
    require(
        resolve_tools({"dimension": "用户评价"}) == ["web_search"],
        "legacy task without research_intent must remain web_search",
    )

    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "kp_r3_t2_research_source_routing"
    )
    web, community, tool_names = run_agent_case(
        root / "community",
        task_id="task_routing_community",
        research_intent="user_feedback",
    )
    require(
        len(community.calls) == 1
        and not web.calls
        and "community_search" in tool_names,
        "user feedback ResearchAgent did not route exclusively to Zhihu mock",
    )

    web, community, tool_names = run_agent_case(
        root / "feature",
        task_id="task_routing_feature",
        research_intent="feature",
    )
    require(
        len(web.calls) == 1
        and not community.calls
        and "web_search" in tool_names,
        "feature ResearchAgent did not route exclusively to Tavily mock",
    )

    web, community, tool_names = run_agent_case(
        root / "legacy",
        task_id="task_routing_legacy",
        research_intent="",
    )
    require(
        len(web.calls) == 1
        and not community.calls
        and "web_search" in tool_names,
        "legacy ResearchAgent task did not default to Tavily/web_search",
    )

    print("check_kp_r3_t2_research_source_routing: PASS")
    print("user_feedback_tool=community_search")
    print("feature_tool=web_search")
    print("legacy_default_tool=web_search")
    print("research_agent_zhihu_mock=true")
    print("research_agent_tavily_mock=true")
    print("real_network_calls=0")


if __name__ == "__main__":
    main()
