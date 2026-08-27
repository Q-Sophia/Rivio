from __future__ import annotations

import threading
from typing import Any

from app.agents import LLMProfessionalWriterAgent, ReviewerAgent
from app.agents.runtime import AgentRuntime
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, LLMConfig, build_deepseek_compatible_config
from app.schemas import (
    AgentContext,
    AgentRole,
    AgentRun,
    AnalysisTask,
    AnalysisClaim,
    AnalysisClaimV2,
    CitationCheck,
    ComparabilityNote,
    CompetitiveReport,
    CompetitorProfile,
    DAGNode,
    ExecutionMode,
    RunStatus,
    ResearchGap,
    TaskRecord,
    TaskStatus,
    TaskType,
    ToolCall,
)
from app.reporting import build_report_statements
from app.workflow.quality_gate import run_review_quality_gate
from app.workflow.snapshot_pipeline import build_snapshot_tool_registry
from app.workflow.taskboard import TaskBoardStore, status_value
from app.workflow.trace import TraceRecorder


WRITER_TASK_KEY = "build_report"
REVIEWER_TASK_KEY = "review_report"
QUALITY_GATE_TASK_KEY = "quality_gate_report"

WRITER_INPUT_REFS = [
    "brief_assessments",
    "competitor_profiles",
    "evidence_coverage",
    "comparability_notes",
    "claims_v2",
    "research_gaps",
    "claims",
    "citation_checks",
]


class ResearchReportingService:
    """Resume-safe task-centric Writer -> Reviewer -> QualityGate orchestration."""

    _lock = threading.RLock()

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        llm_client: LLMClient | None = None,
    ):
        self.store = store or ArtifactStore()
        self.board_store = TaskBoardStore(self.store)
        self._injected_llm_client = llm_client

    def get_payload(self, task_id: str) -> dict[str, Any]:
        self._require_task(task_id)
        reports = self.store.load_many(task_id, "reports")
        statements = self.store.load_many(task_id, "report_statements")
        reviews = self.store.load_many(task_id, "review_feedback")
        gates = self.store.load_many(task_id, "quality_gates")
        writer_calls = [
            item
            for item in self.store.load_many(task_id, "llm_calls")
            if str(item.get("agent_role") or "") == AgentRole.WRITER.value
        ]
        if not reports:
            stage = "awaiting_writer"
        elif not statements:
            stage = "repairing_report_statements"
        elif not reviews:
            stage = "awaiting_reviewer"
        elif not gates:
            stage = "awaiting_quality_gate"
        else:
            stage = "completed"
        return {
            "task_id": task_id,
            "stage": stage,
            "completed": stage == "completed",
            "writer_required": not bool(reports),
            "report_statements_required": bool(reports) and not bool(statements),
            "reviewer_required": bool(reports) and bool(statements) and not bool(reviews),
            "quality_gate_required": bool(reviews) and not bool(gates),
            "report": reports[-1] if reports else None,
            "report_statements": statements,
            "review": reviews[-1] if reviews else None,
            "quality_gate": gates[-1] if gates else None,
            "feedback_tasks": self.store.load_many(task_id, "feedback_tasks"),
            "writer_llm_calls": writer_calls,
        }

    def run(
        self,
        task_id: str,
        *,
        mode: ExecutionMode | str,
        acknowledge_real_llm_call: bool,
    ) -> dict[str, Any]:
        selected_mode = ExecutionMode(mode)
        if selected_mode != ExecutionMode.DEEPSEEK:
            raise ValueError("Reporting 当前只接受显式 DeepSeek（真实）Writer 模式。")

        with self._lock:
            task = self._require_task(task_id)
            self.board_store.require_board(task_id)
            self._validate_inputs(task_id)
            initial = self.get_payload(task_id)
            if initial["completed"]:
                return {
                    **initial,
                    "status": "completed",
                    "real_llm_calls_this_run": 0,
                    "resumed_from": "completed",
                    "message": "报告、审查和质量闸门均已存在；未重复调用 Writer。",
                }

            recorder = self._load_recorder(task_id)
            tools = build_snapshot_tool_registry(store=self.store, recorder=recorder)
            writer_calls_this_run = 0
            resumed_from = initial["stage"]

            if initial["writer_required"]:
                if not acknowledge_real_llm_call:
                    raise ValueError("必须明确确认本次会产生 1 次真实 DeepSeek Writer 调用。")
                llm_client = self._writer_llm_client()
                before_calls = len(self.store.load_many(task_id, "llm_calls"))
                self._run_writer(task, recorder, tools, llm_client)
                writer_calls_this_run = (
                    len(self.store.load_many(task_id, "llm_calls")) - before_calls
                )
                if writer_calls_this_run != 1:
                    raise RuntimeError("Reporting Writer 必须且只能记录 1 次 LLM 调用。")

            self._ensure_report_statements(task_id)

            if not self.store.load_many(task_id, "review_feedback"):
                self._run_reviewer(task, recorder, tools)

            if not self.store.load_many(task_id, "quality_gates"):
                self._ensure_completed_reviewer_record(task_id)
                self._run_quality_gate(task, recorder)

            payload = self.get_payload(task_id)
            if not payload["completed"]:
                raise RuntimeError("Reporting 未生成完整的 report/review/quality gate 产物。")
            return {
                **payload,
                "status": "completed",
                "real_llm_calls_this_run": writer_calls_this_run,
                "resumed_from": resumed_from,
                "message": "当前任务已生成正式报告，并完成 Reviewer 与质量闸门。",
            }

    def _run_writer(
        self,
        task: AnalysisTask,
        recorder: TraceRecorder,
        tools,
        llm_client: LLMClient,
    ) -> None:
        task_id = task.id
        dependency = self._completed_citation_dependency(task_id)
        record = TaskRecord(
            id="queue_task_centric_build_report",
            task_id=task_id,
            task_key=WRITER_TASK_KEY,
            task_type=TaskType.BUILD_REPORT,
            target_agent_role=AgentRole.WRITER,
            status=TaskStatus.READY,
            depends_on=[dependency] if dependency else [],
            input_refs=WRITER_INPUT_REFS,
            output_refs=["reports", "report_statements"],
            reason="基于当前任务已校验的分析结论生成正式竞品报告。",
            metadata={
                "source": "task_centric_research_reporting",
                "real_llm_calls_authorized": 1,
            },
        )
        self.board_store.upsert_record(task_id, record)
        node = self._replace_node(
            recorder,
            DAGNode(
                id="task_centric_build_report",
                task_id=task_id,
                label=WRITER_TASK_KEY,
                agent_role=AgentRole.WRITER,
                status=RunStatus.RUNNING,
                depends_on=["step6f_check_research_citations"] if dependency else [],
                input_refs=WRITER_INPUT_REFS,
            ),
        )
        self.board_store.update_status(
            task_id,
            WRITER_TASK_KEY,
            TaskStatus.CLAIMED,
            claimed_by_agent="professional_research_writer_agent",
            node_id=node.id,
        )
        self.board_store.update_status(
            task_id,
            WRITER_TASK_KEY,
            TaskStatus.RUNNING,
            claimed_by_agent="professional_research_writer_agent",
            node_id=node.id,
        )
        result = AgentRuntime(store=self.store, recorder=recorder).run(
            agent=LLMProfessionalWriterAgent(
                name="professional_research_writer_agent",
                role=AgentRole.WRITER,
                tools=tools,
                llm_client=llm_client,
                input_artifacts=WRITER_INPUT_REFS,
                output_artifacts=["reports", "report_statements"],
            ),
            context=AgentContext(
                task_id=task_id,
                task=task,
                node_id=node.id,
                input_refs=WRITER_INPUT_REFS,
                metadata={
                    "source": "task_centric_research_reporting",
                    "explicit_real_llm_authorization": True,
                },
            ),
            node=node,
        )
        node.status = (
            RunStatus.COMPLETED
            if result.status == RunStatus.COMPLETED.value
            else RunStatus.FAILED
        )
        recorder.save_trace()
        if result.status != RunStatus.COMPLETED.value:
            self.board_store.update_status(
                task_id,
                WRITER_TASK_KEY,
                TaskStatus.FAILED,
                error=result.error,
                claimed_by_agent="professional_research_writer_agent",
                node_id=node.id,
            )
            raise RuntimeError(result.error or "DeepSeek Writer 执行失败。")
        if not self.store.load_many(task_id, "reports"):
            raise RuntimeError("Writer 未保存 reports Artifact。")
        if not self.store.load_many(task_id, "report_statements"):
            raise RuntimeError("Writer 未保存 report_statements Artifact。")
        self.board_store.update_status(
            task_id,
            WRITER_TASK_KEY,
            TaskStatus.COMPLETED,
            output_refs=["reports", "report_statements"],
            claimed_by_agent="professional_research_writer_agent",
            node_id=node.id,
        )

    def _run_reviewer(self, task: AnalysisTask, recorder: TraceRecorder, tools) -> None:
        task_id = task.id
        self._ensure_completed_writer_record(task_id)
        record = TaskRecord(
            id="queue_task_centric_review_report",
            task_id=task_id,
            task_key=REVIEWER_TASK_KEY,
            task_type=TaskType.REVIEW_REPORT,
            target_agent_role=AgentRole.REVIEWER,
            status=TaskStatus.PENDING,
            depends_on=[WRITER_TASK_KEY],
            input_refs=["reports", "claims", "citation_checks", "product_cards"],
            output_refs=["review_feedback"],
            reason="审查当前任务的正式报告及其结论引用链。",
            metadata={"source": "task_centric_research_reporting"},
        )
        self.board_store.upsert_record(task_id, record)
        self.board_store.mark_ready_tasks(task_id)
        node = self._replace_node(
            recorder,
            DAGNode(
                id="task_centric_review_report",
                task_id=task_id,
                label=REVIEWER_TASK_KEY,
                agent_role=AgentRole.REVIEWER,
                status=RunStatus.RUNNING,
                depends_on=["task_centric_build_report"],
                input_refs=record.input_refs,
            ),
        )
        self.board_store.update_status(
            task_id,
            REVIEWER_TASK_KEY,
            TaskStatus.CLAIMED,
            claimed_by_agent="reviewer_agent",
            node_id=node.id,
        )
        self.board_store.update_status(
            task_id,
            REVIEWER_TASK_KEY,
            TaskStatus.RUNNING,
            claimed_by_agent="reviewer_agent",
            node_id=node.id,
        )
        result = AgentRuntime(store=self.store, recorder=recorder).run(
            agent=ReviewerAgent(
                name="reviewer_agent",
                role=AgentRole.REVIEWER,
                tools=tools,
                input_artifacts=record.input_refs,
                output_artifacts=["review_feedback"],
            ),
            context=AgentContext(
                task_id=task_id,
                task=task,
                node_id=node.id,
                input_refs=record.input_refs,
                metadata={"source": "task_centric_research_reporting"},
            ),
            node=node,
        )
        node.status = (
            RunStatus.COMPLETED
            if result.status == RunStatus.COMPLETED.value
            else RunStatus.FAILED
        )
        recorder.save_trace()
        if result.status != RunStatus.COMPLETED.value:
            self.board_store.update_status(
                task_id,
                REVIEWER_TASK_KEY,
                TaskStatus.FAILED,
                error=result.error,
                claimed_by_agent="reviewer_agent",
                node_id=node.id,
            )
            raise RuntimeError(result.error or "ReviewerAgent 执行失败。")
        self.board_store.update_status(
            task_id,
            REVIEWER_TASK_KEY,
            TaskStatus.COMPLETED,
            output_refs=["review_feedback"],
            claimed_by_agent="reviewer_agent",
            node_id=node.id,
        )

    def _run_quality_gate(self, task: AnalysisTask, recorder: TraceRecorder) -> None:
        task_id = task.id
        record = TaskRecord(
            id="queue_task_centric_quality_gate",
            task_id=task_id,
            task_key=QUALITY_GATE_TASK_KEY,
            task_type=TaskType.FINALIZE_REPORT,
            target_agent_role=AgentRole.ORCHESTRATOR,
            status=TaskStatus.PENDING,
            depends_on=[REVIEWER_TASK_KEY],
            input_refs=["reports", "review_feedback", "claims", "citation_checks"],
            output_refs=["quality_gates", "feedback_tasks"],
            reason="根据审查与引用状态生成质量闸门和后续反馈任务。",
            metadata={"source": "task_centric_research_reporting"},
        )
        self.board_store.upsert_record(task_id, record)
        self.board_store.mark_ready_tasks(task_id)
        node = self._replace_node(
            recorder,
            DAGNode(
                id="task_centric_quality_gate",
                task_id=task_id,
                label=QUALITY_GATE_TASK_KEY,
                agent_role=AgentRole.ORCHESTRATOR,
                status=RunStatus.RUNNING,
                depends_on=["task_centric_review_report"],
                input_refs=record.input_refs,
            ),
        )
        self.board_store.update_status(
            task_id,
            QUALITY_GATE_TASK_KEY,
            TaskStatus.RUNNING,
            claimed_by_agent="quality_gate_orchestrator",
            node_id=node.id,
        )
        try:
            run_review_quality_gate(
                task_id=task_id,
                store=self.store,
                task_board_store=self.board_store,
            )
        except Exception as exc:
            node.status = RunStatus.FAILED
            recorder.save_trace()
            self.board_store.update_status(
                task_id,
                QUALITY_GATE_TASK_KEY,
                TaskStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                claimed_by_agent="quality_gate_orchestrator",
                node_id=node.id,
            )
            raise
        node.status = RunStatus.COMPLETED
        node.output_refs = ["quality_gates", "feedback_tasks"]
        recorder.save_trace()
        self.board_store.update_status(
            task_id,
            QUALITY_GATE_TASK_KEY,
            TaskStatus.COMPLETED,
            output_refs=node.output_refs,
            claimed_by_agent="quality_gate_orchestrator",
            node_id=node.id,
        )

    def _ensure_completed_writer_record(self, task_id: str) -> None:
        current = self.board_store.get_record(task_id, WRITER_TASK_KEY)
        if current and status_value(current.status) == TaskStatus.COMPLETED.value:
            return
        self.board_store.upsert_record(
            task_id,
            TaskRecord(
                id="queue_task_centric_build_report",
                task_id=task_id,
                task_key=WRITER_TASK_KEY,
                task_type=TaskType.BUILD_REPORT,
                target_agent_role=AgentRole.WRITER,
                status=TaskStatus.COMPLETED,
                input_refs=WRITER_INPUT_REFS,
                output_refs=["reports", "report_statements"],
                reason="已有报告 Artifact；恢复时不重复调用 Writer。",
                metadata={
                    "source": "task_centric_research_reporting_resume",
                    "writer_skipped_existing_report": True,
                },
            ),
        )

    def _ensure_report_statements(self, task_id: str) -> None:
        if self.store.load_many(task_id, "report_statements"):
            return
        reports = [
            CompetitiveReport(**item)
            for item in self.store.load_many(task_id, "reports")
        ]
        if not reports:
            raise RuntimeError("无法恢复 report_statements：当前任务没有 reports Artifact。")
        report = reports[-1]
        statements = build_report_statements(
            report=report,
            claims_v2=[
                AnalysisClaimV2(**item)
                for item in self.store.load_many(task_id, "claims_v2")
            ],
            legacy_claims=[
                AnalysisClaim(**item)
                for item in self.store.load_many(task_id, "claims")
            ],
            citation_checks=[
                CitationCheck(**item)
                for item in self.store.load_many(task_id, "citation_checks")
            ],
            profiles=[
                CompetitorProfile(**item)
                for item in self.store.load_many(task_id, "competitor_profiles")
            ],
            comparability_notes=[
                ComparabilityNote(**item)
                for item in self.store.load_many(task_id, "comparability_notes")
            ],
            research_gaps=[
                ResearchGap(**item)
                for item in self.store.load_many(task_id, "research_gaps")
            ],
        )
        report = report.model_copy(
            update={
                "sections": {
                    **report.sections,
                    "report_statement_ids": [item.id for item in statements],
                    "report_statement_count": len(statements),
                }
            }
        )
        self.store.save_many(task_id, "reports", [report])
        self.store.save_many(task_id, "report_statements", statements)

    def _completed_citation_dependency(self, task_id: str) -> str:
        for key in ("check_research_claim_citations", "check_citations"):
            record = self.board_store.get_record(task_id, key)
            if record and status_value(record.status) == TaskStatus.COMPLETED.value:
                return key
        return ""

    def _ensure_completed_reviewer_record(self, task_id: str) -> None:
        current = self.board_store.get_record(task_id, REVIEWER_TASK_KEY)
        if current and status_value(current.status) == TaskStatus.COMPLETED.value:
            return
        self._ensure_completed_writer_record(task_id)
        self.board_store.upsert_record(
            task_id,
            TaskRecord(
                id="queue_task_centric_review_report",
                task_id=task_id,
                task_key=REVIEWER_TASK_KEY,
                task_type=TaskType.REVIEW_REPORT,
                target_agent_role=AgentRole.REVIEWER,
                status=TaskStatus.COMPLETED,
                depends_on=[WRITER_TASK_KEY],
                input_refs=["reports", "claims", "citation_checks", "product_cards"],
                output_refs=["review_feedback"],
                reason="已有 ReviewFeedback Artifact；恢复时直接进入质量闸门。",
                metadata={
                    "source": "task_centric_research_reporting_resume",
                    "reviewer_skipped_existing_review": True,
                },
            ),
        )

    def _validate_inputs(self, task_id: str) -> None:
        labels = {
            "brief_assessments": "BriefAssessment（研究简报评估）",
            "competitor_profiles": "CompetitorProfile（竞品档案）",
            "claims_v2": "AnalysisClaimV2（分析结论）",
            "claims": "AnalysisClaim（引用结论）",
            "citation_checks": "CitationCheck（引用检查）",
            "product_cards": "ProductCard（产品卡片）",
        }
        missing = [label for key, label in labels.items() if not self.store.load_many(task_id, key)]
        if missing:
            raise ValueError("缺少 Reporting 输入：" + "、".join(missing))

    def _require_task(self, task_id: str) -> AnalysisTask:
        items = self.store.load_many(task_id, "analysis_tasks")
        if not items:
            raise LookupError("未找到 AnalysisTask（分析任务）。")
        return AnalysisTask(**items[-1])

    def _writer_llm_client(self) -> LLMClient:
        if self._injected_llm_client is not None:
            return self._injected_llm_client
        config = self._deepseek_config()
        config.validate()
        readiness_errors = config.real_call_readiness_errors()
        if readiness_errors:
            raise ValueError("；".join(readiness_errors))
        return LLMClient(config=config, store=self.store)

    def _load_recorder(self, task_id: str) -> TraceRecorder:
        recorder = TraceRecorder(store=self.store, task_id=task_id)
        recorder.dag_nodes = [
            DAGNode(**item) for item in self.store.load_many(task_id, "dag_nodes")
        ]
        recorder.agent_runs = [
            AgentRun(**item) for item in self.store.load_many(task_id, "agent_runs")
        ]
        recorder.tool_calls = [
            ToolCall(**item) for item in self.store.load_many(task_id, "tool_calls")
        ]
        return recorder

    @staticmethod
    def _replace_node(recorder: TraceRecorder, node: DAGNode) -> DAGNode:
        recorder.dag_nodes = [item for item in recorder.dag_nodes if item.id != node.id]
        recorder.dag_nodes.append(node)
        recorder.save_dag_nodes()
        return node

    @staticmethod
    def _deepseek_config() -> LLMConfig:
        return build_deepseek_compatible_config(
            env_prefix="REPORTING",
            default_timeout_seconds=120,
            default_max_tokens=12000,
            temperature=0.2,
            max_retries=0,
            retry_base_seconds=1.0,
        )


_DEFAULT_RESEARCH_REPORTING_SERVICE: ResearchReportingService | None = None
_DEFAULT_RESEARCH_REPORTING_LOCK = threading.Lock()


def get_research_reporting_service() -> ResearchReportingService:
    global _DEFAULT_RESEARCH_REPORTING_SERVICE
    with _DEFAULT_RESEARCH_REPORTING_LOCK:
        if _DEFAULT_RESEARCH_REPORTING_SERVICE is None:
            _DEFAULT_RESEARCH_REPORTING_SERVICE = ResearchReportingService()
        return _DEFAULT_RESEARCH_REPORTING_SERVICE
