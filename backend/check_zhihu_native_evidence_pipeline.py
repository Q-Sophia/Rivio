from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.execution.evidence_feed import build_evidence_feed
from app.execution.research_agent import (
    ProductionResearchTools,
    ResearchEvidenceAgent,
)
from app.schemas import (
    ResearchActionType,
    ResearchAgentAction,
    ResearchAgentRun,
    ResearchSourceCandidate,
    ResearchTask,
)
from app.tools.router import ZHIHU_SEARCH_TOOL
from app.tools.url_identity import canonical_source_url
from app.workflow.trace import TraceRecorder


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


class RejectingWebCollector:
    def fetch_and_persist_url(self, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError(
            "规范化后的 Zhihu MCP 候选不应落入 WebCollector"
        )


def main() -> None:
    task_id = "task_zhihu_native_pipeline"
    candidate_url = (
        "https://zhuanlan.zhihu.com/p/123456"
        "?utm_medium=openapi_platform&utm_source=test"
    )
    action_url = "https://zhuanlan.zhihu.com/p/123456"
    quote = "真实用户认为该功能节省了重复整理资料的时间。"
    content = (
        "这是一篇来自知乎社区的真实使用体验。"
        f"{quote}"
        "作者同时说明了使用场景和局限，不把搜索摘要当作证据。"
    )
    store = InMemoryArtifactStore()
    research_task = ResearchTask(
        task_id=task_id,
        information_need_id="need_zhihu_native_pipeline",
        title="豆包用户反馈",
        objective="采集豆包真实用户体验",
        competitor="豆包",
        dimension="customer",
        research_intent="user_feedback",
        stop_condition="获得经过逐字验证的社区证据",
    )
    candidate = ResearchSourceCandidate(
        task_id=task_id,
        research_task_id=research_task.id,
        query="豆包 用户评价 体验",
        source_tool=ZHIHU_SEARCH_TOOL,
        title="豆包真实使用体验 - 知乎",
        url=candidate_url,
        snippet=content[:60],
        content=content,
        channel="zhihu",
        provider="zhihu_mcp",
        source_type="community",
        selected_for_collection=True,
        metadata={
            "selection_state": "selected",
            "selection_reason": (
                "selected_after_shared_quality_rank_and_url_safety"
            ),
            "quality_rank": 1,
            "final_score": 48.5,
            "source_role": "COMMUNITY",
            "zhihu_original_query": "豆包 用户评价 体验",
            "zhihu_expanded_query": "豆包 使用体验",
            "zhihu_query_expansion_applied": True,
            "author_name": "测试用户",
        },
    )
    rejected_candidate = candidate.model_copy(
        update={
            "id": "sourcecandidate_rejected_quality",
            "url": "https://zhuanlan.zhihu.com/p/999999",
            "selected_for_collection": False,
            "metadata": {
                **candidate.metadata,
                "selection_state": "rejected",
                "selection_reason": "quality_below_minimum:19.99<20",
                "final_score": 19.99,
            },
        }
    )
    tampered_candidate = candidate.model_copy(
        update={
            "id": "sourcecandidate_tampered_quality",
            "url": "https://zhuanlan.zhihu.com/p/888888",
            "selected_for_collection": True,
            "metadata": {
                **candidate.metadata,
                "selection_state": "selected",
                "final_score": 19.99,
            },
        }
    )
    store.save_many(
        task_id,
        "research_source_candidates",
        [candidate, rejected_candidate, tampered_candidate],
    )

    tools = ProductionResearchTools.__new__(ProductionResearchTools)
    tools.store = store
    tools.collector = RejectingWebCollector()

    assert canonical_source_url(candidate_url) == canonical_source_url(
        action_url
    )
    assert canonical_source_url(
        f"{action_url}?page=2&utm_source=test"
    ) != canonical_source_url(f"{action_url}?page=3")

    agent = ResearchEvidenceAgent.__new__(ResearchEvidenceAgent)
    agent.store = store
    agent.tools = tools
    agent.recorder = TraceRecorder(store=store, task_id=task_id)
    state = ResearchAgentRun(
        task_id=task_id,
        research_task_id=research_task.id,
    )
    action = ResearchAgentAction(
        task_id=task_id,
        research_task_id=research_task.id,
        action=ResearchActionType.FETCH,
        rationale="抓取已通过质量审核的知乎候选",
        url=action_url,
    )
    observation = agent._execute_action(
        context=SimpleNamespace(
            task_id=task_id,
            metadata={"agent_run_id": "run_zhihu_native_pipeline"},
        ),
        research_task=research_task,
        need=None,
        state=state,
        action=action,
    )
    source = store.load_many(task_id, "sources")[0]
    chunk = store.load_many(task_id, "source_chunks")[0]
    assert observation.status == "completed", observation.summary
    assert observation.payload["status"] == "completed"
    assert observation.payload["render_mode"] == "mcp_native"
    assert state.source_count == 1
    assert source["source_type"] == "social"
    assert source["metadata"]["candidate_id"] == candidate.id
    assert source["metadata"]["final_score"] == 48.5
    assert source["metadata"]["zhihu_original_query"] == (
        "豆包 用户评价 体验"
    )
    assert build_evidence_feed(store, task_id)[0]["status"] == "collected"

    submitted = tools.submit_evidence(
        task_id=task_id,
        research_task=research_task,
        source_id=source["id"],
        chunk_id=chunk["id"],
        exact_quote=quote,
        supports="豆包用户反馈",
    )
    evidence = store.load_many(task_id, "evidence")[0]
    assert submitted["quote_verified"] is True
    assert evidence["metadata"]["quote_verified"] is True
    assert evidence["metadata"]["provider"] == "zhihu_mcp"
    assert evidence["metadata"]["source_type"] == "community"
    assert evidence["metadata"]["acquisition"]["candidate_id"] == (
        candidate.id
    )
    assert build_evidence_feed(store, task_id)[0]["status"] == "verified"

    source_count_before_rejected_fetch = len(
        store.load_many(task_id, "sources")
    )
    rejected_observation = agent._execute_action(
        context=SimpleNamespace(
            task_id=task_id,
            metadata={"agent_run_id": "run_zhihu_native_pipeline"},
        ),
        research_task=research_task,
        need=None,
        state=state,
        action=ResearchAgentAction(
            task_id=task_id,
            research_task_id=research_task.id,
            action=ResearchActionType.FETCH,
            rationale="不应抓取未通过质量审核的知乎候选",
            url=rejected_candidate.url,
        ),
    )
    assert rejected_observation.status == "failed"
    assert "URL 未通过 Search/Source Quality 选择" in (
        rejected_observation.summary
    )
    assert len(store.load_many(task_id, "sources")) == (
        source_count_before_rejected_fetch
    )
    tampered_observation = agent._execute_action(
        context=SimpleNamespace(
            task_id=task_id,
            metadata={"agent_run_id": "run_zhihu_native_pipeline"},
        ),
        research_task=research_task,
        need=None,
        state=state,
        action=ResearchAgentAction(
            task_id=task_id,
            research_task_id=research_task.id,
            action=ResearchActionType.FETCH,
            rationale="低分候选即使被篡改为 selected 也必须阻止",
            url=tampered_candidate.url,
        ),
    )
    assert tampered_observation.status == "failed"
    assert "未通过 Source Quality Gate" in tampered_observation.summary
    assert len(store.load_many(task_id, "sources")) == (
        source_count_before_rejected_fetch
    )

    print("check_zhihu_native_evidence_pipeline: PASS")
    print("utm_canonical_match=true")
    print("web_collector_bypassed_for_mcp_native=true")
    print("quality_provenance_preserved=true")
    print("quality_rejected_candidate_blocked=true")
    print("quality_score_rechecked_before_native_persist=true")
    print("quote_verified=true")
    print("feed_transition=collected,verified")


if __name__ == "__main__":
    main()
