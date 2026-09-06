from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents.web_evidence import normalize_dimension
from app.execution.research_agent import get_research_evidence_agent_service
from app.execution.evidence_feed import build_evidence_feed_transitions
from app.execution.research_mission import ResearchMissionService
from app.execution.research_mission_supervisor import (
    build_llm_mission_supervisor,
    mission_coverage_and_gaps,
)
from app.harness.artifacts import ArtifactStore
from app.intake import ResearchAgentBoundedRefreshService
from app.schemas import (
    EvidenceCoverageStatus,
    ExecutionMode,
    InformationNeed,
    MissionSupervisorAction,
    ResearchAgentBudget,
    ResearchAgentRun,
    ResearchMissionBudgetState,
    ResearchMissionState,
    ResearchPlan,
    ResearchTask,
    SourceEvidence,
    new_id,
    utc_now,
)


COORDINATOR_RUNS_ARTIFACT = "research_agent_coordinator_runs"
COORDINATOR_EVENTS_ARTIFACT = "research_agent_coordinator_events"
RESEARCH_TASK_FAILURES_ARTIFACT = "research_task_failures"
RESEARCH_BATCH_RESULTS_ARTIFACT = "research_batch_results"

ACTIVE_STATUSES = {"queued", "running", "stopping"}
TERMINAL_STATUSES = {"completed", "failed", "stopped"}
SUFFICIENT_COVERAGE_STATUSES = {
    EvidenceCoverageStatus.SUFFICIENT.value,
    EvidenceCoverageStatus.NOT_APPLICABLE.value,
}

ResearchServiceFactory = Callable[[ArtifactStore], Any]
CoverageServiceFactory = Callable[[ArtifactStore], Any]
MissionSupervisorFactory = Callable[[ArtifactStore], Any]


class ResearchAgentCoordinatorRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("researchagentcoord"))
    task_id: str
    status: str = "queued"
    mode: str = ExecutionMode.DEEPSEEK.value

    research_task_ids: list[str] = Field(default_factory=list)
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0

    current_collection_round: int = 0
    max_collection_rounds: int = 3
    current_round_total: int = 0
    current_round_completed: int = 0
    source_count: int = 0
    max_total_sources: int = 40
    actions_completed: int = 0
    max_actions: int = 100
    coverage_status_counts: dict[str, int] = Field(default_factory=dict)
    research_gap_count: int = 0
    stop_reason: str = ""
    result_status: str = ""

    current_research_task_id: str = ""
    outcomes: dict[str, int] = Field(default_factory=dict)
    progress_percent: int = 0

    message: str = ""
    error: str = ""

    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ResearchAgentCoordinatorEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("researchagentcoordeve"))
    task_id: str
    coordinator_run_id: str
    sequence: int

    event_type: str
    research_task_id: str = ""
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utc_now)


class ResearchTaskFailure(BaseModel):
    id: str = Field(default_factory=lambda: new_id("researchtaskfailure"))
    task_id: str
    research_task_id: str
    research_need_id: str = ""
    mission_id: str = ""
    collection_round: int = Field(default=1, ge=1)
    stage: str
    error_type: str
    error_message: str
    created_at: datetime = Field(default_factory=utc_now)


class ResearchBatchResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("researchbatch"))
    task_id: str
    collection_round: int = Field(ge=1)
    status: str
    completed_tasks: list[str] = Field(default_factory=list)
    failed_tasks: list[ResearchTaskFailure] = Field(default_factory=list)
    pending_tasks: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ResearchAgentCoordinator:
    """
    Batch coordinator for Research & Evidence Agent R1.

    It does not perform research itself.
    It only schedules ResearchTasks that still need Research Agent work and
    records task-level progress. ``waiting_for_collector`` remains an internal
    compatibility status emitted by the current deterministic planner.
    """

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        max_workers: int = 1,
        research_service_factory: ResearchServiceFactory | None = None,
        coverage_service_factory: CoverageServiceFactory | None = None,
        mission_supervisor_factory: MissionSupervisorFactory | None = None,
    ):
        self.store = store or ArtifactStore()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="research-agent-coordinator",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._stop_requested: set[str] = set()
        self._research_service_factory = research_service_factory or (
            lambda _store: get_research_evidence_agent_service()
        )
        self._coverage_service_factory = coverage_service_factory or (
            lambda store: ResearchAgentBoundedRefreshService(store=store)
        )
        self._mission_supervisor_factory = mission_supervisor_factory or (
            lambda store: build_llm_mission_supervisor(store=store)
        )

    def get_latest_run(
        self,
        task_id: str,
    ) -> ResearchAgentCoordinatorRun | None:
        items = self.store.load_many(task_id, COORDINATOR_RUNS_ARTIFACT)
        if not items:
            return None
        return ResearchAgentCoordinatorRun(**items[-1])

    def get_events(
        self,
        task_id: str,
        *,
        after: int = 0,
    ) -> list[ResearchAgentCoordinatorEvent]:
        return [
            ResearchAgentCoordinatorEvent(**item)
            for item in self.store.load_many(
                task_id,
                COORDINATOR_EVENTS_ARTIFACT,
            )
            if int(item.get("sequence", 0)) > after
        ]

    def sync_evidence_events(
        self,
        task_id: str,
        *,
        run: ResearchAgentCoordinatorRun | None = None,
    ) -> list[ResearchAgentCoordinatorEvent]:
        """Append missing Evidence Library transitions to the existing stream."""

        with self._lock:
            current = run or self.get_latest_run(task_id)
            if current is None:
                return []

            existing = self.store.load_many(
                task_id,
                COORDINATOR_EVENTS_ARTIFACT,
            )
            emitted_keys = {
                (
                    str(item.get("data", {}).get("url") or "")
                    .strip()
                    .rstrip("/")
                    .casefold(),
                    str(item.get("data", {}).get("status") or ""),
                )
                for item in existing
                if item.get("event_type") == "evidence_added"
            }
            appended: list[ResearchAgentCoordinatorEvent] = []
            status_labels = {
                "discovered": "已发现",
                "collection_failed": "采集失败",
                "collected": "已采集",
                "verified": "已验证",
            }

            for item in build_evidence_feed_transitions(self.store, task_id):
                key = (
                    str(item.get("url") or "")
                    .strip()
                    .rstrip("/")
                    .casefold(),
                    str(item.get("status") or ""),
                )
                if key in emitted_keys:
                    continue

                payload = {
                    "title": item["title"],
                    "url": item["url"],
                    "source_tool": item["source_tool"],
                    "status": item["status"],
                    "reliability_score": item["reliability_score"],
                }
                event = ResearchAgentCoordinatorEvent(
                    task_id=task_id,
                    coordinator_run_id=current.id,
                    sequence=len(existing) + len(appended) + 1,
                    event_type="evidence_added",
                    message=(
                        f"{status_labels.get(item['status'], item['status'])}："
                        f"{item['title']}"
                    ),
                    data=payload,
                )
                appended.append(event)
                emitted_keys.add(key)

            if appended:
                self.store.append_many(
                    task_id,
                    COORDINATOR_EVENTS_ARTIFACT,
                    appended,
                )
            return appended

    def submit(
        self,
        task_id: str,
        *,
        mode: ExecutionMode | str = ExecutionMode.DEEPSEEK,
        acknowledge_real_llm_call: bool = False,
    ) -> ResearchAgentCoordinatorRun:
        selected_mode = ExecutionMode(mode)

        if selected_mode != ExecutionMode.DEEPSEEK:
            raise ValueError(
                "Research Agent Coordinator 当前只允许显式 DeepSeek 模式。"
            )

        if not acknowledge_real_llm_call:
            raise ValueError(
                "必须显式确认 Research Agent 会产生真实多轮 LLM 调用。"
            )

        ResearchMissionService(store=self.store).ensure_missions(task_id)
        research_task_ids = self._runnable_research_task_ids(task_id)

        if not research_task_ids:
            raise ValueError(
                "当前任务没有需要自动研究的 ResearchTask。"
            )

        with self._lock:
            latest = self.get_latest_run(task_id)

            if latest and latest.status in ACTIVE_STATUSES:
                return latest

            plan = self._latest_plan(task_id)
            if plan is None:
                raise LookupError("尚未生成 ResearchPlan（研究计划）。")
            max_actions = max(
                20,
                min(
                    1000,
                    plan.budget.max_total_sources * 3
                    + len(research_task_ids) * 3,
                ),
            )
            run = ResearchAgentCoordinatorRun(
                task_id=task_id,
                mode=selected_mode.value,
                research_task_ids=research_task_ids,
                total_tasks=len(research_task_ids),
                current_collection_round=min(
                    (
                        item.collection_round
                        for item in self._runnable_research_tasks(task_id)
                    ),
                    default=0,
                ),
                max_collection_rounds=plan.budget.max_collection_rounds,
                source_count=len(self.store.load_many(task_id, "sources")),
                max_total_sources=plan.budget.max_total_sources,
                actions_completed=len(
                    self.store.load_many(task_id, "research_agent_actions")
                ),
                max_actions=max_actions,
                message="Research Agent R1 已进入后台研究队列。",
            )

            self._save_run(run)

            self.store.save_many(
                task_id,
                COORDINATOR_EVENTS_ARTIFACT,
                [],
            )

            self._append_event(
                run,
                event_type="queued",
                message=f"等待执行 {len(research_task_ids)} 个 ResearchTask。",
            )

            self._futures[task_id] = self._pool.submit(
                self._execute,
                run,
                selected_mode,
                acknowledge_real_llm_call,
            )

            return run

    def wait(
        self,
        task_id: str,
        *,
        timeout: float = 300.0,
    ) -> ResearchAgentCoordinatorRun:
        future = self._futures.get(task_id)

        if future is not None:
            future.result(timeout=timeout)

        latest = self.get_latest_run(task_id)

        if latest is None:
            raise LookupError(
                f"未找到 ResearchAgentCoordinatorRun: {task_id}"
            )

        return latest

    def request_stop(
        self,
        task_id: str,
    ) -> ResearchAgentCoordinatorRun:
        """Request cooperative cancellation at the next safe task boundary."""

        with self._lock:
            latest = self.get_latest_run(task_id)
            if latest is None:
                raise LookupError(
                    f"未找到 ResearchAgentCoordinatorRun: {task_id}"
                )
            if latest.status in TERMINAL_STATUSES:
                return latest
            if latest.status == "stopping":
                return latest

            self._stop_requested.add(task_id)
            stopping = latest.model_copy(
                update={
                    "status": "stopping",
                    "stop_reason": "user_requested",
                    "message": (
                        "已收到中止请求；当前 ResearchTask 安全结束后停止。"
                    ),
                }
            )
            self._save_run(stopping)
            self._append_event(
                stopping,
                event_type="stop_requested",
                message=stopping.message,
                data={"stop_reason": "user_requested"},
            )

            future = self._futures.get(task_id)
            if future is None or future.cancel():
                return self._finish(
                    stopping,
                    status="stopped",
                    reason="user_requested",
                    message="Research Agent 已按用户请求中止。",
                )
            return stopping

    def _is_stop_requested(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._stop_requested

    def _stop_if_requested(
        self,
        run: ResearchAgentCoordinatorRun,
    ) -> bool:
        if not self._is_stop_requested(run.task_id):
            return False
        self._finish(
            run,
            status="stopped",
            reason="user_requested",
            message="Research Agent 已按用户请求中止。",
        )
        return True

    def reconcile_interrupted(
        self,
        task_id: str,
    ) -> ResearchAgentCoordinatorRun | None:
        with self._lock:
            latest = self.get_latest_run(task_id)

            if latest is None or latest.status not in ACTIVE_STATUSES:
                return latest

            future = self._futures.get(task_id)

            if future is not None and not future.done():
                return latest

            if latest.status == "stopping":
                return self._finish(
                    latest,
                    status="stopped",
                    reason="user_requested",
                    message="Research Agent 已按用户请求中止。",
                )

            failed = latest.model_copy(
                update={
                    "status": "failed",
                    "message": "Research Agent 后台任务已中断。",
                    "error": (
                        "ResearchAgentCoordinatorInterrupted: "
                        "后台进程重启或 worker 已丢失。"
                    ),
                    "completed_at": utc_now(),
                }
            )

            self._save_run(failed)

            self._append_event(
                failed,
                event_type="failed",
                message=failed.error,
            )

            return failed

    def _execute(
        self,
        run: ResearchAgentCoordinatorRun,
        mode: ExecutionMode,
        acknowledge_real_llm_call: bool,
    ) -> None:
        if self._stop_if_requested(run):
            return

        running = run.model_copy(
            update={
                "status": "running",
                "started_at": utc_now(),
                "progress_percent": 1,
                "message": "Research Agent R1 已开始执行。",
            }
        )

        self._save_run(running)

        self._append_event(
            running,
            event_type="started",
            message="开始按 collection_round 有限轮调度 Research Agent R1。",
        )

        current = running
        failures: list[ResearchTaskFailure] = []
        try:
            while True:
                if self._stop_if_requested(current):
                    return
                current = self._refresh_snapshot(current)
                budget_stop = self._budget_stop(current)
                if budget_stop is not None:
                    reason, message = budget_stop
                    self._finish(current, reason=reason, message=message)
                    return

                runnable = self._runnable_research_tasks(run.task_id)
                if not runnable:
                    self._finish(
                        current,
                        reason="no_runnable_supplement",
                        message=(
                            "Coverage 仍未完全关闭，但没有可运行的补采 ResearchTask；"
                            "已停止补采并交给当前 Analyst。"
                        ),
                    )
                    return

                round_number = min(item.collection_round for item in runnable)
                round_tasks = [
                    item
                    for item in runnable
                    if item.collection_round == round_number
                ]
                if round_number > current.max_collection_rounds:
                    self._finish(
                        current,
                        reason="max_collection_rounds",
                        message="已达到最大采集轮数，停止补采并交给当前 Analyst。",
                    )
                    return

                current = current.model_copy(
                    update={
                        "current_collection_round": round_number,
                        "current_round_total": len(round_tasks),
                        "current_round_completed": 0,
                        "message": (
                            f"Research Agent R1 正在执行第 {round_number} 轮，"
                            f"共 {len(round_tasks)} 个 ResearchTask。"
                        ),
                    }
                )
                self._save_run(current)
                self._append_event(
                    current,
                    event_type="collection_round_started",
                    message=current.message,
                    data={
                        "collection_round": round_number,
                        "research_task_ids": [item.id for item in round_tasks],
                    },
                )

                round_completed_task_ids: list[str] = []
                round_failure_start = len(failures)
                for research_task in round_tasks:
                    if self._stop_if_requested(current):
                        return
                    current = self._refresh_snapshot(current)
                    if self._budget_stop(current) is not None:
                        break
                    failure_count = len(failures)
                    current = self._run_research_task(
                        current,
                        research_task=research_task,
                        mode=mode,
                        acknowledge_real_llm_call=acknowledge_real_llm_call,
                        failures=failures,
                    )
                    if len(failures) == failure_count:
                        round_completed_task_ids.append(research_task.id)

                    if self._stop_if_requested(current):
                        return

                coverage_summary = self._coverage_service_factory(
                    self.store
                ).refresh(run.task_id)
                ResearchMissionService(store=self.store).refresh_coverage(
                    run.task_id
                )
                current = self._refresh_snapshot(current)
                supervisor_decisions = self._supervise_missions(
                    current, completed_round=round_number
                )
                all_task_ids = [
                    item.id
                    for item in self._deduplicated_research_tasks(run.task_id)
                ]
                current = current.model_copy(
                    update={
                        "research_task_ids": all_task_ids,
                        "total_tasks": len(all_task_ids),
                        "message": (
                            f"第 {round_number} 轮完成，已重新生成 R1 "
                            "ProductCard / EvidenceCoverage。"
                        ),
                    }
                )
                current = self._refresh_snapshot(current)
                self._append_event(
                    current,
                    event_type="coverage_refreshed",
                    message=current.message,
                    data={
                        "collection_round": round_number,
                        "coverage_summary": coverage_summary,
                        "mission_supervisor_decisions": supervisor_decisions,
                    },
                )
                round_failures = failures[round_failure_start:]
                pending_task_ids = self._runnable_research_task_ids(
                    run.task_id
                )
                batch_status = (
                    "FAILED"
                    if round_failures and not round_completed_task_ids
                    else "PARTIAL"
                    if round_failures or pending_task_ids
                    else "COMPLETE"
                )
                batch_result = ResearchBatchResult(
                    task_id=run.task_id,
                    collection_round=round_number,
                    status=batch_status,
                    completed_tasks=round_completed_task_ids,
                    failed_tasks=round_failures,
                    pending_tasks=pending_task_ids,
                )
                self._append_batch_result(batch_result)
                self._append_event(
                    current,
                    event_type="research_batch_completed",
                    message=(
                        f"第 {round_number} 轮 batch={batch_status}；"
                        f"完成 {len(round_completed_task_ids)}，"
                        f"失败 {len(round_failures)}，"
                        f"待执行 {len(pending_task_ids)}。"
                    ),
                    data=batch_result.model_dump(mode="json"),
                )

                decision = self._post_coverage_stop(current)
                if decision is not None:
                    reason, message = decision
                    self._finish(current, reason=reason, message=message)
                    return
        except Exception as exc:
            self._finish(
                self._refresh_snapshot(current),
                status="failed",
                reason="failed",
                message="Research Agent R1 bounded loop 执行失败。",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_research_task(
        self,
        current: ResearchAgentCoordinatorRun,
        *,
        research_task: ResearchTask,
        mode: ExecutionMode,
        acknowledge_real_llm_call: bool,
        failures: list[ResearchTaskFailure],
    ) -> ResearchAgentCoordinatorRun:
        research_task_id = research_task.id
        started = current.model_copy(
            update={
                "current_research_task_id": research_task_id,
                "message": f"Research Agent R1 正在执行 {research_task_id}。",
            }
        )
        self._save_run(started)
        self._append_event(
            started,
            event_type="research_task_started",
            research_task_id=research_task_id,
            message=started.message,
            data={"collection_round": research_task.collection_round},
        )
        try:
            mission_service = ResearchMissionService(store=self.store)
            mission_service.ensure_missions(started.task_id)
            mission_service.prepare_worker(started.task_id, research_task)
            remaining_sources = max(
                1,
                started.max_total_sources
                - len(self.store.load_many(started.task_id, "sources")),
            )
            remaining_actions = max(
                1,
                started.max_actions
                - len(
                    self.store.load_many(
                        started.task_id,
                        "research_agent_actions",
                    )
                ),
            )
            plan = self._latest_plan(started.task_id)
            per_task_sources = (
                plan.budget.max_sources_per_task if plan else 5
            )
            budget = ResearchAgentBudget(
                max_steps=min(12, remaining_actions),
                max_searches=min(4, remaining_actions),
                max_sources=min(per_task_sources, remaining_sources),
                max_failed_actions=min(3, remaining_actions),
            )
            service = self._research_service_factory(self.store)
            payload = service.run_once(
                started.task_id,
                research_task_id=research_task_id,
                mode=mode,
                acknowledge_real_llm_call=acknowledge_real_llm_call,
                budget=budget,
            )
            self.sync_evidence_events(
                started.task_id,
                run=started,
            )
            mission_service.merge_worker(
                started.task_id, research_task_id
            )
            agent_runs = payload.get("runs") or []
            latest_agent_run = agent_runs[-1] if agent_runs else {}
            outcome = str(latest_agent_run.get("outcome") or "UNKNOWN")
            outcomes = dict(started.outcomes)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            completed_tasks = started.completed_tasks + 1
            updated = started.model_copy(
                update={
                    "completed_tasks": completed_tasks,
                    "current_round_completed": min(
                        started.current_round_completed + 1,
                        started.current_round_total,
                    ),
                    "outcomes": outcomes,
                    "message": f"{research_task_id} 完成，outcome={outcome}",
                }
            )
            updated = self._refresh_snapshot(updated)
            self._append_event(
                updated,
                event_type="research_task_completed",
                research_task_id=research_task_id,
                message=updated.message,
                data={
                    "outcome": outcome,
                    "collection_round": research_task.collection_round,
                },
            )
            return updated
        except Exception as exc:
            self.sync_evidence_events(
                started.task_id,
                run=started,
            )
            error = f"{research_task_id}: {type(exc).__name__}: {exc}"
            failure = self._record_research_task_failure(
                task_id=started.task_id,
                research_task=research_task,
                error=exc,
            )
            failures.append(failure)
            updated = self._refresh_snapshot(
                started.model_copy(
                    update={
                        "failed_tasks": started.failed_tasks + 1,
                        "current_round_completed": min(
                            started.current_round_completed + 1,
                            started.current_round_total,
                        ),
                        "message": f"{research_task_id} 执行失败。",
                    }
                )
            )
            self._append_event(
                updated,
                event_type="research_task_failed",
                research_task_id=research_task_id,
                message=error,
                data=failure.model_dump(mode="json"),
            )
            return updated

    def _budget_stop(
        self,
        run: ResearchAgentCoordinatorRun,
    ) -> tuple[str, str] | None:
        if run.source_count >= run.max_total_sources:
            return (
                "source_budget_exhausted",
                "已达到来源预算，停止补采并交给当前 Analyst。",
            )
        if run.actions_completed >= run.max_actions:
            return (
                "action_budget_exhausted",
                "已达到 Research Agent 动作预算，停止补采并交给当前 Analyst。",
            )
        return None

    def _record_research_task_failure(
        self,
        *,
        task_id: str,
        research_task: ResearchTask,
        error: Exception,
    ) -> ResearchTaskFailure:
        error_message = str(error)
        stage_match = re.search(r"stage=([^；;\s]+)", error_message)
        nested_error_match = re.search(
            r"(?:^|:\s)([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):",
            error_message,
        )
        mission = ResearchMissionService(store=self.store).mission_for_task(
            task_id, research_task.id
        )
        failure = ResearchTaskFailure(
            task_id=task_id,
            research_task_id=research_task.id,
            research_need_id=research_task.information_need_id,
            mission_id=str(
                research_task.metadata.get("mission_id")
                or (mission.id if mission else "")
            ),
            collection_round=research_task.collection_round,
            stage=(stage_match.group(1) if stage_match else "research_worker"),
            error_type=(
                nested_error_match.group(1)
                if nested_error_match
                else type(error).__name__
            ),
            error_message=error_message[:4000],
        )
        existing_failures = [
            ResearchTaskFailure(**item)
            for item in self.store.load_many(
                task_id, RESEARCH_TASK_FAILURES_ARTIFACT
            )
        ]
        self.store.save_many(
            task_id,
            RESEARCH_TASK_FAILURES_ARTIFACT,
            [*existing_failures, failure],
        )

        runs = [
            ResearchAgentRun(**item)
            for item in self.store.load_many(task_id, "research_agent_runs")
        ]
        matching_indexes = [
            index
            for index, item in enumerate(runs)
            if item.research_task_id == research_task.id
        ]
        failed_evidence_ids: set[str] = set()
        if matching_indexes:
            index = matching_indexes[-1]
            failed_evidence_ids.update(runs[index].verified_evidence_ids)
            runs[index] = runs[index].model_copy(
                update={
                    "status": "failed",
                    "outcome": "FAILED",
                    "verified_evidence_ids": [],
                    "completed_at": utc_now(),
                    "metadata": {
                        **runs[index].metadata,
                        "research_task_failure_id": failure.id,
                    },
                }
            )
        else:
            runs.append(
                ResearchAgentRun(
                    task_id=task_id,
                    research_task_id=research_task.id,
                    mission_id=failure.mission_id,
                    status="failed",
                    outcome="FAILED",
                    remaining_need=research_task.objective,
                    completed_at=utc_now(),
                    metadata={"research_task_failure_id": failure.id},
                )
            )
        self.store.save_many(task_id, "research_agent_runs", runs)

        evidence = [
            SourceEvidence(**item)
            for item in self.store.load_many(task_id, "evidence")
        ]
        self.store.save_many(
            task_id,
            "evidence",
            [
                item
                for item in evidence
                if item.id not in failed_evidence_ids
                and str(item.metadata.get("research_task_id") or "")
                != research_task.id
            ],
        )
        research_tasks = self._all_research_tasks(task_id)
        self.store.save_many(
            task_id,
            "research_tasks",
            [
                item.model_copy(
                    update={
                        "status": "research_failed",
                        "metadata": {
                            **item.metadata,
                            "research_task_failure_id": failure.id,
                        },
                    }
                )
                if item.id == research_task.id
                else item
                for item in research_tasks
            ],
        )
        try:
            ResearchMissionService(store=self.store).merge_worker(
                task_id, research_task.id
            )
        except Exception:
            # Failure isolation must preserve the original task error even if
            # the optional Mission merge cannot be completed.
            pass
        return failure

    def _append_batch_result(self, result: ResearchBatchResult) -> None:
        existing = [
            ResearchBatchResult(**item)
            for item in self.store.load_many(
                result.task_id, RESEARCH_BATCH_RESULTS_ARTIFACT
            )
        ]
        self.store.save_many(
            result.task_id,
            RESEARCH_BATCH_RESULTS_ARTIFACT,
            [*existing, result],
        )

    def _supervise_missions(
        self,
        run: ResearchAgentCoordinatorRun,
        *,
        completed_round: int,
    ) -> list[dict[str, Any]]:
        if self._budget_stop(run) is not None:
            return []
        mission_service = ResearchMissionService(store=self.store)
        missions = mission_service.ensure_missions(run.task_id)
        if not missions:
            return []
        supervisor = self._mission_supervisor_factory(self.store)
        decisions: list[dict[str, Any]] = []
        for mission in missions:
            if mission.status == "finished":
                continue
            worker_result = mission_service.latest_worker_result(
                run.task_id, mission.id
            )
            if worker_result is None:
                continue
            state = next(
                item
                for item in (
                    ResearchMissionState(**raw)
                    for raw in self.store.load_many(
                        run.task_id, "research_mission_states"
                    )
                )
                if item.mission_id == mission.id
            )
            coverage, gaps = mission_coverage_and_gaps(
                store=self.store, task_id=run.task_id, mission=mission
            )
            budget_state = ResearchMissionBudgetState(
                collection_round=max(1, completed_round),
                max_collection_rounds=run.max_collection_rounds,
                completed_units=len(state.worker_result_ids),
                max_units=max(
                    1,
                    len(mission.information_need_ids)
                    * run.max_collection_rounds,
                ),
                sources_used=run.source_count,
                max_total_sources=run.max_total_sources,
                actions_used=run.actions_completed,
                max_actions=run.max_actions,
            )
            decision = supervisor.decide(
                task_id=run.task_id,
                mission=mission,
                state=state,
                worker_result=worker_result,
                coverage=coverage,
                gaps=gaps,
                budget_state=budget_state,
            )
            bounded_reason = ""

            all_needs_sufficient = bool(mission.information_need_ids) and all(
                state.coverage_status_by_need.get(need_id)
                in SUFFICIENT_COVERAGE_STATUSES
                for need_id in mission.information_need_ids
            )

            if all_needs_sufficient:
                bounded_reason = "Mission Coverage 已充分。"
            elif completed_round >= run.max_collection_rounds:
                bounded_reason = "Mission 已达到最大采集轮数。"
            elif budget_state.completed_units >= budget_state.max_units:
                bounded_reason = "Mission 已达到 Research Unit 上限。"
            elif budget_state.sources_used >= budget_state.max_total_sources:
                bounded_reason = "Mission 已达到 Source 预算上限。"
            elif budget_state.actions_used >= budget_state.max_actions:
                bounded_reason = "Mission 已达到 Action 预算上限。"

            # Direction A:
            # Supervisor 想继续，但确定性 Gate 已证明必须停止。
            if (
                    bounded_reason
                    and decision.action != MissionSupervisorAction.FINISH.value
            ):
                decision = decision.model_copy(
                    update={
                        "action": MissionSupervisorAction.FINISH.value,
                        "target_need": "",
                        "research_goal": "",
                        "reason": bounded_reason,
                        "metadata": {
                            **decision.metadata,
                            "bounded_override": True,
                            "original_action": str(decision.action),
                            "gate_direction": "force_finish",
                        },
                    }
                )

            # Direction B:
            # Supervisor 想提前 FINISH，但预算仍允许，并且存在：
            #   未充分覆盖的真实 InformationNeed
            #   + 与它对应的 ResearchGap
            # 则拒绝 premature FINISH，再补一轮证据。
            elif (
                    decision.action == MissionSupervisorAction.FINISH.value
                    and not bounded_reason
            ):
                needs = {
                    item.id: item
                    for item in (
                        InformationNeed(**raw)
                        for raw in self.store.load_many(
                        run.task_id, "research_information_needs"
                    )
                    )
                    if item.id in mission.information_need_ids
                }

                unresolved_need_ids = [
                    need_id
                    for need_id in mission.information_need_ids
                    if state.coverage_status_by_need.get(need_id)
                       not in SUFFICIENT_COVERAGE_STATUSES
                ]

                actionable_gap = None
                target_need_id = ""

                for need_id in unresolved_need_ids:
                    need = needs.get(need_id)
                    if need is None:
                        continue

                    match = next(
                        (
                            gap
                            for gap in gaps
                            if normalize_dimension(gap.dimension)
                               == normalize_dimension(need.dimension)
                               and gap.missing_information.strip()
                               and (
                                       gap.suggested_queries
                                       or gap.preferred_source_types
                                       or gap.decision_blocked.strip()
                               )
                        ),
                        None,
                    )

                    if match is not None:
                        actionable_gap = match
                        target_need_id = need_id
                        break

                if actionable_gap is not None and target_need_id:
                    already_researched = target_need_id in state.outcome_by_need

                    decision = decision.model_copy(
                        update={
                            "action": (
                                MissionSupervisorAction.REQUEST_MORE_EVIDENCE.value
                                if already_researched
                                else MissionSupervisorAction.CREATE_RESEARCH_UNIT.value
                            ),
                            "target_need": target_need_id,
                            "research_goal": (
                                "补充以下尚未充分覆盖的信息："
                                f"{actionable_gap.missing_information}"
                            ),
                            "reason": (
                                "Supervisor 请求 FINISH，但当前仍存在未充分覆盖的 "
                                "InformationNeed 和可执行 ResearchGap，且 Mission "
                                "预算尚未耗尽，因此阻止过早结束并继续研究。"
                            ),
                            "metadata": {
                                **decision.metadata,
                                "finish_veto": True,
                                "gate_direction": "prevent_premature_finish",
                                "original_action": str(decision.action),
                                "research_gap_id": actionable_gap.id,
                            },
                        }
                    )
            mission_service.record_supervisor_decision(decision)
            unit = None
            if decision.action == MissionSupervisorAction.FINISH.value:
                mission_service.finish_mission(run.task_id, mission.id)
            else:
                unit = mission_service.materialize_research_unit(
                    decision,
                    collection_round=completed_round + 1,
                    max_collection_rounds=run.max_collection_rounds,
                )
            decisions.append(
                {
                    "decision_id": decision.id,
                    "mission_id": mission.id,
                    "action": str(decision.action),
                    "target_need": decision.target_need,
                    "research_unit_id": unit.id if unit else "",
                    "reason": decision.reason,
                }
            )
            self._append_event(
                run,
                event_type="mission_supervisor_decision",
                message=decision.reason,
                research_task_id=unit.id if unit else "",
                data=decisions[-1],
            )
        return decisions

    def _post_coverage_stop(
        self,
        run: ResearchAgentCoordinatorRun,
    ) -> tuple[str, str] | None:
        coverage = self.store.load_many(run.task_id, "evidence_coverage")
        if coverage and all(
            str(item.get("status") or "") in SUFFICIENT_COVERAGE_STATUSES
            for item in coverage
        ):
            return (
                "coverage_sufficient",
                "所有基础 Coverage 均已达到 SUFFICIENT，停止补采并交给当前 Analyst。",
            )
        budget_stop = self._budget_stop(run)
        if budget_stop is not None:
            return budget_stop
        runnable = self._runnable_research_tasks(run.task_id)
        if runnable:
            return None
        missions = self.store.load_many(run.task_id, "research_missions")
        if missions and all(item.get("status") == "finished" for item in missions):
            return (
                "mission_supervisor_finish",
                "Mission Supervisor 已确认研究可以结束，交给当前 Analyst。",
            )
        if run.current_collection_round >= run.max_collection_rounds:
            return (
                "max_collection_rounds",
                "基础 Coverage 仍不足，但已达到最大采集轮数；停止补采并交给当前 Analyst。",
            )
        return (
            "no_runnable_supplement",
            "基础 Coverage 仍不足，但没有可运行补采任务；停止补采并交给当前 Analyst。",
        )

    def _refresh_snapshot(
        self,
        run: ResearchAgentCoordinatorRun,
    ) -> ResearchAgentCoordinatorRun:
        coverage_counts: dict[str, int] = {}
        for item in self.store.load_many(run.task_id, "evidence_coverage"):
            status = str(item.get("status") or "unknown")
            coverage_counts[status] = coverage_counts.get(status, 0) + 1
        plan = self._latest_plan(run.task_id)
        execution_tasks = self._deduplicated_research_tasks(run.task_id)
        research_task_ids = [item.id for item in execution_tasks]
        total_tasks = len(research_task_ids)
        processed_tasks = run.completed_tasks + run.failed_tasks
        progress_percent = (
            min(max(int(100 * processed_tasks / total_tasks), 1), 99)
            if total_tasks
            else min(max(run.progress_percent, 0), 99)
        )
        updated = run.model_copy(
            update={
                "research_task_ids": research_task_ids,
                "total_tasks": total_tasks,
                "progress_percent": progress_percent,
                "source_count": len(
                    self.store.load_many(run.task_id, "sources")
                ),
                "max_total_sources": (
                    plan.budget.max_total_sources
                    if plan
                    else run.max_total_sources
                ),
                "max_collection_rounds": (
                    plan.budget.max_collection_rounds
                    if plan
                    else run.max_collection_rounds
                ),
                "actions_completed": len(
                    self.store.load_many(
                        run.task_id,
                        "research_agent_actions",
                    )
                ),
                "coverage_status_counts": coverage_counts,
                "research_gap_count": len(
                    self.store.load_many(run.task_id, "research_gaps")
                ),
            }
        )
        self._save_run(updated)
        return updated

    def _finish(
        self,
        run: ResearchAgentCoordinatorRun,
        *,
        reason: str,
        message: str,
        status: str = "completed",
        error: str = "",
    ) -> ResearchAgentCoordinatorRun:
        with self._lock:
            if (
                run.task_id in self._stop_requested
                and status != "failed"
            ):
                status = "stopped"
                reason = "user_requested"
                message = "Research Agent 已按用户请求中止。"

            result_status = (
                "STOPPED"
                if status == "stopped"
                else "FAILED"
                if status == "failed"
                or (run.failed_tasks and not run.completed_tasks)
                else "PARTIAL"
                if run.failed_tasks
                else "COMPLETE"
            )
            final = self._refresh_snapshot(run).model_copy(
                update={
                    "status": status,
                    "result_status": result_status,
                    "stop_reason": reason,
                    "current_research_task_id": "",
                    "progress_percent": (
                        run.progress_percent if status == "stopped" else 100
                    ),
                    "message": message,
                    "error": error,
                    "completed_at": utc_now(),
                }
            )
            self._save_run(final)
            self._append_event(
                final,
                event_type=status,
                message=message,
                data={
                    "stop_reason": reason,
                    "result_status": final.result_status,
                    "completed_tasks": final.completed_tasks,
                    "failed_tasks": final.failed_tasks,
                    "outcomes": final.outcomes,
                },
            )
            self._stop_requested.discard(run.task_id)
            return final

    def _runnable_research_task_ids(
        self,
        task_id: str,
    ) -> list[str]:
        # The planner still persists ``waiting_for_collector`` for legacy
        # readers. Product code treats it as "ready for Research Agent" and
        # does not expose Collector as a required user-facing stage.
        return [item.id for item in self._runnable_research_tasks(task_id)]

    def _all_research_tasks(self, task_id: str) -> list[ResearchTask]:
        return [
            ResearchTask(**item)
            for item in self.store.load_many(task_id, "research_tasks")
        ]

    @staticmethod
    def _is_bounded_supplement(item: ResearchTask) -> bool:
        return (
            item.metadata.get("source") == "r1_bounded_coverage_supplement"
            or bool(item.research_gap_id)
            or bool(item.parent_research_task_id)
        )

    def _deduplicated_research_tasks(
        self,
        task_id: str,
    ) -> list[ResearchTask]:
        """Defensively admit one executable supplement per round/scope."""
        representatives: dict[tuple[Any, ...], ResearchTask] = {}
        ordered_keys: list[tuple[Any, ...]] = []
        for item in self._all_research_tasks(task_id):
            if self._is_bounded_supplement(item):
                key: tuple[Any, ...] = (
                    "supplement",
                    item.task_id,
                    item.collection_round,
                    item.competitor,
                    normalize_dimension(item.dimension),
                )
            else:
                key = ("research_task", item.id)
            if key not in representatives:
                representatives[key] = item
                ordered_keys.append(key)
                continue
            existing = representatives[key]
            if (
                existing.status == "waiting_for_collector"
                and item.status != "waiting_for_collector"
            ):
                # A completed/failed duplicate proves this semantic supplement
                # was already attempted; do not schedule another waiting copy.
                representatives[key] = item
        return [representatives[key] for key in ordered_keys]

    def _runnable_research_tasks(self, task_id: str) -> list[ResearchTask]:
        finished_task_ids = {
            research_task_id
            for mission in self.store.load_many(task_id, "research_missions")
            if mission.get("status") == "finished"
            for research_task_id in mission.get("research_task_ids", [])
        }
        return [
            item
            for item in self._deduplicated_research_tasks(task_id)
            if item.status == "waiting_for_collector"
            and item.id not in finished_task_ids
        ]

    def _latest_plan(self, task_id: str) -> ResearchPlan | None:
        items = self.store.load_many(task_id, "research_plans")
        return ResearchPlan(**items[-1]) if items else None

    def _save_run(
        self,
        run: ResearchAgentCoordinatorRun,
    ) -> None:
        with self._lock:
            existing = [
                ResearchAgentCoordinatorRun(**item)
                for item in self.store.load_many(
                    run.task_id,
                    COORDINATOR_RUNS_ARTIFACT,
                )
            ]

            replaced = False
            updated: list[ResearchAgentCoordinatorRun] = []

            for item in existing:
                if item.id == run.id:
                    if (
                        item.status == "stopping"
                        and run.status in {"queued", "running"}
                    ):
                        updated.append(item)
                    else:
                        updated.append(run)
                    replaced = True
                else:
                    updated.append(item)

            if not replaced:
                updated.append(run)

            self.store.save_many(
                run.task_id,
                COORDINATOR_RUNS_ARTIFACT,
                updated,
            )

    def _append_event(
        self,
        run: ResearchAgentCoordinatorRun,
        *,
        event_type: str,
        message: str,
        research_task_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> ResearchAgentCoordinatorEvent:
        with self._lock:
            existing = self.store.load_many(
                run.task_id,
                COORDINATOR_EVENTS_ARTIFACT,
            )

            event = ResearchAgentCoordinatorEvent(
                task_id=run.task_id,
                coordinator_run_id=run.id,
                sequence=len(existing) + 1,
                event_type=event_type,
                research_task_id=research_task_id,
                message=message,
                data={
                    "active_collection_round": run.current_collection_round,
                    "max_collection_rounds": run.max_collection_rounds,
                    "current_round_completed": run.current_round_completed,
                    "current_round_total": run.current_round_total,
                    "cumulative_completed": run.completed_tasks,
                    "completed_tasks": run.completed_tasks,
                    "failed_tasks": run.failed_tasks,
                    "total_tasks": run.total_tasks,
                    "outcomes": run.outcomes,
                    "progress_percent": min(
                        max(run.progress_percent, 0),
                        100,
                    ),
                    **(data or {}),
                },
            )

            self.store.append_many(
                run.task_id,
                COORDINATOR_EVENTS_ARTIFACT,
                [event],
            )

            return event


_DEFAULT_COORDINATOR: ResearchAgentCoordinator | None = None
_DEFAULT_COORDINATOR_LOCK = threading.Lock()


def get_research_agent_coordinator() -> ResearchAgentCoordinator:
    global _DEFAULT_COORDINATOR

    with _DEFAULT_COORDINATOR_LOCK:
        if _DEFAULT_COORDINATOR is None:
            _DEFAULT_COORDINATOR = ResearchAgentCoordinator()

        return _DEFAULT_COORDINATOR
