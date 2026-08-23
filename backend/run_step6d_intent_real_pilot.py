from __future__ import annotations

import argparse
import json

from app.harness.artifacts import ArtifactStore
from app.intake.service import IntentDraftService, build_intent_llm_config


DEFAULT_DRAFT_ID = "draft_step6d_intent_deepseek_v4_pilot"
DEFAULT_REQUEST = (
    "请对比 ClassIn、腾讯会议和 BigBlueButton 在中国高校在线教学场景中的产品能力、"
    "价格、部署责任和生态集成，帮助学校信息化负责人判断下一学年应优先试用哪条方案。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run exactly one real DeepSeek intent-parse call for Step6D.2."
    )
    parser.add_argument("--draft-id", default=DEFAULT_DRAFT_ID)
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = ArtifactStore()
    store.save_many(args.draft_id, "llm_calls", [])
    store.save_many(args.draft_id, "llm_outputs", [])
    service = IntentDraftService(store=store, config=build_intent_llm_config())
    draft, llm_call = service.parse_request(args.request, draft_id=args.draft_id)
    summary = {
        "step": "Step6D.2",
        "draft_id": draft.id,
        "provider": llm_call["provider"],
        "model": llm_call["model"],
        "used_fallback": llm_call["used_fallback"],
        "validation_status": llm_call["validation_status"],
        "prompt_version": llm_call["prompt_version"],
        "ready_for_confirmation": draft.ready_for_confirmation,
        "status": draft.status,
        "industry": draft.industry,
        "competitors": draft.competitors,
        "report_subject": draft.report_subject,
        "missing_fields": draft.missing_fields,
        "execution_started": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
