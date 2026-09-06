from __future__ import annotations

import tempfile
from pathlib import Path

from app.execution.research_agent import ProductionResearchTools
from app.harness.artifacts import ArtifactStore
from app.schemas import ResearchSourceCandidate, ResearchTask


class FakeRegistry:
    def call(self, tool_name: str, **kwargs):
        assert tool_name == "web_search"

        return {
            "query": kwargs["query"],
            "search_scope": kwargs["search_scope"],
            "result_count": 1,
            "results": [
                {
                    "title": "ClassIn 官方页面",
                    "url": "https://www.classin.com/example",
                    "snippet": "ClassIn official result",
                    "source_level": "first_party",
                    "official_confidence": "confirmed",
                    "freshness": "",
                }
            ],
        }


def build_task() -> ResearchTask:
    return ResearchTask(
        task_id="task_multi_source_smoke",
        information_need_id="need_multi_source_smoke",
        title="ClassIn 用户评价",
        objective="研究 ClassIn 用户评价与真实使用体验",
        competitor="ClassIn",
        dimension="用户评价",
        stop_condition="获得足够的用户评价证据",
    )


def test_joint_search() -> None:
    with tempfile.TemporaryDirectory(
        prefix="multi_source_joint_"
    ) as temp_dir:
        store = ArtifactStore(Path(temp_dir))
        task = build_task()

        tools = ProductionResearchTools.__new__(
            ProductionResearchTools
        )
        tools.store = store
        tools.tool_registry = FakeRegistry()

        # 只要非 None，search() 就会启用知乎补充路径。
        tools.zhihu_client = object()

        zhihu_candidate = ResearchSourceCandidate(
            task_id=task.task_id,
            research_task_id=task.id,
            query="ClassIn 用户评价",
            source_tool="zhihu_search",
            title="ClassIn 使用体验 - 知乎",
            url="https://www.zhihu.com/question/example",
            snippet="教师分享 ClassIn 实际使用体验。",
            channel="zhihu",
            provider="zhihu_mcp",
            source_type="community",
            selected_for_collection=True,
            metadata={
                "selection_state": "selected",
                "source_role": "COMMUNITY",
                "official_confidence": "unknown",
            },
        )

        def fake_zhihu_search(**kwargs):
            store.save_many(
                task.task_id,
                "research_source_candidates",
                [zhihu_candidate],
            )
            return [zhihu_candidate]

        def fake_external_selection(**kwargs):
            return [zhihu_candidate]

        tools._search_zhihu_candidates = fake_zhihu_search
        tools._select_pending_external_candidates = (
            fake_external_selection
        )

        payload = tools.search(
            task_id=task.task_id,
            research_task=task,
            query="ClassIn 用户评价",
            search_scope="community",
            limit=5,
            agent_run_id="multi_source_smoke",
        )

        urls = {
            item["url"]
            for item in payload["results"]
        }

        assert "https://www.classin.com/example" in urls
        assert (
            "https://www.zhihu.com/question/example"
            in urls
        )

        assert (
            payload["source_breakdown"]["web_search"]
            == 1
        )
        assert (
            payload["source_breakdown"][
                "zhihu_candidates"
            ]
            == 1
        )
        assert (
            payload["source_breakdown"]["zhihu_selected"]
            == 1
        )
        assert payload["supplemental_errors"] == []

        print("joint_search=true")


def test_zhihu_failure_isolated() -> None:
    with tempfile.TemporaryDirectory(
        prefix="multi_source_failure_"
    ) as temp_dir:
        store = ArtifactStore(Path(temp_dir))
        task = build_task()

        tools = ProductionResearchTools.__new__(
            ProductionResearchTools
        )
        tools.store = store
        tools.tool_registry = FakeRegistry()
        tools.zhihu_client = object()

        def failing_zhihu_search(**kwargs):
            raise RuntimeError("simulated zhihu failure")

        tools._search_zhihu_candidates = (
            failing_zhihu_search
        )

        payload = tools.search(
            task_id=task.task_id,
            research_task=task,
            query="ClassIn 用户评价",
            search_scope="community",
            limit=5,
            agent_run_id="multi_source_failure_smoke",
        )

        assert len(payload["results"]) == 1
        assert (
            payload["results"][0]["url"]
            == "https://www.classin.com/example"
        )

        assert len(
            payload["supplemental_errors"]
        ) == 1

        assert (
            payload["supplemental_errors"][0][
                "source_tool"
            ]
            == "zhihu_search"
        )

        print("zhihu_failure_isolated=true")


def main() -> None:
    test_joint_search()
    test_zhihu_failure_isolated()

    print("check_multi_source_search: PASS")


if __name__ == "__main__":
    main()
