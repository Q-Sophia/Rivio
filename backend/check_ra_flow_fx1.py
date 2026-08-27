from __future__ import annotations

from pathlib import Path

import app.api.main as api_main
from app.agents.research_planner import ResearchPlannerAgent
from app.execution.research_agent_coordinator import ResearchAgentCoordinatorRun
from app.intake import IntentDraftService
from app.intake.service import build_intent_llm_config
from app.schemas import (
    AgentContext,
    AnalysisTaskDraft,
    ConfirmAnalysisTaskRequest,
    DatasetCompatibilityAssessment,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakePlanningService:
    prepared = False

    def get_latest_plan(self, task_id: str):
        require(task_id == "task_ra_flow_fx1", "Planning 收到错误 task_id")
        return None

    def build(self, task_id: str) -> dict:
        require(task_id == "task_ra_flow_fx1", "Planning 收到错误 task_id")
        self.__class__.prepared = True
        return {
            "research_plan": {"id": "researchplan_fixture", "status": "ready"},
            "research_tasks": [
                {
                    "id": "researchtask_fixture",
                    "status": "waiting_for_collector",
                }
            ],
            "kiqs": [],
            "information_needs": [],
            "task_board": {"tasks": []},
        }


class FakeCoordinator:
    submitted = False

    def submit(self, task_id: str, **kwargs) -> ResearchAgentCoordinatorRun:
        require(FakePlanningService.prepared, "Coordinator 在 plan ensure 前启动")
        require(task_id == "task_ra_flow_fx1", "Coordinator 收到错误 task_id")
        require(
            kwargs == {
                "mode": "deepseek",
                "acknowledge_real_llm_call": True,
            },
            "Coordinator mode/ack 接线错误",
        )
        self.__class__.submitted = True
        return ResearchAgentCoordinatorRun(
            task_id=task_id,
            research_task_ids=["researchtask_fixture"],
            total_tasks=1,
        )


class MemoryArtifactStore:
    def __init__(self):
        self.data: dict[tuple[str, str], list[dict]] = {}

    def save_many(self, task_id: str, artifact_type: str, items) -> None:
        self.data[(task_id, artifact_type)] = [
            item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else dict(item)
            for item in items
        ]

    def load_many(self, task_id: str, artifact_type: str) -> list[dict]:
        return [
            dict(item)
            for item in self.data.get((task_id, artifact_type), [])
        ]


class MemoryIntentDraftService(IntentDraftService):
    def get_draft(self, draft_id: str):
        items = self.store.load_many(draft_id, "task_drafts")
        return AnalysisTaskDraft(**items[-1]) if items else None


def main() -> None:
    original_planning = api_main.ResearchPlanningService
    original_coordinator = api_main.get_research_agent_coordinator
    try:
        api_main.ResearchPlanningService = FakePlanningService
        api_main.get_research_agent_coordinator = lambda: FakeCoordinator()
        payload = api_main.start_research_agent_coordinator(
            "task_ra_flow_fx1",
            api_main.StartResearchAgentCoordinatorRequest(
                mode="deepseek",
                acknowledge_real_llm_call=True,
            ),
        )
    finally:
        api_main.ResearchPlanningService = original_planning
        api_main.get_research_agent_coordinator = original_coordinator

    require(FakeCoordinator.submitted, "Coordinator 未启动")
    require(payload["research_plan_created"] is True, "新 plan 标记错误")
    require(
        payload["research_plan"]["research_plan"]["id"]
        == "researchplan_fixture",
        "自动准备的 plan 未返回前端",
    )

    root = Path(__file__).resolve().parents[1]
    app_js = (root / "frontend" / "src" / "app.js").read_text(
        encoding="utf-8"
    )
    index_html = (root / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )
    start_body = app_js.split("async function startResearchLoop()", 1)[1].split(
        "function renderResearchAnalysis", 1
    )[0]
    require(
        "!state.researchPlan" not in start_body,
        "前端仍要求用户先手工生成 ResearchPlan",
    )
    require(
        "/research-agent/run" in app_js and "/research-loop`" not in app_js,
        "前端正式启动路径未切到 Research Agent Coordinator",
    )
    require(
        "Research Loop Runner（研究循环执行器）" not in index_html
        and "开始自动研究" in index_html,
        "前端仍展示旧 Research Loop Runner 正式文案",
    )
    require(
        'id="run-collector-once-btn"' in index_html
        and 'class="planning-boundary hidden"' in index_html,
        "旧单步执行 DOM 未作为隐藏兼容入口处理",
    )
    require(
        'id="active-research-request"' in index_html
        and "analysisTask?.metadata?.request_text" in app_js,
        "前端顶部没有从 AnalysisTask 原始 request_text 展示研究需求",
    )

    original_request = "请调研一下小红书和抖音的竞品分析"
    store = MemoryArtifactStore()
    draft = AnalysisTaskDraft(
        id="draft_broad_request",
        request_text=original_request,
        decision_question="比较小红书和抖音的整体竞争定位与差异",
        competitors=["小红书", "抖音"],
        primary_target="小红书",
        comparison_targets=["抖音"],
        cross_competitor_comparison=True,
        ready_for_confirmation=True,
    )
    store.save_many(draft.id, "task_drafts", [draft])
    _confirmed, analysis_task = MemoryIntentDraftService(
        store=store,
        config=build_intent_llm_config(force_mock=True),
    ).confirm_draft(
        draft.id,
        ConfirmAnalysisTaskRequest(
            decision_question=draft.decision_question,
            competitors=draft.competitors,
        ),
    )
    require(
        analysis_task.metadata.get("request_text") == original_request,
        "宽泛 User Request 未保留在 AnalysisTask.metadata.request_text",
    )
    require(
        "收费模式" not in analysis_task.query
        and "ClassIn" not in analysis_task.model_dump_json(),
        "R1 Live 测试内容污染 AnalysisTask 顶层语义",
    )
    ResearchPlannerAgent(store=store).execute(
        AgentContext(
            task_id=analysis_task.id,
            task=analysis_task,
            node_id="broad_request_planning",
            metadata={
                "dataset_assessment": DatasetCompatibilityAssessment(
                    task_id=analysis_task.id,
                    dataset_id="semantic_fixture",
                    dataset_label="semantic fixture",
                    requested_competitors=analysis_task.competitors,
                    missing_competitors=analysis_task.competitors,
                    unsupported_focus_areas=analysis_task.focus_areas,
                ).model_dump(mode="json"),
            },
        )
    )
    information_needs = store.load_many(
        analysis_task.id,
        "research_information_needs",
    )
    research_tasks = store.load_many(analysis_task.id, "research_tasks")
    require(
        len(information_needs) > 1 and len(research_tasks) > 1,
        "宽泛 AnalysisTask 未在 ResearchPlanning 后拆成多个研究问题",
    )
    require(
        all(
            item["competitor"] in {"小红书", "抖音"}
            for item in research_tasks
        ),
        "ResearchTask 出现未请求的固定测试产品",
    )

    print("check_ra_flow_fx1: PASS")
    print("analysis_task_to_plan_ensure_to_coordinator=true")
    print("manual_plan_not_required=true")
    print("legacy_research_loop_not_product_entry=true")
    print("broad_user_request_preserved=true")
    print("research_task_created_only_after_planning=true")
    print("frontend_top_reads_original_request=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
