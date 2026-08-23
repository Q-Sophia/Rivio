from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.harness.artifacts import ArtifactStore
from app.intake.planning import ExecutionPlanningService
from app.schemas import AnalysisTask


ARTIFACT_TYPES = [
    "analysis_tasks",
    "dataset_compatibility_assessments",
    "execution_plans",
    "execution_authorizations",
    "task_board",
    "task_records",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def save_task(store: ArtifactStore, task: AnalysisTask) -> None:
    for artifact_type in ARTIFACT_TYPES:
        store.save_many(task.id, artifact_type, [])
    store.save_many(task.id, "analysis_tasks", [task])


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6d3"
    root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root)
    service = ExecutionPlanningService(store=store)

    compatible_task = AnalysisTask(
        id="task_step6d3_compatible",
        query="比较三种在线教学方案，为高校下一学年技术选型提供依据。",
        industry="在线教育",
        competitors=[
            "ClassIn",
            "腾讯云实时互动 / TRTC 教育方案",
            "BigBlueButton",
        ],
        focus_areas=["产品能力", "价格", "生态", "部署责任"],
        report_subject="高校在线教学云方案竞品分析",
        metadata={"execution_started": False},
    )
    partial_task = AnalysisTask(
        id="task_step6d3_partial",
        query="比较在线教学工具，为高校选型提供依据。",
        industry="教育",
        competitors=["ClassIn", "腾讯会议", "BigBlueButton"],
        focus_areas=["产品能力", "价格", "生态"],
        report_subject="高校在线教学工具竞品分析",
        metadata={"execution_started": False},
    )
    incompatible_task = AnalysisTask(
        id="task_step6d3_incompatible",
        query="比较三款医疗影像系统。",
        industry="医疗健康",
        competitors=["产品甲", "产品乙", "产品丙"],
        focus_areas=["诊断准确率"],
        report_subject="医疗影像系统竞品分析",
        metadata={"execution_started": False},
    )
    for task in (compatible_task, partial_task, incompatible_task):
        save_task(store, task)

    original_service = api_main.ExecutionPlanningService
    api_main.ExecutionPlanningService = lambda: service
    try:
        client = TestClient(api_main.app)
        compatible_response = client.post(
            f"/api/analysis-tasks/{compatible_task.id}/plan"
        )
        require(compatible_response.status_code == 200, compatible_response.text)
        compatible = compatible_response.json()
        assessment = compatible["compatibility_assessment"]
        plan = compatible["execution_plan"]
        require(assessment["status"] == "compatible", "兼容任务没有通过资料闸门")
        require(assessment["competitor_coverage_ratio"] == 1.0, "竞品覆盖率不是 1.0")
        require(plan["status"] == "ready", "兼容计划没有进入 ready")
        require(plan["authorization_available"] is True, "兼容计划不可授权")
        require(plan["estimated_real_llm_calls"] == 2, "真实模型调用估算错误")

        no_ack = client.post(
            f"/api/analysis-tasks/{compatible_task.id}/authorize",
            json={"plan_id": plan["id"], "acknowledge_dataset_scope": False},
        )
        require(no_ack.status_code == 422, "未确认资料范围却被授权")

        authorize_response = client.post(
            f"/api/analysis-tasks/{compatible_task.id}/authorize",
            json={"plan_id": plan["id"], "acknowledge_dataset_scope": True},
        )
        require(authorize_response.status_code == 200, authorize_response.text)
        authorized = authorize_response.json()
        require(authorized["execution_started"] is False, "授权动作错误启动了执行")
        require(authorized["execution_plan"]["status"] == "authorized", "计划未标记授权")
        require(len(authorized["task_board"]["tasks"]) == 6, "任务板步骤数量错误")
        require(
            sum(item["status"] == "ready" for item in authorized["task_board"]["tasks"]) == 1,
            "入队后应该只有第一个任务 ready",
        )
        require(
            not (root / compatible_task.id / "pipeline_summary.json").exists(),
            "授权入队意外生成了正式运行 summary",
        )

        partial_response = client.post(f"/api/analysis-tasks/{partial_task.id}/plan")
        require(partial_response.status_code == 200, partial_response.text)
        partial = partial_response.json()
        require(partial["compatibility_assessment"]["status"] == "partial", "部分兼容未识别")
        require(
            partial["compatibility_assessment"]["missing_competitors"] == ["腾讯会议"],
            "没有准确识别缺失的腾讯会议资料",
        )
        require(partial["execution_plan"]["status"] == "blocked", "部分兼容计划未阻断")
        blocked_authorize = client.post(
            f"/api/analysis-tasks/{partial_task.id}/authorize",
            json={
                "plan_id": partial["execution_plan"]["id"],
                "acknowledge_dataset_scope": True,
            },
        )
        require(blocked_authorize.status_code == 422, "部分兼容任务被错误授权")

        incompatible_response = client.post(
            f"/api/analysis-tasks/{incompatible_task.id}/plan"
        )
        require(incompatible_response.status_code == 200, incompatible_response.text)
        require(
            incompatible_response.json()["compatibility_assessment"]["status"] == "incompatible",
            "跨行业任务未被阻断",
        )
    finally:
        api_main.ExecutionPlanningService = original_service

    print("check_step6d3_planning: PASS")
    print("compatible_plan_ready=true")
    print("partial_dataset_blocked=true")
    print("cross_industry_blocked=true")
    print("explicit_authorization_required=true")
    print("authorization_starts_execution=false")
    print("queued_task_records=6")


if __name__ == "__main__":
    main()
