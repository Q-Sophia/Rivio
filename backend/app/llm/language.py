from __future__ import annotations

import re
from typing import Any


ZH_CN = "zh-CN"
_CHINESE_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def contains_chinese(value: Any) -> bool:
    return bool(_CHINESE_CHARACTER.search(str(value or "")))


def validate_structured_output_language(
    output_schema: str,
    raw_output: dict[str, Any],
    *,
    output_language: str = ZH_CN,
) -> list[str]:
    """Validate user-facing business text while leaving stable ids untouched."""

    if output_language.lower() != ZH_CN.lower():
        return []

    errors: list[str] = []
    if output_schema == "ProductCard[]":
        items = raw_output.get("items", [])
        if not items:
            return ["ProductCard[] 没有可检查的条目"]
        for index, item in enumerate(items):
            card_id = item.get("id", f"items[{index}]")
            required_text = {
                "positioning": item.get("positioning", ""),
                "pricing_summary": item.get("pricing_summary", ""),
            }
            for field, value in required_text.items():
                if not contains_chinese(value):
                    errors.append(f"{card_id}.{field} 不是简体中文业务文本")
            for field in ("target_users", "core_features", "strengths", "weaknesses"):
                values = item.get(field, [])
                if not values or any(not contains_chinese(value) for value in values):
                    errors.append(f"{card_id}.{field} 存在非中文或空内容")
    elif output_schema == "AnalysisClaim[]":
        items = raw_output.get("items", [])
        if not items:
            return ["AnalysisClaim[] 没有可检查的条目"]
        for index, item in enumerate(items):
            claim_id = item.get("id", f"items[{index}]")
            if not contains_chinese(item.get("claim_text", "")):
                errors.append(f"{claim_id}.claim_text 不是简体中文业务文本")
    elif output_schema == "CompetitiveReport":
        item = raw_output.get("item", {})
        report_id = item.get("id", "item")
        if not contains_chinese(item.get("title", "")):
            errors.append(f"{report_id}.title 不是简体中文业务文本")
        if not contains_chinese(item.get("markdown", "")):
            errors.append(f"{report_id}.markdown 不是简体中文业务文本")
    elif output_schema in {
        "CompetitiveAnalysisPortfolioV2",
        "AnalystBriefProfilesStage",
        "AnalystClaimsStage",
        "AnalystAssessmentStage",
    }:
        item = raw_output.get("item", {})
        if not item:
            return [f"{output_schema} 没有可检查的对象"]
        for profile in item.get("competitor_profiles", []):
            profile_id = profile.get("id", "competitor_profile")
            for field in ("selection_reason", "represented_path", "value_proposition"):
                if not contains_chinese(profile.get(field, "")):
                    errors.append(f"{profile_id}.{field} 不是简体中文业务文本")
        for question in item.get("key_intelligence_questions", []):
            question_id = question.get("id", "kiq")
            if not contains_chinese(question.get("question", "")):
                errors.append(f"{question_id}.question 不是简体中文业务文本")
        for claim in item.get("items", []):
            claim_id = claim.get("id", "claim_v2")
            for field in (
                "claim_text",
                "reasoning_summary",
                "uncertainty",
                "decision_impact",
            ):
                if not contains_chinese(claim.get(field, "")):
                    errors.append(f"{claim_id}.{field} 不是简体中文业务文本")
        for gap in item.get("research_gaps", []):
            gap_id = gap.get("id", "research_gap")
            if not contains_chinese(gap.get("missing_information", "")):
                errors.append(f"{gap_id}.missing_information 不是简体中文业务文本")
        for assessment in item.get("dimension_assessments", []):
            assessment_id = (
                f"{assessment.get('competitor', '')}/"
                f"{assessment.get('dimension_id', '')}"
            )
            for field in ("reasoning", "decision_impact"):
                if not contains_chinese(assessment.get(field, "")):
                    errors.append(
                        f"{assessment_id}.{field} 不是简体中文业务文本"
                    )
        for insight in item.get("insights", []):
            if not contains_chinese(insight.get("summary", "")):
                errors.append("AssessmentInsight.summary 不是简体中文业务文本")
    elif output_schema == "AnalysisTaskDraft":
        item = raw_output.get("item", {})
        if not item:
            return ["AnalysisTaskDraft 没有可检查的对象"]
        for field in ("request_text", "decision_question", "industry", "report_subject"):
            value = item.get(field, "")
            if value and not contains_chinese(value):
                errors.append(f"AnalysisTaskDraft.{field} 不是简体中文业务文本")
    return errors
