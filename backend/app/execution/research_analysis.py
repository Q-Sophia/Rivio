from __future__ import annotations

import os
import threading
from typing import Any

from app.agents import CitationAgent, LLMProfessionalAnalystAgent
from app.agents.runtime import AgentRuntime
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, LLMConfig
from app.schemas import (
    AgentContext,
    AgentRole,
    AgentRun,
    AnalysisTask,
    DAGNode,
    ExecutionMode,
    LLMMode,
    LLMProvider,
    ResearchPlan,
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
        return {
            "task_id": task_id,
            "completed": bool(portfolios),
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
            "analyst_llm_calls": analyst_calls,
        }

    def run_once(
        self,
        task_id: str,
        *,
        mode: ExecutionMode | str,
        acknowledge_real_llm_call: bool,
    ) -> dict[str, Any]:
        selected_mode = ExecutionMode(mode)
        if selected_mode != ExecutionMode.DEEPSEEK:
            raise ValueError("Step6F 当前只接受显式 DeepSeek（真实）分析模式。")
        if not acknowledge_real_llm_call:
            raise ValueError("必须明确确认本次将产生 1 次真实 DeepSeek Analyst 调用。")

        with self._lock:
            existing = self.get_payload(task_id)
            if existing["completed"]:
                raise ValueError("当前研究产物已经生成过 Step6F 分析结论，不能重复计费执行。")
            task = self._require_task(task_id)
            plan = self._require_analyzable_plan(task_id)
            self.board_store.require_board(task_id)
            self._validate_inputs(task_id)
            config = self._deepseek_config()
            config.validate()
            readiness_errors = config.real_call_readiness_errors()
            if readiness_errors:
                raise ValueError("；".join(readiness_errors))

            deterministic_coverage = self.store.load_many(task_id, "evidence_coverage")
            deterministic_gaps = self.store.load_many(task_id, "research_gaps")
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
                        "sources",
                        "evidence",
                        "product_cards",
                        "evidence_coverage",
                        "research_gaps",
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
                raise RuntimeError(analyst_result.error or "DeepSeek Analyst 执行失败。")

            after_call_count = len(self.store.load_many(task_id, "llm_calls"))
            if after_call_count - before_call_count != 1:
                raise RuntimeError("Step6F 必须且只能记录 1 次 Analyst LLM 调用。")
            if self.store.load_many(task_id, "evidence_coverage") != deterministic_coverage:
                raise RuntimeError("确定性 EvidenceCoverage 被 Analyst 覆盖。")
            if self.store.load_many(task_id, "research_gaps") != deterministic_gaps:
                raise RuntimeError("确定性 ResearchGap 被 Analyst 覆盖。")

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
                ],
                claimed_by_agent="professional_research_analyst_agent",
            )
            citation_result = self._run_citation(task, recorder, tools)
            payload = self.get_payload(task_id)
            return {
                **payload,
                "status": "completed",
                "real_llm_calls_this_run": 1,
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
                "sources",
                "evidence",
                "product_cards",
                "evidence_coverage",
                "research_gaps",
            ],
            reason="基于 Step6E 结构化研究产物生成当前任务专属分析结论。",
            metadata={
                "source": "step6f_research_analysis",
                "real_llm_calls_authorized": 1,
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
        required = {
            "sources": "SourceDocument（来源文档）",
            "evidence": "SourceEvidence（来源证据）",
            "product_cards": "ProductCard（产品卡片）",
            "evidence_coverage": "EvidenceCoverage（证据覆盖）",
        }
        missing = [label for key, label in required.items() if not self.store.load_many(task_id, key)]
        if missing:
            raise ValueError("缺少 Step6F 输入：" + "、".join(missing))

    @staticmethod
    def _deepseek_config() -> LLMConfig:
        return LLMConfig(
            provider=LLMProvider.COMPATIBLE,
            model=os.environ.get("STEP6F_LLM_MODEL", "deepseek-v4-flash"),
            mode=LLMMode.LLM,
            base_url=os.environ.get("STEP6F_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            api_key_env=os.environ.get("STEP6F_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"),
            timeout_seconds=int(os.environ.get("STEP6F_LLM_TIMEOUT_SECONDS", "120")),
            max_tokens=int(os.environ.get("STEP6F_LLM_MAX_TOKENS", "16000")),
            temperature=0.2,
            output_language="zh-CN",
            max_retries=0,
            retry_base_seconds=1.0,
            enable_real_calls=True,
            api_style="chat_completions",
            structured_output_mode="json_object",
            thinking_mode="disabled",
        )


_DEFAULT_RESEARCH_ANALYSIS_SERVICE: ResearchAnalysisService | None = None
_DEFAULT_RESEARCH_ANALYSIS_LOCK = threading.Lock()


def get_research_analysis_service() -> ResearchAnalysisService:
    global _DEFAULT_RESEARCH_ANALYSIS_SERVICE
    with _DEFAULT_RESEARCH_ANALYSIS_LOCK:
        if _DEFAULT_RESEARCH_ANALYSIS_SERVICE is None:
            _DEFAULT_RESEARCH_ANALYSIS_SERVICE = ResearchAnalysisService()
        return _DEFAULT_RESEARCH_ANALYSIS_SERVICE
