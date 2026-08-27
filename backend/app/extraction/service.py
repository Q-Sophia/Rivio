from __future__ import annotations

from app.agents.runtime import AgentRuntime
from app.agents.web_evidence import WebEvidenceExtractorAgent
from app.harness.artifacts import ArtifactStore
from app.retrieval import (
    DenseBackend,
    RerankerBackend,
    SourceRAGService,
    normalize_source_rag_mode,
)
from app.schemas import (
    AgentContext,
    AgentRole,
    AgentRun,
    AnalysisTask,
    DAGNode,
    ResearchTask,
    RunStatus,
    TaskStatus,
    TaskRecord,
    TaskType,
    ToolCall,
)
from app.workflow.taskboard import TaskBoardStore, status_value
from app.workflow.trace import TraceRecorder


class ExtractorQueueService:
    """Claim one extraction task and preserve a runtime/audit boundary."""

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        source_rag_mode: str | None = None,
        dense_backend: DenseBackend | None = None,
        reranker_backend: RerankerBackend | None = None,
    ):
        self.store = store or ArtifactStore()
        self.board_store = TaskBoardStore(self.store)
        self.source_rag_mode = normalize_source_rag_mode(source_rag_mode)
        self.dense_backend = dense_backend
        self.reranker_backend = reranker_backend

    def run_once(self, task_id: str) -> dict:
        ready = [
            record
            for record in self.board_store.ready_records(task_id)
            if status_value(record.target_agent_role) == AgentRole.EXTRACTOR.value
            and status_value(record.task_type) == "extract_source_evidence"
        ]
        if not ready:
            raise LookupError("没有可由 Extractor Agent（抽取智能体）领取的证据抽取任务。")
        record = ready[0]
        research_task_id = str(record.metadata.get("research_task_id") or "")
        research_tasks = [
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
        ]
        research_task = next(
            (item for item in research_tasks if item.id == research_task_id),
            None,
        )
        if research_task is None:
            raise ValueError(f"抽取任务引用了不存在的 ResearchTask：{research_task_id}")

        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.CLAIMED,
            claimed_by_agent="web_evidence_extractor_agent",
        )
        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.RUNNING,
            claimed_by_agent="web_evidence_extractor_agent",
        )
        task_items = self.store.load_many(task_id, "analysis_tasks")
        task = (
            AnalysisTask(**task_items[-1])
            if task_items
            else AnalysisTask(
                id=task_id,
                query=research_task.objective,
                competitors=[research_task.competitor],
                focus_areas=[research_task.dimension],
            )
        )
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
        source_ids = list(record.metadata.get("source_ids") or [])
        retrieval_run = None
        selected_chunks = []
        if self.source_rag_mode != "off":
            retrieval_run, selected_chunks = SourceRAGService(
                store=self.store,
                retrieval_mode=self.source_rag_mode,
                dense_backend=self.dense_backend,
                reranker_backend=self.reranker_backend,
            ).run(
                task_id=task_id,
                research_task=research_task,
                source_ids=source_ids,
            )
        node = DAGNode(
            id=f"extract_{research_task.id}",
            task_id=task_id,
            label="extract_source_evidence",
            agent_role=AgentRole.EXTRACTOR,
            status=RunStatus.RUNNING,
            depends_on=[record.depends_on[0]] if record.depends_on else [],
            input_refs=(
                ["sources", "web_pages", "research_tasks"]
                if retrieval_run is None
                else [
                    "sources",
                    "web_pages",
                    "source_chunks",
                    "source_retrieval_runs",
                    "research_tasks",
                ]
            ),
        )
        recorder.dag_nodes.append(node)
        recorder.save_dag_nodes()
        result = AgentRuntime(store=self.store, recorder=recorder).run(
            agent=WebEvidenceExtractorAgent(store=self.store),
            context=AgentContext(
                task_id=task_id,
                task=task,
                node_id=node.id,
                input_refs=node.input_refs,
                metadata={
                    "research_task": research_task.model_dump(mode="json"),
                    "source_ids": source_ids,
                    "source_rag_mode": self.source_rag_mode,
                    "source_chunk_ids": [item.id for item in selected_chunks],
                    "retrieval_run_id": (
                        retrieval_run.id if retrieval_run is not None else ""
                    ),
                },
            ),
            node=node,
        )
        if retrieval_run is not None:
            recorder.record_tool_call(
                agent_run_id=result.agent_run.id,
                tool_name="retrieve_source_chunks",
                input_data={
                    "research_task_id": research_task.id,
                    "source_ids": source_ids,
                    "algorithm": retrieval_run.algorithm,
                    "top_k": retrieval_run.top_k,
                },
                output_summary=(
                    f"从 {retrieval_run.candidate_chunk_count} 个候选 chunk 中选择 "
                    f"{len(selected_chunks)} 个，共 {retrieval_run.selected_text_chars} 字符"
                ),
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
                record.task_key,
                TaskStatus.FAILED,
                error=result.error,
                claimed_by_agent="web_evidence_extractor_agent",
            )
            return {"status": "failed", "message": result.error, "evidence_count": 0}

        self.board_store.update_status(
            task_id,
            record.task_key,
            TaskStatus.COMPLETED,
            output_refs=["evidence", "evidence_extraction_attempts"],
            claimed_by_agent="web_evidence_extractor_agent",
        )
        self.store.save_many(
            task_id,
            "research_tasks",
            [
                item.model_copy(update={"status": "evidence_extracted"})
                if item.id == research_task.id
                else item
                for item in research_tasks
            ],
        )
        evaluation_task_key = f"evaluate_coverage_{research_task.id}"
        self.board_store.upsert_record(
            task_id,
            TaskRecord(
                id=f"queue_{evaluation_task_key}",
                task_id=task_id,
                task_key=evaluation_task_key,
                task_type=TaskType.EVALUATE_EVIDENCE_COVERAGE,
                target_agent_role=AgentRole.ANALYST,
                status=TaskStatus.PENDING,
                priority=research_task.priority,
                depends_on=[record.task_key],
                input_refs=["sources", "evidence", "research_plans", "research_tasks"],
                reason=(
                    f"更新 {research_task.competitor} 的 ProductCard（产品卡片）与 "
                    "EvidenceCoverage（证据覆盖），并判断是否需要有限轮次补采"
                ),
                metadata={
                    "research_task_id": research_task.id,
                    "collection_round": research_task.collection_round,
                    "source": "Step6E.4 evidence gap loop",
                },
            ),
        )
        self.board_store.mark_ready_tasks(task_id)
        evidence_ids = result.output_artifacts.get("evidence", [])
        return {
            "status": "completed",
            "research_task_id": research_task.id,
            "evidence_count": len(evidence_ids),
            "evidence_ids": evidence_ids,
            "coverage_evaluation_task_id": evaluation_task_key,
            "coverage_evaluation_ready": True,
            "message": result.output_summary,
        }
