from __future__ import annotations

import re

from app.reporting.professional import REQUIRED_REPORT_SECTIONS
from app.schemas import (
    AnalysisClaimV2,
    CompetitiveReport,
    ReportStatement,
    ResearchGap,
)


def evaluate_professional_report(
    *,
    report: CompetitiveReport,
    claims: list[AnalysisClaimV2],
    research_gaps: list[ResearchGap],
    report_statements: list[ReportStatement],
    known_evidence_ids: set[str],
) -> dict[str, dict[str, object]]:
    claim_ids = {item.id for item in claims}
    valid_claim_refs = {
        claim_id
        for claim_id in report.claim_ids
        if claim_id in claim_ids and f"[{claim_id}]" in report.markdown
    }
    section_hits = [
        section
        for section in REQUIRED_REPORT_SECTIONS
        if f"## {section}" in report.markdown
    ]
    gap_ids = {item.id for item in research_gaps}
    declared_gap_ids = {
        str(item) for item in report.sections.get("research_gap_ids", [])
    }
    valid_gap_refs = {
        gap_id
        for gap_id in declared_gap_ids
        if gap_id in gap_ids and f"[{gap_id}]" in report.markdown
    }
    duplicated_claims = [
        item.id
        for item in claims
        if item.claim_text and report.markdown.count(item.claim_text) > 1
    ]
    title_specific = bool(report.title.strip()) and report.title != "通用竞品分析报告"
    heading_aligned = report.markdown.startswith(f"# {report.title}")
    report_statements = [
        item for item in report_statements if item.report_id == report.id
    ]
    lines_before_index = report.markdown.split("## 结论引用索引", 1)[0].splitlines()
    referenced_line_indexes = {
        index
        for index, line in enumerate(lines_before_index)
        if "[" in line and "]" in line
    }
    mapped_line_indexes = {item.line_index for item in report_statements}
    valid_evidence_links = sum(
        1
        for item in report_statements
        if all(evidence_id in known_evidence_ids for evidence_id in item.evidence_ids)
    )
    claim_statement_count = sum(
        1 for item in report_statements if item.claim_ids
    )
    claim_statements_with_evidence = sum(
        1 for item in report_statements if item.claim_ids and item.evidence_ids
    )
    traced_claim_ids = {
        claim_id for item in report_statements for claim_id in item.claim_ids
    }
    traced_gap_ids = {
        gap_id for item in report_statements for gap_id in item.research_gap_ids
    }
    reader_markdown = report.markdown.split("## 结论引用索引", 1)[0]
    reader_markdown = re.sub(r"\[[^\]]+\]", "", reader_markdown)
    audit_phrases = ["现有证据表明", "根据现有资料", "根据现有证据"]
    coursework_phrases = ["本文首先", "本文将", "综上所述", "本次作业"]
    audit_phrase_count = sum(reader_markdown.count(item) for item in audit_phrases)
    coursework_phrase_count = sum(
        reader_markdown.count(item) for item in coursework_phrases
    )
    return {
        "writer_title_task_specific": _metric(
            title_specific and heading_aligned,
            title_specific and heading_aligned,
            f"title={report.title}; heading_aligned={heading_aligned}",
        ),
        "writer_required_section_coverage": _metric(
            _rate(len(section_hits), len(REQUIRED_REPORT_SECTIONS)),
            len(section_hits) == len(REQUIRED_REPORT_SECTIONS),
            f"covered={len(section_hits)}; total={len(REQUIRED_REPORT_SECTIONS)}",
        ),
        "writer_claim_reference_coverage": _metric(
            _rate(len(valid_claim_refs), len(report.claim_ids)),
            len(valid_claim_refs) == len(report.claim_ids),
            f"covered={len(valid_claim_refs)}; total={len(report.claim_ids)}",
        ),
        "writer_research_gap_disclosure_rate": _metric(
            _rate(len(valid_gap_refs), len(gap_ids)),
            not gap_ids or valid_gap_refs == gap_ids,
            f"covered={len(valid_gap_refs)}; total={len(gap_ids)}",
        ),
        "writer_repeated_claim_copy_rate": _metric(
            _rate(len(duplicated_claims), len(claims), empty=0.0),
            not duplicated_claims,
            f"duplicated_claim_ids={duplicated_claims}",
        ),
        "writer_report_statement_mapping_rate": _metric(
            _rate(
                len(referenced_line_indexes & mapped_line_indexes),
                len(referenced_line_indexes),
            ),
            referenced_line_indexes == mapped_line_indexes,
            (
                f"referenced_lines={len(referenced_line_indexes)}; "
                f"mapped_lines={len(mapped_line_indexes)}"
            ),
        ),
        "writer_report_statement_evidence_valid_rate": _metric(
            _rate(valid_evidence_links, len(report_statements)),
            bool(report_statements) and valid_evidence_links == len(report_statements),
            f"valid={valid_evidence_links}; total={len(report_statements)}",
        ),
        "writer_claim_statement_evidence_coverage": _metric(
            _rate(claim_statements_with_evidence, claim_statement_count),
            bool(claim_statement_count)
            and claim_statements_with_evidence == claim_statement_count,
            (
                f"covered={claim_statements_with_evidence}; "
                f"total={claim_statement_count}"
            ),
        ),
        "writer_reader_claim_trace_coverage": _metric(
            _rate(len(traced_claim_ids & set(report.claim_ids)), len(report.claim_ids)),
            traced_claim_ids == set(report.claim_ids),
            f"traced={len(traced_claim_ids)}; total={len(report.claim_ids)}",
        ),
        "writer_reader_gap_trace_coverage": _metric(
            _rate(len(traced_gap_ids & gap_ids), len(gap_ids)),
            traced_gap_ids == gap_ids,
            f"traced={len(traced_gap_ids)}; total={len(gap_ids)}",
        ),
        "writer_audit_phrase_count": _metric(
            audit_phrase_count,
            audit_phrase_count == 0,
            f"count={audit_phrase_count}; phrases={audit_phrases}",
        ),
        "writer_coursework_phrase_count": _metric(
            coursework_phrase_count,
            coursework_phrase_count == 0,
            f"count={coursework_phrase_count}; phrases={coursework_phrases}",
        ),
        "writer_reader_body_char_count": _metric(
            len(reader_markdown.strip()),
            len(reader_markdown.strip()) >= 1200,
            f"chars={len(reader_markdown.strip())}; minimum=1200",
        ),
    }


def _rate(numerator: int, denominator: int, *, empty: float = 1.0) -> float:
    if denominator <= 0:
        return empty
    return round(numerator / denominator, 4)


def _metric(value: object, passed: bool, details: str) -> dict[str, object]:
    return {"value": value, "passed": passed, "details": details}
