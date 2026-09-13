from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class AgentRole(str, Enum):
    INTENT = "intent"
    ORCHESTRATOR = "orchestrator"
    COLLECTOR = "collector"
    RESEARCHER = "researcher"
    EXTRACTOR = "extractor"
    ANALYST = "analyst"
    WRITER = "writer"
    REVIEWER = "reviewer"
    CITATION = "citation"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskMode(str, Enum):
    SNAPSHOT = "snapshot"
    LIVE = "live"
    DISCOVERY = "discovery"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    CLAIMED = "claimed"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    REQUIRES_HUMAN = "requires_human"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskType(str, Enum):
    INITIAL_PLANNING = "initial_planning"
    COLLECT_SOURCES = "collect_sources"
    SUPPLEMENT_COLLECTION = "supplement_collection"
    EXTRACT_PRODUCT_CARD = "extract_product_card"
    EXTRACT_SOURCE_EVIDENCE = "extract_source_evidence"
    EVALUATE_EVIDENCE_COVERAGE = "evaluate_evidence_coverage"
    BUILD_PRODUCT_CARDS = "build_product_cards"
    ANALYZE_DIMENSION = "analyze_dimension"
    BUILD_CLAIMS = "build_claims"
    SUPPLEMENT_ANALYSIS = "supplement_analysis"
    CHECK_CITATIONS = "check_citations"
    WRITE_REPORT = "write_report"
    BUILD_REPORT = "build_report"
    REVISE_REPORT = "revise_report"
    REVIEW_REPORT = "review_report"
    FINALIZE_REPORT = "finalize_report"


class SourceType(str, Enum):
    OFFICIAL_SITE = "official_site"
    PRICING_PAGE = "pricing_page"
    DOCS = "docs"
    BLOG = "blog"
    NEWS = "news"
    REPORT = "report"
    SOCIAL = "social"
    OTHER = "other"


class SourceRole(str, Enum):
    PRIMARY = "PRIMARY"
    AUTHORITATIVE_SECONDARY = "AUTHORITATIVE_SECONDARY"
    GENERAL_THIRD_PARTY = "GENERAL_THIRD_PARTY"
    COMMUNITY = "COMMUNITY"
    LOW_QUALITY = "LOW_QUALITY"


class OfficialConfidence(str, Enum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class EvidenceDimension(str, Enum):
    POSITIONING = "positioning"
    PRICING = "pricing"
    FEATURE = "feature"
    ECOSYSTEM = "ecosystem"
    MARKET = "market"
    CUSTOMER = "customer"
    FUNDING = "funding"
    RISK = "risk"
    OTHER = "other"


class CitationStatus(str, Enum):
    PENDING = "pending"
    SUPPORTED = "supported"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_EVIDENCE = "invalid_evidence"


class IssueSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryScope(str, Enum):
    WORKING = "working"
    RUN = "run"
    LONG_TERM = "long_term"


class MemoryKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    DOMAIN_FRAMEWORK = "domain_framework"
    ANALYSIS_PATTERN = "analysis_pattern"
    RUN_SUMMARY = "run_summary"
    SOURCE_POLICY = "source_policy"
    APPROVED_CLAIM = "approved_claim"
    INFORMATION_GAP = "information_gap"
    QUALITY_ISSUE = "quality_issue"


class GuardrailStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class LLMMode(str, Enum):
    RULE = "rule"
    LLM = "llm"
    LLM_WITH_FALLBACK = "llm_with_fallback"


class LLMProvider(str, Enum):
    MOCK = "mock"
    OPENAI = "openai"
    COMPATIBLE = "compatible"


class CompetitorRole(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    SUBSTITUTE = "substitute"
    BENCHMARK = "benchmark"
    EMERGING = "emerging"


class AnalysisClaimType(str, Enum):
    FACT = "fact"
    COMPARISON = "comparison"
    BASELINE = "baseline"
    INFERENCE = "inference"
    RISK = "risk"
    OPPORTUNITY = "opportunity"
    RECOMMENDATION = "recommendation"


class EvidenceCoverageStatus(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    CONFLICTING = "conflicting"
    WEAK = "weak"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class AnalysisAssessmentStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class DimensionAssessmentStatus(str, Enum):
    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CompletionCriterionStatus(str, Enum):
    MET = "met"
    UNMET = "unmet"
    NOT_APPLICABLE = "not_applicable"


class ResearchGapType(str, Enum):
    MISSING_FACT = "missing_fact"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MISSING_SOURCE_TYPE = "missing_source_type"
    MISSING_COMPARISON = "missing_comparison"
    DECISION_BLOCKING = "decision_blocking"


class ResearchGapImpact(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResearchGapOrigin(str, Enum):
    RESEARCH_AGENT = "research_agent"
    DETERMINISTIC_COVERAGE = "deterministic_coverage"
    PROFESSIONAL_ANALYST = "professional_analyst"


class ResearchActionType(str, Enum):
    SEARCH = "SEARCH"
    FETCH = "FETCH"
    READ = "READ"
    SUBMIT_EVIDENCE = "SUBMIT_EVIDENCE"
    FINISH = "FINISH"


class MissionSupervisorAction(str, Enum):
    CREATE_RESEARCH_UNIT = "CREATE_RESEARCH_UNIT"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    FINISH = "FINISH"


class ResearchTaskOutcome(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EXHAUSTED = "EXHAUSTED"


class SchemaModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    schema_version: str = "v1"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftStatus(str, Enum):
    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"
    READY = "ready"
    CONFIRMED = "confirmed"


class DatasetCompatibilityStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    COMPATIBLE = "compatible"
    PARTIAL = "partial"
    INCOMPATIBLE = "incompatible"


class ExecutionPlanStatus(str, Enum):
    BLOCKED = "blocked"
    READY = "ready"
    AUTHORIZED = "authorized"


class ExecutionRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionMode(str, Enum):
    MOCK = "mock"
    DEEPSEEK = "deepseek"


class ResearchLoopRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    REQUIRES_HUMAN = "requires_human"
    FAILED = "failed"


class ResearchLoopStopReason(str, Enum):
    NONE = ""
    COVERAGE_SUFFICIENT = "coverage_sufficient"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_RUNNABLE_TASK = "no_runnable_task"
    FAILED = "failed"


class ResearchPlanStatus(str, Enum):
    READY_FOR_ANALYSIS = "ready_for_analysis"
    NEEDS_COLLECTION = "needs_collection"
    BLOCKED = "blocked"


class AnalysisTaskDraft(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("draft"))
    task_id: str = Field(default="", description="Draft audit id; mirrors id.")
    request_text: str
    decision_question: str = ""
    industry: str = ""
    competitors: list[str] = Field(default_factory=list)
    target_customers: list[str] = Field(default_factory=list)
    core_scenarios: list[str] = Field(default_factory=list)
    focus_areas: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    research_mode: str = ""

    primary_target: str = ""
    comparison_targets: list[str] = Field(default_factory=list)
    reference_products: list[str] = Field(default_factory=list)

    target_profiling: bool = False
    market_scoping: bool = False
    competitor_discovery: bool = False
    cross_competitor_comparison: bool = False
    decision_oriented_analysis: bool = False
    research_gap_tracking: bool = True

    report_subject: str = ""
    preferred_title: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    ready_for_confirmation: bool = False
    status: DraftStatus = DraftStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def set_task_id(self) -> "AnalysisTaskDraft":
        if not self.task_id:
            self.task_id = self.id
        return self


class IntentParseRequest(BaseModel):
    request_text: str = Field(min_length=10, max_length=4000)


class ConfirmAnalysisTaskRequest(BaseModel):
    decision_question: str = ""
    industry: str = ""
    competitors: list[str] = Field(default_factory=list)
    target_customers: list[str] = Field(default_factory=list)
    core_scenarios: list[str] = Field(default_factory=list)
    focus_areas: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    report_subject: str = ""
    preferred_title: str = ""
    workspace_origin: str = ""


class AnalysisTask(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    task_id: str = Field(default="", description="Mirrors id for task records.")
    query: str
    competitors: list[str] = Field(default_factory=list)
    industry: str = ""
    focus_areas: list[str] = Field(default_factory=list)
    report_subject: str = Field(
        default="",
        description="Normalized subject used to derive a task-specific report title.",
    )
    preferred_title: str = Field(
        default="",
        description="Optional user-confirmed report title; never required for analysis.",
    )
    mode: TaskMode = TaskMode.SNAPSHOT
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def set_task_id(self) -> "AnalysisTask":
        if not self.task_id:
            self.task_id = self.id
        return self


class DatasetCompatibilityAssessment(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("compat"))
    task_id: str
    dataset_id: str
    dataset_label: str
    status: DatasetCompatibilityStatus = DatasetCompatibilityStatus.NOT_CHECKED
    industry_match: bool = False
    requested_competitors: list[str] = Field(default_factory=list)
    matched_competitors: list[str] = Field(default_factory=list)
    matched_competitor_map: dict[str, str] = Field(default_factory=dict)
    missing_competitors: list[str] = Field(default_factory=list)
    supported_focus_areas: list[str] = Field(default_factory=list)
    unsupported_focus_areas: list[str] = Field(default_factory=list)
    competitor_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    source_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    evaluated_at: datetime = Field(default_factory=utc_now)


class ExecutionPlanStep(BaseModel):
    step_key: str
    label: str
    agent_role: AgentRole
    provider: str
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    status: str = "planned"
    note: str = ""


class ExecutionPlan(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("execplan"))
    task_id: str
    compatibility_assessment_id: str
    title: str
    dataset_id: str
    steps: list[ExecutionPlanStep] = Field(default_factory=list)
    estimated_real_llm_calls: int = Field(default=0, ge=0)
    estimated_mock_llm_calls: int = Field(default=0, ge=0)
    planned_model: str = ""
    requires_explicit_authorization: bool = True
    authorization_available: bool = False
    status: ExecutionPlanStatus = ExecutionPlanStatus.BLOCKED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AuthorizeExecutionRequest(BaseModel):
    plan_id: str
    acknowledge_dataset_scope: bool = False


class ExecutionAuthorization(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("authorization"))
    task_id: str
    plan_id: str
    authorized: bool = True
    queue_status: str = "queued"
    requested_by: str = "user_ui"
    execution_started: bool = False
    authorized_at: datetime = Field(default_factory=utc_now)


class StartExecutionRequest(BaseModel):
    mode: ExecutionMode = ExecutionMode.MOCK


class RunResearchAnalysisRequest(BaseModel):
    mode: ExecutionMode = ExecutionMode.DEEPSEEK
    acknowledge_real_llm_call: bool = False


class RunResearchReportingRequest(BaseModel):
    mode: ExecutionMode = ExecutionMode.DEEPSEEK
    acknowledge_real_llm_call: bool = False


class RunResearchAgentRequest(BaseModel):
    research_task_id: str
    mode: ExecutionMode = ExecutionMode.DEEPSEEK
    acknowledge_real_llm_call: bool = False
    budget: ResearchAgentBudget = Field(default_factory=lambda: ResearchAgentBudget())


class ExecutionRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("execrun"))
    task_id: str
    plan_id: str
    authorization_id: str
    mode: ExecutionMode = ExecutionMode.MOCK
    status: ExecutionRunStatus = ExecutionRunStatus.QUEUED
    current_step: str = ""
    progress_percent: int = Field(default=0, ge=0, le=100)
    message: str = ""
    error: str = ""
    result_task_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExecutionEvent(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("execevent"))
    task_id: str
    execution_run_id: str
    sequence: int = Field(ge=1)
    event_type: str
    status: ExecutionRunStatus
    step_key: str = ""
    message: str
    progress_percent: int = Field(default=0, ge=0, le=100)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ResearchLoopRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchloop"))
    task_id: str
    research_plan_id: str
    status: ResearchLoopRunStatus = ResearchLoopRunStatus.QUEUED
    stop_reason: ResearchLoopStopReason = ResearchLoopStopReason.NONE
    current_stage: str = ""
    progress_percent: int = Field(default=0, ge=0, le=100)
    message: str = ""
    error: str = ""
    actions_completed: int = Field(default=0, ge=0)
    max_actions: int = Field(default=100, ge=1)
    collector_runs: int = Field(default=0, ge=0)
    extractor_runs: int = Field(default=0, ge=0)
    coverage_runs: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    max_total_sources: int = Field(default=40, ge=1)
    current_collection_round: int = Field(default=0, ge=0)
    max_collection_rounds: int = Field(default=3, ge=1)
    coverage_status_counts: dict[str, int] = Field(default_factory=dict)
    research_gap_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ResearchLoopEvent(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchevent"))
    task_id: str
    research_loop_run_id: str
    sequence: int = Field(ge=1)
    event_type: str
    status: ResearchLoopRunStatus
    stage: str = ""
    message: str
    progress_percent: int = Field(default=0, ge=0, le=100)
    action_index: int = Field(default=0, ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ResearchBudget(BaseModel):
    max_collection_rounds: int = Field(default=3, ge=1, le=10)
    max_sources_per_task: int = Field(default=5, ge=1, le=20)
    max_total_sources: int = Field(default=40, ge=1, le=200)
    max_real_llm_calls: int = Field(default=0, ge=0)
    stop_when_all_required_facts_covered: bool = True


class DimensionDefinition(SchemaModel):
    """One versioned planning dimension resolved by FrameworkRegistry."""

    dimension_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    evidence_dimension: EvidenceDimension
    research_intent: str = Field(min_length=1)
    objective_template: str = Field(min_length=1)
    research_questions: list[str] = Field(min_length=1)
    required_facts: list[str] = Field(min_length=1)
    query_templates: list[str] = Field(min_length=1)
    preferred_source_types: list[SourceType] = Field(min_length=1)
    comparability_basis: str = Field(min_length=1)
    completion_criteria: list[str] = Field(min_length=1)
    priority: TaskPriority = TaskPriority.MEDIUM

    @model_validator(mode="after")
    def validate_unique_text_values(self) -> "DimensionDefinition":
        for field_name in (
            "aliases",
            "research_questions",
            "required_facts",
            "query_templates",
            "completion_criteria",
        ):
            values = [str(item).strip() for item in getattr(self, field_name)]
            if any(not item for item in values):
                raise ValueError(f"{field_name} 不能包含空字符串")
            if len({item.casefold() for item in values}) != len(values):
                raise ValueError(f"{field_name} 不能包含重复项")
            setattr(self, field_name, values)
        return self


class FrameworkDefinition(SchemaModel):
    """Validated business definition; YAML is an implementation detail."""

    framework_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1)
    description: str = ""
    default_dimension_ids: list[str] = Field(min_length=1)
    dimensions: list[DimensionDefinition] = Field(min_length=1)
    content_hash: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def validate_dimension_index(self) -> "FrameworkDefinition":
        dimension_ids = [item.dimension_id for item in self.dimensions]
        if len(set(dimension_ids)) != len(dimension_ids):
            raise ValueError("Framework dimensions 存在重复 dimension_id")
        missing = sorted(set(self.default_dimension_ids) - set(dimension_ids))
        if missing:
            raise ValueError(
                "default_dimension_ids 引用了不存在的维度: " + ", ".join(missing)
            )
        aliases: dict[str, str] = {}
        for dimension in self.dimensions:
            for value in [dimension.dimension_id, dimension.label, *dimension.aliases]:
                key = "".join(str(value).strip().casefold().replace("-", "_").split())
                owner = aliases.get(key)
                if owner is not None and owner != dimension.dimension_id:
                    raise ValueError(
                        f"Framework dimension alias 冲突: {value!r} 同时属于 "
                        f"{owner} 与 {dimension.dimension_id}"
                    )
                aliases[key] = dimension.dimension_id
        return self


class ResearchTask(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchtask"))
    task_id: str
    information_need_id: str
    title: str
    objective: str
    competitor: str
    dimension: str
    research_intent: str = ""
    query_hints: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    snapshot_source_ids: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: str = "waiting_for_collector"
    stop_condition: str
    depends_on: list[str] = Field(default_factory=list)
    assigned_role: AgentRole = AgentRole.COLLECTOR
    collection_round: int = Field(default=1, ge=1, le=10)
    parent_research_task_id: str = ""
    research_gap_id: str = ""
    framework_id: str = Field(default="", pattern=r"^(?:|[a-z][a-z0-9_]*)$")
    framework_version: str = Field(default="", pattern=r"^(?:|\d+\.\d+\.\d+)$")
    framework_dimension_id: str = Field(
        default="",
        pattern=r"^(?:|[a-z][a-z0-9_]*)$",
    )
    framework_content_hash: str = Field(
        default="",
        pattern=r"^(?:|[0-9a-f]{64})$",
    )

    @model_validator(mode="after")
    def validate_framework_provenance(self) -> "ResearchTask":
        framework_values = (
            self.framework_id,
            self.framework_version,
            self.framework_dimension_id,
            self.framework_content_hash,
        )
        populated = [bool(str(value).strip()) for value in framework_values]
        if any(populated) and not all(populated):
            raise ValueError("ResearchTask framework provenance 必须完整填写")
        if self.schema_version == "v2" and not all(populated):
            raise ValueError("ResearchTask v2 必须绑定完整 Framework provenance")
        return self


class ResearchAgentBudget(BaseModel):
    max_steps: int = Field(default=12, ge=1, le=50)
    max_searches: int = Field(default=4, ge=1, le=20)
    max_sources: int = Field(default=6, ge=1, le=30)
    max_failed_actions: int = Field(default=3, ge=1, le=20)


class ObservedResearchTerm(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("observedterm"))
    task_id: str
    research_task_id: str
    term: str
    discovered_from: str
    provenance_id: str
    created_at: datetime = Field(default_factory=utc_now)


class ResearchAgentAction(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchaction"))
    task_id: str
    research_task_id: str
    action: ResearchActionType
    rationale: str
    query: str = ""
    search_scope: str = "auto"
    url: str = ""
    source_id: str = ""
    chunk_id: str = ""
    exact_quote: str = ""
    supports: str = ""
    remaining_need: str = ""
    finish_status: str = ""
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def normalize_runtime_defaults(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_scope = data.get("search_scope", "auto")
        normalized_scope = str(raw_scope or "auto").strip().casefold()
        if normalized_scope not in {"auto", "general", "community"}:
            normalized_scope = "auto"
        data["search_scope"] = normalized_scope
        return data

    @model_validator(mode="after")
    def validate_action_arguments(self):
        action_value = str(self.action)
        if action_value == ResearchActionType.FINISH.value:
            if not str(self.finish_status).strip():
                raise ValueError("FINISH 缺少字段：finish_status")
            ResearchTaskOutcome(self.finish_status)
        return self


class ResearchAgentObservation(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchobservation"))
    task_id: str
    research_task_id: str
    action_id: str
    action: ResearchActionType
    status: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ResearchAgentRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchagentrun"))
    task_id: str
    research_task_id: str
    mission_id: str = ""
    status: RunStatus = RunStatus.RUNNING
    outcome: str = ""
    attempted_queries: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    rejected_sources: list[str] = Field(default_factory=list)
    observed_terms: list[ObservedResearchTerm] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)
    failed_actions: list[str] = Field(default_factory=list)
    remaining_need: str = ""
    action_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    step_count: int = Field(default=0, ge=0)
    search_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    budget: ResearchAgentBudget = Field(default_factory=ResearchAgentBudget)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class ResearchActionContextTrace(SchemaModel):
    """Lightweight size telemetry for one Research Agent LLM decision."""

    id: str = Field(default_factory=lambda: new_id("researchcontexttrace"))
    task_id: str
    research_task_id: str
    agent_run_id: str
    action_index: int = Field(ge=1)
    context_mode: str
    section_chars: dict[str, int] = Field(default_factory=dict)
    section_estimated_tokens: dict[str, int] = Field(default_factory=dict)
    total_chars: int = Field(default=0, ge=0)
    estimated_input_tokens: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    llm_latency_ms: int = Field(default=0, ge=0)
    observation_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    available_candidate_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    observed_term_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ResearchMission(SchemaModel):
    """A deterministic coherent research scope for one competitor."""

    id: str = Field(default_factory=lambda: new_id("researchmission"))
    task_id: str
    competitor: str
    goal: str = ""
    information_need_ids: list[str] = Field(default_factory=list)
    research_task_ids: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ResearchMissionState(SchemaModel):
    """Compact shared facts; never contains Worker message/observation history."""

    id: str = Field(default_factory=lambda: new_id("missionstate"))
    task_id: str
    mission_id: str
    competitor: str
    attempted_queries: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)
    observed_terms: list[str] = Field(default_factory=list)
    confirmed_official_domains: list[str] = Field(default_factory=list)
    worker_run_ids: list[str] = Field(default_factory=list)
    worker_result_ids: list[str] = Field(default_factory=list)
    supervisor_decision_ids: list[str] = Field(default_factory=list)
    outcome_by_need: dict[str, str] = Field(default_factory=dict)
    coverage_status_by_need: dict[str, str] = Field(default_factory=dict)
    remaining_need_by_id: dict[str, str] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class ResearchWorkerNeedContext(BaseModel):
    id: str
    question_id: str = ""
    dimension: str
    required_facts: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    comparability_basis: str = ""
    decision_link: str = ""
    coverage_status: str = ""
    remaining_need: str = ""


class ResearchWorkerSourceContext(BaseModel):
    source_id: str
    title: str = ""
    url: str
    source_type: str = ""
    official_confidence: str = "unknown"


class ResearchWorkerEvidenceContext(BaseModel):
    evidence_id: str
    source_id: str
    dimension: str
    fact: str


class ResearchWorkerGapContext(BaseModel):
    gap_id: str
    dimension: str
    missing_information: str
    insufficiency_reason: str = ""


class ResearchMissionBudgetState(BaseModel):
    collection_round: int = Field(default=1, ge=1)
    max_collection_rounds: int = Field(default=3, ge=1)
    completed_units: int = Field(default=0, ge=0)
    max_units: int = Field(default=1, ge=1)
    sources_used: int = Field(default=0, ge=0)
    max_total_sources: int = Field(default=40, ge=1)
    actions_used: int = Field(default=0, ge=0)
    max_actions: int = Field(default=100, ge=1)
    worker_max_steps: int = Field(default=12, ge=1)
    worker_max_searches: int = Field(default=4, ge=1)
    worker_max_sources: int = Field(default=6, ge=1)


class ResearchWorkerContext(SchemaModel):
    """Filtered Mission context visible to one focused Research Worker."""

    id: str = Field(default_factory=lambda: new_id("researchworkercontext"))
    task_id: str
    mission_id: str
    mission_goal: str
    competitor: str
    current_need: ResearchWorkerNeedContext
    related_discovered_terms: list[str] = Field(default_factory=list)
    confirmed_official_domains: list[str] = Field(default_factory=list)
    related_sources: list[ResearchWorkerSourceContext] = Field(default_factory=list)
    related_verified_evidence: list[ResearchWorkerEvidenceContext] = Field(
        default_factory=list
    )
    previous_queries: list[str] = Field(default_factory=list)
    previous_query_count: int = Field(default=0, ge=0)
    visited_urls: list[str] = Field(default_factory=list)
    visited_url_count: int = Field(default=0, ge=0)
    remaining_gaps: list[ResearchWorkerGapContext] = Field(default_factory=list)
    budget_state: ResearchMissionBudgetState = Field(
        default_factory=ResearchMissionBudgetState
    )
    conversation_history_shared: bool = False


class ResearchWorkerResult(SchemaModel):
    """Structured Worker outcome merged into MissionState."""

    id: str = Field(default_factory=lambda: new_id("researchworkerresult"))
    task_id: str
    mission_id: str
    research_task_id: str
    information_need_id: str
    outcome: str
    attempted_queries: list[str] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)
    discovered_terms: list[str] = Field(default_factory=list)
    remaining_need: str = ""
    steps_used: int = Field(default=0, ge=0)
    searches_used: int = Field(default=0, ge=0)
    sources_used: int = Field(default=0, ge=0)
    completed_at: datetime = Field(default_factory=utc_now)


class ResearchMissionDecision(SchemaModel):
    """One structured Mission Supervisor decision; never performs research."""

    id: str = Field(default_factory=lambda: new_id("missiondecision"))
    task_id: str
    mission_id: str
    action: MissionSupervisorAction
    target_need: str = ""
    research_goal: str = ""
    reason: str
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_research_target(self):
        if self.action != MissionSupervisorAction.FINISH.value:
            if not self.target_need.strip():
                raise ValueError("继续研究的 Supervisor decision 缺少 target_need")
            if not self.research_goal.strip():
                raise ValueError("继续研究的 Supervisor decision 缺少 research_goal")
        return self


class WebPageContent(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("webpage"))
    task_id: str
    source_id: str
    requested_url: str
    final_url: str
    title: str = ""
    text: str
    content_type: str = "text/html"
    content_hash: str
    render_mode: str = "http"
    browser_engine: str = ""
    fetched_at: datetime = Field(default_factory=utc_now)


class SourceChunk(SchemaModel):
    id: str
    task_id: str
    source_id: str
    web_page_id: str
    content_hash: str
    chunk_index: int = Field(ge=0)
    source_text_start: int = Field(ge=0)
    source_text_end: int = Field(ge=0)
    text: str
    title: str = ""
    competitor: str = ""
    origin_research_task_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class SourceRetrievalHit(BaseModel):
    chunk_id: str
    rank: int = Field(ge=1)
    score: float = Field(ge=0.0)
    matched_terms: list[str] = Field(default_factory=list)
    source_id: str = ""
    bm25_rank: int | None = Field(default=None, ge=1)
    bm25_score: float | None = None
    dense_rank: int | None = Field(default=None, ge=1)
    dense_score: float | None = None
    rrf_rank: int | None = Field(default=None, ge=1)
    rrf_score: float | None = None
    rerank_rank: int | None = Field(default=None, ge=1)
    rerank_score: float | None = None
    final_rank: int | None = Field(default=None, ge=1)


class SourceRetrievalRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("retrieval"))
    task_id: str
    research_task_id: str
    algorithm: str = "bm25_v1"
    query_text: str
    query_hash: str
    candidate_chunk_count: int = Field(default=0, ge=0)
    selected_chunk_ids: list[str] = Field(default_factory=list)
    selected_chunks: list[SourceRetrievalHit] = Field(default_factory=list)
    top_k: int = Field(default=10, ge=1)
    selected_text_chars: int = Field(default=0, ge=0)
    total_candidate_chars: int = Field(default=0, ge=0)
    fallback_used: bool = False
    fallback_reason: str = ""
    retrieval_mode: str = "bm25_v1"
    embedding_model: str = ""
    reranker_model: str = ""
    bm25_candidate_count: int = Field(default=0, ge=0)
    dense_candidate_count: int = Field(default=0, ge=0)
    fusion_candidate_count: int = Field(default=0, ge=0)
    reranked_count: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0)
    rrf_k: int = Field(default=60, ge=1)
    embedding_cache_hits: int = Field(default=0, ge=0)
    embedding_cache_misses: int = Field(default=0, ge=0)
    bm25_ms: float = Field(default=0.0, ge=0.0)
    dense_ms: float = Field(default=0.0, ge=0.0)
    fusion_ms: float = Field(default=0.0, ge=0.0)
    rerank_ms: float = Field(default=0.0, ge=0.0)
    total_ms: float = Field(default=0.0, ge=0.0)
    created_at: datetime = Field(default_factory=utc_now)


class CollectionAttempt(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("collectattempt"))
    task_id: str
    research_task_id: str
    requested_url: str
    final_url: str = ""
    status: str
    http_status: int = Field(default=0, ge=0)
    source_document_id: str = ""
    extracted_chars: int = Field(default=0, ge=0)
    content_hash: str = ""
    render_mode: str = "http"
    browser_engine: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class SearchAttempt(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("searchattempt"))
    task_id: str
    research_task_id: str
    provider: str
    query: str
    status: str
    result_count: int = Field(default=0, ge=0)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class WebSearchResult(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("searchresult"))
    task_id: str
    research_task_id: str
    search_attempt_id: str
    provider: str
    query: str
    rank: int = Field(ge=1)
    title: str = ""
    url: str
    snippet: str = ""
    site_name: str = ""
    published_at: str = ""
    selected_for_collection: bool = False
    rejection_reason: str = ""
    created_at: datetime = Field(default_factory=utc_now)

class ResearchSourceCandidate(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("sourcecandidate"))
    task_id: str
    research_task_id: str

    query: str

    # 哪个平级检索 Tool 发现的
    source_tool: str

    title: str = ""
    url: str
    snippet: str = ""
    content: str = ""

    # 如 web / zhihu / bilibili / xiaohongshu
    channel: str = ""

    provider: str = ""

    # community / general_third_party / ...
    source_type: str = ""

    published_at: str = ""

    selected_for_collection: bool = False

    metadata: dict[str, Any] = Field(default_factory=dict)


class OfficialDomainContext(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("officialdomain"))
    task_id: str
    competitor: str
    domain: str
    confidence: OfficialConfidence = OfficialConfidence.PROBABLE
    research_task_ids: list[str] = Field(default_factory=list)
    search_result_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SourceTaskAssociation(SchemaModel):
    """A task-local authorization to reuse one globally deduplicated source."""

    id: str = Field(default_factory=lambda: new_id("sourceassoc"))
    task_id: str
    research_task_id: str
    source_id: str
    discovery_method: str
    search_result_id: str = ""
    requested_url: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class SourceSelectionRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("sourceselection"))
    task_id: str
    research_task_id: str
    search_attempt_id: str
    search_result_id: str
    query: str = ""
    url: str
    domain: str
    source_role: SourceRole
    official_confidence: OfficialConfidence
    relevance_score: float = 0.0
    dimension_fit_score: float = 0.0
    authority_score: float = 0.0
    freshness_score: float = 0.0
    penalties: list[str] = Field(default_factory=list)
    penalty_score: float = 0.0
    diversity_penalty: float = 0.0
    final_score: float = 0.0
    quality_rank: int = Field(ge=1)
    selected: bool = False
    selection_reason: str = ""
    ranking_version: str = "source_quality_v1"
    created_at: datetime = Field(default_factory=utc_now)


class ResearchPlan(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchplan"))
    task_id: str
    decision_question: str
    status: ResearchPlanStatus
    kiq_ids: list[str] = Field(default_factory=list)
    information_need_ids: list[str] = Field(default_factory=list)
    research_task_ids: list[str] = Field(default_factory=list)
    covered_competitors: list[str] = Field(default_factory=list)
    missing_competitors: list[str] = Field(default_factory=list)
    covered_dimensions: list[str] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    planner_provider: str = "mock-research-planner-v1"
    created_at: datetime = Field(default_factory=utc_now)


class TaskRecord(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("taskrec"))
    task_id: str = Field(description="AnalysisTask/run id that owns this task.")
    task_key: str = Field(
        default="",
        description="Stable task-board key, e.g. collect_sources.",
    )
    parent_task_id: str = Field(
        default="",
        description="Parent TaskRecord id or task_key.",
    )
    task_type: TaskType
    target_agent_role: AgentRole
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    depends_on: list[str] = Field(
        default_factory=list,
        description="TaskRecord ids or task_keys that must complete first.",
    )
    blocked_by: list[str] = Field(
        default_factory=list,
        description="TaskRecord ids, task_keys, or issue ids blocking this task.",
    )
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    created_by_agent_run_id: str = ""
    claimed_by_agent: str = ""
    node_id: str = Field(
        default="",
        description="Optional DAGNode id when this task is materialized as a DAG node.",
    )
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str = ""

    @model_validator(mode="after")
    def set_task_key(self) -> "TaskRecord":
        if not self.task_key:
            self.task_key = self.id
        return self


class TaskBoard(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("board"))
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    tasks: list[TaskRecord] = Field(default_factory=list)
    max_review_rounds: int = Field(default=3, ge=0)
    current_review_round: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_board(self) -> "TaskBoard":
        if self.current_review_round > self.max_review_rounds:
            raise ValueError("current_review_round must be <= max_review_rounds")

        refs: dict[str, TaskRecord] = {}
        for task in self.tasks:
            if task.task_id != self.task_id:
                raise ValueError(
                    f"TaskRecord {task.id} has task_id={task.task_id}, "
                    f"expected {self.task_id}"
                )
            for ref in (task.id, task.task_key):
                if not ref:
                    continue
                existing = refs.get(ref)
                if existing is not None and existing.id != task.id:
                    raise ValueError(f"Duplicate task reference: {ref}")
                refs[ref] = task

        graph: dict[str, list[str]] = {task.id: [] for task in self.tasks}
        for task in self.tasks:
            if task.parent_task_id and task.parent_task_id not in refs:
                raise ValueError(
                    f"TaskRecord {task.id} references missing parent_task_id="
                    f"{task.parent_task_id}"
                )
            for ref in task.depends_on:
                if ref not in refs:
                    raise ValueError(
                        f"TaskRecord {task.id} references missing dependency={ref}"
                    )
                dependency_id = refs[ref].id
                if dependency_id == task.id:
                    raise ValueError(f"TaskRecord {task.id} depends on itself")
                graph[task.id].append(dependency_id)
            for ref in task.blocked_by:
                if ref.startswith("issue_"):
                    continue
                if ref not in refs:
                    raise ValueError(
                        f"TaskRecord {task.id} references missing blocker={ref}"
                    )

        self._validate_dependency_graph_is_acyclic(graph)
        return self

    @staticmethod
    def _validate_dependency_graph_is_acyclic(graph: dict[str, list[str]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError(f"TaskBoard dependency cycle detected at {node_id}")
            visiting.add(node_id)
            for dependency_id in graph.get(node_id, []):
                visit(dependency_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in graph:
            visit(node_id)


class DAGNode(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("node"))
    task_id: str
    label: str
    agent_role: AgentRole
    status: RunStatus = RunStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    task_id: str
    node_id: str
    agent_role: AgentRole
    status: RunStatus = RunStatus.PENDING
    input_summary: str = ""
    output_summary: str = ""
    reasoning_summary: str = ""
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ToolCall(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("tool"))
    task_id: str
    agent_run_id: str
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output_summary: str = ""
    status: RunStatus = RunStatus.COMPLETED
    duration_ms: int = Field(default=0, ge=0)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class AgentContext(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("ctx"))
    task_id: str
    task: AnalysisTask
    node_id: str
    input_refs: list[str] = Field(default_factory=list)
    artifacts: dict[str, list[str]] = Field(default_factory=dict)


class AgentResult(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("result"))
    task_id: str
    agent_run: AgentRun
    status: RunStatus = RunStatus.COMPLETED
    output_summary: str = ""
    output_artifacts: dict[str, list[str]] = Field(default_factory=dict)
    error: str = ""


class SourceDocument(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("src"))
    task_id: str
    title: str
    url: str
    source_type: SourceType = SourceType.OTHER
    competitor: str
    accessed_at: datetime = Field(default_factory=utc_now)
    content_excerpt: str = ""
    reliability_score: float = Field(default=0.5, ge=0.0, le=1.0)


class SourceEvidence(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    task_id: str
    source_id: str
    competitor: str
    dimension: EvidenceDimension = EvidenceDimension.OTHER
    snippet: str
    normalized_fact: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_text_start: int = Field(default=0, ge=0)
    source_text_end: int = Field(default=0, ge=0)
    extraction_method: str = ""


class EvidenceExtractionAttempt(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("extractattempt"))
    task_id: str
    research_task_id: str
    source_id: str
    web_page_id: str
    status: str
    extraction_method: str
    evidence_ids: list[str] = Field(default_factory=list)
    extracted_count: int = Field(default=0, ge=0)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class ProductCard(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("prod"))
    task_id: str
    name: str
    company: str = ""
    positioning: str = ""
    target_users: list[str] = Field(default_factory=list)
    pricing_summary: str = ""
    core_features: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class AnalysisClaim(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("cl"))
    task_id: str
    dimension: EvidenceDimension = EvidenceDimension.OTHER
    claim_text: str
    competitors: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    produced_by_agent_run_id: str
    citation_status: CitationStatus = CitationStatus.PENDING


class BriefAssessment(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("brief"))
    task_id: str
    decision_question: str
    industry: str = ""
    target_customers: list[str] = Field(default_factory=list)
    core_scenarios: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    sufficient_for_analysis: bool = False


class CompetitorProfile(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("competitor"))
    task_id: str
    name: str
    role: CompetitorRole
    selection_reason: str
    represented_path: str
    comparable_dimensions: list[str] = Field(default_factory=list)
    non_comparable_dimensions: list[str] = Field(default_factory=list)
    target_customers: list[str] = Field(default_factory=list)
    core_scenarios: list[str] = Field(default_factory=list)
    value_proposition: str = ""
    offering_scope: str = ""
    delivery_model: str = ""
    business_model: str = ""
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class KeyIntelligenceQuestion(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("kiq"))
    task_id: str
    question: str
    decision_link: str
    dimensions: list[str] = Field(default_factory=list)
    priority: TaskPriority = TaskPriority.MEDIUM


class InformationNeed(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("need"))
    task_id: str
    question_id: str
    dimension: str
    research_intent: str = ""
    required_facts: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    comparability_basis: str
    decision_link: str


class EvidenceCoverage(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("coverage"))
    task_id: str
    competitor: str
    dimension: str
    status: EvidenceCoverageStatus
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: str = ""


class ComparabilityNote(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("compare"))
    task_id: str
    competitors: list[str] = Field(default_factory=list)
    dimension: str
    comparable: bool = False
    basis: str
    limitations: str = ""


class AnalysisClaimV2(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("clv2"))
    task_id: str
    dimension: str
    claim_type: AnalysisClaimType
    claim_text: str
    competitors: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    reasoning_summary: str
    uncertainty: str
    decision_impact: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    produced_by_agent_run_id: str
    citation_status: CitationStatus = CitationStatus.PENDING


class AnalystBriefProfilesStage(SchemaModel):
    """Bounded Analyst stage for the decision brief and competitor profiles."""

    id: str = Field(default_factory=lambda: new_id("analyststage"))
    task_id: str
    brief_assessment: BriefAssessment
    competitor_profiles: list[CompetitorProfile] = Field(
        default_factory=list,
        min_length=1,
        max_length=8,
    )


class AnalystClaimsStage(SchemaModel):
    """Bounded Analyst stage for comparability decisions and evidence-led claims."""

    id: str = Field(default_factory=lambda: new_id("analyststage"))
    task_id: str
    comparability_notes: list[ComparabilityNote] = Field(
        default_factory=list,
        max_length=16,
    )
    items: list[AnalysisClaimV2] = Field(
        default_factory=list,
        min_length=1,
        max_length=18,
    )


class AnalystDimensionAssessmentDraft(BaseModel):
    """Semantic LLM output; application code owns identity and scores."""

    dimension_id: str
    competitor: str
    status: DimensionAssessmentStatus
    covered_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    completion_criteria_evaluations: list["CompletionCriterionEvaluation"] = Field(
        default_factory=list,
        description=(
            "Preferred stable-ID criterion partition; include every scope criterion once."
        ),
    )
    completion_criteria_met: list[str] = Field(
        default_factory=list,
        description="Legacy text projection; leave empty when ID evaluations are used.",
    )
    completion_criteria_unmet: list[str] = Field(
        default_factory=list,
        description="Legacy text projection; leave empty when ID evaluations are used.",
    )
    completion_criteria_not_applicable: list[str] = Field(
        default_factory=list,
        description="Legacy text projection for conditional criteria.",
    )
    reasoning: str
    decision_impact: str


class CompletionCriterionEvaluation(BaseModel):
    criterion_id: str = Field(pattern=r"^criterion_[0-9a-f]{12}$")
    status: CompletionCriterionStatus


class AssessmentInsight(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("assessmentinsight"))
    task_id: str
    summary: str
    dimension_id: str
    competitors: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_impact: str


class AnalystAssessmentInsightDraft(BaseModel):
    summary: str
    dimension_id: str
    competitors: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_impact: str


class AnalystResearchGapDraft(BaseModel):
    dimension_id: str
    competitors: list[str] = Field(default_factory=list)
    gap_type: ResearchGapType
    impact: ResearchGapImpact
    missing_facts: list[str] = Field(default_factory=list)
    missing_information: str
    why_existing_evidence_is_insufficient: str
    suggested_queries: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    blocks_decision: bool = False
    decision_blocked: str
    stop_condition: str


class AnalystAssessmentStage(SchemaModel):
    """One bounded Framework-aware critique stage from the existing Analyst."""

    id: str = Field(default_factory=lambda: new_id("analyststage"))
    task_id: str
    dimension_assessments: list[AnalystDimensionAssessmentDraft] = Field(
        default_factory=list,
        min_length=1,
        max_length=48,
    )
    insights: list[AnalystAssessmentInsightDraft] = Field(
        default_factory=list,
        max_length=12,
    )
    research_gaps: list[AnalystResearchGapDraft] = Field(
        default_factory=list,
        max_length=24,
    )


class DimensionAssessment(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("dimensionassessment"))
    task_id: str
    research_task_ids: list[str] = Field(default_factory=list)
    dimension_id: str
    evidence_dimension: str
    competitor: str
    status: DimensionAssessmentStatus
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    covered_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    completion_criteria_evaluations: list[CompletionCriterionEvaluation] = Field(
        default_factory=list
    )
    completion_criteria_met: list[str] = Field(default_factory=list)
    completion_criteria_unmet: list[str] = Field(default_factory=list)
    completion_criteria_not_applicable: list[str] = Field(default_factory=list)
    reasoning: str
    decision_impact: str

    @model_validator(mode="after")
    def validate_fact_partition(self) -> "DimensionAssessment":
        overlap = set(self.covered_facts) & set(self.missing_facts)
        if overlap:
            raise ValueError(
                "covered_facts 与 missing_facts 不能重叠: "
                + ", ".join(sorted(overlap))
            )
        if self.status == DimensionAssessmentStatus.COVERED.value and (
            self.missing_facts or self.completion_criteria_unmet
        ):
            raise ValueError("COVERED dimension 不能包含缺失事实或未满足标准")
        if self.status == DimensionAssessmentStatus.MISSING.value and self.evidence_ids:
            raise ValueError("MISSING dimension 不能引用支持性 Evidence")
        return self


class ResearchGap(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("gap"))
    task_id: str
    competitors: list[str] = Field(default_factory=list)
    dimension: str
    missing_information: str
    decision_blocked: str
    why_existing_evidence_is_insufficient: str
    suggested_queries: list[str] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)
    priority: TaskPriority = TaskPriority.MEDIUM
    stop_condition: str
    related_evidence_ids: list[str] = Field(default_factory=list)
    gap_type: ResearchGapType = ResearchGapType.MISSING_FACT
    impact: ResearchGapImpact = ResearchGapImpact.MEDIUM
    origin: ResearchGapOrigin = ResearchGapOrigin.DETERMINISTIC_COVERAGE
    missing_facts: list[str] = Field(default_factory=list)
    assessment_id: str = ""
    framework_id: str = ""
    framework_version: str = ""
    framework_dimension_id: str = ""
    framework_content_hash: str = ""
    research_task_ids: list[str] = Field(default_factory=list)
    blocks_decision: bool = False


class AnalysisAssessment(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("assessment"))
    pipeline_id: str
    task_id: str
    assessment_round: int = Field(default=1, ge=1)
    framework_id: str
    framework_version: str
    framework_content_hash: str
    evidence_batch_hash: str
    evidence_ids: list[str] = Field(default_factory=list)
    overall_status: AnalysisAssessmentStatus
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    dimension_assessments: list[DimensionAssessment] = Field(default_factory=list)
    insights: list[AssessmentInsight] = Field(default_factory=list)
    research_gaps: list[ResearchGap] = Field(default_factory=list)
    analyst_agent_run_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CompetitiveAnalysisPortfolioV2(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("portfolio"))
    task_id: str
    prompt_id: str
    prompt_version: str
    brief_assessment: BriefAssessment
    competitor_profiles: list[CompetitorProfile] = Field(default_factory=list)
    key_intelligence_questions: list[KeyIntelligenceQuestion] = Field(
        default_factory=list
    )
    information_needs: list[InformationNeed] = Field(default_factory=list)
    evidence_coverage: list[EvidenceCoverage] = Field(default_factory=list)
    comparability_notes: list[ComparabilityNote] = Field(default_factory=list)
    items: list[AnalysisClaimV2] = Field(default_factory=list)
    research_gaps: list[ResearchGap] = Field(default_factory=list)


class ReviewIssue(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("issue"))
    task_id: str
    severity: IssueSeverity = IssueSeverity.MEDIUM
    target_type: str = Field(description="claim, report, evidence, or task")
    target_id: str = ""
    message: str


class ReviewFeedback(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("review"))
    task_id: str
    reviewer_run_id: str
    overall_score: float = Field(default=0.0, ge=0.0, le=10.0)
    approved: bool = False
    issues: list[ReviewIssue] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class QualityGateDecision(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("gate"))
    task_id: str
    gate_name: str
    status: str = Field(
        default="pending",
        description="passed, passed_with_warnings, blocked, or failed.",
    )
    passed: bool = False
    blocking: bool = False
    severity: IssueSeverity = IssueSeverity.LOW
    issue_ids: list[str] = Field(default_factory=list)
    citation_check_ids: list[str] = Field(default_factory=list)
    created_task_ids: list[str] = Field(default_factory=list)
    message: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class MemoryItem(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    task_id: str
    scope: MemoryScope = MemoryScope.RUN
    kind: MemoryKind = MemoryKind.RUN_SUMMARY
    key: str
    content: str
    summary: str = ""
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    citation_check_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    ttl_days: int | None = Field(default=None, ge=1)
    last_verified_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkingMemory(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("wm"))
    task_id: str
    current_goal: str = ""
    task_summary: str = ""
    active_competitors: list[str] = Field(default_factory=list)
    focus_areas: list[str] = Field(default_factory=list)
    current_task_key: str = ""
    completed_task_keys: list[str] = Field(default_factory=list)
    pending_task_keys: list[str] = Field(default_factory=list)
    blocked_task_keys: list[str] = Field(default_factory=list)
    skipped_task_keys: list[str] = Field(default_factory=list)
    selected_source_ids: list[str] = Field(default_factory=list)
    selected_evidence_ids: list[str] = Field(default_factory=list)
    selected_product_card_ids: list[str] = Field(default_factory=list)
    selected_claim_ids: list[str] = Field(default_factory=list)
    weak_claim_ids: list[str] = Field(default_factory=list)
    invalid_claim_ids: list[str] = Field(default_factory=list)
    review_issue_ids: list[str] = Field(default_factory=list)
    feedback_task_ids: list[str] = Field(default_factory=list)
    quality_gate_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class ContextBundle(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("ctxbundle"))
    task_id: str
    agent_role: AgentRole
    node_id: str = ""
    task_key: str = ""
    system_context: list[str] = Field(default_factory=list)
    task_context: dict[str, Any] = Field(default_factory=dict)
    working_context: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: dict[str, list[str]] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    product_card_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    citation_check_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    task_record_ids: list[str] = Field(default_factory=list)
    memory_item_ids: list[str] = Field(default_factory=list)
    guardrail_policy: list[str] = Field(default_factory=list)
    compression_level: int = Field(default=0, ge=0, le=4)
    estimated_tokens: int = Field(default=0, ge=0)
    token_budget: int = Field(default=4000, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class GuardrailCheck(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("guard"))
    task_id: str
    guardrail_name: str
    status: GuardrailStatus = GuardrailStatus.PASSED
    passed: bool = True
    severity: IssueSeverity = IssueSeverity.LOW
    target_type: str = ""
    target_id: str = ""
    message: str = ""
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    citation_check_ids: list[str] = Field(default_factory=list)
    task_record_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class LLMCall(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("llmcall"))
    task_id: str
    agent_run_id: str = ""
    node_id: str = ""
    agent_role: AgentRole
    provider: LLMProvider = LLMProvider.MOCK
    model: str = "mock-structured-v1"
    mode: LLMMode = LLMMode.LLM_WITH_FALLBACK
    prompt_id: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    prompt_summary: str = ""
    context_bundle_id: str = ""
    input_artifact_refs: dict[str, list[str]] = Field(default_factory=dict)
    output_schema: str = ""
    output_summary: str = ""
    status: RunStatus = RunStatus.COMPLETED
    used_fallback: bool = False
    fallback_reason: str = ""
    duration_ms: int = Field(default=0, ge=0)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class LLMOutput(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("llmout"))
    task_id: str
    llm_call_id: str
    agent_role: AgentRole
    output_schema: str
    raw_output: dict[str, Any] = Field(default_factory=dict)
    parsed_object_ids: list[str] = Field(default_factory=list)
    validation_status: str = "pending"
    validation_errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CitationCheck(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("cite"))
    task_id: str
    claim_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: CitationStatus = CitationStatus.PENDING
    message: str = ""
    checked_by_agent_run_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class CompetitiveReport(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("report"))
    task_id: str
    title: str = ""
    markdown: str
    claim_ids: list[str] = Field(default_factory=list)
    created_by_agent_run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    sections: dict[str, Any] = Field(default_factory=dict)


class ReportStatement(SchemaModel):
    """A reader-visible report statement with machine-auditable evidence links."""

    id: str = Field(default_factory=lambda: new_id("statement"))
    task_id: str
    report_id: str
    section: str
    line_index: int = Field(ge=0)
    statement_kind: str = "claim"
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    research_gap_ids: list[str] = Field(default_factory=list)
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    citation_status: CitationStatus = CitationStatus.PENDING
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
