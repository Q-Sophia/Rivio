from __future__ import annotations

from typing import Any

from app.execution.research_agent import ProductionResearchTools
from app.schemas import ResearchSourceCandidate, ResearchTask
from app.tools.registry import ToolRegistry
from app.tools.router import WEB_SEARCH_TOOL, ZHIHU_SEARCH_TOOL
from app.tools.schemas import ToolResult
from app.tools.zhihu_mcp import (
    ZhihuSearchMCPAdapter,
    expand_zhihu_search_queries,
)


class FakeZhihuClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, arguments: dict[str, Any]) -> list[ToolResult]:
        self.calls.append(dict(arguments))
        call_number = len(self.calls)
        return [
            ToolResult(
                title=f"知乎结果 {call_number}",
                url=f"https://www.zhihu.com/question/{call_number}",
                content=f"对应查询：{arguments['query']}",
                provider="fake_zhihu_mcp",
                metadata={"channel": "zhihu"},
            )
        ]


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def load_many(
        self,
        task_id: str,
        artifact_type: str,
    ) -> list[dict[str, Any]]:
        return list(self.artifacts.get((task_id, artifact_type), []))

    def save_many(
        self,
        task_id: str,
        artifact_type: str,
        values: list[Any],
    ) -> None:
        self.artifacts[(task_id, artifact_type)] = [
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item)
            for item in values
        ]


def _research_task(
    *,
    task_id: str,
    dimension: str,
    research_intent: str,
) -> ResearchTask:
    return ResearchTask(
        task_id=task_id,
        information_need_id=f"need_{task_id}",
        title=f"豆包 {dimension}",
        objective=f"研究豆包的 {dimension}",
        competitor="豆包",
        dimension=dimension,
        research_intent=research_intent,
        stop_condition="获得候选来源",
    )


def main() -> None:
    original_query = "豆包 用户评价 体验"
    expected_expanded_queries = [
        "豆包 好用吗",
        "豆包 使用体验",
        "豆包 真实感受",
        "豆包 怎么样",
        "豆包 用户评价",
    ]

    assert expand_zhihu_search_queries(
        original_query,
        competitor="豆包",
        dimension="customer",
        research_intent="user_feedback",
    ) == expected_expanded_queries
    assert expand_zhihu_search_queries(
        "豆包 产品定位",
        competitor="豆包",
        dimension="positioning",
        research_intent="product_information",
    ) == ["豆包 产品定位"]
    for dimension in (
        "customer",
        "user_feedback",
        "customer_feedback",
        "experience",
        "usage",
    ):
        assert len(
            expand_zhihu_search_queries(
                original_query,
                competitor="豆包",
                dimension=dimension,
            )
        ) == 5
    for dimension in (
        "positioning",
        "feature",
        "pricing",
        "ecosystem",
    ):
        assert expand_zhihu_search_queries(
            original_query,
            competitor="豆包",
            dimension=dimension,
            research_intent="user_feedback",
        ) == [original_query]

    store = InMemoryArtifactStore()
    fake_client = FakeZhihuClient()
    registry = ToolRegistry()
    web_queries: list[str] = []

    def fake_web_search(**kwargs: Any) -> dict[str, Any]:
        web_queries.append(str(kwargs["query"]))
        return {
            "query": kwargs["query"],
            "search_scope": kwargs["search_scope"],
            "result_count": 0,
            "results": [],
        }

    registry.register(WEB_SEARCH_TOOL, fake_web_search)
    registry.register(
        ZhihuSearchMCPAdapter(invoke=fake_client.invoke)
    )

    tools = ProductionResearchTools.__new__(ProductionResearchTools)
    tools.store = store
    tools.zhihu_client = fake_client
    tools.tool_registry = registry
    tools._select_pending_external_candidates = lambda **_kwargs: []

    customer_task = _research_task(
        task_id="task_customer",
        dimension="customer",
        research_intent="user_feedback",
    )
    tools.search(
        task_id=customer_task.task_id,
        research_task=customer_task,
        query=original_query,
        search_scope="community",
        limit=5,
        agent_run_id="run_customer",
    )
    customer_candidates = [
        ResearchSourceCandidate(**raw)
        for raw in store.load_many(
            customer_task.task_id,
            "research_source_candidates",
        )
    ]

    assert web_queries == [original_query]
    assert [item["query"] for item in fake_client.calls] == (
        expected_expanded_queries
    )
    assert len(customer_candidates) == len(expected_expanded_queries)
    assert all(
        item.query == original_query
        for item in customer_candidates
    )
    assert {
        str(item.metadata.get("zhihu_expanded_query") or "")
        for item in customer_candidates
    } == set(expected_expanded_queries)
    assert all(
        item.metadata.get("zhihu_original_query") == original_query
        for item in customer_candidates
    )

    fake_client.calls.clear()
    positioning_query = "豆包 产品定位"
    positioning_task = _research_task(
        task_id="task_positioning",
        dimension="positioning",
        research_intent="product_information",
    )
    positioning_candidates = tools._search_zhihu_candidates(
        task_id=positioning_task.task_id,
        research_task=positioning_task,
        query=positioning_query,
        limit=5,
        agent_run_id="run_positioning",
    )

    assert [item["query"] for item in fake_client.calls] == [
        positioning_query
    ]
    assert len(positioning_candidates) == 1
    assert positioning_candidates[0].metadata.get(
        "zhihu_query_expansion_applied"
    ) is False

    print("check_zhihu_query_expansion: PASS")
    print("customer_queries=", expected_expanded_queries)
    print("positioning_queries=", [positioning_query])


if __name__ == "__main__":
    main()
