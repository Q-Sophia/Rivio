from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.frameworks import (
    DEFAULT_FRAMEWORK_ID,
    DEFAULT_FRAMEWORK_VERSION,
    FrameworkRegistry,
    get_framework_registry,
)
from app.schemas import (
    AnalysisAssessment,
    AnalysisAssessmentStatus,
    AnalysisTask,
    AnalystAssessmentStage,
    AnalystResearchGapDraft,
    AssessmentInsight,
    CompletionCriterionEvaluation,
    CompletionCriterionStatus,
    DimensionAssessment,
    DimensionAssessmentStatus,
    FrameworkDefinition,
    ResearchGap,
    ResearchGapImpact,
    ResearchGapOrigin,
    ResearchGapType,
    ResearchTask,
    SourceEvidence,
    TaskPriority,
)


@dataclass(frozen=True)
class FrameworkAssessmentBinding:
    framework: FrameworkDefinition
    scope: list[dict[str, Any]]
    binding_mode: str


def _key(value: Any) -> str:
    return "".join(str(value or "").strip().casefold().replace("-", "_").split())


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def completion_criterion_id(dimension_id: str, criterion: str) -> str:
    """Derive a stable protocol ID without changing Framework business YAML."""

    canonical = f"{_key(dimension_id)}\x00{str(criterion).strip()}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"criterion_{digest}"


def _criterion_refs(dimension_id: str, criteria: list[str]) -> list[dict[str, str]]:
    return [
        {
            "criterion_id": completion_criterion_id(dimension_id, criterion),
            "text": criterion,
        }
        for criterion in criteria
    ]


def _dimension_aliases(dimension) -> set[str]:
    return {
        _key(value)
        for value in (
            dimension.dimension_id,
            dimension.label,
            dimension.evidence_dimension,
            *dimension.aliases,
        )
    }


def _dimension_index(framework: FrameworkDefinition) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dimension in framework.dimensions:
        for alias in _dimension_aliases(dimension):
            result[alias] = dimension
    return result


def resolve_framework_assessment_binding(
    *,
    task: AnalysisTask,
    research_tasks: list[ResearchTask],
    registry: FrameworkRegistry | None = None,
    framework_payload: dict[str, Any] | None = None,
) -> FrameworkAssessmentBinding:
    registry = registry or get_framework_registry()
    versioned = [item for item in research_tasks if item.framework_id]
    if versioned:
        references = {
            (
                item.framework_id,
                item.framework_version,
                item.framework_content_hash,
            )
            for item in versioned
        }
        if len(references) != 1:
            raise ValueError("一次 Analyst assessment 不能混用多个 Framework 版本")
        framework_id, version, content_hash = next(iter(references))
        framework = registry.load_framework(framework_id, version)
        if framework.content_hash != content_hash:
            raise ValueError("ResearchTask 固定的 Framework hash 与 Registry 内容不一致")
        binding_mode = "research_task_v2"
    elif framework_payload:
        framework = FrameworkDefinition(**framework_payload)
        binding_mode = "injected"
    else:
        framework = registry.load_framework(
            DEFAULT_FRAMEWORK_ID,
            DEFAULT_FRAMEWORK_VERSION,
        )
        binding_mode = "legacy_default"

    by_id = {item.dimension_id: item for item in framework.dimensions}
    aliases = _dimension_index(framework)
    grouped: dict[tuple[str, str], list[str]] = {}
    if versioned:
        for research_task in versioned:
            if research_task.framework_dimension_id not in by_id:
                raise ValueError(
                    "ResearchTask 引用了 Framework 中不存在的 dimension: "
                    + research_task.framework_dimension_id
                )
            key = (research_task.competitor, research_task.framework_dimension_id)
            grouped.setdefault(key, []).append(research_task.id)
    elif research_tasks:
        for research_task in research_tasks:
            dimension = aliases.get(_key(research_task.dimension))
            if dimension is None:
                continue
            grouped.setdefault(
                (research_task.competitor, dimension.dimension_id), []
            ).append(research_task.id)
    else:
        selected_dimensions = []
        if task.focus_areas:
            for focus in task.focus_areas:
                dimension = aliases.get(_key(focus))
                if dimension and dimension.dimension_id not in {
                    item.dimension_id for item in selected_dimensions
                }:
                    selected_dimensions.append(dimension)
        if not selected_dimensions:
            selected_dimensions = [
                by_id[item] for item in framework.default_dimension_ids
            ]
        for competitor in task.competitors:
            for dimension in selected_dimensions:
                grouped[(competitor, dimension.dimension_id)] = []

    scope = []
    for (competitor, dimension_id), research_task_ids in sorted(grouped.items()):
        dimension = by_id[dimension_id]
        scope.append(
            {
                "competitor": competitor,
                "dimension_id": dimension.dimension_id,
                "dimension_label": dimension.label,
                "evidence_dimension": dimension.evidence_dimension,
                "research_task_ids": sorted(set(research_task_ids)),
                "required_facts": list(dimension.required_facts),
                "completion_criteria": list(dimension.completion_criteria),
                "completion_criteria_refs": _criterion_refs(
                    dimension.dimension_id,
                    list(dimension.completion_criteria),
                ),
                "preferred_source_types": list(dimension.preferred_source_types),
                "query_templates": list(dimension.query_templates),
            }
        )
    if not scope:
        raise ValueError("当前任务无法映射到 Framework assessment scope")
    return FrameworkAssessmentBinding(
        framework=framework,
        scope=scope,
        binding_mode=binding_mode,
    )


def compute_evidence_batch_hash(evidence: list[SourceEvidence]) -> str:
    payload = [
        item.model_dump(mode="json")
        for item in sorted(evidence, key=lambda value: value.id)
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_analyst_assessment_stage(
    *,
    stage: AnalystAssessmentStage,
    framework: FrameworkDefinition,
    scope: list[dict[str, Any]],
    evidence: list[SourceEvidence],
) -> None:
    scope_by_pair = {
        (str(item["competitor"]), str(item["dimension_id"])): item
        for item in scope
    }
    actual_pairs = [
        (item.competitor, item.dimension_id)
        for item in stage.dimension_assessments
    ]
    if len(actual_pairs) != len(set(actual_pairs)):
        raise ValueError("Analyst assessment 包含重复 competitor/dimension")
    if set(actual_pairs) != set(scope_by_pair):
        raise ValueError("Analyst assessment 未完整覆盖 Framework assessment scope")

    evidence_by_id = {item.id: item for item in evidence}
    dimension_ids = {item.dimension_id for item in framework.dimensions}
    for item in stage.dimension_assessments:
        expected = scope_by_pair[(item.competitor, item.dimension_id)]
        required_facts = set(expected["required_facts"])
        covered_facts = set(item.covered_facts)
        missing_facts = set(item.missing_facts)
        if covered_facts & missing_facts:
            raise ValueError("Analyst covered_facts 与 missing_facts 重叠")
        if covered_facts | missing_facts != required_facts:
            raise ValueError("Analyst fact coverage 未严格对应 Framework required_facts")
        criterion_refs = list(
            expected.get("completion_criteria_refs")
            or _criterion_refs(item.dimension_id, expected["completion_criteria"])
        )
        criterion_text_by_id = {
            str(ref["criterion_id"]): str(ref["text"])
            for ref in criterion_refs
        }
        if item.completion_criteria_evaluations:
            evaluation_ids = [
                value.criterion_id
                for value in item.completion_criteria_evaluations
            ]
            if len(evaluation_ids) != len(set(evaluation_ids)):
                raise ValueError("Analyst completion criterion ID 重复")
            if set(evaluation_ids) != set(criterion_text_by_id):
                raise ValueError(
                    "Analyst criterion ID coverage 未严格对应 Framework completion_criteria"
                )
            status_by_id = {
                value.criterion_id: _value(value.status)
                for value in item.completion_criteria_evaluations
            }
            item.completion_criteria_met = [
                ref["text"]
                for ref in criterion_refs
                if status_by_id[ref["criterion_id"]]
                == CompletionCriterionStatus.MET.value
            ]
            item.completion_criteria_unmet = [
                ref["text"]
                for ref in criterion_refs
                if status_by_id[ref["criterion_id"]]
                == CompletionCriterionStatus.UNMET.value
            ]
            item.completion_criteria_not_applicable = [
                ref["text"]
                for ref in criterion_refs
                if status_by_id[ref["criterion_id"]]
                == CompletionCriterionStatus.NOT_APPLICABLE.value
            ]
        else:
            criteria = set(expected["completion_criteria"])
            met = set(item.completion_criteria_met)
            unmet = set(item.completion_criteria_unmet)
            not_applicable = set(item.completion_criteria_not_applicable)
            if (
                met & unmet
                or met & not_applicable
                or unmet & not_applicable
                or met | unmet | not_applicable != criteria
            ):
                raise ValueError(
                    "Analyst criteria coverage 未严格对应 Framework completion_criteria"
                )
            status_by_text = {
                **{text: CompletionCriterionStatus.MET for text in met},
                **{text: CompletionCriterionStatus.UNMET for text in unmet},
                **{
                    text: CompletionCriterionStatus.NOT_APPLICABLE
                    for text in not_applicable
                },
            }
            item.completion_criteria_evaluations = [
                CompletionCriterionEvaluation(
                    criterion_id=ref["criterion_id"],
                    status=status_by_text[ref["text"]],
                )
                for ref in criterion_refs
            ]
        for evidence_id in item.evidence_ids:
            candidate = evidence_by_id.get(evidence_id)
            if candidate is None:
                raise ValueError(f"Analyst 引用了未授权 Evidence: {evidence_id}")
            if candidate.competitor != item.competitor:
                raise ValueError(
                    f"dimension_assessment competitor={item.competitor} 引用了 "
                    f"Evidence {evidence_id}，但该 Evidence 的精确 competitor="
                    f"{candidate.competitor}；竞品标识不得拆分、合并或近似匹配"
                )
            if str(candidate.dimension) != str(expected["evidence_dimension"]):
                raise ValueError(
                    f"dimension_assessment dimension={item.dimension_id} 引用了 "
                    f"Evidence {evidence_id}，但该 Evidence dimension="
                    f"{candidate.dimension}，预期 evidence_dimension="
                    f"{expected['evidence_dimension']}"
                )
        if item.status == DimensionAssessmentStatus.COVERED.value and (
            item.missing_facts or item.completion_criteria_unmet
        ):
            raise ValueError("COVERED assessment 仍有缺失事实或未满足标准")
        if item.status == DimensionAssessmentStatus.MISSING.value and item.evidence_ids:
            raise ValueError("MISSING assessment 不得引用支持性 Evidence")

    known_competitors = {str(item["competitor"]) for item in scope}
    known_evidence_ids = set(evidence_by_id)
    framework_dimension_by_id = {
        item.dimension_id: item for item in framework.dimensions
    }
    scoped_pairs = set(scope_by_pair)
    for insight in stage.insights:
        if insight.dimension_id not in dimension_ids:
            raise ValueError("AssessmentInsight 引用了未知 Framework dimension")
        if not set(insight.competitors) <= known_competitors:
            raise ValueError("AssessmentInsight 引用了 scope 外竞品")
        if not set(insight.evidence_ids) <= known_evidence_ids:
            raise ValueError("AssessmentInsight 引用了未授权 Evidence")
        dimension = framework_dimension_by_id[insight.dimension_id]
        if not any(
            pair[1] == insight.dimension_id for pair in scoped_pairs
        ):
            raise ValueError("AssessmentInsight 引用了 assessment scope 外维度")
        for evidence_id in insight.evidence_ids:
            candidate = evidence_by_id[evidence_id]
            if str(candidate.dimension) != str(dimension.evidence_dimension):
                raise ValueError(
                    f"AssessmentInsight dimension={insight.dimension_id} 引用了 "
                    f"Evidence {evidence_id}，但该 Evidence dimension="
                    f"{candidate.dimension}，预期 evidence_dimension="
                    f"{dimension.evidence_dimension}"
                )
            if insight.competitors and candidate.competitor not in insight.competitors:
                raise ValueError(
                    f"AssessmentInsight competitors={insight.competitors} 引用了 "
                    f"Evidence {evidence_id}，但该 Evidence 的精确 competitor="
                    f"{candidate.competitor}；若 insight 未明确评估该对象则移除该 "
                    "Evidence，竞品标识不得拆分、合并或近似匹配"
                )
    for gap in stage.research_gaps:
        if gap.dimension_id not in dimension_ids:
            raise ValueError("ResearchGap 引用了未知 Framework dimension")
        if not set(gap.competitors) <= known_competitors:
            raise ValueError("ResearchGap 引用了 scope 外竞品")
        if gap.competitors and any(
            (competitor, gap.dimension_id) not in scoped_pairs
            for competitor in gap.competitors
        ):
            raise ValueError("ResearchGap 引用了 assessment scope 外组合")
        if not gap.competitors and not any(
            pair[1] == gap.dimension_id for pair in scoped_pairs
        ):
            raise ValueError("ResearchGap 引用了 assessment scope 外维度")
        dimension = next(
            item for item in framework.dimensions if item.dimension_id == gap.dimension_id
        )
        if not set(gap.missing_facts) <= set(dimension.required_facts):
            raise ValueError("ResearchGap missing_facts 不属于 Framework required_facts")


def _stable_id(prefix: str, *values: Any) -> str:
    raw = "|".join(str(value) for value in values)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _render_queries(templates: list[str], competitor: str, label: str, query: str) -> list[str]:
    return [
        template.format(
            competitor=competitor,
            dimension_label=label,
            decision_question=query,
        )
        for template in templates
    ]


def materialize_analysis_assessment(
    *,
    stage: AnalystAssessmentStage,
    binding: FrameworkAssessmentBinding,
    evidence: list[SourceEvidence],
    task: AnalysisTask,
    pipeline_id: str,
    assessment_round: int,
    analyst_agent_run_id: str,
    evidence_batch_hash: str | None = None,
) -> AnalysisAssessment:
    if stage.task_id != task.id:
        raise ValueError("Analyst assessment task_id 与 AnalysisTask 不一致")
    validate_analyst_assessment_stage(
        stage=stage,
        framework=binding.framework,
        scope=binding.scope,
        evidence=evidence,
    )
    batch_hash = evidence_batch_hash or compute_evidence_batch_hash(evidence)
    assessment_id = _stable_id(
        "assessment",
        pipeline_id,
        assessment_round,
        binding.framework.content_hash,
        batch_hash,
    )
    scope_by_pair = {
        (str(item["competitor"]), str(item["dimension_id"])): item
        for item in binding.scope
    }
    assessments: list[DimensionAssessment] = []
    for draft in stage.dimension_assessments:
        scope_item = scope_by_pair[(draft.competitor, draft.dimension_id)]
        fact_count = len(scope_item["required_facts"])
        score = len(draft.covered_facts) / fact_count if fact_count else 1.0
        if draft.status == DimensionAssessmentStatus.NOT_APPLICABLE.value:
            status = DimensionAssessmentStatus.NOT_APPLICABLE
        elif draft.status == DimensionAssessmentStatus.CONFLICTING.value:
            status = DimensionAssessmentStatus.CONFLICTING
        elif not draft.covered_facts:
            status = DimensionAssessmentStatus.MISSING
        elif not draft.missing_facts and not draft.completion_criteria_unmet:
            status = DimensionAssessmentStatus.COVERED
        else:
            status = DimensionAssessmentStatus.PARTIAL
        assessments.append(
            DimensionAssessment(
                id=_stable_id(
                    "dimensionassessment",
                    assessment_id,
                    draft.competitor,
                    draft.dimension_id,
                ),
                task_id=task.id,
                research_task_ids=scope_item["research_task_ids"],
                dimension_id=draft.dimension_id,
                evidence_dimension=scope_item["evidence_dimension"],
                competitor=draft.competitor,
                status=status,
                coverage_score=round(score, 4),
                covered_facts=draft.covered_facts,
                missing_facts=draft.missing_facts,
                evidence_ids=draft.evidence_ids,
                completion_criteria_evaluations=(
                    draft.completion_criteria_evaluations
                ),
                completion_criteria_met=draft.completion_criteria_met,
                completion_criteria_unmet=draft.completion_criteria_unmet,
                completion_criteria_not_applicable=(
                    draft.completion_criteria_not_applicable
                ),
                reasoning=draft.reasoning,
                decision_impact=draft.decision_impact,
            )
        )

    dimensions = {
        item.dimension_id: item for item in binding.framework.dimensions
    }
    gap_drafts = list(stage.research_gaps)
    covered_gap_pairs = {
        (competitor, gap.dimension_id)
        for gap in gap_drafts
        for competitor in gap.competitors
    }
    for assessment in assessments:
        pair = (assessment.competitor, assessment.dimension_id)
        if assessment.missing_facts and pair not in covered_gap_pairs:
            dimension = dimensions[assessment.dimension_id]
            gap_drafts.append(
                AnalystResearchGapDraft(
                    dimension_id=assessment.dimension_id,
                    competitors=[assessment.competitor],
                    gap_type=ResearchGapType.MISSING_FACT,
                    impact=ResearchGapImpact.MEDIUM,
                    missing_facts=assessment.missing_facts,
                    missing_information="；".join(assessment.missing_facts),
                    why_existing_evidence_is_insufficient=assessment.reasoning,
                    suggested_queries=_render_queries(
                        dimension.query_templates,
                        assessment.competitor,
                        dimension.label,
                        task.query,
                    ),
                    preferred_source_types=dimension.preferred_source_types,
                    decision_blocked=assessment.decision_impact,
                    stop_condition="；".join(dimension.completion_criteria),
                )
            )

    impact_priority = {
        ResearchGapImpact.LOW.value: TaskPriority.LOW,
        ResearchGapImpact.MEDIUM.value: TaskPriority.MEDIUM,
        ResearchGapImpact.HIGH.value: TaskPriority.HIGH,
        ResearchGapImpact.CRITICAL.value: TaskPriority.CRITICAL,
    }
    gaps: list[ResearchGap] = []
    for draft in gap_drafts:
        related_assessments = [
            item
            for item in assessments
            if item.dimension_id == draft.dimension_id
            and (not draft.competitors or item.competitor in draft.competitors)
        ]
        gaps.append(
            ResearchGap(
                id=_stable_id(
                    "gap",
                    assessment_id,
                    draft.dimension_id,
                    ",".join(sorted(draft.competitors)),
                    _value(draft.gap_type),
                    ",".join(sorted(draft.missing_facts)),
                ),
                task_id=task.id,
                competitors=draft.competitors,
                dimension=dimensions[draft.dimension_id].evidence_dimension,
                missing_information=draft.missing_information,
                decision_blocked=draft.decision_blocked,
                why_existing_evidence_is_insufficient=(
                    draft.why_existing_evidence_is_insufficient
                ),
                suggested_queries=draft.suggested_queries,
                preferred_source_types=draft.preferred_source_types,
                priority=impact_priority[_value(draft.impact)],
                stop_condition=draft.stop_condition,
                related_evidence_ids=sorted(
                    {
                        evidence_id
                        for item in related_assessments
                        for evidence_id in item.evidence_ids
                    }
                ),
                gap_type=draft.gap_type,
                impact=draft.impact,
                origin=ResearchGapOrigin.PROFESSIONAL_ANALYST,
                missing_facts=draft.missing_facts,
                assessment_id=assessment_id,
                framework_id=binding.framework.framework_id,
                framework_version=binding.framework.version,
                framework_dimension_id=draft.dimension_id,
                framework_content_hash=binding.framework.content_hash,
                research_task_ids=sorted(
                    {
                        research_task_id
                        for item in related_assessments
                        for research_task_id in item.research_task_ids
                    }
                ),
                blocks_decision=draft.blocks_decision,
                metadata={"source": "professional_analyst_assessment"},
            )
        )

    insights = [
        AssessmentInsight(
            id=_stable_id("assessmentinsight", assessment_id, index),
            task_id=task.id,
            summary=draft.summary,
            dimension_id=draft.dimension_id,
            competitors=draft.competitors,
            evidence_ids=draft.evidence_ids,
            confidence=draft.confidence,
            decision_impact=draft.decision_impact,
        )
        for index, draft in enumerate(stage.insights, start=1)
    ]
    scored = [
        item.coverage_score
        for item in assessments
        if item.status != DimensionAssessmentStatus.NOT_APPLICABLE.value
    ]
    coverage_score = round(sum(scored) / len(scored), 4) if scored else 0.0
    blocking = any(
        item.blocks_decision
        and item.impact in {
            ResearchGapImpact.HIGH.value,
            ResearchGapImpact.CRITICAL.value,
        }
        for item in gaps
    )
    if assessments and all(
        item.status
        in {
            DimensionAssessmentStatus.COVERED.value,
            DimensionAssessmentStatus.NOT_APPLICABLE.value,
        }
        for item in assessments
    ) and not blocking:
        overall_status = AnalysisAssessmentStatus.SUFFICIENT
    elif coverage_score <= 0:
        overall_status = AnalysisAssessmentStatus.INSUFFICIENT
    else:
        overall_status = AnalysisAssessmentStatus.PARTIAL
    return AnalysisAssessment(
        id=assessment_id,
        pipeline_id=pipeline_id,
        task_id=task.id,
        assessment_round=assessment_round,
        framework_id=binding.framework.framework_id,
        framework_version=binding.framework.version,
        framework_content_hash=binding.framework.content_hash,
        evidence_batch_hash=batch_hash,
        evidence_ids=sorted(item.id for item in evidence),
        overall_status=overall_status,
        coverage_score=coverage_score,
        dimension_assessments=assessments,
        insights=insights,
        research_gaps=gaps,
        analyst_agent_run_id=analyst_agent_run_id,
        metadata={
            "source": "professional_analyst_assessment",
            "framework_binding_mode": binding.binding_mode,
        },
    )


def merge_research_gaps(
    existing: list[ResearchGap],
    incoming: list[ResearchGap],
) -> list[ResearchGap]:
    by_id = {item.id: item for item in existing}
    for item in incoming:
        if item.id not in by_id:
            by_id[item.id] = item
    return list(by_id.values())
