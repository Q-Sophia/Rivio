from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.execution.runner import ExecutionRunner
from app.harness.artifacts import ArtifactStore
from app.intake.planning import ExecutionPlanningService
from app.schemas import AnalysisTask, AuthorizeExecutionRequest
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6d4"
    root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root)
    task = AnalysisTask(
        id="task_step6d4_mock_execution",
        query="比较三种在线教学方案，为高校下一学年技术选型提供依据。",
        industry="在线教育",
        competitors=[
            "ClassIn",
            "腾讯云实时互动 / TRTC 教育方案",
            "BigBlueButton",
        ],
        focus_areas=["产品能力", "价格", "生态", "部署责任"],
        report_subject="高校在线教学云方案竞品分析",
        preferred_title="Step6D.4 用户任务执行验收报告",
        metadata={"execution_started": False},
    )
    for artifact_type in [
        "analysis_tasks",
        "dataset_compatibility_assessments",
        "execution_plans",
        "execution_authorizations",
        "execution_runs",
        "execution_events",
        "task_board",
        "task_records",
    ]:
        store.save_many(task.id, artifact_type, [])
    store.save_many(task.id, "analysis_tasks", [task])

    planning = ExecutionPlanningService(store=store)
    _task, _assessment, plan = planning.build_plan(task.id)
    planning.authorize(
        task.id,
        AuthorizeExecutionRequest(
            plan_id=plan.id,
            acknowledge_dataset_scope=True,
        ),
    )
    runner = ExecutionRunner(store=store)

    original_runner = api_main.get_execution_runner
    api_main.get_execution_runner = lambda: runner
    try:
        client = TestClient(api_main.app)
        response = client.post(
            f"/api/analysis-tasks/{task.id}/execute",
            json={"mode": "mock"},
        )
        require(response.status_code == 202, response.text)
        require(
            response.json()["execution_run"]["status"] in {"queued", "running"},
            "启动接口没有立即返回队列状态",
        )
        completed = runner.wait(task.id, timeout=30)
        require(completed.status == "completed", completed.error)
        require(completed.progress_percent == 100, "完成进度不是 100%")

        status_response = client.get(f"/api/analysis-tasks/{task.id}/execution")
        require(status_response.status_code == 200, status_response.text)
        status_payload = status_response.json()
        require(status_payload["terminal"] is True, "完成任务未标记 terminal")

        events = status_payload["events"]
        sequences = [item["sequence"] for item in events]
        require(sequences == list(range(1, len(events) + 1)), "事件序号不连续")
        require(events[0]["event_type"] == "queued", "首事件不是 queued")
        require(events[-1]["event_type"] == "completed", "末事件不是 completed")
        completed_steps = {
            item["step_key"]
            for item in events
            if item["event_type"] == "step_completed"
        }
        require(len(completed_steps) == 6, "没有记录六个完成步骤")

        with client.stream(
            "GET",
            f"/api/analysis-tasks/{task.id}/events/stream?after=0",
        ) as stream:
            sse_text = "".join(stream.iter_text())
        require("event: execution" in sse_text, "SSE 没有 execution 事件")
        require('"event_type": "completed"' in sse_text, "SSE 没有完成事件")

        board = TaskBoardStore(store).require_board(task.id)
        require(str(board.status) == "completed", "TaskBoard 没有完成")
        summaries = store.load_many(task.id, "pipeline_summary")
        require(summaries[-1]["pipeline_status"] == "completed", "pipeline 未完成")
        require(
            summaries[-1]["metadata"]["user_task_execution"] is True,
            "summary 未标记用户任务执行",
        )
        reports = store.load_many(task.id, "reports")
        require(
            reports[-1]["title"] == task.preferred_title,
            "报告标题没有使用用户确认标题",
        )
        calls = store.load_many(task.id, "llm_calls")
        require(len(calls) == 3, "Mock 执行应该有 3 次结构化 LLM 调用")
        require(all(item["provider"] == "mock" for item in calls), "验收意外调用真实模型")

        repeat = client.post(
            f"/api/analysis-tasks/{task.id}/execute",
            json={"mode": "mock"},
        )
        require(repeat.status_code == 422, "完成任务被重复启动")
    finally:
        api_main.get_execution_runner = original_runner

    print("check_step6d4_execution: PASS")
    print("background_execution=completed")
    print("sse_event_stream=passed")
    print("step_events=6")
    print("user_task_preserved=true")
    print("task_specific_title=true")
    print("mock_only_no_network=true")
    print("duplicate_start_blocked=true")


if __name__ == "__main__":
    main()
