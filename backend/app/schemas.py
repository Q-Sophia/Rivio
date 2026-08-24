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


class ResearchTask(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("researchtask"))
    task_id: str
    information_need_id: str
    title: str
    objective: str
    competitor: str
    dimension: str
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
