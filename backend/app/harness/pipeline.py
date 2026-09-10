from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.execution.research_agent_coordinator import (
    ResearchAgentCoordinator,
    get_research_agent_coordinator,
)
from app.execution.research_analysis import get_research_analysis_service
from app.execution.research_reporting import get_research_reporting_service
from app.harness.artifacts import ArtifactStore
from app.harness.protocol import (
    AgentHandoff,
    ArtifactReference,
    PipelineCheckpoint,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
)
from app.intake import ResearchPlanningService
from app.schemas import ExecutionMode, utc_now


PIPELINE_RUNS_ARTIFACT = "pipeline_runs"
PIPELINE_EVENTS_ARTIFACT = "pipeline_events"
PIPELINE_CHECKPOINTS_ARTIFACT = "pipeline_checkpoints"
AGENT_HANDOFFS_ARTIFACT = "agent_handoffs"

ACTIVE_PIPELINE_STATUSES = {"queued", "running", "stopping"}
TERMINAL_PIPELINE_STATUSES = {
    "completed",
    "failed",
    "stopped",
    "interrupted",
}

PLANNING_OUTPUTS = [
    "analysis_tasks",
    "research_plans",
    "research_kiqs",
    "research_information_needs",
    "research_tasks",
    "task_board",
]
RESEARCH_OUTPUTS = [
    "sources",
    "evidence",
    "product_cards",
    "evidence_coverage",
    "research_gaps",
    "research_agent_runs",
    "research_worker_results",
]
ANALYSIS_OUTPUTS = [
    "analysis_portfolios",
    "analysis_assessments",
    "brief_assessments",
    "competitor_profiles",
    "claims_v2",
    "claims",
    "analysis_evidence_coverage",
    "analysis_research_gaps",
    "research_gaps",
]
CITATION_OUTPUTS = ["claims", "claims_v2", "citation_checks"]
REPORTING_OUTPUTS = [
    "reports",
    "report_statements",
    "review_feedback",
    "quality_gates",
    "feedback_tasks",
]

PlanningFactory = Callable[[ArtifactStore], Any]
CoordinatorFactory = Callable[[ArtifactStore], Any]
AnalysisFactory = Callable[[ArtifactStore], Any]
ReportingFactory = Callable[[ArtifactStore], Any]


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


class ResearchPipelineHarness:
    """Durable control plane for the existing evidence-first agent services.

    The harness owns lifecycle, stage transitions, checkpointing, handoff
    envelopes and cooperative cancellation. Domain work remains in the
    existing Planner, Research, Analyst/Citation and Writer/Reviewer services.
    """

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        max_workers: int = 1,
        planning_service_factory: PlanningFactory | None = None,
        research_coordinator_factory: CoordinatorFactory | None = None,
        analysis_service_factory: AnalysisFactory | None = None,
        reporting_service_factory: ReportingFactory | None = None,
        poll_interval_seconds: float = 0.2,
    ):
        self.store = store or ArtifactStore()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="research-pipeline-harness",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._poll_interval_seconds = max(poll_interval_seconds, 0.01)
        self._planning_service_factory = planning_service_factory or (
            lambda store: ResearchPlanningService(store=store)
        )
        self._research_coordinator_factory = research_coordinator_factory or (
            lambda _store: get_research_agent_coordinator()
        )
        self._analysis_service_factory = analysis_service_factory or (
            lambda _store: get_research_analysis_service()
        )
        self._reporting_service_factory = reporting_service_factory or (
            lambda _store: get_research_reporting_service()
        )

    def submit(
        self,
        task_id: str,
        *,
        mode: ExecutionMode | str = ExecutionMode.DEEPSEEK,
        acknowledge_real_llm_call: bool = False,
    ) -> PipelineRun:
        selected_mode = ExecutionMode(mode)
        if selected_mode != ExecutionMode.DEEPSEEK:
            raise ValueError("统一 Pipeline 当前只接受显式 DeepSeek 模式。")
        if not acknowledge_real_llm_call:
            raise ValueError(
                "必须显式确认自动 Pipeline 会调用 Research、Analyst 与 Writer 的真实 LLM。"
            )

        with self._lock:
            latest = self.get_latest_run(task_id)
            future = self._futures.get(task_id)
            if latest and _value(latest.status) in ACTIVE_PIPELINE_STATUSES:
                if future is not None and not future.done():
                    return latest
                latest = self.reconcile_interrupted(task_id)

            if latest and _value(latest.status) == "completed":
                return latest

            if latest is None:
                run = PipelineRun(
                    task_id=task_id,
                    mode=selected_mode,
                    real_llm_call_authorized=True,
                    message="统一多 Agent Pipeline 已进入后台队列。",
                )
            else:
                run = latest.model_copy(
                    update={
                        "mode": selected_mode,
                        "real_llm_call_authorized": True,
                        "status": PipelineRunStatus.QUEUED,
                        "stop_requested": False,
                        "stop_reason": "",
                        "resume_count": latest.resume_count + 1,
                        "message": "统一多 Agent Pipeline 已排队恢复。",
                        "error": "",
                        "completed_at": None,
                        "updated_at": utc_now(),
                    }
                )

            run = self._save_run(run)
            self._append_event(
                run,
                event_type="pipeline_queued",
                message=run.message,
            )
            self._futures[task_id] = self._pool.submit(
                self._execute,
                task_id,
                run.id,
            )
            return run

    def request_stop(self, task_id: str) -> PipelineRun:
        with self._lock:
            latest = self.get_latest_run(task_id)
            if latest is None:
                raise LookupError(f"未找到统一 PipelineRun: {task_id}")
            if _value(latest.status) in TERMINAL_PIPELINE_STATUSES:
                return latest

            stopping = latest.model_copy(
                update={
                    "status": PipelineRunStatus.STOPPING,
                    "stop_requested": True,
                    "stop_reason": "user_requested",
                    "message": "已收到中止请求；当前 Agent 安全结束后停止整个 Pipeline。",
                    "updated_at": utc_now(),
                }
            )
            stopping = self._save_run(stopping)
            self._append_event(
                stopping,
                event_type="stop_requested",
                message=stopping.message,
                data={"stop_reason": "user_requested"},
            )

            if _value(stopping.current_stage) == "researching":
                coordinator = self._research_coordinator_factory(self.store)
                coordinator_run = coordinator.get_latest_run(task_id)
                if coordinator_run and _value(coordinator_run.status) in {
                    "queued",
                    "running",
                    "stopping",
                }:
                    coordinator.request_stop(task_id)

            future = self._futures.get(task_id)
            if future is None or future.cancel():
                return self._finish_stopped(stopping)
            return stopping

    def reconcile_interrupted(self, task_id: str) -> PipelineRun | None:
        with self._lock:
            latest = self.get_latest_run(task_id)
            if latest is None or _value(latest.status) not in ACTIVE_PIPELINE_STATUSES:
                return latest
            future = self._futures.get(task_id)
            if future is not None and not future.done():
                return latest
            if latest.stop_requested or _value(latest.status) == "stopping":
                return self._finish_stopped(latest)

            interrupted = latest.model_copy(
                update={
                    "status": PipelineRunStatus.INTERRUPTED,
                    "message": "统一 Pipeline 后台 worker 已中断，可从最近 checkpoint 恢复。",
                    "error": (
                        "ResearchPipelineInterrupted: 后台进程重启或 worker 已丢失。"
                    ),
                    "updated_at": utc_now(),
                    "completed_at": utc_now(),
                }
            )
            interrupted = self._save_run(interrupted)
            self._append_event(
                interrupted,
                event_type="pipeline_interrupted",
                message=interrupted.message,
            )
            return interrupted

    def get_latest_run(self, task_id: str) -> PipelineRun | None:
        items = self.store.load_many(task_id, PIPELINE_RUNS_ARTIFACT)
        return PipelineRun(**items[-1]) if items else None

    def get_events(self, task_id: str, *, after: int = 0) -> list[PipelineEvent]:
        return [
            PipelineEvent(**item)
            for item in self.store.load_many(task_id, PIPELINE_EVENTS_ARTIFACT)
            if int(item.get("sequence", 0)) > after
        ]

    def get_handoffs(self, task_id: str) -> list[AgentHandoff]:
        return [
            AgentHandoff(**item)
            for item in self.store.load_many(task_id, AGENT_HANDOFFS_ARTIFACT)
        ]

    def get_checkpoints(self, task_id: str) -> list[PipelineCheckpoint]:
        return [
            PipelineCheckpoint(**item)
            for item in self.store.load_many(task_id, PIPELINE_CHECKPOINTS_ARTIFACT)
        ]

    def wait(self, task_id: str, *, timeout: float = 300.0) -> PipelineRun:
        future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        latest = self.get_latest_run(task_id)
        if latest is None:
            raise LookupError(f"未找到统一 PipelineRun: {task_id}")
        return latest

    def sync_research_events(self, task_id: str) -> list[PipelineEvent]:
        with self._lock:
            run = self.get_latest_run(task_id)
            if run is None:
                return []
            coordinator = self._research_coordinator_factory(self.store)
            coordinator.sync_evidence_events(task_id)
            coordinator_run = coordinator.get_latest_run(task_id)
            coordinator_run_id = str(
                getattr(coordinator_run, "id", "") or ""
            )
            if (
                coordinator_run_id
                and coordinator_run_id != run.research_coordinator_run_id
            ):
                run = self._save_run(
                    run.model_copy(
                        update={
                            "research_coordinator_run_id": coordinator_run_id,
                            "research_event_cursor": 0,
                            "updated_at": utc_now(),
                        }
                    )
                )
            source_events = coordinator.get_events(
                task_id,
                after=run.research_event_cursor,
            )
            appended: list[PipelineEvent] = []
            terminal_event_names = {
                "queued": "research_queued",
                "started": "research_started",
                "completed": "research_completed",
                "failed": "research_failed",
                "stopped": "research_stopped",
            }
            for source in source_events:
                current = self.get_latest_run(task_id) or run
                coordinator_run = coordinator.get_latest_run(task_id)
                coordinator_progress = int(
                    getattr(coordinator_run, "progress_percent", 0) or 0
                )
                pipeline_progress = min(65, 15 + coordinator_progress // 2)
                current = current.model_copy(
                    update={
                        "research_event_cursor": max(
                            current.research_event_cursor,
                            source.sequence,
                        ),
                        "research_coordinator_run_id": coordinator_run_id,
                        "progress_percent": max(
                            current.progress_percent,
                            pipeline_progress,
                        ),
                        "updated_at": utc_now(),
                    }
                )
                run = self._save_run(current)
                event = self._append_event(
                    run,
                    event_type=terminal_event_names.get(
                        source.event_type,
                        source.event_type,
                    ),
                    message=source.message,
                    research_task_id=source.research_task_id,
                    data={
                        **source.data,
                        "source_event_id": source.id,
                        "source_event_sequence": source.sequence,
                        "source_stream": "research_agent_coordinator",
                    },
                )
                appended.append(event)

            # Project the Research Agent's durable action log into the public
            # Pipeline event stream.  This is intentionally a strict whitelist:
            # rationale, prompts, chain-of-thought and raw model/tool output must
            # never cross this product-facing SSE boundary.
            current = self.get_latest_run(task_id) or run
            if _value(current.status) in ACTIVE_PIPELINE_STATUSES:
                existing_action_ids = {
                    str(item.get("data", {}).get("action_id") or "")
                    for item in self.store.load_many(task_id, PIPELINE_EVENTS_ARTIFACT)
                    if item.get("event_type") == "agent_action"
                }
                observations = {
                    str(item.get("action_id") or ""): item
                    for item in self.store.load_many(
                        task_id,
                        "research_agent_observations",
                    )
                }
                research_tasks = {
                    str(item.get("id") or ""): item
                    for item in self.store.load_many(task_id, "research_tasks")
                }
                sources = {
                    str(item.get("id") or ""): item
                    for item in self.store.load_many(task_id, "sources")
                }
                for action in self.store.load_many(
                    task_id,
                    "research_agent_actions",
                ):
                    action_id = str(action.get("id") or "")
                    action_name = str(action.get("action") or "").upper()
                    observation = observations.get(action_id)
                    if (
                        not action_id
                        or action_id in existing_action_ids
                        or action_name not in {
                            "SEARCH",
                            "FETCH",
                            "READ",
                            "SUBMIT_EVIDENCE",
                            "FINISH",
                        }
                        or (action_name != "FINISH" and observation is None)
                    ):
                        continue

                    observation_payload = (
                        observation.get("payload", {})
                        if isinstance(observation, dict)
                        else {}
                    )
                    source_id = str(
                        action.get("source_id")
                        or observation_payload.get("source_id")
                        or ""
                    )
                    source = sources.get(source_id, {})
                    source_url = str(
                        action.get("url")
                        or observation_payload.get("source_url")
                        or source.get("url")
                        or ""
                    )
                    source_title = str(
                        observation_payload.get("source_title")
                        or source.get("title")
                        or ""
                    )
                    research_task_id = str(
                        action.get("research_task_id") or ""
                    )
                    research_task = research_tasks.get(research_task_id, {})
                    query = str(action.get("query") or "")
                    finish_status = str(action.get("finish_status") or "")
                    public_summary = {
                        "SEARCH": f"检索公开资料：{query}" if query else "检索公开资料。",
                        "FETCH": (
                            f"采集来源：{source_title or source_url}"
                            if source_title or source_url
                            else "采集公开来源。"
                        ),
                        "READ": (
                            f"阅读来源：{source_title or source_url}"
                            if source_title or source_url
                            else "阅读来源内容。"
                        ),
                        "SUBMIT_EVIDENCE": "提交并验证一条可引用证据。",
                        "FINISH": (
                            f"完成研究任务，结果为 {finish_status}。"
                            if finish_status
                            else "完成研究任务。"
                        ),
                    }[action_name]
                    event = self._append_event(
                        current,
                        event_type="agent_action",
                        message=public_summary,
                        research_task_id=research_task_id,
                        data={
                            "agent_label": "Research Agent",
                            "action_id": action_id,
                            "action": action_name,
                            "action_status": (
                                str(observation.get("status") or "completed")
                                if observation
                                else "completed"
                            ),
                            "query": query,
                            "source_id": source_id,
                            "source_url": source_url,
                            "source_title": source_title,
                            "chunk_id": str(
                                action.get("chunk_id")
                                or observation_payload.get("chunk_id")
                                or ""
                            ),
                            "evidence_id": str(
                                observation_payload.get("evidence_id") or ""
                            ),
                            "quote_verified": bool(
                                observation_payload.get("quote_verified", False)
                            ),
                            "research_task_title": str(
                                research_task.get("title")
                                or research_task.get("objective")
                                or ""
                            ),
                            "competitor": str(
                                research_task.get("competitor") or ""
                            ),
                            "dimension": str(
                                research_task.get("dimension") or ""
                            ),
                            "finish_status": finish_status,
                            "action_created_at": action.get("created_at"),
                            "public_summary": public_summary,
                        },
                    )
                    appended.append(event)
                    existing_action_ids.add(action_id)
            return appended

    def _execute(self, task_id: str, run_id: str) -> None:
        run = self.get_latest_run(task_id)
        if run is None or run.id != run_id:
            return
        try:
            run = self._save_run(
                run.model_copy(
                    update={
                        "status": PipelineRunStatus.RUNNING,
                        "started_at": run.started_at or utc_now(),
                        "completed_at": None,
                        "message": "统一多 Agent Pipeline 已开始执行。",
                        "updated_at": utc_now(),
                    }
                )
            )
            self._append_event(
                run,
                event_type="pipeline_started",
                message=run.message,
            )
            if self._stop_if_requested(run):
                return

            if not self._stage_is_complete(run, PipelineStage.PLANNING):
                run = self._begin_stage(
                    run,
                    PipelineStage.PLANNING,
                    progress=5,
                    message="Planner 正在准备结构化 Research Plan。",
                )
                self._planning_service_factory(self.store).build(run.task_id)
                run = self._complete_stage(
                    run,
                    PipelineStage.PLANNING,
                    PLANNING_OUTPUTS,
                )
                self._handoff(
                    run,
                    sender="research_planner_agent",
                    recipient="research_agent_coordinator",
                    artifact_types=PLANNING_OUTPUTS,
                    purpose="execute_research_plan",
                )
            else:
                run = self._resume_stage(run, PipelineStage.PLANNING)
            if self._stop_if_requested(run):
                return

            if not self._stage_is_complete(run, PipelineStage.RESEARCHING):
                run = self._begin_stage(
                    run,
                    PipelineStage.RESEARCHING,
                    progress=15,
                    message="Research Agent 正在搜索、采集并验证证据。",
                )
                self._run_research(run)
                run = self.get_latest_run(run.task_id) or run
                if self._stop_if_requested(run):
                    return
                run = self._complete_stage(
                    run,
                    PipelineStage.RESEARCHING,
                    RESEARCH_OUTPUTS,
                )
                self._handoff(
                    run,
                    sender="research_evidence_agent",
                    recipient="professional_research_analyst_agent",
                    artifact_types=[*RESEARCH_OUTPUTS, "research_tasks"],
                    purpose="evaluate_verified_evidence_against_framework",
                )
            else:
                run = self._resume_stage(run, PipelineStage.RESEARCHING)

            if not self._stage_is_complete(run, PipelineStage.ANALYZING):
                run = self._begin_stage(
                    run,
                    PipelineStage.ANALYZING,
                    progress=70,
                    message="Analyst 正在形成结论，随后由 Citation Agent 校验引用。",
                )
                analysis_service = self._analysis_service_factory(self.store)
                if not analysis_service.get_payload(run.task_id).get("completed"):
                    analysis_service.run_once(
                        run.task_id,
                        mode=run.mode,
                        acknowledge_real_llm_call=run.real_llm_call_authorized,
                        pipeline_id=run.id,
                    )
                self._handoff(
                    run,
                    sender="professional_research_analyst_agent",
                    recipient="citation_agent",
                    artifact_types=ANALYSIS_OUTPUTS,
                    purpose="validate_claim_evidence_links",
                )
                self._handoff(
                    run,
                    sender="citation_agent",
                    recipient="professional_writer_agent",
                    artifact_types=CITATION_OUTPUTS,
                    purpose="write_only_from_citation_checked_claims",
                )
                run = self.get_latest_run(run.task_id) or run
                run = self._complete_stage(
                    run,
                    PipelineStage.ANALYZING,
                    ANALYSIS_OUTPUTS + ["citation_checks"],
                )
            else:
                run = self._resume_stage(run, PipelineStage.ANALYZING)
            if self._stop_if_requested(run):
                return

            reporting_service = self._reporting_service_factory(self.store)
            if not self._stage_is_complete(run, PipelineStage.REPORTING):
                run = self._begin_stage(
                    run,
                    PipelineStage.REPORTING,
                    progress=85,
                    message="Writer 正在生成报告，随后执行 Reviewer 与 QualityGate。",
                )
                reporting_payload = reporting_service.get_payload(run.task_id)
                if not reporting_payload.get("completed"):
                    reporting_payload = reporting_service.run(
                        run.task_id,
                        mode=run.mode,
                        acknowledge_real_llm_call=run.real_llm_call_authorized,
                    )
                self._handoff(
                    run,
                    sender="professional_writer_agent",
                    recipient="reviewer_agent",
                    artifact_types=["reports", "report_statements"],
                    purpose="review_traceable_report",
                )
                self._handoff(
                    run,
                    sender="reviewer_agent",
                    recipient="quality_gate_orchestrator",
                    artifact_types=["review_feedback"],
                    purpose="apply_final_quality_gate",
                )
                run = self.get_latest_run(run.task_id) or run
                run = self._complete_stage(
                    run,
                    PipelineStage.REPORTING,
                    REPORTING_OUTPUTS,
                )
            else:
                run = self._resume_stage(run, PipelineStage.REPORTING)
                reporting_payload = reporting_service.get_payload(run.task_id)
            if self._stop_if_requested(run):
                return

            final = run.model_copy(
                update={
                    "status": PipelineRunStatus.COMPLETED,
                    "current_stage": PipelineStage.COMPLETED,
                    "progress_percent": 100,
                    "message": (
                        "Research、Analyst、Citation、Writer、Reviewer 与 QualityGate "
                        "已自动完成。"
                    ),
                    "result_summary": {
                        "reporting_stage": reporting_payload.get("stage", "completed"),
                        "quality_gate_status": (
                            (reporting_payload.get("quality_gate") or {}).get("status", "")
                        ),
                    },
                    "updated_at": utc_now(),
                    "completed_at": utc_now(),
                }
            )
            final = self._save_run(final)
            self._append_event(
                final,
                event_type="pipeline_completed",
                message=final.message,
                data=final.result_summary,
            )
        except Exception as exc:
            latest = self.get_latest_run(task_id) or run
            if latest.stop_requested:
                self._finish_stopped(latest)
                return
            failed = latest.model_copy(
                update={
                    "status": PipelineRunStatus.FAILED,
                    "message": "统一多 Agent Pipeline 执行失败；已保存的 checkpoint 可用于恢复。",
                    "error": f"{type(exc).__name__}: {exc}",
                    "updated_at": utc_now(),
                    "completed_at": utc_now(),
                }
            )
            failed = self._save_run(failed)
            self._append_event(
                failed,
                event_type="pipeline_failed",
                message=failed.message,
                data={"error": failed.error},
            )

    def _run_research(self, run: PipelineRun) -> None:
        coordinator: ResearchAgentCoordinator = self._research_coordinator_factory(
            self.store
        )
        coordinator_run = coordinator.reconcile_interrupted(run.task_id)
        if coordinator_run is None or _value(coordinator_run.status) != "completed":
            coordinator_run = coordinator.submit(
                run.task_id,
                mode=run.mode,
                acknowledge_real_llm_call=run.real_llm_call_authorized,
            )

        while True:
            self.sync_research_events(run.task_id)
            current = self.get_latest_run(run.task_id) or run
            if current.stop_requested:
                latest_research = coordinator.get_latest_run(run.task_id)
                if latest_research and _value(latest_research.status) in {
                    "queued",
                    "running",
                    "stopping",
                }:
                    coordinator.request_stop(run.task_id)

            coordinator_run = coordinator.get_latest_run(run.task_id)
            if coordinator_run is None:
                raise RuntimeError("Research Coordinator 未保存运行状态。")
            status = _value(coordinator_run.status)
            if status == "completed":
                self.sync_research_events(run.task_id)
                return
            if status == "stopped":
                self.sync_research_events(run.task_id)
                if current.stop_requested:
                    return
                raise RuntimeError("Research Coordinator 非预期中止。")
            if status == "failed":
                self.sync_research_events(run.task_id)
                raise RuntimeError(coordinator_run.error or coordinator_run.message)
            time.sleep(self._poll_interval_seconds)

    def _begin_stage(
        self,
        run: PipelineRun,
        stage: PipelineStage,
        *,
        progress: int,
        message: str,
    ) -> PipelineRun:
        current = self.get_latest_run(run.task_id) or run
        attempts = dict(current.stage_attempts)
        key = _value(stage)
        attempts[key] = attempts.get(key, 0) + 1
        updated = current.model_copy(
            update={
                "status": (
                    PipelineRunStatus.STOPPING
                    if current.stop_requested
                    else PipelineRunStatus.RUNNING
                ),
                "current_stage": stage,
                "progress_percent": max(current.progress_percent, progress),
                "stage_attempts": attempts,
                "message": message,
                "updated_at": utc_now(),
            }
        )
        updated = self._save_run(updated)
        self._append_event(
            updated,
            event_type="stage_started",
            message=message,
            data={"stage_attempt": attempts[key]},
        )
        return updated

    @staticmethod
    def _stage_is_complete(run: PipelineRun, stage: PipelineStage) -> bool:
        return _value(stage) in run.completed_stages

    def _resume_stage(
        self,
        run: PipelineRun,
        stage: PipelineStage,
    ) -> PipelineRun:
        current = self.get_latest_run(run.task_id) or run
        updated = current.model_copy(
            update={
                "current_stage": stage,
                "message": (
                    f"已从 checkpoint 恢复，跳过已完成阶段 {_value(stage)}。"
                ),
                "updated_at": utc_now(),
            }
        )
        updated = self._save_run(updated)
        self._append_event(
            updated,
            event_type="stage_resumed",
            message=updated.message,
            data={"checkpoint_id": updated.last_checkpoint_id},
        )
        return updated

    def _complete_stage(
        self,
        run: PipelineRun,
        stage: PipelineStage,
        artifact_types: list[str],
    ) -> PipelineRun:
        current = self.get_latest_run(run.task_id) or run
        stage_name = _value(stage)
        completed = list(current.completed_stages)
        if stage_name not in completed:
            completed.append(stage_name)
        updated = current.model_copy(
            update={
                "completed_stages": completed,
                "message": f"Pipeline 阶段 {stage_name} 已完成。",
                "updated_at": utc_now(),
            }
        )
        updated = self._save_run(updated)
        checkpoint = self._checkpoint(updated, stage, artifact_types)
        updated = self._save_run(
            updated.model_copy(
                update={
                    "last_checkpoint_id": checkpoint.id,
                    "updated_at": utc_now(),
                }
            )
        )
        self._append_event(
            updated,
            event_type="stage_completed",
            message=updated.message,
            data={"checkpoint_id": checkpoint.id},
        )
        return updated

    def _artifact_refs(
        self,
        task_id: str,
        artifact_types: list[str],
    ) -> list[ArtifactReference]:
        refs: list[ArtifactReference] = []
        for artifact_type in dict.fromkeys(artifact_types):
            items = self.store.load_many(task_id, artifact_type)
            canonical = json.dumps(
                items,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            refs.append(
                ArtifactReference(
                    artifact_type=artifact_type,
                    artifact_ids=[
                        str(item.get("id"))
                        for item in items
                        if item.get("id")
                    ],
                    count=len(items),
                    content_hash=hashlib.sha256(canonical).hexdigest(),
                )
            )
        return refs

    def _handoff(
        self,
        run: PipelineRun,
        *,
        sender: str,
        recipient: str,
        artifact_types: list[str],
        purpose: str,
    ) -> AgentHandoff:
        key = f"{run.id}:{sender}:{recipient}:{purpose}"
        for existing in self.get_handoffs(run.task_id):
            if existing.idempotency_key == key:
                return existing
        refs = self._artifact_refs(run.task_id, artifact_types)
        handoff = AgentHandoff(
            task_id=run.task_id,
            pipeline_run_id=run.id,
            sender=sender,
            recipient=recipient,
            artifact_refs=refs,
            correlation_id=run.id,
            causation_id=run.last_checkpoint_id,
            idempotency_key=key,
            payload={"purpose": purpose},
        )
        self.store.append_many(run.task_id, AGENT_HANDOFFS_ARTIFACT, [handoff])
        current = self.get_latest_run(run.task_id) or run
        self._save_run(
            current.model_copy(
                update={
                    "current_handoff_id": handoff.id,
                    "updated_at": utc_now(),
                }
            )
        )
        self._append_event(
            current,
            event_type="handoff_created",
            message=f"{sender} 已将结构化 Artifact 引用交接给 {recipient}。",
            data={
                "handoff_id": handoff.id,
                "sender": sender,
                "recipient": recipient,
                "artifact_types": [item.artifact_type for item in refs],
            },
        )
        return handoff

    def _checkpoint(
        self,
        run: PipelineRun,
        stage: PipelineStage,
        artifact_types: list[str],
    ) -> PipelineCheckpoint:
        existing = self.get_checkpoints(run.task_id)
        refs = self._artifact_refs(run.task_id, artifact_types)
        state_payload = {
            "run_id": run.id,
            "stage": _value(stage),
            "completed_stages": run.completed_stages,
            "artifact_hashes": {
                item.artifact_type: item.content_hash for item in refs
            },
        }
        state_hash = hashlib.sha256(
            json.dumps(
                state_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        checkpoint = PipelineCheckpoint(
            task_id=run.task_id,
            pipeline_run_id=run.id,
            sequence=len(existing) + 1,
            stage=stage,
            completed_stages=run.completed_stages,
            artifact_refs=refs,
            state_hash=state_hash,
        )
        self.store.append_many(
            run.task_id,
            PIPELINE_CHECKPOINTS_ARTIFACT,
            [checkpoint],
        )
        return checkpoint

    def _stop_if_requested(self, run: PipelineRun) -> bool:
        current = self.get_latest_run(run.task_id) or run
        if not current.stop_requested:
            return False
        self._finish_stopped(current)
        return True

    def _finish_stopped(self, run: PipelineRun) -> PipelineRun:
        stopped = run.model_copy(
            update={
                "status": PipelineRunStatus.STOPPED,
                "stop_requested": True,
                "stop_reason": run.stop_reason or "user_requested",
                "message": "整个多 Agent Pipeline 已在安全边界中止；现有 Artifact 已保留。",
                "updated_at": utc_now(),
                "completed_at": utc_now(),
            }
        )
        stopped = self._save_run(stopped)
        events = self.get_events(run.task_id)
        if not any(
            item.pipeline_run_id == run.id and item.event_type == "pipeline_stopped"
            for item in events
        ):
            self._append_event(
                stopped,
                event_type="pipeline_stopped",
                message=stopped.message,
                data={"stop_reason": stopped.stop_reason},
            )
        return stopped

    def _save_run(self, run: PipelineRun) -> PipelineRun:
        with self._lock:
            existing = [
                PipelineRun(**item)
                for item in self.store.load_many(run.task_id, PIPELINE_RUNS_ARTIFACT)
            ]
            updated: list[PipelineRun] = []
            replaced = False
            saved = run
            for item in existing:
                if item.id != run.id:
                    updated.append(item)
                    continue
                is_explicit_resume = (
                    _value(item.status) in TERMINAL_PIPELINE_STATUSES
                    and _value(run.status) == "queued"
                    and run.resume_count > item.resume_count
                )
                if (
                    item.stop_requested
                    and not run.stop_requested
                    and not is_explicit_resume
                ):
                    saved = run.model_copy(
                        update={
                            "stop_requested": True,
                            "stop_reason": item.stop_reason,
                            "status": (
                                PipelineRunStatus.STOPPING
                                if _value(run.status) in {"queued", "running"}
                                else run.status
                            ),
                            "message": (
                                item.message
                                if _value(run.status) in {"queued", "running"}
                                else run.message
                            ),
                        }
                    )
                updated.append(saved)
                replaced = True
            if not replaced:
                updated.append(saved)
            self.store.save_many(run.task_id, PIPELINE_RUNS_ARTIFACT, updated)
            return saved

    def _append_event(
        self,
        run: PipelineRun,
        *,
        event_type: str,
        message: str,
        research_task_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        with self._lock:
            existing = self.store.load_many(run.task_id, PIPELINE_EVENTS_ARTIFACT)
            sequence = max(
                (int(item.get("sequence", 0)) for item in existing),
                default=0,
            ) + 1
            event = PipelineEvent(
                task_id=run.task_id,
                pipeline_run_id=run.id,
                sequence=sequence,
                event_type=event_type,
                stage=_value(run.current_stage),
                status=_value(run.status),
                research_task_id=research_task_id,
                message=message,
                progress_percent=run.progress_percent,
                data=data or {},
            )
            self.store.append_many(
                run.task_id,
                PIPELINE_EVENTS_ARTIFACT,
                [event],
            )
            return event

_DEFAULT_PIPELINE_HARNESS: ResearchPipelineHarness | None = None
_DEFAULT_PIPELINE_HARNESS_LOCK = threading.Lock()


def get_research_pipeline_harness() -> ResearchPipelineHarness:
    global _DEFAULT_PIPELINE_HARNESS
    with _DEFAULT_PIPELINE_HARNESS_LOCK:
        if _DEFAULT_PIPELINE_HARNESS is None:
            _DEFAULT_PIPELINE_HARNESS = ResearchPipelineHarness()
        return _DEFAULT_PIPELINE_HARNESS
