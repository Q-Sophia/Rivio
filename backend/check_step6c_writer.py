from __future__ import annotations

from app.harness.artifacts import ArtifactStore
from app.prompts import PromptRegistry
from app.reporting import resolve_report_title, validate_professional_report
from app.schemas import (
    AnalysisClaimV2,
    AnalysisTask,
    BriefAssessment,
    CompetitiveReport,
    LLMCall,
    ReportStatement,
    ResearchGap,
    SourceEvidence,
)
from check_snapshot import build_snapshot_task
from run_step6c_professional_workflow_demo import DEFAULT_STEP6C_TASK_ID


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    store = ArtifactStore()
    task_id = DEFAULT_STEP6C_TASK_ID
    reports = [
        CompetitiveReport(**item)
        for item in store.load_many(task_id, "reports")
    ]
    briefs = [
        BriefAssessment(**item)
        for item in store.load_many(task_id, "brief_assessments")
    ]
    claims = [
        AnalysisClaimV2(**item)
        for item in store.load_many(task_id, "claims_v2")
    ]
    gaps = [
        ResearchGap(**item)
        for item in store.load_many(task_id, "research_gaps")
    ]
    calls = [LLMCall(**item) for item in store.load_many(task_id, "llm_calls")]
    statements = [
        ReportStatement(**item)
        for item in store.load_many(task_id, "report_statements")
    ]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    require(bool(reports and briefs and claims), "缺少 Step6C Writer 验收产物")

    task = build_snapshot_task(task_id=task_id)
    report = reports[-1]
    brief = briefs[-1]
    expected_title, title_source = resolve_report_title(task, brief)
    validate_professional_report(
        report,
        task=task,
        brief=brief,
        known_claim_ids={item.id for item in claims},
        known_research_gap_ids={item.id for item in gaps},
    )
    require(report.schema_version == "v2", "报告 schema_version 不是 v2")
    require(report.title == expected_title, "任务化标题不一致")
    require(title_source == "report_subject", "演示标题没有来自 report_subject")
    require("通用竞品分析报告" not in report.markdown, "仍残留写死通用标题")
    require("现有证据表明" not in report.markdown, "报告仍使用重复的审计式开头")
    require(
        all(report.markdown.count(item.claim_text) <= 1 for item in claims),
        "报告重复复制同一 AnalysisClaim",
    )
    report_statements = [item for item in statements if item.report_id == report.id]
    require(bool(report_statements), "未生成 ReportStatement（报告论点）")
    declared_statement_ids = set(report.sections.get("report_statement_ids", []))
    require(
        declared_statement_ids == {item.id for item in report_statements},
        "报告声明的 ReportStatement 编号与 artifact 不一致",
    )
    report_lines = report.markdown.splitlines()
    known_evidence_ids = {item.id for item in evidence}
    for statement in report_statements:
        require(statement.line_index < len(report_lines), "报告论点行号越界")
        require("[" not in statement.text, "读者可见论点仍包含内部编号")
        require(
            all(item in known_evidence_ids for item in statement.evidence_ids),
            f"报告论点 {statement.id} 引用了未知证据",
        )
        if statement.claim_ids:
            require(bool(statement.evidence_ids), f"报告结论 {statement.id} 没有证据")

    writer_calls = [item for item in calls if item.agent_role == "writer"]
    require(len(writer_calls) == 1, "Writer LLMCall 数量不是 1")
    writer_call = writer_calls[0]
    require(writer_call.prompt_id == "competitive_writer", "Writer prompt_id 错误")
    require(
        writer_call.prompt_version == "2.1.1-candidate",
        "Writer prompt_version 错误",
    )
    payload_types = set(writer_call.metadata.get("input_payload_artifact_types", []))
    require("sources" not in payload_types, "Professional Writer 不应读取原始 sources")
    require("evidence" not in payload_types, "Professional Writer 不应读取原始 evidence")
    require("claims_v2" in payload_types, "Professional Writer 未读取 claims_v2")
    require("research_gaps" in payload_types, "Professional Writer 未读取 research_gaps")

    prompt = PromptRegistry().load("competitive_writer", allow_candidate=True)
    runtime_prompt = prompt.build_writer_runtime_prompt(
        task,
        resolved_title=expected_title,
    )
    require(expected_title in runtime_prompt, "运行时 Prompt 缺少解析后的标题")
    require("ResearchGap" in runtime_prompt, "运行时 Prompt 缺少研究缺口规则")
    require("逐条复制" in runtime_prompt, "运行时 Prompt 缺少防复制规则")

    preferred = AnalysisTask(
        query="比较三个方案",
        preferred_title="教育云方案选型对比",
    )
    require(
        resolve_report_title(preferred)[0] == "教育云方案选型对比报告",
        "preferred_title 优先级错误",
    )
    subject = AnalysisTask(
        query="比较三个方案",
        industry="专业服务",
        report_subject="企业法律顾问服务",
    )
    require(
        resolve_report_title(subject)[0] == "企业法律顾问服务竞品分析报告",
        "report_subject 优先级错误",
    )
    industry = AnalysisTask(query="比较三个方案", industry="消费电子")
    require(
        resolve_report_title(industry)[0] == "消费电子竞品分析报告",
        "industry 回退标题错误",
    )

    print("STEP6C_WRITER_CHECK_PASS")
    print(f"title={report.title}")
    print(f"title_source={report.sections.get('title_source')}")
    print(f"writer_prompt={writer_call.prompt_id}@{writer_call.prompt_version}")
    print(f"writer_prompt_hash={writer_call.prompt_hash}")
    print(f"claim_count={len(report.claim_ids)}")
    print(f"research_gap_count={len(gaps)}")
    print(f"report_statement_count={len(report_statements)}")
    print("reader_visible_internal_ids=false")
    print("raw_sources_sent_to_writer=false")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
