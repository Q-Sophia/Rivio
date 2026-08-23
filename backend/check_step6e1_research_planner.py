from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.harness.artifacts import ArtifactStore
from app.intake.research_planning import ResearchPlanningService
from app.schemas import AnalysisTask


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6e1"
    root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root)
    task = AnalysisTask(
        id="task_step6e1_dynamic_research",
        query="比较三种在线教学工具，为高校平台选型提供依据。",
        industry="在线教育",
        competitors=["ClassIn", "腾讯会议", "BigBlueButton"],
        focus_areas=["产品能力", "价格", "生态"],
        report_subject="高校在线教学工具竞品分析",
        metadata={"execution_started": False},
    )
    for artifact_type in [
        "analysis_tasks", "research_plans", "research_kiqs",
        "research_information_needs", "research_tasks", "task_board",
        "task_records", "agent_runs", "dag_nodes", "llm_calls",
    ]:
        store.save_many(task.id, artifact_type, [])
    store.save_many(task.id, "analysis_tasks", [task])
    service = ResearchPlanningService(store=store)

    original = api_main.ResearchPlanningService
    api_main.ResearchPlanningService = lambda: service
    try:
        client = TestClient(api_main.app)
        response = client.post(f"/api/analysis-tasks/{task.id}/research-plan")
        require(response.status_code == 200, response.text)
        payload = response.json()
        plan = payload["research_plan"]
        tasks = payload["research_tasks"]
        require(plan["status"] == "needs_collection", "缺失竞品未触发采集")
        require(len(payload["kiqs"]) == 3, "没有按关注维度生成 KIQ")
        require(len(payload["information_needs"]) == 3, "信息需求数量错误")
        require(len(tasks) == 9, "没有按竞品×维度拆分研究任务")
        waiting = [item for item in tasks if item["status"] == "waiting_for_collector"]
        require(any(item["competitor"] == "腾讯会议" for item in waiting), "未识别腾讯会议资料缺口")
        require(all(item["stop_condition"] for item in waiting), "采集任务缺少停止条件")
        require(plan["budget"]["max_collection_rounds"] == 3, "缺少研究轮次预算")
        require(payload["task_board"]["metadata"]["workflow_shape"] == "dynamic_research_tasks", "仍是固定任务板")
        require(any(item["status"] == "ready" for item in payload["task_board"]["tasks"]), "缺失任务未发布到任务板")
        require(len(store.load_many(task.id, "llm_calls")) == 0, "Mock 规划意外调用真实模型")
        require(store.load_many(task.id, "agent_runs")[-1]["agent_role"] == "orchestrator", "规划没有留下 Orchestrator 运行记录")

        read = client.get(f"/api/analysis-tasks/{task.id}/research-plan")
        require(read.status_code == 200, read.text)
        require(read.json()["research_plan"]["id"] == plan["id"], "读取接口重复生成计划")
    finally:
        api_main.ResearchPlanningService = original

    print("check_step6e1_research_planner: PASS")
    print("kiq_count=3")
    print("dynamic_research_tasks=9")
    print("missing_competitor_detected=true")
    print("taskboard_published=true")
    print("research_budget_bounded=true")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
