from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.execution.research_agent import ResearchEvidenceAgentService
from app.execution.research_agent_coordinator import ResearchAgentCoordinator
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisTask,
    InformationNeed,
    ResearchAgentAction,
    ResearchAgentRun,
    ResearchMissionDecision,
    ResearchTask,
    SourceDocument,
    SourceEvidence,
    SourceType,
)
from app.tools.base import ResearchTool, WebSearchTool
from app.tools.mcp_adapter import MCPToolAdapter
from app.tools.providers.tavily_provider import TavilyProvider

from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolResult
from check_r1_bounded_research_r1 import FakeR1Service, seed_case


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FailureThenSuccessR1:
    def __init__(self, store: ArtifactStore):
        self.store = store
        self.delegate = FakeR1Service(
            store,
            {
                ("Acme", "pricing", 1): (2, False),
                ("Acme", "feature", 2): (2, False),
            },
        )
        self.calls: list[tuple[str, int]] = []

    def run_once(self, task_id: str, *, research_task_id: str, **kwargs):
        task = next(
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
            if item.get("id") == research_task_id
        )
        self.calls.append((task.dimension, task.collection_round))
        if task.dimension == "feature" and task.collection_round == 1:
            source = SourceDocument(
                id="source_failed_worker",
                task_id=task_id,
                title="failed worker source",
                url="https://offline.invalid/failed",
                source_type=SourceType.DOCS,
                competitor="Acme",
                metadata={"research_task_id": task.id},
            )
            evidence = SourceEvidence(
                id="evidence_failed_worker",
                task_id=task_id,
                source_id=source.id,
                competitor="Acme",
                dimension="feature",
                snippet="must be removed",
                normalized_fact="must be removed",
                metadata={"research_task_id": task.id},
            )
            self.store.save_many(task_id, "sources", [source])
            self.store.save_many(task_id, "evidence", [evidence])
            self.store.save_many(
                task_id,
                "research_agent_runs",
                [
                    ResearchAgentRun(
                        task_id=task_id,
                        research_task_id=task.id,
                        verified_evidence_ids=[evidence.id],
                        remaining_need=task.objective,
                    )
                ],
            )
            raise RuntimeError("fixture worker failure at stage=research_step_3")
        return self.delegate.run_once(
            task_id,
            research_task_id=research_task_id,
            **kwargs,
        )


class RecordingSupervisor:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def decide(
        self,
        *,
        task_id,
        mission,
        state,
        worker_result,
        coverage,
        gaps,
        budget_state,
    ) -> ResearchMissionDecision:
        self.calls.append(
            {
                "worker_outcome": worker_result.outcome,
                "coverage": dict(state.coverage_status_by_need),
                "round": budget_state.collection_round,
            }
        )
        if budget_state.collection_round == 1:
            return ResearchMissionDecision(
                task_id=task_id,
                mission_id=mission.id,
                action="REQUEST_MORE_EVIDENCE",
                target_need="need_Acme_feature",
                research_goal="Retry the failed focused feature need.",
                reason="Feature coverage remains missing after isolated failure.",
            )
        return ResearchMissionDecision(
            task_id=task_id,
            mission_id=mission.id,
            action="FINISH",
            reason="Coverage is sufficient.",
        )


def check_failure_isolation(root: Path) -> None:
    task_id = "task_kp_r2_fix2_failure_isolation"
    store = ArtifactStore(root / "failure_isolation")
    seed_case(
        store,
        task_id=task_id,
        competitors=["Acme"],
        dimensions=["feature", "pricing"],
        max_rounds=2,
    )
    for artifact in (
        "research_missions",
        "research_mission_states",
        "research_worker_contexts",
        "research_worker_results",
        "research_mission_decisions",
        "research_task_failures",
        "research_batch_results",
    ):
        store.save_many(task_id, artifact, [])
    store.save_many(
        task_id,
        "research_information_needs",
        [
            InformationNeed(
                id="need_Acme_feature",
                task_id=task_id,
                question_id="question_product",
                dimension="feature",
                required_facts=["feature facts"],
                comparability_basis="same product scope",
                decision_link="product decision",
            ),
            InformationNeed(
                id="need_Acme_pricing",
                task_id=task_id,
                question_id="question_product",
                dimension="pricing",
                required_facts=["pricing facts"],
                comparability_basis="same billing basis",
                decision_link="product decision",
            ),
        ],
    )
    worker = FailureThenSuccessR1(store)
    supervisor = RecordingSupervisor()
    coordinator = ResearchAgentCoordinator(
        store=store,
        research_service_factory=lambda _store: worker,
        mission_supervisor_factory=lambda _store: supervisor,
    )
    coordinator.submit(task_id, acknowledge_real_llm_call=True)
    result = coordinator.wait(task_id, timeout=20)

    require(
        worker.calls[:2] == [("feature", 1), ("pricing", 1)],
        "Task A failure 后 Task B 未继续执行",
    )
    require(
        ("feature", 2) in worker.calls,
        "PARTIAL Mission 未继续执行 Supervisor 创建的下一 Research Unit",
    )
    failures = store.load_many(task_id, "research_task_failures")
    require(
        len(failures) == 1
        and failures[0]["research_need_id"] == "need_Acme_feature"
        and failures[0]["stage"] == "research_step_3",
        "失败任务没有生成结构化 failure record",
    )
    batches = store.load_many(task_id, "research_batch_results")
    require(
        batches[0]["status"] == "PARTIAL"
        and batches[0]["completed_tasks"]
        and batches[0]["failed_tasks"]
        and batches[0]["pending_tasks"],
        "ResearchBatchResult 未区分 completed/failed/pending",
    )
    require(supervisor.calls, "任务失败后 Mission Supervisor 未继续决策")
    failed_run = next(
        item
        for item in store.load_many(task_id, "research_agent_runs")
        if item["research_task_id"] == "researchtask_Acme_feature_round_1"
    )
    require(
        failed_run["outcome"] == "FAILED"
        and not failed_run["verified_evidence_ids"]
        and all(
            item["id"] != "evidence_failed_worker"
            for item in store.load_many(task_id, "evidence")
        ),
        "失败 Task 的 Evidence 仍进入 verified authority",
    )
    require(
        result.status == "completed" and result.result_status == "PARTIAL",
        "局部失败仍导致 Mission crash 或未标记 PARTIAL",
    )


class EchoTool(ResearchTool):
    name = "echo"
    description = "offline echo"
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"value": kwargs["value"]}


def check_tool_runtime() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    require(registry.get("echo").name == "echo", "ToolRegistry.get 失败")
    require(
        [item.name for item in registry.list_tools()] == ["echo"],
        "ToolRegistry.list_tools 失败",
    )
    require(
        registry.call(
            "echo", agent_run_id="offline", task_id="task", value="ok"
        )["value"]
        == "ok",
        "ToolRegistry.call 失败",
    )

    tavily = TavilyProvider(
        "offline-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Official docs",
                            "url": "https://docs.example.com/product",
                            "content": "product documentation",
                            "score": 0.9,
                        }
                    ]
                },
                request=request,
            )
        ),
    )
    tavily_result = WebSearchTool(tavily).execute(query="example docs")[0]
    require(
        isinstance(tavily_result, ToolResult)
        and tavily_result.provider == "tavily",
        "Mock Tavily 未返回统一 ToolResult",
    )
    tavily.close()

    raw_marker = "raw-provider-payload-must-not-escape"
    zhihu = ZhihuProvider(
        "offline-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "target": {
                                "id": "42",
                                "type": "answer",
                                "title": "知乎产品讨论",
                                "url": "https://www.zhihu.com/question/1/answer/42",
                                "excerpt": "社区用户的产品体验",
                                "private_raw": raw_marker,
                            },
                            "tracking": raw_marker,
                        }
                    ]
                },
                request=request,
            )
        ),
    )
    zhihu_result = WebSearchTool(zhihu).execute(query="产品体验")[0]
    require(
        zhihu_result.provider == "zhihu"
        and zhihu_result.source_type == "community"
        and raw_marker not in zhihu_result.model_dump_json(),
        "Zhihu 未统一为 community ToolResult 或泄露原始响应",
    )
    zhihu.close()


def check_mcp_adapter() -> None:
    invocations: list[dict[str, Any]] = []
    adapter = MCPToolAdapter(
        name="external_lookup",
        description="offline MCP-compatible tool",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        invoke=lambda arguments: invocations.append(arguments)
        or {"ok": True, "query": arguments["query"]},
    )
    registry = ToolRegistry()
    registry.register(adapter)
    result = registry.call(
        "external_lookup",
        agent_run_id="offline",
        task_id="task",
        query="Acme",
    )
    require(
        registry.get("external_lookup").input_schema["required"] == ["query"]
        and result["ok"]
        and invocations[0]["query"] == "Acme",
        "MCPToolAdapter metadata/invoke 接入失败",
    )


class SearchThenFinishDecider:
    def __init__(self):
        self.calls = 0

    def decide(self, *, task_id, research_task, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return ResearchAgentAction(
                task_id=task_id,
                research_task_id=research_task.id,
                action="SEARCH",
                rationale="Find an anchored source.",
                query="Acme feature documentation",
            )
        return ResearchAgentAction(
            task_id=task_id,
            research_task_id=research_task.id,
            action="FINISH",
            rationale="Offline registry test complete.",
            finish_status="PARTIAL",
        )


class RegistryBackedResearchTools:
    def __init__(self):
        self.registry = ToolRegistry()
        self.registry.register(
            WebSearchTool(
                type(
                    "OfflineProvider",
                    (),
                    {
                        "name": "offline",
                        "search": lambda _self, query, **_kwargs: [
                            ToolResult(
                                title="Acme docs",
                                url="https://docs.acme.example/features",
                                content="Acme feature docs",
                                provider="offline",
                                source_type="general_third_party",
                            )
                        ],
                    },
                )()
            )
        )
        self.registry_calls = 0

    def search(self, *, task_id, query, limit, **_kwargs):
        self.registry_calls += 1
        results = self.registry.call(
            "web_search",
            agent_run_id="research_agent_offline",
            task_id=task_id,
            query=query,
            count=limit,
        )
        return {
            "query": query,
            "results": [item.model_dump(mode="json") for item in results],
        }


def check_research_agent_registry_path(root: Path) -> None:
    task_id = "task_kp_r2_fix2_agent_registry"
    store = ArtifactStore(root / "agent_registry")
    task = ResearchTask(
        id="researchtask_agent_registry",
        task_id=task_id,
        information_need_id="need_agent_registry",
        title="Acme feature research",
        objective="Research Acme features",
        competitor="Acme",
        dimension="feature",
        stop_condition="offline registry check",
    )
    store.save_many(
        task_id,
        "analysis_tasks",
        [AnalysisTask(id=task_id, query=task.objective, competitors=["Acme"])],
    )
    store.save_many(task_id, "research_tasks", [task])
    store.save_many(
        task_id,
        "research_information_needs",
        [
            InformationNeed(
                id="need_agent_registry",
                task_id=task_id,
                question_id="question_agent_registry",
                dimension="feature",
                required_facts=["feature facts"],
                comparability_basis="same product scope",
                decision_link="product decision",
            )
        ],
    )
    for artifact in (
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
        "dag_nodes",
        "agent_runs",
        "tool_calls",
    ):
        store.save_many(task_id, artifact, [])
    tools = RegistryBackedResearchTools()
    payload = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=task.id,
        decider=SearchThenFinishDecider(),
        tools=tools,
    )
    require(
        payload["status"] == "completed" and tools.registry_calls == 1,
        "ResearchAgent SEARCH 未通过 ToolRegistry",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "kp_r2_fix2_runtime_tool_mcp"
    )
    check_failure_isolation(root)
    check_tool_runtime()
    check_mcp_adapter()
    check_research_agent_registry_path(root)
    print("check_kp_r2_fix2_runtime_tool_mcp: PASS")
    print("failure_isolation=true")
    print("partial_mission_supervisor_continue=true")
    print("failed_task_evidence_authority=false")
    print("tool_registry=true")
    print("tavily_provider=true")
    print("zhihu_provider=true")
    print("mcp_adapter=true")
    print("research_agent_tool_registry=true")
    print("real_network_calls=0")


if __name__ == "__main__":
    main()
