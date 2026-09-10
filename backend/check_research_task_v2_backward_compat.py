from __future__ import annotations

from pathlib import Path

from app.execution.research_agent import ResearchEvidenceAgent
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentContext,
    AnalysisTask,
    ResearchActionType,
    ResearchAgentAction,
    ResearchAgentBudget,
    ResearchTask,
)
from app.workflow.trace import TraceRecorder


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FinishDecider:
    def decide(self, *, task_id: str, research_task: ResearchTask, **_kwargs) -> ResearchAgentAction:
        return ResearchAgentAction(
            task_id=task_id,
            research_task_id=research_task.id,
            action=ResearchActionType.FINISH,
            finish_status="EXHAUSTED",
            rationale="兼容性测试：无工具调用结束",
        )


def main() -> None:
    legacy_payload = {
        "id": "researchtask_legacy_v1",
        "task_id": "task_legacy_v1",
        "information_need_id": "need_legacy_v1",
        "title": "旧任务",
        "objective": "核实旧任务仍可执行",
        "competitor": "Legacy Product",
        "dimension": "feature",
        "research_intent": "product_capability",
        "stop_condition": "完成或记录缺口",
    }
    legacy_task = ResearchTask(**legacy_payload)
    require(legacy_task.schema_version == "v1", "旧任务默认 schema_version 被改变")
    require(not legacy_task.framework_id, "旧任务被强制绑定 Framework")

    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "research_task_v1_compat"
    root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root)
    for artifact_type in (
        "research_agent_runs",
        "research_agent_actions",
        "research_agent_observations",
        "tool_calls",
    ):
        store.save_many(legacy_task.task_id, artifact_type, [])
    recorder = TraceRecorder(store=store, task_id=legacy_task.task_id)
    context = AgentContext(
        task_id=legacy_task.task_id,
        task=AnalysisTask(
            id=legacy_task.task_id,
            query=legacy_task.objective,
            competitors=[legacy_task.competitor],
        ),
        node_id="legacy_research_agent",
        metadata={
            "agent_run_id": "run_legacy_research_agent",
            "research_task": legacy_task.model_dump(mode="json"),
            "information_need": None,
        },
    )
    result = ResearchEvidenceAgent(
        store=store,
        recorder=recorder,
        decider=FinishDecider(),
        tools=object(),
        budget=ResearchAgentBudget(max_steps=1),
    ).execute(context)
    require(result.status == "completed", "旧 ResearchTask 无法被 Research Agent 执行")
    run = store.load_many(legacy_task.task_id, "research_agent_runs")[-1]
    require(run["outcome"] == "EXHAUSTED", "旧 ResearchTask 没有正常进入终态")

    print("check_research_task_v2_backward_compat: PASS")
    print("legacy_schema_version=v1")
    print("legacy_research_agent_execution=completed")


if __name__ == "__main__":
    main()
