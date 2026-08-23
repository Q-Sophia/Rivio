from __future__ import annotations

import argparse

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaimV2,
    CompetitiveReport,
    LLMCall,
    LLMOutput,
    ReportStatement,
    ResearchGap,
    RunStatus,
)
from run_step6c_writer_real_pilot import DEFAULT_TASK_ID


MOCK_BASELINE_TASK_ID = "snapshot_step6c_professional_mock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the bounded Step6C.2D real Writer pilot."
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    args = parse_args()
    store = ArtifactStore()
    summaries = store.load_many(args.task_id, "pipeline_summary")
    reports = [
        CompetitiveReport(**item)
        for item in store.load_many(args.task_id, "reports")
    ]
    statements = [
        ReportStatement(**item)
        for item in store.load_many(args.task_id, "report_statements")
    ]
    claims = [
        AnalysisClaimV2(**item)
        for item in store.load_many(args.task_id, "claims_v2")
    ]
    gaps = [
        ResearchGap(**item)
        for item in store.load_many(args.task_id, "research_gaps")
    ]
    calls = [LLMCall(**item) for item in store.load_many(args.task_id, "llm_calls")]
    outputs = [
        LLMOutput(**item) for item in store.load_many(args.task_id, "llm_outputs")
    ]
    require(bool(summaries and reports and statements), "缺少 Step6C.2D 产物")

    summary = summaries[-1]
    metadata = summary.get("metadata") or {}
    report = reports[-1]
    reader_body = report.markdown.split("## 结论引用索引", 1)[0]
    writer_calls = [item for item in calls if item.agent_role == "writer"]
    real_calls = [item for item in calls if item.provider != "mock"]
    mock_calls = [item for item in calls if item.provider == "mock"]

    require(summary.get("pipeline_status") == "completed", "Pipeline 未完成")
    require(bool(summary.get("approved")), "Reviewer 未批准报告")
    require(metadata.get("writer_only_real") is True, "不是仅 Writer 真实路由")
    require(metadata.get("real_writer_calls_count") == 1, "真实 Writer 调用数不是 1")
    require(metadata.get("real_llm_calls_count") == 1, "真实 LLM 调用总数不是 1")
    require(metadata.get("mock_llm_calls_count") == 2, "上游 mock 调用数不是 2")
    require(metadata.get("llm_fallback_count") == 0, "真实 Writer 发生了 mock 回退")
    require(len(calls) == 3 and len(outputs) == 3, "LLM 调用或输出总数不是 3")
    require(len(writer_calls) == 1 and len(real_calls) == 1, "真实调用角色不唯一")
    require(writer_calls[0].provider == "compatible", "Writer 不是 compatible Provider")
    require(writer_calls[0].model == "deepseek-v4-flash", "Writer 模型不是 DeepSeek V4 Flash")
    require(writer_calls[0].mode == "llm", "Writer 未关闭 fallback")
    require(writer_calls[0].status == RunStatus.COMPLETED, "Writer 调用未完成")
    require(writer_calls[0].prompt_version == "2.1.1-candidate", "Writer Prompt 版本错误")
    require(len(mock_calls) == 2, "Extractor / Analyst 没有固定为 mock")
    require(
        all(item.validation_status == "passed" for item in outputs),
        "存在未通过结构校验的 LLMOutput",
    )
    payload_types = set(
        writer_calls[0].metadata.get("input_payload_artifact_types", [])
    )
    require("sources" not in payload_types, "真实 Writer 不应读取原始 sources")
    require("evidence" not in payload_types, "真实 Writer 不应读取原始 evidence")
    require("claims_v2" in payload_types, "真实 Writer 未读取 claims_v2")
    require("research_gaps" in payload_types, "真实 Writer 未读取 research_gaps")
    require(
        not writer_calls[0].metadata.get("input_internal_reference_fields", []),
        "真实 Writer 输入仍包含 source/evidence 内部编号字段",
    )
    require(
        report.metadata.get("internal_reference_fields_hidden") is True,
        "报告未记录内部引用编号隔离状态",
    )

    claim_ids = {item.id for item in claims}
    gap_ids = {item.id for item in gaps}
    traced_claim_ids = {item for statement in statements for item in statement.claim_ids}
    traced_gap_ids = {
        item for statement in statements for item in statement.research_gap_ids
    }
    require(traced_claim_ids == claim_ids, "读者正文没有覆盖全部 claim")
    require(traced_gap_ids == gap_ids, "读者正文没有覆盖全部 ResearchGap")
    require(len(reader_body.strip()) >= 1200, "真实 Writer 报告正文过短")
    require("现有证据表明" not in reader_body, "仍存在审计式套话")
    require("通用竞品分析报告" not in report.markdown, "仍使用通用报告标题")
    require("综上所述" not in reader_body, "仍存在课程论文式措辞")
    require(
        "内容审核与 AI 降噪相关的风险" not in reader_body,
        "把内容审核与 AI 降噪控制能力反向写成了风险",
    )

    baseline_reports = store.load_many(MOCK_BASELINE_TASK_ID, "reports")
    if baseline_reports:
        baseline = CompetitiveReport(**baseline_reports[-1])
        require(report.markdown != baseline.markdown, "真实 Writer 报告与 mock 模板完全相同")

    print("STEP6C2D_REAL_WRITER_CHECK_PASS")
    print(f"task_id={args.task_id}")
    print(f"writer_model={writer_calls[0].model}")
    print(f"writer_prompt={writer_calls[0].prompt_id}@{writer_calls[0].prompt_version}")
    print("real_writer_calls=1")
    print("mock_upstream_calls=2")
    print("fallback_count=0")
    print(f"report_statement_count={len(statements)}")
    print(f"reader_body_chars={len(reader_body.strip())}")
    print("real_llm_called=true")


if __name__ == "__main__":
    main()
