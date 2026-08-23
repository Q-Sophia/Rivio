from __future__ import annotations

import argparse

from app.harness.artifacts import ArtifactStore
from app.llm.structured import validate_portfolio_v2_refs
from app.schemas import (
    CompetitiveAnalysisPortfolioV2,
    CompetitiveReport,
    LLMCall,
    LLMOutput,
    ReportStatement,
    RunStatus,
    SourceDocument,
    SourceEvidence,
)
from run_step6c_dual_real_pilot import DEFAULT_TASK_ID


MOCK_BASELINE_TASK_ID = "snapshot_step6c_professional_mock"
WRITER_ONLY_BASELINE_TASK_ID = "snapshot_step6c2d_writer_deepseek_v4_pilot_v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the bounded Step6C.3 real Analyst + Writer pilot."
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
    portfolios = [
        CompetitiveAnalysisPortfolioV2(**item)
        for item in store.load_many(args.task_id, "analysis_portfolios")
    ]
    sources = [
        SourceDocument(**item)
        for item in store.load_many(args.task_id, "sources")
    ]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(args.task_id, "evidence")
    ]
    calls = [LLMCall(**item) for item in store.load_many(args.task_id, "llm_calls")]
    outputs = [
        LLMOutput(**item) for item in store.load_many(args.task_id, "llm_outputs")
    ]
    require(bool(summaries and reports and statements and portfolios), "缺少 Step6C.3 产物")

    summary = summaries[-1]
    metadata = summary.get("metadata") or {}
    report = reports[-1]
    portfolio = portfolios[-1]
    reader_body = report.markdown.split("## 结论引用索引", 1)[0]
    extractor_calls = [item for item in calls if item.agent_role == "extractor"]
    analyst_calls = [item for item in calls if item.agent_role == "analyst"]
    writer_calls = [item for item in calls if item.agent_role == "writer"]
    real_calls = [item for item in calls if item.provider != "mock"]

    require(summary.get("pipeline_status") == "completed", "Pipeline 未完成")
    require(bool(summary.get("approved")), "Reviewer 未批准报告")
    require(metadata.get("analyst_writer_real") is True, "不是 Analyst + Writer 双真实路由")
    require(metadata.get("writer_only_real") is False, "仍被错误标记为仅 Writer 真实")
    require(metadata.get("real_llm_calls_count") == 2, "真实 LLM 调用总数不是 2")
    require(metadata.get("real_analyst_calls_count") == 1, "真实 Analyst 调用数不是 1")
    require(metadata.get("real_writer_calls_count") == 1, "真实 Writer 调用数不是 1")
    require(metadata.get("real_extractor_calls_count") == 0, "Extractor 不应真实调用")
    require(metadata.get("mock_llm_calls_count") == 1, "Extractor mock 调用数不是 1")
    require(metadata.get("llm_fallback_count") == 0, "真实调用发生了 mock 回退")
    require(len(calls) == 3 and len(outputs) == 3, "LLM 调用或输出总数不是 3")
    require(len(extractor_calls) == 1 and extractor_calls[0].provider == "mock", "Extractor 路由错误")
    require(len(analyst_calls) == 1 and len(writer_calls) == 1, "Analyst / Writer 调用不唯一")
    require(len(real_calls) == 2, "真实调用角色数量不正确")

    analyst_call = analyst_calls[0]
    writer_call = writer_calls[0]
    for label, call in [("Analyst", analyst_call), ("Writer", writer_call)]:
        require(call.provider == "compatible", f"{label} 不是 compatible Provider")
        require(call.model == "deepseek-v4-flash", f"{label} 模型不是 DeepSeek V4 Flash")
        require(call.mode == "llm", f"{label} 未关闭 fallback")
        require(call.status == RunStatus.COMPLETED, f"{label} 调用未完成")
        require(call.used_fallback is False, f"{label} 使用了 fallback")
        require(call.metadata.get("input_tokens", 0) > 0, f"{label} 缺少输入 Token")
        require(call.metadata.get("output_tokens", 0) > 0, f"{label} 缺少输出 Token")
    require(analyst_call.prompt_version == "2.2.2-candidate", "Analyst Prompt 版本错误")
    require(writer_call.prompt_version == "2.1.1-candidate", "Writer Prompt 版本错误")
    require(
        all(item.validation_status == "passed" for item in outputs),
        "存在未通过结构校验的 LLMOutput",
    )

    writer_payload_types = set(writer_call.metadata.get("input_payload_artifact_types", []))
    require("sources" not in writer_payload_types, "Writer 不应读取原始 sources")
    require("evidence" not in writer_payload_types, "Writer 不应读取原始 evidence")
    require("claims_v2" in writer_payload_types, "Writer 未读取 claims_v2")
    require(
        not writer_call.metadata.get("input_internal_reference_fields", []),
        "Writer 输入仍包含来源/证据内部编号字段",
    )
    require(
        report.metadata.get("internal_reference_fields_hidden") is True,
        "报告未记录内部引用编号隔离状态",
    )

    validate_portfolio_v2_refs(
        portfolio,
        known_source_ids={item.id for item in sources},
        known_evidence_ids={item.id for item in evidence},
        known_competitors={item.name for item in portfolio.competitor_profiles},
        evidence_competitors={item.id: item.competitor for item in evidence},
    )
    require(len(portfolio.items) >= 3, "真实 Analyst 生成的有效结论过少")
    require(bool(portfolio.research_gaps), "真实 Analyst 没有披露 ResearchGap")
    rejected_count = int(
        analyst_call.metadata.get("rejected_portfolio_claims_count", 0)
    )
    require(
        metadata.get("analyst_rejected_claims_count") == rejected_count,
        "Pipeline summary 未准确记录被拒绝结论数",
    )
    rejected_items = analyst_call.metadata.get("rejected_portfolio_claims", [])
    require(
        all(item.get("claim_id") and item.get("reasons") for item in rejected_items),
        "被拒绝结论缺少编号或原因审计",
    )

    claim_ids = {item.id for item in portfolio.items}
    gap_ids = {item.id for item in portfolio.research_gaps}
    traced_claim_ids = {item for statement in statements for item in statement.claim_ids}
    traced_gap_ids = {
        item for statement in statements for item in statement.research_gap_ids
    }
    require(traced_claim_ids == claim_ids, "报告正文没有追溯全部真实 Analyst 结论")
    require(traced_gap_ids == gap_ids, "报告正文没有追溯全部 ResearchGap")
    require(len(reader_body.strip()) >= 1200, "真实 Writer 报告正文过短")
    require("现有证据表明" not in reader_body, "仍存在审计式套话")
    require("通用竞品分析报告" not in report.markdown, "仍使用通用报告标题")
    require("综上所述" not in reader_body, "仍存在课程论文式措辞")
    require(
        "内容审核与 AI 降噪相关的风险" not in reader_body,
        "把内容审核与 AI 降噪控制能力反向写成了风险",
    )

    mock_portfolios = store.load_many(MOCK_BASELINE_TASK_ID, "analysis_portfolios")
    if mock_portfolios:
        mock_portfolio = CompetitiveAnalysisPortfolioV2(**mock_portfolios[-1])
        require(
            {item.claim_text for item in portfolio.items}
            != {item.claim_text for item in mock_portfolio.items},
            "真实 Analyst 结论与 mock 基线完全相同",
        )
    writer_only_reports = store.load_many(WRITER_ONLY_BASELINE_TASK_ID, "reports")
    if writer_only_reports:
        writer_only_report = CompetitiveReport(**writer_only_reports[-1])
        require(
            report.markdown != writer_only_report.markdown,
            "双真实报告与仅 Writer 真实报告完全相同",
        )

    print("STEP6C3_DUAL_REAL_CHECK_PASS")
    print(f"task_id={args.task_id}")
    print("real_analyst_calls=1")
    print("real_writer_calls=1")
    print("mock_extractor_calls=1")
    print("fallback_count=0")
    print(f"analyst_prompt={analyst_call.prompt_id}@{analyst_call.prompt_version}")
    print(f"writer_prompt={writer_call.prompt_id}@{writer_call.prompt_version}")
    print(f"accepted_claims={len(portfolio.items)}")
    print(f"rejected_claims={rejected_count}")
    print(f"research_gaps={len(portfolio.research_gaps)}")
    print(f"report_statements={len(statements)}")
    print(f"reader_body_chars={len(reader_body.strip())}")
    print("real_llm_called=true")


if __name__ == "__main__":
    main()
