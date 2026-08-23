from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisTask,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    SourceDocument,
    SourceEvidence,
    TaskMode,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_task(task_id: str, competitor: str) -> AnalysisTask:
    return AnalysisTask(
        id=task_id,
        query=f"研究 {competitor} 的竞品情况",
        competitors=[competitor],
        report_subject=f"{competitor} 竞品分析",
        mode=TaskMode.LIVE,
    )


def main() -> None:
    check_root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "e2e_task_workspace"
    )
    store = ArtifactStore(check_root)
    task_a = make_task("task_workspace_a_v1", "Alpha")
    task_b = make_task("task_workspace_b_v1", "Beta")
    for task in (task_a, task_b):
        store.save_many(task.id, "analysis_tasks", [task])
        store.save_many(task.id, "sources", [])
        store.save_many(task.id, "evidence", [])

    original_get_store = api_main.get_store
    api_main.get_store = lambda: store
    client = TestClient(api_main.app)
    try:
        initial = client.get(f"/api/analysis-tasks/{task_a.id}/workspace")
        require(initial.status_code == 200, f"initial workspace failed: {initial.text}")
        payload = initial.json()
        require(payload["taskId"] == task_a.id, "workspace taskId 不正确")
        require(payload["analysisTask"]["id"] == task_a.id, "AnalysisTask 未进入 workspace")
        require(payload["stage"] == "confirmed", "只有 AnalysisTask 时阶段不是 confirmed")
        require(payload["summary"] is None, "缺少 pipeline_summary 时没有返回 null")
        require(payload["report"] is None, "缺少 report 时没有返回 null")
        require(payload["review"] is None, "缺少 review 时没有返回 null")
        require(payload["claims"] == [], "缺少 claims 时没有返回空数组")
        require(payload["trace"]["dag_nodes"] == [], "缺少 trace 时没有返回空数组")
        require(
            not (check_root / task_a.id / "pipeline_summary.json").exists(),
            "Workspace 读取意外创建 pipeline_summary",
        )

        research_task = ResearchTask(
            id="researchtask_workspace_a",
            task_id=task_a.id,
            information_need_id="need_workspace_a",
            title="采集 Alpha 官方资料",
            objective="验证 Alpha 的公开产品能力",
            competitor="Alpha",
            dimension="feature",
            seed_urls=["https://example.test/alpha"],
            stop_condition="获得一条可追溯官方证据",
        )
        research_plan = ResearchPlan(
            id="researchplan_workspace_a",
            task_id=task_a.id,
            decision_question=task_a.query,
            status=ResearchPlanStatus.NEEDS_COLLECTION,
            research_task_ids=[research_task.id],
            missing_dimensions=["feature"],
        )
        store.save_many(task_a.id, "research_plans", [research_plan])
        store.save_many(task_a.id, "research_tasks", [research_task])
        planned = client.get(f"/api/analysis-tasks/{task_a.id}/workspace").json()
        require(planned["researchPlan"]["id"] == research_plan.id, "ResearchPlan 未进入 workspace")
        require([item["id"] for item in planned["researchTasks"]] == [research_task.id], "ResearchTask 未进入 workspace")
        require(planned["stage"] == "research_planned", "ResearchPlan 没有推进 workspace stage")

        source_a = SourceDocument(
            id="src_workspace_a",
            task_id=task_a.id,
            title="Alpha 官方资料",
            url="https://example.test/alpha",
            competitor="Alpha",
            content_excerpt="Alpha 提供课堂互动功能。",
        )
        evidence_a = SourceEvidence(
            id="ev_workspace_a",
            task_id=task_a.id,
            source_id=source_a.id,
            competitor="Alpha",
            snippet="Alpha 提供课堂互动功能。",
            normalized_fact="Alpha 提供课堂互动功能。",
            source_text_end=14,
            extraction_method="workspace_fixture",
        )
        store.save_many(task_a.id, "sources", [source_a])
        store.save_many(task_a.id, "evidence", [evidence_a])

        progressive = client.get(f"/api/analysis-tasks/{task_a.id}/workspace")
        require(progressive.status_code == 200, "渐进式 workspace 读取失败")
        progressive_payload = progressive.json()
        require([item["id"] for item in progressive_payload["sources"]] == [source_a.id], "新增 source 未出现")
        require([item["id"] for item in progressive_payload["evidence"]] == [evidence_a.id], "新增 evidence 未出现")
        require(progressive_payload["stage"] == "evidence_extracted", "stage 未随 Artifact 前进")

        source_b = SourceDocument(
            id="src_workspace_b",
            task_id=task_b.id,
            title="Beta 官方资料",
            url="https://example.test/beta",
            competitor="Beta",
        )
        store.save_many(task_b.id, "sources", [source_b])
        isolated = client.get(f"/api/analysis-tasks/{task_b.id}/workspace").json()
        require([item["id"] for item in isolated["sources"]] == [source_b.id], "task B 来源不正确")
        require(isolated["evidence"] == [], "task B 串入了 task A 的 evidence")
        require(source_a.id not in {item["id"] for item in isolated["sources"]}, "task A 来源串入 task B")

        runs = client.get("/api/runs")
        require(runs.status_code == 200, "/api/runs 核心接口被破坏")
        run_task_ids = {item["task_id"] for item in runs.json()["runs"]}
        require(task_a.id not in run_task_ids and task_b.id not in run_task_ids, "无 summary 的新任务混入 /api/runs")
    finally:
        api_main.get_store = original_get_store

    legacy_client = TestClient(api_main.app)
    legacy_runs = legacy_client.get("/api/runs")
    require(legacy_runs.status_code == 200, "恢复默认 ArtifactStore 后 /api/runs 失败")
    if legacy_runs.json()["runs"]:
        first = legacy_runs.json()["runs"][0]
        legacy_dashboard = legacy_client.get(
            f"/api/runs/{first['run_id']}/tasks/{first['task_id']}/dashboard"
        )
        require(legacy_dashboard.status_code == 200, "历史 dashboard 核心回归失败")

    frontend_path = Path(__file__).resolve().parents[1] / "frontend" / "src" / "app.js"
    frontend = frontend_path.read_text(encoding="utf-8")
    require('activeTaskId: ""' in frontend, "前端没有明确 activeTaskId")
    require("endpoints.workspace(taskId)" in frontend, "前端没有读取 task workspace")
    require("setActiveTaskContext(payload.analysis_task.id)" in frontend, "确认任务后没有激活 task_id")
    confirm_start = frontend.index("async function confirmDraft()")
    confirm_end = frontend.index("function resetExecutionPlanning()", confirm_start)
    confirm_source = frontend[confirm_start:confirm_end]
    require(
        confirm_source.index("setActiveTaskContext(payload.analysis_task.id)")
        < confirm_source.index("loadTaskWorkspace(payload.analysis_task.id)"),
        "确认任务后没有按 activeTaskId 加载 workspace",
    )
    refresh_start = frontend.index("async function loadTask()")
    refresh_end = frontend.index("function render()", refresh_start)
    refresh_source = frontend[refresh_start:refresh_end]
    require(
        refresh_source.index("if (state.activeTaskId)")
        < refresh_source.index("loadLegacyDashboard"),
        "刷新按钮仍优先回退历史 Run",
    )
    require("当前任务尚未生成该阶段产物。" in frontend, "主页面缺少统一空态")
    require("if (!report)" in frontend, "报告页没有安全处理 null report")
    require("if (claims.length)" in frontend, "结论页没有安全处理空 claims")

    print("check_e2e_task_workspace: PASS")
    print("analysis_task_only_workspace=true")
    print("missing_artifacts_use_defaults=true")
    print("progressive_artifacts_visible=true")
    print("task_isolation=true")
    print("new_task_independent_from_runs=true")
    print("legacy_runs_and_dashboard_preserved=true")
    print("frontend_active_task_workspace=true")
    print("refresh_does_not_fallback=true")
    print("report_and_claims_empty_states=true")
    print("real_external_calls=0")


if __name__ == "__main__":
    main()
