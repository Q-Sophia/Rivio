from __future__ import annotations

from app.agents.research_planner import ResearchPlannerAgent
from app.agents.runtime import AgentRuntime
from app.harness.artifacts import ArtifactStore
from app.intake.planning import ExecutionPlanningService
from app.schemas import (
    AgentContext,
    AgentRole,
    DAGNode,
    ResearchPlan,
    ResearchTask,
    RunStatus,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.workflow.taskboard import TaskBoardStore
from app.workflow.trace import TraceRecorder


class ResearchPlanningService:
    """Step6E.1 boundary: one planner run, artifacts, then dynamic TaskBoard records."""

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()

    def get_task(self, task_id: str):
        return ExecutionPlanningService(store=self.store).get_task(task_id)

    def get_latest_plan(self, task_id: str) -> ResearchPlan | None:
        items = self.store.load_many(task_id, "research_plans")
        return ResearchPlan(**items[-1]) if items else None

    def get_payload(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        plan = self.get_latest_plan(task_id)
        if task is None:
            raise LookupError(f"未找到 AnalysisTask（分析任务）: {task_id}")
        if plan is None:
            raise LookupError("尚未生成 ResearchPlan（研究计划）。")
        return {
            "analysis_task": task.model_dump(mode="json"),
            "research_plan": plan.model_dump(mode="json"),
            "kiqs": self.store.load_many(task_id, "research_kiqs"),
            "information_needs": self.store.load_many(task_id, "research_information_needs"),
            "research_tasks": self.store.load_many(task_id, "research_tasks"),
            "product_cards": self.store.load_many(task_id, "product_cards"),
            "evidence_coverage": self.store.load_many(task_id, "evidence_coverage"),
            "research_gaps": self.store.load_many(task_id, "research_gaps"),
            "task_board": TaskBoardStore(self.store).require_board(task_id).model_dump(mode="json"),
            "execution_started": False,
        }

    def build(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        if task is None:
            raise LookupError(f"未找到 AnalysisTask（分析任务）: {task_id}")
        if self.get_latest_plan(task_id) is not None:
            return self.get_payload(task_id)
        if task.metadata.get("execution_authorized") or task.metadata.get("execution_started"):
            raise ValueError("任务已经授权或开始执行，不能重写研究计划。")

        recorder = TraceRecorder(store=self.store, task_id=task_id)
        assessment = ExecutionPlanningService(store=self.store).assess_task(task)
        node = DAGNode(
            id="research_planning",
            task_id=task_id,
            label="research_planning",
            agent_role=AgentRole.ORCHESTRATOR,
            status=RunStatus.RUNNING,
            input_refs=["analysis_tasks", "dataset_profile"],
        )
        recorder.set_dag_nodes([node])
        result = AgentRuntime(store=self.store, recorder=recorder).run(
            agent=ResearchPlannerAgent(store=self.store),
            context=AgentContext(
                task_id=task_id,
                task=task,
                node_id=node.id,
                input_refs=node.input_refs,
                metadata={"dataset_assessment": assessment.model_dump(mode="json")},
            ),
            node=node,
        )
        if result.status != RunStatus.COMPLETED.value:
            raise RuntimeError(result.error or "Research Planner 执行失败。")
        node.status = RunStatus.COMPLETED
        recorder.save_trace()

        research_tasks = [
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
        ]
        records = [
            TaskRecord(
                id=f"queue_{item.id}",
                task_id=task_id,
                task_key=item.id,
                task_type=TaskType.SUPPLEMENT_COLLECTION,
                target_agent_role=AgentRole.COLLECTOR,
                status=(
                    TaskStatus.COMPLETED
                    if item.status == "covered_by_snapshot"
                    else TaskStatus.READY
                ),
                priority=item.priority,
                input_refs=[f"information_need:{item.information_need_id}"],
                output_refs=(
                    ["existing_snapshot_evidence"]
                    if item.status == "covered_by_snapshot"
                    else []
                ),
                reason=item.objective,
                metadata={
                    "research_task_id": item.id,
                    "stop_condition": item.stop_condition,
                    "planner": "research_planner_agent",
                },
            )
            for item in research_tasks
        ]
        has_waiting = any(item.status == "waiting_for_collector" for item in research_tasks)
        TaskBoardStore(self.store).save_board(
            TaskBoard(
                task_id=task_id,
                status=TaskStatus.READY if has_waiting else TaskStatus.COMPLETED,
                tasks=records,
                metadata={
                    "source": "Step6E.1 Research Planner",
                    "workflow_shape": "dynamic_research_tasks",
                    "execution_started": False,
                },
            )
        )
        return self.get_payload(task_id)
