from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.schemas import (
    AnalysisClaim,
    AnalysisClaimV2,
    AnalysisTask,
    BriefAssessment,
    CitationCheck,
    ComparabilityNote,
    CompetitiveReport,
    CompetitorProfile,
    EvidenceCoverage,
    ReportStatement,
    ResearchGap,
)


REPORT_PROMPT_ID = "competitive_writer"
REPORT_PROMPT_VERSION = "2.1.1-candidate"
REQUIRED_REPORT_SECTIONS = [
    "执行摘要",
    "研究目标与决策背景",
    "竞品与解决路径",
    "核心维度对比",
    "成本、交付与采用条件",
    "风险、限制与不确定性",
    "后续研究缺口",
    "决策建议",
    "结论引用索引",
]


def resolve_report_title(
    task: AnalysisTask,
    brief: BriefAssessment | None = None,
) -> tuple[str, str]:
    """Resolve a delivery title from task semantics, not the platform name."""

    preferred = _clean_title(task.preferred_title)
    if preferred:
        return _ensure_report_suffix(preferred), "preferred_title"

    subject = _clean_title(task.report_subject)
    if subject:
        return _ensure_competitive_report_suffix(subject), "report_subject"

    industry = _localize_industry(
        (brief.industry if brief else "") or task.industry
    )
    if industry:
        return f"{industry}竞品分析报告", "industry"

    competitors = [item.strip() for item in task.competitors if item.strip()]
    if competitors:
        subject = "、".join(competitors[:3])
        return f"{subject}竞品分析报告", "competitors"

    return "当前分析任务竞品分析报告", "fallback"


def build_professional_mock_report(
    *,
    task_id: str,
    task: AnalysisTask,
    brief: BriefAssessment,
    profiles: list[CompetitorProfile],
    coverage: list[EvidenceCoverage],
    comparability_notes: list[ComparabilityNote],
    claims_v2: list[AnalysisClaimV2],
    legacy_claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    research_gaps: list[ResearchGap],
    output_language: str,
) -> CompetitiveReport:
    title, title_source = resolve_report_title(task, brief)
    supported_claim_ids = {
        item.claim_id
        for item in citation_checks
        if item.status in {"supported", "weak"}
    }
    claims = [
        item for item in claims_v2 if item.id in supported_claim_ids
    ] or claims_v2
    claim_by_dimension: dict[str, list[AnalysisClaimV2]] = defaultdict(list)
    for claim in claims:
        claim_by_dimension[claim.dimension].append(claim)

    claim_ids = [item.id for item in claims]
    competitors = "、".join(item.name for item in profiles)
    path_lines = "\n".join(
        (
            f"- **{item.name}**：{_strip_terminal_punctuation(item.represented_path or item.selection_reason)}；"
            f"交付方式为{_strip_terminal_punctuation(item.delivery_model or '待进一步确认')}。 "
            f"[{item.id}]"
        )
        for item in profiles
    )
    if not path_lines:
        path_lines = "- 当前结构化资料尚未形成竞品路径画像。"

    executive_claims = sorted(
        claims,
        key=lambda item: (
            item.claim_type in {"comparison", "risk", "recommendation"},
            item.confidence,
        ),
        reverse=True,
    )[:3]
    executive_lines = "\n".join(
        (
            f"- **{_dimension_label(item.dimension)}**：{item.decision_impact} "
            f"[{item.id}]"
        )
        for item in executive_claims
    ) or "- 当前没有通过引用检查的核心结论。"

    dimension_lines: list[str] = []
    for dimension in _ordered_dimensions(claim_by_dimension):
        label = _dimension_label(dimension)
        dimension_lines.append(f"### {label}")
        for claim in claim_by_dimension[dimension]:
            dimension_lines.append(
                f"- {_report_style_claim(claim.claim_text)} [{claim.id}]\n"
                f"  - 决策含义：{claim.decision_impact} [{claim.id}]\n"
                f"  - 限制：{claim.uncertainty} [{claim.id}]"
            )
    if not dimension_lines:
        dimension_lines.append("当前没有可进入报告的专业分析结论。")

    cost_claims = [
        item
        for item in claims
        if item.dimension in {"pricing", "ecosystem", "customer", "positioning"}
    ]
    cost_lines = "\n".join(
        f"- {item.decision_impact} [{item.id}]" for item in cost_claims[:4]
    ) or "- 当前证据不足以形成同口径的成本与采用判断。"

    risk_claims = [
        item
        for item in claims
        if item.claim_type == "risk" or item.dimension == "risk"
    ]
    risk_lines = "\n".join(
        (
            f"- 风险判断依据：{_strip_terminal_punctuation(item.reasoning_summary)}；"
            f"决策影响：{_strip_terminal_punctuation(item.decision_impact)}；"
            f"限制：{_strip_terminal_punctuation(item.uncertainty)} [{item.id}]"
        )
        for item in risk_claims
    ) or "- 当前风险资料覆盖不完整，不能据此断言某一方案风险更低。"

    incomparable = [item for item in comparability_notes if not item.comparable]
    comparability_lines = "\n".join(
        f"- **{_dimension_label(item.dimension)}**：{item.basis}；限制：{item.limitations} [{item.id}]"
        for item in incomparable
    ) or "- 当前未发现需要禁止直接比较的维度。"

    gap_lines = "\n".join(
        (
            f"- **{item.missing_information}** [{item.id}]\n"
            f"  - 阻塞决策：{item.decision_blocked} [{item.id}]\n"
            f"  - 停止条件：{item.stop_condition} [{item.id}]"
        )
        for item in research_gaps
    ) or "- 当前没有已登记的高价值研究缺口。"

    recommendation_claims = [
        item for item in claims if item.claim_type == "recommendation"
    ]
    if recommendation_claims:
        recommendation_lines = "\n".join(
            f"- {item.claim_text}；验证要求：{item.uncertainty} [{item.id}]"
            for item in recommendation_claims
        )
    else:
        recommendation_lines = "\n".join(
            f"- {item.decision_impact} [{item.id}]"
            for item in executive_claims
        ) or "- 在关键研究缺口关闭前，暂不形成正式选型建议。"

    coverage_counts: dict[str, int] = defaultdict(int)
    for item in coverage:
        coverage_counts[str(item.status)] += 1
    coverage_summary = "、".join(
        f"{key}={value}" for key, value in sorted(coverage_counts.items())
    ) or "无覆盖记录"

    claim_index = "\n".join(
        f"- [{item.id}] {_dimension_label(item.dimension)} · {item.claim_type}"
        for item in claims
    )
    gap_index = "\n".join(f"- [{item.id}] {item.missing_information}" for item in research_gaps)

    markdown = f"""# {title}

## 执行摘要

本报告围绕“{brief.decision_question}”整理现有证据，覆盖 {competitors or '当前研究对象'}。以下判断均来自已经结构化并通过引用检查的 AnalysisClaim（分析结论），不把资料未提及解释为产品不具备。

{executive_lines}

## 研究目标与决策背景

- 行业：{_localize_industry(brief.industry or task.industry) or '待确认'}
- 用户原始需求：{task.query}
- 当前决策问题：{brief.decision_question}
- 重点维度：{'、'.join(brief.selected_dimensions) or '待确认'}
- 资料充分性：{'足以开展初步分析' if brief.sufficient_for_analysis else '仅足以形成带限制的初步分析'}
- 证据覆盖摘要：{coverage_summary}

## 竞品与解决路径

{path_lines}

这些对象代表的产品形态、交付责任和采用路径并不完全相同，因此不能用功能数量或单一价格直接排序。 {' '.join(f'[{item.id}]' for item in profiles)}

## 核心维度对比

{chr(10).join(dimension_lines)}

## 成本、交付与采用条件

{cost_lines}

## 风险、限制与不确定性

{risk_lines}

### 可比性限制

{comparability_lines}

## 后续研究缺口

{gap_lines}

## 决策建议

以下内容是基于现有证据的条件性建议；若引用结论只达到 weak（弱支持）或关键缺口尚未关闭，应先完成验证再做最终选择。

{recommendation_lines}

## 结论引用索引

{claim_index}

### ResearchGap（研究缺口）索引

{gap_index or '- 无'}
"""
    return CompetitiveReport(
        schema_version="v2",
        id=f"report_professional_{_slug(task_id)}",
        task_id=task_id,
        title=title,
        markdown=markdown,
        claim_ids=claim_ids,
        created_by_agent_run_id="mock_professional_writer",
        sections={
            "report_version": REPORT_PROMPT_VERSION,
            "prompt_id": REPORT_PROMPT_ID,
            "title_source": title_source,
            "decision_question": brief.decision_question,
            "section_order": REQUIRED_REPORT_SECTIONS,
            "research_gap_ids": [item.id for item in research_gaps],
            "claim_count": len(claim_ids),
            "research_gap_count": len(research_gaps),
            "output_language": output_language,
        },
        metadata={
            "generation_mode": "mock_llm",
            "source": "build_professional_mock_report",
            "output_language": output_language,
            "writer_prompt_id": REPORT_PROMPT_ID,
            "writer_prompt_version": REPORT_PROMPT_VERSION,
        },
    )


def build_report_statements(
    *,
    report: CompetitiveReport,
    claims_v2: list[AnalysisClaimV2],
    legacy_claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    profiles: list[CompetitorProfile],
    comparability_notes: list[ComparabilityNote],
    research_gaps: list[ResearchGap],
) -> list[ReportStatement]:
    """Turn inline audit references into a stable report-to-evidence map."""

    claim_by_id = {item.id: item for item in legacy_claims}
    claim_v2_by_id = {item.id: item for item in claims_v2}
    check_by_claim_id = {item.claim_id: item for item in citation_checks}
    profile_by_id = {item.id: item for item in profiles}
    comparability_by_id = {item.id: item for item in comparability_notes}
    gap_by_id = {item.id: item for item in research_gaps}
    known_ids = (
        set(claim_by_id)
        | set(claim_v2_by_id)
        | set(profile_by_id)
        | set(comparability_by_id)
        | set(gap_by_id)
    )
    status_rank = {
        "pending": 0,
        "supported": 1,
        "weak": 2,
        "unsupported": 3,
        "missing_evidence": 4,
        "invalid_evidence": 5,
    }
    statements: list[ReportStatement] = []
    section = ""

    for line_index, raw_line in enumerate(report.markdown.splitlines()):
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            if section == "结论引用索引":
                break
            continue
        if not stripped or stripped.startswith("#"):
            continue

        refs = [
            item
            for item in re.findall(r"\[([^\]]+)\]", stripped)
            if item in known_ids
        ]
        if not refs:
            continue

        claim_ids = _unique(
            item for item in refs if item in claim_by_id or item in claim_v2_by_id
        )
        gap_ids = _unique(item for item in refs if item in gap_by_id)
        profile_ids = _unique(item for item in refs if item in profile_by_id)
        comparability_ids = _unique(
            item for item in refs if item in comparability_by_id
        )
        evidence_ids: list[str] = []
        confidences: list[float] = []
        statuses: list[str] = []
        for claim_id in claim_ids:
            claim = claim_by_id.get(claim_id)
            claim_v2 = claim_v2_by_id.get(claim_id)
            if claim:
                evidence_ids.extend(claim.evidence_ids)
                confidences.append(claim.confidence)
            elif claim_v2:
                evidence_ids.extend(claim_v2.evidence_ids)
                confidences.append(claim_v2.confidence)
            check = check_by_claim_id.get(claim_id)
            statuses.append(str(check.status if check else "pending"))
        for profile_id in profile_ids:
            profile = profile_by_id[profile_id]
            evidence_ids.extend(profile.evidence_ids)
            confidences.append(profile.confidence)
        for gap_id in gap_ids:
            evidence_ids.extend(gap_by_id[gap_id].related_evidence_ids)

        if claim_ids:
            statement_kind = "claim"
        elif gap_ids:
            statement_kind = "research_gap"
        elif profile_ids:
            statement_kind = "profile"
        else:
            statement_kind = "comparability"
        citation_status = max(
            statuses or ["pending"],
            key=lambda item: status_rank.get(item, 0),
        )
        text = _clean_statement_text(stripped)
        if not text:
            continue
        statements.append(
            ReportStatement(
                schema_version="v1",
                id=f"stmt_{_slug(report.id)}_{line_index:03d}",
                task_id=report.task_id,
                report_id=report.id,
                section=section,
                line_index=line_index,
                statement_kind=statement_kind,
                text=text,
                claim_ids=claim_ids,
                evidence_ids=_unique(evidence_ids),
                research_gap_ids=gap_ids,
                supporting_artifact_ids=_unique(refs),
                citation_status=citation_status,
                confidence=min(confidences) if confidences else 0.5,
                metadata={
                    "profile_ids": profile_ids,
                    "comparability_note_ids": comparability_ids,
                    "reference_count": len(refs),
                },
            )
        )
    return statements


def validate_professional_report(
    report: CompetitiveReport,
    *,
    task: AnalysisTask,
    brief: BriefAssessment,
    known_claim_ids: set[str],
    known_research_gap_ids: set[str],
) -> None:
    expected_title, _ = resolve_report_title(task, brief)
    errors: list[str] = []
    if report.title != expected_title:
        errors.append(
            f"报告标题与任务不一致: expected={expected_title}; actual={report.title}"
        )
    if report.title == "通用竞品分析报告":
        errors.append("专业报告不得使用写死的通用标题")
    if not report.markdown.startswith(f"# {report.title}"):
        errors.append("Markdown 一级标题与 report.title 不一致")
    missing_sections = [
        section
        for section in REQUIRED_REPORT_SECTIONS
        if f"## {section}" not in report.markdown
    ]
    if missing_sections:
        errors.append("缺少专业报告章节: " + ", ".join(missing_sections))
    unknown_claim_ids = sorted(set(report.claim_ids) - known_claim_ids)
    if unknown_claim_ids:
        errors.append("报告引用未知 claim_id: " + ", ".join(unknown_claim_ids))
    missing_claim_refs = [
        claim_id
        for claim_id in report.claim_ids
        if f"[{claim_id}]" not in report.markdown
    ]
    if missing_claim_refs:
        errors.append("报告正文缺少 claim_id: " + ", ".join(missing_claim_refs))
    declared_gaps = {
        str(item) for item in report.sections.get("research_gap_ids", [])
    }
    unknown_gaps = sorted(declared_gaps - known_research_gap_ids)
    if unknown_gaps:
        errors.append("报告引用未知 ResearchGap: " + ", ".join(unknown_gaps))
    missing_gap_refs = sorted(
        gap_id for gap_id in declared_gaps if f"[{gap_id}]" not in report.markdown
    )
    if missing_gap_refs:
        errors.append("报告正文缺少 ResearchGap 引用: " + ", ".join(missing_gap_refs))
    if errors:
        raise ValueError("；".join(errors))


def _clean_title(value: str) -> str:
    value = re.sub(r"[\r\n#]+", " ", value or "")
    return re.sub(r"\s+", " ", value).strip(" -—：:")[:80]


def _ensure_report_suffix(value: str) -> str:
    return value if value.endswith("报告") else value + "报告"


def _ensure_competitive_report_suffix(value: str) -> str:
    if value.endswith("竞品分析报告"):
        return value
    if value.endswith("报告"):
        return value
    if value.endswith("竞品分析"):
        return value + "报告"
    return value + "竞品分析报告"


def _localize_industry(value: str) -> str:
    normalized = (value or "").strip()
    mapping = {
        "online education": "在线教育",
        "education technology": "教育科技",
        "software as a service": "SaaS（软件即服务）",
    }
    return mapping.get(normalized.lower(), normalized)


def _ordered_dimensions(grouped: dict[str, list[AnalysisClaimV2]]) -> list[str]:
    order = ["positioning", "customer", "feature", "ecosystem", "pricing", "risk"]
    return [item for item in order if item in grouped] + sorted(
        set(grouped) - set(order)
    )


def _dimension_label(value: str) -> str:
    return {
        "positioning": "产品定位与解决路径",
        "customer": "目标客户与适用场景",
        "feature": "核心能力",
        "ecosystem": "生态与集成",
        "pricing": "定价与总体成本",
        "risk": "风险与责任边界",
        "market": "市场信息",
    }.get(value, value)


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_")


def _strip_terminal_punctuation(value: str) -> str:
    return (value or "").rstrip("。；;，, ")


def _report_style_claim(value: str) -> str:
    """Remove audit-like lead-ins while preserving the governed claim meaning."""

    text = (value or "").strip()
    for prefix in ("现有证据表明，", "现有资料表明，", "根据现有证据，"):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _clean_statement_text(value: str) -> str:
    text = re.sub(r"\[([^\]]+)\]", "", value)
    text = re.sub(r"^\s*-\s*", "", text)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _unique(values) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item)))
