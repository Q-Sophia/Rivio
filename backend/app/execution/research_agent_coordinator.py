from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents.web_evidence import normalize_dimension
from app.execution.research_agent import get_research_evidence_agent_service
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
    MissionSupervisorAction,
    ResearchAgentBudget,
    ResearchMissionBudgetState,
    ResearchMissionState,
    ResearchPlan,
    ResearchTask,
    new_id,
    utc_now,
)


COORDINATOR_RUNS_ARTIFACT = "research_agent_coordinator_runs"
COORDINATOR_EVENTS_ARTIFACT = "research_agent_coordinator_events"

ACTIVE_STATUSES = {"queued", "running"}
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
        errors: list[str] = []
        try:
            while True:
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

                for research_task in round_tasks:
                    current = self._refresh_snapshot(current)
                    if self._budget_stop(current) is not None:
                        break
                    current = self._run_research_task(
                        current,
                        research_task=research_task,
                        mode=mode,
                        acknowledge_real_llm_call=acknowledge_real_llm_call,
                        errors=errors,
                    )

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

                if errors:
                    self._finish(
                        current,
                        status="failed",
                        reason="research_task_failed",
                        message="Research Agent R1 有任务执行失败，bounded loop 已停止。",
                        error="\n".join(errors),
                    )
                    return

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
        errors: list[str],
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
            error = f"{research_task_id}: {type(exc).__name__}: {exc}"
            errors.append(error)
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
            if mission.information_need_ids and all(
                state.coverage_status_by_need.get(need_id)
                in SUFFICIENT_COVERAGE_STATUSES
                for need_id in mission.information_need_ids
            ):
                bounded_reason = "Mission Coverage 已充分。"
            elif completed_round >= run.max_collection_rounds:
                bounded_reason = "Mission 已达到最大采集轮数。"
            elif budget_state.completed_units >= budget_state.max_units:
                bounded_reason = "Mission 已达到 Research Unit 上限。"
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
        final = self._refresh_snapshot(run).model_copy(
            update={
                "status": status,
                "stop_reason": reason,
                "current_research_task_id": "",
                "progress_percent": 100,
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
                "completed_tasks": final.completed_tasks,
                "failed_tasks": final.failed_tasks,
                "outcomes": final.outcomes,
            },
        )
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
