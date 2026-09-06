from __future__ import annotations
from app.collection.service import CollectorQueueService

import tempfile
from pathlib import Path

from app.execution.research_agent import ProductionResearchTools
from app.harness.artifacts import ArtifactStore
from app.schemas import ResearchSourceCandidate, ResearchTask
from app.tools.mcp_adapter import MCPToolAdapter
from app.tools.registry import ToolRegistry
from app.tools.router import ZHIHU_SEARCH_TOOL
from app.tools.zhihu_mcp import (
    ZhihuRemoteMCPClient,
    build_zhihu_mcp_tool_input_schema,
)


def main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="zhihu_candidate_smoke_"
    ) as temp_dir:
        store = ArtifactStore(Path(temp_dir))

        research_task = ResearchTask(
            task_id="task_zhihu_candidate_smoke",
            information_need_id="need_zhihu_candidate_smoke",
            title="研究 ClassIn 用户评价",
            objective="收集 ClassIn 的第三方用户体验和评价线索",
            competitor="ClassIn",
            dimension="用户评价",
            stop_condition="获得可用于后续 Evidence 验证的候选来源",
        )

        client = ZhihuRemoteMCPClient()

        registry = ToolRegistry()
        registry.register(
            MCPToolAdapter(
                name=ZHIHU_SEARCH_TOOL,
                description="Zhihu Remote MCP search",
                input_schema=build_zhihu_mcp_tool_input_schema(),
                invoke=client.invoke,
            )
        )

        # 这里只测试 _search_zhihu_candidates，
        # 不需要启动完整 Collector/Web Research。
        tools = ProductionResearchTools.__new__(
            ProductionResearchTools
        )
        tools.store = store
        tools.zhihu_client = client
        tools.tool_registry = registry
        tools.collector = CollectorQueueService(store=store)

        try:
            candidates = tools._search_zhihu_candidates(
                task_id=research_task.task_id,
                research_task=research_task,
                query="ClassIn 用户评价",
                limit=2,
                agent_run_id="run_zhihu_candidate_smoke",
            )

            selected = tools._select_pending_external_candidates(
                task_id=research_task.task_id,
                research_task=research_task,
            )

            persisted = [
                ResearchSourceCandidate(**item)
                for item in store.load_many(
                    research_task.task_id,
                    "research_source_candidates",
                )
            ]

            assert candidates, "未生成 Zhihu Candidate"
            assert persisted, "Candidate 未写入 ArtifactStore"

            assert all(
                str(item.metadata.get("selection_state") or "")
                in {"selected", "rejected"}
                for item in persisted
            ), "存在未经过统一 Selection Gate 的 Candidate"

            assert all(
                "final_score" in item.metadata
                for item in persisted
            ), "Candidate 缺少统一质量评分"

            assert all(
                item.channel == "zhihu"
                for item in persisted
            ), "channel 不是 zhihu"

            assert all(
                item.selected_for_collection is False
                for item in persisted
            ), (
                "Zhihu Candidate 不应在统一质量选择前"
                "直接 selected_for_collection=True"
            )

            assert all(
                item.url
                for item in persisted
            ), "存在空 URL Candidate"

            print(
                "check_zhihu_candidate_integration: PASS"
            )
            print(
                "candidate_count=",
                len(persisted),
            )

            for item in persisted:
                print(
                    "-",
                    item.source_tool,
                    item.metadata.get("selection_state"),
                    "score=",
                    item.metadata.get("final_score"),
                    "role=",
                    item.metadata.get("source_role"),
                    "reason=",
                    item.metadata.get("selection_reason"),
                    item.title[:50],
                )

        finally:

            tools.collector.close()

            client.close()


if __name__ == "__main__":
    main()