from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.context import build_context_memory_artifacts, run_guardrail_checks
from app.agents import (
    AnalystAgent,
    LLMAnalystAgent,
    LLMExtractorAgent,
    LLMProfessionalAnalystAgent,
    LLMProfessionalWriterAgent,
    LLMWriterAgent,
    CitationAgent,
    CollectorAgent,
    ExtractorAgent,
    ReviewerAgent,
    WriterAgent,
)
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, LLMConfig, load_llm_config
from app.schemas import (
    AgentRole,
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    ProductCard,
    ReviewFeedback,
    RunStatus,
    SchemaModel,
    SourceDocument,
    SourceEvidence,
)
from app.tools.registry import ToolRegistry
from app.workflow.dynamic_dag import TaskBoardDrivenDAGExecutor
from app.workflow.dag import DAGExecutor, StepSpec
from app.workflow.quality_gate import run_review_quality_gate
from app.workflow.taskboard import (
    TaskBoardStore,
    build_fixed_snapshot_task_board,
)
from app.workflow.trace import PipelineSummary, TraceRecorder
from check_snapshot import DEFAULT_SNAPSHOT_ID, build_snapshot_task, collect_snapshot
from run_citation_check_demo import run_citation_checks
from run_review_demo import build_review_feedback


FIXED_WORKFLOW_STEPS = [
    "collect_sources",
    "build_product_cards",
    "build_claims",
    "check_citations",
    "build_report",
    "review_report",
]


def build_snapshot_tool_registry(
    *,
    store: ArtifactStore,
    recorder: TraceRecorder,
) -> ToolRegistry:
    registry = ToolRegistry(recorder=recorder)

    def load_many(task_id: str, artifact_types: list[str]) -> dict[str, list[dict]]:
        return {
            artifact_type: store.load_many(task_id, artifact_type)
            for artifact_type in artifact_types
        }

    def save_many(
        task_id: str,
        artifact_type: str,
        items: list[SchemaModel],
    ) -> dict[str, Any]:
        store.save_many(task_id, artifact_type, items)
        return {"artifact_type": artifact_type, "count": len(items)}

    def collect(
        task_id: str,
        snapshot_id: str,
        snapshot_root: Path | str | None = None,
    ) -> tuple[list[SourceDocument], list[SourceEvidence]]:
        task = build_snapshot_task(snapshot_id=snapshot_id, task_id=task_id)
        return collect_snapshot(task, snapshot_root=snapshot_root)

    def check_refs(task_id: str, node_id: str, message: str) -> dict[str, str]:
        return {"task_id": task_id, "node_id": node_id, "message": message}

    def check_claims(
        task_id: str,
        sources: list[SourceDocument],
        evidence: list[SourceEvidence],
        claims: list[AnalysisClaim],
    ) -> tuple[list[AnalysisClaim], list[CitationCheck]]:
        return run_citation_checks(task_id, sources, evidence, claims)

    def check_report(
        task_id: str,
        report: CompetitiveReport,
        claims: list[AnalysisClaim],
        citation_checks: list[CitationCheck],
        product_cards: list[ProductCard],
    ) -> ReviewFeedback:
        return build_review_feedback(
            task_id,
            report,
            claims,
            citation_checks,
            product_cards,
        )

    registry.register(
        "artifact_store.load_many",
        load_many,
        description="Load one or more artifact lists for a task.",
    )
    registry.register(
        "artifact_store.save_many",
        save_many,
        description="Persist a typed artifact list for a task.",
    )
    registry.register(
        "snapshot_collector.collect",
        collect,
        description="Collect local snapshot SourceDocument and SourceEvidence items.",
    )
    registry.register(
        "artifact_validator.check_refs",
        check_refs,
        description="Record a completed local reference validation check.",
    )
    registry.register(
        "citation_checker.check_claims",
        check_claims,
        description="Run deterministic claim-to-evidence citation checks.",
    )
    registry.register(
        "review_checker.check_report",
        check_report,
        description="Run deterministic report review checks.",
    )
    return registry


def build_llm_snapshot_workflow_steps(
    *,
    tools: ToolRegistry,
    llm_client: LLMClient,
    analyst_llm_client: LLMClient | None = None,
    writer_llm_client: LLMClient | None = None,
    snapshot_id: str,
    snapshot_root: Path | str | None = None,
    professional_analysis: bool = False,
) -> list[StepSpec]:
    analyst_llm_client = analyst_llm_client or llm_client
    writer_llm_client = writer_llm_client or llm_client
    collector = CollectorAgent(
        name="collector",
        role=AgentRole.COLLECTOR,
        tools=tools,
        input_artifacts=["snapshot:sources", "snapshot:evidence"],
        output_artifacts=["sources", "evidence"],
        snapshot_id=snapshot_id,
        snapshot_root=snapshot_root,
    )
    extractor = LLMExtractorAgent(
        name="extractor",
        role=AgentRole.EXTRACTOR,
        tools=tools,
        llm_client=llm_client,
        input_artifacts=["sources", "evidence", "context_bundles"],
        output_artifacts=["product_cards"],
    )
    analyst_class = (
        LLMProfessionalAnalystAgent if professional_analysis else LLMAnalystAgent
    )
    analyst_output_artifacts = (
        [
            "analysis_portfolios",
            "brief_assessments",
            "competitor_profiles",
            "intelligence_questions",
            "information_needs",
            "evidence_coverage",
            "comparability_notes",
            "claims_v2",
            "research_gaps",
            "claims",
        ]
        if professional_analysis
        else ["claims"]
    )
    analyst = analyst_class(
        name="analyst",
        role=AgentRole.ANALYST,
        tools=tools,
        llm_client=analyst_llm_client,
        input_artifacts=[
            "sources",
            "product_cards",
            "evidence",
            "context_bundles",
        ],
        output_artifacts=analyst_output_artifacts,
    )
    citation = CitationAgent(
        name="citation",
        role=AgentRole.CITATION,
        tools=tools,
        input_artifacts=["sources", "evidence", "claims"],
        output_artifacts=["claims", "citation_checks"],
    )
    writer_class = LLMProfessionalWriterAgent if professional_analysis else LLMWriterAgent
    writer_input_artifacts = (
        [
            "brief_assessments",
            "competitor_profiles",
            "evidence_coverage",
            "comparability_notes",
            "claims_v2",
            "research_gaps",
            "claims",
            "citation_checks",
        ]
        if professional_analysis
        else [
            "product_cards",
            "claims",
            "citation_checks",
            "sources",
            "evidence",
            "context_bundles",
        ]
    )
    writer = writer_class(
        name="writer",
        role=AgentRole.WRITER,
        tools=tools,
        llm_client=writer_llm_client,
        input_artifacts=writer_input_artifacts,
        output_artifacts=(
            ["reports", "report_statements"]
            if professional_analysis
            else ["reports"]
        ),
    )
    reviewer = ReviewerAgent(
        name="reviewer",
        role=AgentRole.REVIEWER,
        tools=tools,
        input_artifacts=["reports", "claims", "citation_checks", "product_cards"],
        output_artifacts=["review_feedback"],
    )
    return [
        StepSpec(
            id="collect_sources",
            label="collect_sources",
            agent=collector,
            input_refs=collector.input_artifacts,
            expected_output_refs=collector.output_artifacts,
        ),
        StepSpec(
            id="build_product_cards",
            label="build_product_cards",
            agent=extractor,
            input_refs=extractor.input_artifacts,
            expected_output_refs=extractor.output_artifacts,
            depends_on=["collect_sources"],
        ),
        StepSpec(
            id="build_claims",
            label="build_claims",
            agent=analyst,
            input_refs=analyst.input_artifacts,
            expected_output_refs=analyst.output_artifacts,
            depends_on=["build_product_cards"],
        ),
        StepSpec(
            id="check_citations",
            label="check_citations",
            agent=citation,
            input_refs=citation.input_artifacts,
            expected_output_refs=citation.output_artifacts,
            depends_on=["build_claims"],
        ),
        StepSpec(
            id="build_report",
            label="build_report",
            agent=writer,
            input_refs=writer.input_artifacts,
            expected_output_refs=writer.output_artifacts,
            depends_on=["check_citations"],
        ),
        StepSpec(
            id="review_report",
            label="review_report",
            agent=reviewer,
            input_refs=reviewer.input_artifacts,
            expected_output_refs=reviewer.output_artifacts,
            depends_on=["build_report"],
        ),
    ]


def build_snapshot_workflow_steps(
    *,
    tools: ToolRegistry,
    snapshot_id: str,
    snapshot_root: Path | str | None = None,
) -> list[StepSpec]:
    collector = CollectorAgent(
        name="collector",
        role=AgentRole.COLLECTOR,
        tools=tools,
        input_artifacts=["snapshot:sources", "snapshot:evidence"],
        output_artifacts=["sources", "evidence"],
        snapshot_id=snapshot_id,
        snapshot_root=snapshot_root,
    )
    extractor = ExtractorAgent(
        name="extractor",
        role=AgentRole.EXTRACTOR,
        tools=tools,
        input_artifacts=["sources", "evidence"],
        output_artifacts=["product_cards"],
    )
    analyst = AnalystAgent(
        name="analyst",
        role=AgentRole.ANALYST,
        tools=tools,
        input_artifacts=["product_cards", "evidence"],
        output_artifacts=["claims"],
    )
    citation = CitationAgent(
        name="citation",
        role=AgentRole.CITATION,
        tools=tools,
        input_artifacts=["sources", "evidence", "claims"],
        output_artifacts=["claims", "citation_checks"],
    )
    writer = WriterAgent(
        name="writer",
        role=AgentRole.WRITER,
        tools=tools,
        input_artifacts=[
            "product_cards",
            "claims",
            "citation_checks",
            "sources",
            "evidence",
        ],
        output_artifacts=["reports"],
    )
    reviewer = ReviewerAgent(
        name="reviewer",
        role=AgentRole.REVIEWER,
        tools=tools,
        input_artifacts=["reports", "claims", "citation_checks", "product_cards"],
        output_artifacts=["review_feedback"],
    )

    return [
        StepSpec(
            id="collect_sources",
            label="collect_sources",
            agent=collector,
            input_refs=collector.input_artifacts,
            expected_output_refs=collector.output_artifacts,
        ),
        StepSpec(
            id="build_product_cards",
            label="build_product_cards",
            agent=extractor,
            input_refs=extractor.input_artifacts,
            expected_output_refs=extractor.output_artifacts,
            depends_on=["collect_sources"],
        ),
        StepSpec(
            id="build_claims",
            label="build_claims",
            agent=analyst,
            input_refs=analyst.input_artifacts,
            expected_output_refs=analyst.output_artifacts,
            depends_on=["build_product_cards"],
        ),
        StepSpec(
            id="check_citations",
            label="check_citations",
            agent=citation,
            input_refs=citation.input_artifacts,
            expected_output_refs=citation.output_artifacts,
            depends_on=["build_claims"],
        ),
        StepSpec(
            id="build_report",
            label="build_report",
            agent=writer,
            input_refs=writer.input_artifacts,
            expected_output_refs=writer.output_artifacts,
            depends_on=["check_citations"],
        ),
        StepSpec(
            id="review_report",
            label="review_report",
            agent=reviewer,
            input_refs=reviewer.input_artifacts,
            expected_output_refs=reviewer.output_artifacts,
            depends_on=["build_report"],
        ),
    ]


def run_snapshot_agent_workflow(
    *,
    task_id: str,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
) -> tuple[PipelineSummary, TraceRecorder]:
    store = ArtifactStore(root_dir=artifact_root)
    recorder = TraceRecorder(store=store, task_id=task_id)
    tools = build_snapshot_tool_registry(store=store, recorder=recorder)
    task = build_snapshot_task(snapshot_id=snapshot_id, task_id=task_id)
    executor = DAGExecutor(
        task_id=task_id,
        task=task,
        store=store,
        recorder=recorder,
        steps=build_snapshot_workflow_steps(
            tools=tools,
            snapshot_id=snapshot_id,
            snapshot_root=snapshot_root,
        ),
    )
    recorder = executor.execute()
    summary = build_pipeline_summary(task_id=task_id, store=store, recorder=recorder)
    store.save_many(task_id, "pipeline_summary", [summary])
    return summary, recorder



def run_snapshot_taskboard_workflow(
    *,
    task_id: str,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
) -> tuple[PipelineSummary, TraceRecorder]:
    store = ArtifactStore(root_dir=artifact_root)
    recorder = TraceRecorder(store=store, task_id=task_id)
    tools = build_snapshot_tool_registry(store=store, recorder=recorder)
    task = build_snapshot_task(snapshot_id=snapshot_id, task_id=task_id)
    task_board_store = TaskBoardStore(store=store)
    board = build_fixed_snapshot_task_board(task_id, completed=False)
    board.metadata = {
        **board.metadata,
        "workflow_runtime": "taskboard_driven_dynamic_dag_v1",
        "scheduler": "TaskBoardStore.ready_records",
    }
    task_board_store.save_board(board)

    executor = TaskBoardDrivenDAGExecutor(
        task_id=task_id,
        task=task,
        store=store,
        recorder=recorder,
        task_board_store=task_board_store,
        steps=build_snapshot_workflow_steps(
            tools=tools,
            snapshot_id=snapshot_id,
            snapshot_root=snapshot_root,
        ),
    )
    recorder = executor.execute()
    quality_gate = None
    context_result = None
    guardrail_checks = []
    if recorder.dag_nodes and all(node.status == RunStatus.COMPLETED for node in recorder.dag_nodes):
        quality_gate = run_review_quality_gate(
            task_id=task_id,
            store=store,
            task_board_store=task_board_store,
        )
        context_result = build_context_memory_artifacts(
            task_id=task_id,
            store=store,
        )
        guardrail_checks = run_guardrail_checks(
            task_id=task_id,
            store=store,
        )

    summary = build_pipeline_summary(task_id=task_id, store=store, recorder=recorder)
    summary.metadata = {
        **summary.metadata,
        "workflow_steps": FIXED_WORKFLOW_STEPS,
        "runtime": "taskboard_driven_dynamic_dag_v1",
        "tool_boundary": "local_tool_registry",
        "quality_gate_status": quality_gate.status if quality_gate else "not_run",
        "quality_gate_passed": quality_gate.passed if quality_gate else False,
        "feedback_tasks_count": (
            len(quality_gate.created_task_ids) if quality_gate else 0
        ),
        "working_memory_count": 1 if context_result else 0,
        "memory_items_count": (
            len(context_result["memory_items"]) if context_result else 0
        ),
        "context_bundles_count": (
            len(context_result["context_bundles"]) if context_result else 0
        ),
        "guardrail_checks_count": len(guardrail_checks),
        "guardrail_failed_count": sum(
            1 for check in guardrail_checks if check.status == "failed"
        ),
        "guardrail_warning_count": sum(
            1 for check in guardrail_checks if check.status == "warning"
        ),
    }
    store.save_many(task_id, "pipeline_summary", [summary])
    return summary, recorder


def run_snapshot_llm_agent_workflow(
    *,
    task_id: str,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
    llm_mode: str | None = None,
    professional_analysis: bool = False,
    llm_config_override: LLMConfig | None = None,
    analyst_llm_config: LLMConfig | None = None,
    writer_llm_config: LLMConfig | None = None,
    runtime_name: str | None = None,
    analysis_task: AnalysisTask | None = None,
    reuse_task_board: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> tuple[PipelineSummary, TraceRecorder]:
    store = ArtifactStore(root_dir=artifact_root)
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])
    store.save_many(task_id, "report_statements", [])
    if professional_analysis:
        for artifact_type in [
            "analysis_portfolios",
            "brief_assessments",
            "competitor_profiles",
            "intelligence_questions",
            "information_needs",
            "evidence_coverage",
            "comparability_notes",
            "claims_v2",
            "research_gaps",
        ]:
            store.save_many(task_id, artifact_type, [])
    recorder = TraceRecorder(store=store, task_id=task_id)
    tools = build_snapshot_tool_registry(store=store, recorder=recorder)
    task = analysis_task or build_snapshot_task(snapshot_id=snapshot_id, task_id=task_id)
    if task.id != task_id or task.task_id != task_id:
        task = task.model_copy(update={"id": task_id, "task_id": task_id})
    llm_config = llm_config_override or load_llm_config(mode=llm_mode)
    effective_analyst_config = analyst_llm_config or llm_config
    effective_writer_config = writer_llm_config or llm_config
    routed_analyst = effective_analyst_config != llm_config
    routed_writer = effective_writer_config != llm_config
    role_routed = routed_analyst or routed_writer
    writer_only_real = (
        routed_writer
        and not llm_config.is_real_provider
        and not effective_analyst_config.is_real_provider
        and effective_writer_config.is_real_provider
        and effective_writer_config.enable_real_calls
    )
    analyst_writer_real = (
        not llm_config.is_real_provider
        and effective_analyst_config.is_real_provider
        and effective_analyst_config.enable_real_calls
        and effective_writer_config.is_real_provider
        and effective_writer_config.enable_real_calls
    )
    resolved_runtime = runtime_name or (
        "taskboard_driven_step6c_professional_workflow_v2"
        if professional_analysis
        else "taskboard_driven_llm_agent_workflow_v1"
    )
    task_board_store = TaskBoardStore(store=store)
    existing_board = task_board_store.load_board(task_id) if reuse_task_board else None
    board = existing_board or build_fixed_snapshot_task_board(task_id, completed=False)
    board.metadata = {
        **board.metadata,
        "workflow_runtime": resolved_runtime,
        "scheduler": "TaskBoardStore.ready_records",
        "llm_provider": llm_config.provider.value,
        "llm_model": llm_config.model,
        "llm_mode": llm_config.mode.value,
        "output_language": llm_config.output_language,
        "real_calls_enabled": llm_config.enable_real_calls,
        "llm_api_style": llm_config.api_style,
        "structured_output_mode": llm_config.structured_output_mode,
        "thinking_mode": llm_config.thinking_mode,
        "analyst_llm_provider": effective_analyst_config.provider.value,
        "analyst_llm_model": effective_analyst_config.model,
        "analyst_real_calls_enabled": effective_analyst_config.enable_real_calls,
        "writer_llm_provider": effective_writer_config.provider.value,
        "writer_llm_model": effective_writer_config.model,
        "writer_real_calls_enabled": effective_writer_config.enable_real_calls,
        "writer_only_real": writer_only_real,
        "analyst_writer_real": analyst_writer_real,
        "user_task_execution": analysis_task is not None,
    }
    task_board_store.save_board(board)
    llm_client = LLMClient(config=llm_config, store=store)
    analyst_llm_client = (
        LLMClient(config=effective_analyst_config, store=store)
        if routed_analyst
        else llm_client
    )
    writer_llm_client = (
        LLMClient(config=effective_writer_config, store=store)
        if routed_writer
        else llm_client
    )

    executor = TaskBoardDrivenDAGExecutor(
        task_id=task_id,
        task=task,
        store=store,
        recorder=recorder,
        task_board_store=task_board_store,
        steps=build_llm_snapshot_workflow_steps(
            tools=tools,
            llm_client=llm_client,
            analyst_llm_client=analyst_llm_client,
            writer_llm_client=writer_llm_client,
            snapshot_id=snapshot_id,
            snapshot_root=snapshot_root,
            professional_analysis=professional_analysis,
        ),
        progress_callback=progress_callback,
    )
    recorder = executor.execute()
    quality_gate = None
    context_result = None
    guardrail_checks = []
    if recorder.dag_nodes and all(node.status == RunStatus.COMPLETED for node in recorder.dag_nodes):
        quality_gate = run_review_quality_gate(
            task_id=task_id,
            store=store,
            task_board_store=task_board_store,
        )
        context_result = build_context_memory_artifacts(
            task_id=task_id,
            store=store,
        )
        guardrail_checks = run_guardrail_checks(
            task_id=task_id,
            store=store,
        )

    llm_calls = store.load_many(task_id, "llm_calls")
    llm_outputs = store.load_many(task_id, "llm_outputs")
    analyst_calls = [
        call for call in llm_calls if call.get("agent_role") == "analyst"
    ]
    latest_analyst_call = analyst_calls[-1] if analyst_calls else {}
    reports = store.load_many(task_id, "reports")
    latest_report = reports[-1] if reports else {}
    summary = build_pipeline_summary(task_id=task_id, store=store, recorder=recorder)
    summary.metadata = {
        **summary.metadata,
        "workflow_steps": FIXED_WORKFLOW_STEPS,
        "runtime": resolved_runtime,
        "tool_boundary": "local_tool_registry",
        "llm_provider": (
            "mixed" if role_routed else llm_client.config.provider.value
        ),
        "llm_model": (
            (
                f"{effective_analyst_config.model} (Analyst) + "
                f"{effective_writer_config.model} (Writer) + "
                f"{llm_client.config.model} (Extractor)"
            )
            if routed_analyst
            else (
                f"{effective_writer_config.model} (Writer) + "
                f"{llm_client.config.model} (upstream)"
                if routed_writer
                else llm_client.config.model
            )
        ),
        "llm_mode": (
            "role_routed" if role_routed else llm_client.config.mode.value
        ),
        "output_language": llm_client.config.output_language,
        "real_calls_enabled": (
            llm_client.config.enable_real_calls
            or effective_analyst_config.enable_real_calls
            or effective_writer_config.enable_real_calls
        ),
        "llm_api_surface": (
            "role_routed" if role_routed else llm_client.config.api_style
        ),
        "structured_output_mode": effective_writer_config.structured_output_mode,
        "thinking_mode": effective_writer_config.thinking_mode,
        "llm_max_retries": effective_writer_config.max_retries,
        "llm_calls_count": len(llm_calls),
        "llm_outputs_count": len(llm_outputs),
        "llm_fallback_count": sum(
            1 for call in llm_calls if call.get("used_fallback")
        ),
        "mock_llm_calls_count": sum(
            1 for call in llm_calls if call.get("provider") == "mock"
        ),
        "real_llm_calls_count": sum(
            1 for call in llm_calls if call.get("provider") != "mock"
        ),
        "real_writer_calls_count": sum(
            1
            for call in llm_calls
            if call.get("provider") != "mock" and call.get("agent_role") == "writer"
        ),
        "real_analyst_calls_count": sum(
            1
            for call in llm_calls
            if call.get("provider") != "mock" and call.get("agent_role") == "analyst"
        ),
        "real_extractor_calls_count": sum(
            1
            for call in llm_calls
            if call.get("provider") != "mock" and call.get("agent_role") == "extractor"
        ),
        "analyst_rejected_claims_count": sum(
            int((call.get("metadata") or {}).get("rejected_portfolio_claims_count", 0))
            for call in llm_calls
            if call.get("agent_role") == "analyst"
        ),
        "writer_only_real": writer_only_real,
        "analyst_writer_real": analyst_writer_real,
        "analyst_llm_provider": effective_analyst_config.provider.value,
        "analyst_llm_model": effective_analyst_config.model,
        "analyst_llm_mode": effective_analyst_config.mode.value,
        "analyst_prompt_id": latest_analyst_call.get("prompt_id", ""),
        "analyst_prompt_version": latest_analyst_call.get("prompt_version", ""),
        "writer_llm_provider": effective_writer_config.provider.value,
        "writer_llm_model": effective_writer_config.model,
        "writer_llm_mode": effective_writer_config.mode.value,
        "upstream_llm_provider": llm_client.config.provider.value,
        "upstream_llm_model": llm_client.config.model,
        "professional_analysis": professional_analysis,
        "user_task_execution": analysis_task is not None,
        "analysis_task_id": task.id,
        "analysis_subject": task.report_subject or task.query,
        "analysis_portfolios_count": len(
            store.load_many(task_id, "analysis_portfolios")
        ),
        "competitor_profiles_count": len(
            store.load_many(task_id, "competitor_profiles")
        ),
        "claims_v2_count": len(store.load_many(task_id, "claims_v2")),
        "research_gaps_count": len(store.load_many(task_id, "research_gaps")),
        "professional_writer": professional_analysis,
        "report_statements_count": len(
            store.load_many(task_id, "report_statements")
        ),
        "report_title": latest_report.get("title", ""),
        "writer_prompt_id": (latest_report.get("sections") or {}).get(
            "prompt_id", ""
        ),
        "writer_prompt_version": (latest_report.get("sections") or {}).get(
            "report_version", ""
        ),
        "quality_gate_status": quality_gate.status if quality_gate else "not_run",
        "quality_gate_passed": quality_gate.passed if quality_gate else False,
        "feedback_tasks_count": (
            len(quality_gate.created_task_ids) if quality_gate else 0
        ),
        "working_memory_count": 1 if context_result else 0,
        "memory_items_count": (
            len(context_result["memory_items"]) if context_result else 0
        ),
        "context_bundles_count": (
            len(context_result["context_bundles"]) if context_result else 0
        ),
        "guardrail_checks_count": len(guardrail_checks),
        "guardrail_failed_count": sum(
            1 for check in guardrail_checks if check.status == "failed"
        ),
        "guardrail_warning_count": sum(
            1 for check in guardrail_checks if check.status == "warning"
        ),
    }
    store.save_many(task_id, "pipeline_summary", [summary])
    return summary, recorder


def run_step6c_professional_workflow(
    *,
    task_id: str,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
    llm_mode: str | None = None,
) -> tuple[PipelineSummary, TraceRecorder]:
    """Run the candidate v2.2 analyst through the existing evidence-first chain."""
    return run_snapshot_llm_agent_workflow(
        task_id=task_id,
        snapshot_id=snapshot_id,
        snapshot_root=snapshot_root,
        artifact_root=artifact_root,
        llm_mode=llm_mode,
        professional_analysis=True,
    )


def run_step6c_writer_real_pilot_workflow(
    *,
    task_id: str,
    writer_llm_config: LLMConfig,
    upstream_llm_config: LLMConfig,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
) -> tuple[PipelineSummary, TraceRecorder]:
    """Run governed mock upstream agents with exactly one real Writer call."""

    return run_snapshot_llm_agent_workflow(
        task_id=task_id,
        snapshot_id=snapshot_id,
        snapshot_root=snapshot_root,
        artifact_root=artifact_root,
        professional_analysis=True,
        llm_config_override=upstream_llm_config,
        writer_llm_config=writer_llm_config,
        runtime_name="taskboard_driven_step6c_writer_real_pilot_v1",
    )


def run_step6c_dual_real_pilot_workflow(
    *,
    task_id: str,
    analyst_llm_config: LLMConfig,
    writer_llm_config: LLMConfig,
    extractor_llm_config: LLMConfig,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
) -> tuple[PipelineSummary, TraceRecorder]:
    """Run a mock Extractor with exactly one real Analyst and Writer call each."""

    return run_snapshot_llm_agent_workflow(
        task_id=task_id,
        snapshot_id=snapshot_id,
        snapshot_root=snapshot_root,
        artifact_root=artifact_root,
        professional_analysis=True,
        llm_config_override=extractor_llm_config,
        analyst_llm_config=analyst_llm_config,
        writer_llm_config=writer_llm_config,
        runtime_name="taskboard_driven_step6c3_dual_real_pilot_v1",
    )


def build_pipeline_summary(
    *,
    task_id: str,
    store: ArtifactStore,
    recorder: TraceRecorder,
) -> PipelineSummary:
    sources = load_schema_items(store, task_id, "sources", SourceDocument)
    evidence = load_schema_items(store, task_id, "evidence", SourceEvidence)
    product_cards = load_schema_items(store, task_id, "product_cards", ProductCard)
    claims = load_schema_items(store, task_id, "claims", AnalysisClaim)
    citation_checks = load_schema_items(
        store,
        task_id,
        "citation_checks",
        CitationCheck,
    )
    reports = load_schema_items(store, task_id, "reports", CompetitiveReport)
    review_feedback = load_schema_items(
        store,
        task_id,
        "review_feedback",
        ReviewFeedback,
    )

    supported_count = sum(1 for check in citation_checks if check.status == "supported")
    weak_count = sum(1 for check in citation_checks if check.status == "weak")
    latest_review = review_feedback[-1] if review_feedback else None
    pipeline_status = (
        "completed"
        if recorder.dag_nodes
        and all(node.status == RunStatus.COMPLETED for node in recorder.dag_nodes)
        else "failed"
    )

    return PipelineSummary(
        task_id=task_id,
        sources_count=len(sources),
        evidence_count=len(evidence),
        product_cards_count=len(product_cards),
        claims_count=len(claims),
        citation_checks_count=len(citation_checks),
        reports_count=len(reports),
        review_feedback_count=len(review_feedback),
        dag_nodes_count=len(recorder.dag_nodes),
        agent_runs_count=len(recorder.agent_runs),
        tool_calls_count=len(recorder.tool_calls),
        supported_count=supported_count,
        weak_count=weak_count,
        approved=latest_review.approved if latest_review else False,
        review_score=latest_review.overall_score if latest_review else 0.0,
        pipeline_status=pipeline_status,
        metadata={
            "workflow_steps": FIXED_WORKFLOW_STEPS,
            "runtime": "lightweight_agent_runtime",
            "tool_boundary": "local_tool_registry",
        },
    )


def load_schema_items(store: ArtifactStore, task_id: str, artifact_type: str, model):
    return [model(**item) for item in store.load_many(task_id, artifact_type)]
