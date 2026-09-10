from __future__ import annotations

import threading
from typing import Any

from app.agents import CitationAgent, LLMProfessionalAnalystAgent
from app.agents.runtime import AgentRuntime
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, LLMConfig, build_deepseek_compatible_config
from app.schemas import (
    AgentContext,
    AgentRole,
    AgentRun,
    AnalysisTask,
    DAGNode,
    ExecutionMode,
    ResearchPlan,
    ResearchAgentRun,
    ResearchGap,
    ResearchGapImpact,
    ResearchGapOrigin,
    ResearchGapType,
    ResearchTask,
    ResearchTaskOutcome,
    RunStatus,
    TaskRecord,
    TaskStatus,
    TaskType,
    ToolCall,
)
from app.workflow.snapshot_pipeline import build_snapshot_tool_registry
from app.workflow.taskboard import TaskBoardStore
from app.workflow.trace import TraceRecorder


ANALYSIS_TASK_KEY = "analyze_research_evidence"
CITATION_TASK_KEY = "check_research_claim_citations"


class ResearchAnalysisOutputTruncatedError(RuntimeError):
    """All bounded retries for a named Analyst stage were exhausted."""


class ResearchAnalysisService:
    """Run one explicitly authorized DeepSeek Analyst over Step6E research artifacts."""

    _lock = threading.RLock()

    def __init__(self, *, store: ArtifactStore | None = None):
        self.store = store or ArtifactStore()
        self.board_store = TaskBoardStore(self.store)

    def get_payload(self, task_id: str) -> dict[str, Any]:
        portfolios = [
            item
            for item in self.store.load_many(task_id, "analysis_portfolios")
            if item.get("metadata", {}).get("source") == "step6f_research_analysis"
        ]
        analyst_calls = [
            item
            for item in self.store.load_many(task_id, "llm_calls")
            if str(item.get("agent_role") or "") == AgentRole.ANALYST.value
        ]
        readiness = self._analysis_readiness(task_id)
        assessments = self.store.load_many(task_id, "analysis_assessments")
        return {
            "task_id": task_id,
            "completed": bool(portfolios),
            **readiness,
            "analysis_portfolio": portfolios[-1] if portfolios else None,
            "claims": self.store.load_many(task_id, "claims"),
            "claims_v2": self.store.load_many(task_id, "claims_v2"),
            "citation_checks": self.store.load_many(task_id, "citation_checks"),
            "analysis_evidence_coverage": self.store.load_many(
                task_id, "analysis_evidence_coverage"
            ),
            "analysis_research_gaps": self.store.load_many(
                task_id, "analysis_research_gaps"
            ),
            "analysis_assessment": assessments[-1] if assessments else None,
            "analysis_assessments": assessments,
            "analyst_llm_calls": analyst_calls,
        }

    def run_once(
        self,
        task_id: str,
        *,
        mode: ExecutionMode | str,
        acknowledge_real_llm_call: bool,
        pipeline_id: str = "",
    ) -> dict[str, Any]:
        selected_mode = ExecutionMode(mode)
        if selected_mode != ExecutionMode.DEEPSEEK:
            raise ValueError("Step6F 当前只接受显式 DeepSeek（真实）分析模式。")
        if not acknowledge_real_llm_call:
            raise ValueError(
                "必须明确确认本次通常产生 3 次、仅截断时最多 6 次真实 DeepSeek Analyst 调用。"
            )

        with self._lock:
            existing = self.get_payload(task_id)
            if existing["completed"]:
                raise ValueError("当前研究产物已经生成过 Step6F 分析结论，不能重复计费执行。")
            task = self._require_task(task_id)
            plan = self._require_analyzable_plan(task_id)
            self.board_store.require_board(task_id)
            self._validate_inputs(task_id)
            self._ensure_research_agent_gaps(task_id)
            config = self._deepseek_config()
            config.validate()
            readiness_errors = config.real_call_readiness_errors()
            if readiness_errors:
                raise ValueError("；".join(readiness_errors))

            deterministic_coverage = self.store.load_many(task_id, "evidence_coverage")
            deterministic_gaps = self.store.load_many(task_id, "research_gaps")
            authorized_evidence_ids = self._authorized_r1_evidence_ids(task_id)
            self._prepare_analysis_task(task_id)
            recorder = self._load_recorder(task_id)
            tools = build_snapshot_tool_registry(store=self.store, recorder=recorder)
            analyst_node = self._replace_node(
                recorder,
                DAGNode(
                    id="step6f_analyze_research_evidence",
                    task_id=task_id,
                    label=ANALYSIS_TASK_KEY,
                    agent_role=AgentRole.ANALYST,
                    status=RunStatus.RUNNING,
                    input_refs=[
                        "analysis_tasks",
                        "research_plans",
                        "research_kiqs",
                        "research_information_needs",
                        "research_tasks",
                        "sources",
                        "evidence",
                        "product_cards",
                        "evidence_coverage",
                        "research_gaps",
                        "analysis_assessments",
                    ],
                ),
            )
            self.board_store.update_status(
                task_id,
                ANALYSIS_TASK_KEY,
                TaskStatus.CLAIMED,
                claimed_by_agent="professional_research_analyst_agent",
            )
            self.board_store.update_status(
                task_id,
                ANALYSIS_TASK_KEY,
                TaskStatus.RUNNING,
                claimed_by_agent="professional_research_analyst_agent",
            )
            before_call_count = len(self.store.load_many(task_id, "llm_calls"))
            analyst_result = AgentRuntime(store=self.store, recorder=recorder).run(
                agent=LLMProfessionalAnalystAgent(
                    name="professional_research_analyst_agent",
                    role=AgentRole.ANALYST,
                    tools=tools,
                    llm_client=LLMClient(config=config, store=self.store),
                    input_artifacts=analyst_node.input_refs,
                    output_artifacts=[
                        "analysis_portfolios",
                        "competitor_profiles",
                        "claims_v2",
                        "claims",
                        "analysis_evidence_coverage",
                        "analysis_research_gaps",
                        "analysis_assessments",
                        "research_gaps",
                    ],
                ),
                context=AgentContext(
                    task_id=task_id,
                    task=task,
                    node_id=analyst_node.id,
                    input_refs=analyst_node.input_refs,
                    metadata={
                        "preserve_research_artifacts": True,
                        "research_plan_id": plan.id,
                        "explicit_real_llm_authorization": True,
                        "authorized_evidence_ids": sorted(
                            authorized_evidence_ids
                        ),
                        "require_r1_evidence_authority": True,
                        "pipeline_id": (
                            pipeline_id or f"standalone_analysis_{task_id}"
                        ),
                    },
                ),
                node=analyst_node,
            )
            analyst_node.status = (
                RunStatus.COMPLETED
                if analyst_result.status == RunStatus.COMPLETED.value
                else RunStatus.FAILED
            )
            recorder.save_trace()
            if analyst_result.status != RunStatus.COMPLETED.value:
                self.board_store.update_status(
                    task_id,
                    ANALYSIS_TASK_KEY,
                    TaskStatus.FAILED,
                    error=analyst_result.error,
                    claimed_by_agent="professional_research_analyst_agent",
                )
                if (
                    "LLM 输出被截断" in analyst_result.error
                    or "finish_reason=length" in analyst_result.error
                ):
                    raise ResearchAnalysisOutputTruncatedError(
                        analyst_result.error
                    )
                raise RuntimeError(analyst_result.error or "DeepSeek Analyst 执行失败。")

            after_call_count = len(self.store.load_many(task_id, "llm_calls"))
            call_count = after_call_count - before_call_count
            if not 2 <= call_count <= 6:
                raise RuntimeError(
                    "Step6F Analyst 必须记录 2 至 6 次有限 LLM 调用；"
                    "2 次仅允许复用已持久化的相同 Framework/Evidence assessment。"
                )
            if self.store.load_many(task_id, "evidence_coverage") != deterministic_coverage:
                raise RuntimeError("确定性 EvidenceCoverage 被 Analyst 覆盖。")
            merged_gaps = {
                str(item.get("id") or ""): item
                for item in self.store.load_many(task_id, "research_gaps")
            }
            if any(
                merged_gaps.get(str(item.get("id") or "")) != item
                for item in deterministic_gaps
            ):
                raise RuntimeError("Analyst 修改或删除了上游 ResearchGap。")

            self.board_store.update_status(
                task_id,
                ANALYSIS_TASK_KEY,
                TaskStatus.COMPLETED,
                output_refs=[
                    "analysis_portfolios",
                    "competitor_profiles",
                    "claims_v2",
                    "claims",
                    "analysis_evidence_coverage",
                    "analysis_research_gaps",
                    "analysis_assessments",
                    "research_gaps",
                ],
                claimed_by_agent="professional_research_analyst_agent",
            )
            citation_result = self._run_citation(task, recorder, tools)
            payload = self.get_payload(task_id)
            return {
                **payload,
                "status": "completed",
                "real_llm_calls_this_run": call_count,
                "analyst_summary": analyst_result.output_summary,
                "citation_summary": citation_result.output_summary,
                "message": "DeepSeek Analyst 已基于当前结构化研究产物生成并校验分析结论。",
            }

    def _run_citation(
        self,
        task: AnalysisTask,
        recorder: TraceRecorder,
        tools,
    ):
        task_id = task.id
        citation_record = TaskRecord(
            id=f"queue_{CITATION_TASK_KEY}",
            task_id=task_id,
            task_key=CITATION_TASK_KEY,
            task_type=TaskType.CHECK_CITATIONS,
            target_agent_role=AgentRole.CITATION,
            status=TaskStatus.PENDING,
            depends_on=[ANALYSIS_TASK_KEY],
            input_refs=["sources", "evidence", "claims"],
            reason="校验 Step6F 新分析结论的 evidence_ids 与来源链。",
            metadata={"source": "step6f_research_analysis"},
        )
        self.board_store.upsert_record(task_id, citation_record)
        self.board_store.mark_ready_tasks(task_id)
        self.board_store.update_status(
            task_id,
            CITATION_TASK_KEY,
            TaskStatus.CLAIMED,
            claimed_by_agent="citation_agent",
        )
        self.board_store.update_status(
            task_id,
            CITATION_TASK_KEY,
            TaskStatus.RUNNING,
            claimed_by_agent="citation_agent",
        )
        node = self._replace_node(
            recorder,
            DAGNode(
                id="step6f_check_research_citations",
                task_id=task_id,
                label=CITATION_TASK_KEY,
                agent_role=AgentRole.CITATION,
                status=RunStatus.RUNNING,
                depends_on=["step6f_analyze_research_evidence"],
                input_refs=["sources", "evidence", "claims"],
            ),
        )
        result = AgentRuntime(store=self.store, recorder=recorder).run(
            agent=CitationAgent(
                name="citation_agent",
                role=AgentRole.CITATION,
                tools=tools,
                input_artifacts=node.input_refs,
                output_artifacts=["claims", "citation_checks"],
            ),
            context=AgentContext(
                task_id=task_id,
                task=task,
                node_id=node.id,
                input_refs=node.input_refs,
                metadata={"source": "step6f_research_analysis"},
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
                CITATION_TASK_KEY,
                TaskStatus.FAILED,
                error=result.error,
                claimed_by_agent="citation_agent",
            )
            raise RuntimeError(result.error or "CitationAgent 执行失败。")
        self.board_store.update_status(
            task_id,
            CITATION_TASK_KEY,
            TaskStatus.COMPLETED,
            output_refs=["claims", "citation_checks"],
            claimed_by_agent="citation_agent",
        )
        return result

    def _prepare_analysis_task(self, task_id: str) -> None:
        record = TaskRecord(
            id=f"queue_{ANALYSIS_TASK_KEY}",
            task_id=task_id,
            task_key=ANALYSIS_TASK_KEY,
            task_type=TaskType.BUILD_CLAIMS,
            target_agent_role=AgentRole.ANALYST,
            status=TaskStatus.READY,
            input_refs=[
                "analysis_tasks",
                "research_plans",
                "research_kiqs",
                "research_information_needs",
                "research_tasks",
                "sources",
                "evidence",
                "product_cards",
                "evidence_coverage",
                "research_gaps",
                "analysis_assessments",
            ],
            reason="基于 Step6E 结构化研究产物生成当前任务专属分析结论。",
            metadata={
                "source": "step6f_research_analysis",
                "real_llm_calls_authorized": 6,
                "normal_llm_calls_expected": 3,
            },
        )
        self.board_store.upsert_record(task_id, record)

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

    def _require_task(self, task_id: str) -> AnalysisTask:
        items = self.store.load_many(task_id, "analysis_tasks")
        if not items:
            raise LookupError("未找到 AnalysisTask（分析任务）。")
        return AnalysisTask(**items[-1])

    def _require_analyzable_plan(self, task_id: str) -> ResearchPlan:
        items = self.store.load_many(task_id, "research_plans")
        if not items:
            raise LookupError("未找到 ResearchPlan（研究计划）。")
        return ResearchPlan(**items[-1])

    def _validate_inputs(self, task_id: str) -> None:
        readiness = self._analysis_readiness(task_id)
        if not readiness["can_analyze"]:
            raise ValueError(readiness["analysis_blocking_reason"])

    def _analysis_readiness(self, task_id: str) -> dict[str, Any]:
        sources = self.store.load_many(task_id, "sources")
        evidence = self.store.load_many(task_id, "evidence")
        authorized_ids = self._authorized_r1_evidence_ids(task_id)
        source_ids = {str(item.get("id") or "") for item in sources}
        analyzable = [
            item
            for item in evidence
            if str(item.get("id") or "") in authorized_ids
            if str(item.get("source_id") or "") in source_ids
        ]
        if not authorized_ids:
            reason = (
                "当前任务没有可分析的 Verified Evidence；请先完成至少一条证据验证。"
            )
        elif not analyzable:
            reason = (
                "当前任务的 Evidence 无法追溯到 SourceDocument，暂不能进入 Analyst。"
            )
        else:
            reason = ""
        return {
            "can_analyze": bool(analyzable),
            "analysis_blocking_reason": reason,
            "analyzable_evidence_count": len(analyzable),
            "authorized_evidence_ids": sorted(authorized_ids),
            "product_card_count": len(
                self.store.load_many(task_id, "product_cards")
            ),
        }

    def _authorized_r1_evidence_ids(self, task_id: str) -> set[str]:
        known_ids = {
            str(item.get("id") or "")
            for item in self.store.load_many(task_id, "evidence")
        }
        return {
            evidence_id
            for raw in self.store.load_many(task_id, "research_agent_runs")
            for evidence_id in ResearchAgentRun(**raw).verified_evidence_ids
            if evidence_id in known_ids
        }

    def _ensure_research_agent_gaps(self, task_id: str) -> None:
        """Bridge terminal R1 outcomes into deterministic gaps without overwriting legacy gaps."""

        existing = [
            ResearchGap(**item)
            for item in self.store.load_many(task_id, "research_gaps")
        ]
        existing_research_task_ids = {
            str(item.metadata.get("research_task_id") or "") for item in existing
        }
        research_tasks = {
            item.id: item
            for item in (
                ResearchTask(**raw)
                for raw in self.store.load_many(task_id, "research_tasks")
            )
        }
        latest_runs: dict[str, ResearchAgentRun] = {}
        for raw in self.store.load_many(task_id, "research_agent_runs"):
            run = ResearchAgentRun(**raw)
            latest_runs[run.research_task_id] = run

        known_evidence_ids = {
            str(item.get("id") or "")
            for item in self.store.load_many(task_id, "evidence")
        }
        generated: list[ResearchGap] = []
        for research_task_id, run in latest_runs.items():
            if research_task_id in existing_research_task_ids:
                continue
            if run.outcome not in {
                ResearchTaskOutcome.PARTIAL.value,
                ResearchTaskOutcome.EXHAUSTED.value,
            }:
                continue
            research_task = research_tasks.get(research_task_id)
            if research_task is None:
                continue
            generated.append(
                ResearchGap(
                    id=f"gap_research_agent_{research_task_id}",
                    task_id=task_id,
                    competitors=(
                        [research_task.competitor]
                        if research_task.competitor
                        else []
                    ),
                    dimension=research_task.dimension,
                    missing_information=(
                        run.remaining_need.strip() or research_task.objective
                    ),
                    decision_blocked=(
                        "当前证据只能支持阶段性分析，不能完整回答该 ResearchTask。"
                    ),
                    why_existing_evidence_is_insufficient=(
                        f"Research & Evidence Agent 以 {run.outcome} 结束；"
                        "Analyst 应保留现有证据支持的结论，并披露该缺口。"
                    ),
                    suggested_queries=research_task.query_hints,
                    preferred_source_types=research_task.preferred_source_types,
                    priority=research_task.priority,
                    stop_condition=research_task.stop_condition,
                    related_evidence_ids=[
                        evidence_id
                        for evidence_id in run.verified_evidence_ids
                        if evidence_id in known_evidence_ids
                    ],
                    gap_type=ResearchGapType.INSUFFICIENT_EVIDENCE,
                    impact=ResearchGapImpact(research_task.priority),
                    origin=ResearchGapOrigin.RESEARCH_AGENT,
                    framework_id=research_task.framework_id,
                    framework_version=research_task.framework_version,
                    framework_dimension_id=(
                        research_task.framework_dimension_id
                    ),
                    framework_content_hash=(
                        research_task.framework_content_hash
                    ),
                    research_task_ids=[research_task_id],
                    metadata={
                        "source": "research_agent_r1_bridge",
                        "research_task_id": research_task_id,
                        "research_agent_run_id": run.id,
                        "research_task_outcome": run.outcome,
                    },
                )
            )
        if generated:
            self.store.save_many(task_id, "research_gaps", [*existing, *generated])

    @staticmethod
    def _deepseek_config() -> LLMConfig:
        return build_deepseek_compatible_config(
            env_prefix="STEP6F",
            default_timeout_seconds=120,
            default_max_tokens=8000,
            temperature=0.2,
            max_retries=2,
            retry_base_seconds=1.0,
        )


_DEFAULT_RESEARCH_ANALYSIS_SERVICE: ResearchAnalysisService | None = None
_DEFAULT_RESEARCH_ANALYSIS_LOCK = threading.Lock()


def get_research_analysis_service() -> ResearchAnalysisService:
    global _DEFAULT_RESEARCH_ANALYSIS_SERVICE
    with _DEFAULT_RESEARCH_ANALYSIS_LOCK:
        if _DEFAULT_RESEARCH_ANALYSIS_SERVICE is None:
            _DEFAULT_RESEARCH_ANALYSIS_SERVICE = ResearchAnalysisService()
        return _DEFAULT_RESEARCH_ANALYSIS_SERVICE
