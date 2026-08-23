from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_main
from app.harness.artifacts import ArtifactStore
from app.intake.service import IntentDraftService, build_intent_llm_config


REQUEST_TEXT = (
    "请对比 ClassIn、腾讯会议和 BigBlueButton 在在线教学场景中的产品能力、"
    "价格和生态，帮助高校选择教学云平台。"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "step6d"
    root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root)
    draft_id = "draft_step6d_check"
    store.save_many(draft_id, "llm_calls", [])
    store.save_many(draft_id, "llm_outputs", [])
    service = IntentDraftService(
        store=store,
        config=build_intent_llm_config(force_mock=True),
    )
    original_service = api_main.IntentDraftService
    api_main.IntentDraftService = lambda: service
    try:
        client = TestClient(api_main.app)
        draft, llm_call = service.parse_request(REQUEST_TEXT, draft_id=draft_id)
        require(draft.ready_for_confirmation is True, "完整需求未进入可确认状态")
        require(len(draft.competitors) == 3, "比较对象抽取数量错误")
        require("在线教育" in draft.industry, "行业识别错误")
        require("通用竞品分析" not in draft.report_subject, "报告主题仍是通用占位符")
        require(llm_call["validation_status"] == "passed", "结构化输出未通过校验")

        response = client.get(f"/api/task-drafts/{draft_id}")
        require(response.status_code == 200, f"draft API failed: {response.text}")
        payload = response.json()
        draft = payload["draft"]
        require(draft["metadata"]["execution_started"] is False, "解析错误启动了执行")
        calls_before = store.load_many(draft_id, "llm_calls")
        read_response = client.get(f"/api/task-drafts/{draft_id}")
        require(read_response.status_code == 200, "草稿读取 API 失败")
        calls_after = store.load_many(draft_id, "llm_calls")
        require(len(calls_before) == len(calls_after) == 1, "读取草稿时重复调用了 LLM")

        confirm_payload = {
            key: draft[key]
            for key in (
                "decision_question",
                "industry",
                "competitors",
                "target_customers",
                "core_scenarios",
                "focus_areas",
                "constraints",
                "report_subject",
                "preferred_title",
            )
        }
        confirm_response = client.post(
            f"/api/task-drafts/{draft_id}/confirm",
            json=confirm_payload,
        )
        require(confirm_response.status_code == 200, f"confirm API failed: {confirm_response.text}")
        confirmed = confirm_response.json()
        task = confirmed["analysis_task"]
        require(confirmed["execution_started"] is False, "确认任务错误启动了执行")
        require(task["status"] == "pending", "新任务状态不是 pending")
        require(task["metadata"]["execution_started"] is False, "任务元数据错误标记为已执行")
        require(task["report_subject"] == draft["report_subject"], "动态报告主题未进入 AnalysisTask")
        require(
            not (root / task["id"] / "pipeline_summary.json").exists(),
            "确认任务意外生成了正式运行 summary",
        )

        repeat_response = client.post(
            f"/api/task-drafts/{draft_id}/confirm",
            json=confirm_payload,
        )
        require(repeat_response.status_code == 422, "重复确认没有被阻止")
    finally:
        api_main.IntentDraftService = original_service

    print("check_step6d_intake: PASS")
    print("intent_structured_output=passed")
    print("draft_read_does_not_call_llm=true")
    print("confirmation_starts_execution=false")
    print("dynamic_report_subject=true")


if __name__ == "__main__":
    main()
