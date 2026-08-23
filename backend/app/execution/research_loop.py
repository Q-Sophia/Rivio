from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from app.collection import CollectorQueueService
from app.extraction import ExtractorQueueService
from app.harness.artifacts import ArtifactStore
from app.intake import Step6E4QueueService
from app.schemas import (
    EvidenceCoverageStatus,
    ResearchLoopEvent,
    ResearchLoopRun,
    ResearchLoopRunStatus,
    ResearchLoopStopReason,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    TaskStatus,
    TaskType,
    utc_now,
)
from app.workflow.taskboard import TaskBoardStore, status_value


TERMINAL_RESEARCH_LOOP_STATUSES = {
    ResearchLoopRunStatus.COMPLETED.value,
    ResearchLoopRunStatus.REQUIRES_HUMAN.value,
    ResearchLoopRunStatus.FAILED.value,
}


ServiceFactory = Callable[[ArtifactStore], object]


class ResearchLoopRunner:
    """Run the bounded Collector -> Extractor -> Analyst research loop."""

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        max_workers: int = 1,
        collector_factory: ServiceFactory | None = None,
        extractor_factory: ServiceFactory | None = None,
        coverage_factory: ServiceFactory | None = None,
    ):
        self.store = store or ArtifactStore()
        self.board_store = TaskBoardStore(self.store)
        self._collector_factory = collector_factory or (
            lambda store: CollectorQueueService(store=store)
        )
        self._extractor_factory = extractor_factory or (
            lambda store: ExtractorQueueService(store=store)
        )
        self._coverage_factory = coverage_factory or (
            lambda store: Step6E4QueueService(store=store)
        )
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="step6e5-research-loop",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}

    def get_latest_run(self, task_id: str) -> ResearchLoopRun | None:
        items = self.store.load_many(task_id, "research_loop_runs")
        return ResearchLoopRun(**items[-1]) if items else None

    def get_events(self, task_id: str, *, after: int = 0) -> list[ResearchLoopEvent]:
        return [
            ResearchLoopEvent(**item)
            for item in self.store.load_many(task_id, "research_loop_events")
            if int(item.get("sequence", 0)) > after
        ]

    def reconcile_interrupted(self, task_id: str) -> ResearchLoopRun | None:
        """Persist an explicit failure when an active worker vanished after restart."""
        with self._lock:
            latest = self.get_latest_run(task_id)
            future = self._futures.get(task_id)
            if (
                latest
                and status_value(latest.status) not in TERMINAL_RESEARCH_LOOP_STATUSES
                and (future is None or future.done())
            ):
                failed = latest.model_copy(
                    update={
                        "status": ResearchLoopRunStatus.FAILED,
                        "stop_reason": ResearchLoopStopReason.FAILED,
                        "message": "后端进程曾中断，原研究循环线程已不存在。",
                        "error": (
                            "ResearchLoopInterrupted: 后端重启导致本次循环中断；"
                            "系统不会把不完整研究标记为成功。"
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
            return latest

    def submit(self, task_id: str) -> ResearchLoopRun:
        plan = self._latest_plan(task_id)
        if plan is None:
            raise LookupError("尚未生成 ResearchPlan（研究计划）。")
        board = self.board_store.load_board(task_id)
        if board is None:
            raise LookupError("尚未生成 TaskBoard（任务板）。")
        research_tasks = self.store.load_many(task_id, "research_tasks")
        if not research_tasks and plan.status != ResearchPlanStatus.READY_FOR_ANALYSIS:
            raise ValueError("研究计划没有可执行的 ResearchTask（研究任务）。")

        with self._lock:
            latest = self.get_latest_run(task_id)
            if latest and status_value(latest.status) not in TERMINAL_RESEARCH_LOOP_STATUSES:
                return latest
            if latest:
                raise ValueError("该研究任务已经有终态循环记录；为避免重复采集，不能再次启动。")

            max_actions = max(
                20,
                min(
                    1000,
                    plan.budget.max_total_sources * 3 + len(board.tasks) * 3,
                ),
            )
            run = ResearchLoopRun(
                task_id=task_id,
                research_plan_id=plan.id,
                status=ResearchLoopRunStatus.QUEUED,
                progress_percent=0,
                message="研究循环已进入后台队列。",
                max_actions=max_actions,
                source_count=len(self.store.load_many(task_id, "sources")),
                max_total_sources=plan.budget.max_total_sources,
                max_collection_rounds=plan.budget.max_collection_rounds,
            )
            self.store.save_many(task_id, "research_loop_runs", [run])
            self.store.save_many(task_id, "research_loop_events", [])
            self._append_event(
                run,
                event_type="queued",
                message="研究循环进入单工作线程队列。",
            )
            self._futures[task_id] = self._pool.submit(self._execute, run)
            return run

    def wait(self, task_id: str, timeout: float = 30.0) -> ResearchLoopRun:
        future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        latest = self.get_latest_run(task_id)
        if latest is None:
            raise LookupError(f"未找到 ResearchLoopRun（研究循环运行）: {task_id}")
        return latest

    def _execute(self, run: ResearchLoopRun) -> None:
        running = run.model_copy(
            update={
                "status": ResearchLoopRunStatus.RUNNING,
                "started_at": utc_now(),
                "progress_percent": 2,
                "message": "后台研究循环已经开始。",
            }
        )
        self._save_run(running)
        self._append_event(
            running,
            event_type="started",
            message="开始按 TaskBoard（任务板）调度采集、抽取与覆盖检查。",
        )

        try:
            while True:
                current = self._refresh_snapshot(self.get_latest_run(run.task_id) or running)
                decision = self._stop_decision(current)
                if decision is not None:
                    status, reason, message = decision
                    self._finish(current, status=status, reason=reason, message=message)
                    return

                stage = self._next_stage(run.task_id)
                if stage is None:
                    self._finish(
                        current,
                        status=ResearchLoopRunStatus.REQUIRES_HUMAN,
                        reason=ResearchLoopStopReason.NO_RUNNABLE_TASK,
                        message=(
                            "仍有未关闭的研究需求，但 TaskBoard（任务板）没有可执行任务，"
                            "需要人工检查失败任务、URL 或依赖关系。"
                        ),
                    )
                    return

                action_index = current.actions_completed + 1
                started = current.model_copy(
                    update={
                        "current_stage": stage,
                        "message": self._stage_message(stage, running=True),
                    }
                )
                self._save_run(started)
                self._append_event(
                    started,
                    event_type="action_started",
                    stage=stage,
                    message=started.message,
                    action_index=action_index,
                )

                result = self._run_action(started, stage)
                updates = {
                    "actions_completed": action_index,
                    "collector_runs": started.collector_runs + (stage == "collector"),
                    "extractor_runs": started.extractor_runs + (stage == "extractor"),
                    "coverage_runs": started.coverage_runs + (stage == "coverage"),
                    "message": self._stage_message(stage, running=False),
                }
                updated = started.model_copy(update=updates)
                updated = self._refresh_snapshot(updated)
                event_type = (
                    "action_completed"
                    if result.get("status") == "completed"
                    else "action_needs_attention"
                )
                self._append_event(
                    updated,
                    event_type=event_type,
                    stage=stage,
                    message=str(result.get("message") or updated.message),
                    action_index=action_index,
                    data={"result": result},
                )
        except Exception as exc:
            latest = self.get_latest_run(run.task_id) or running
            failed = self._refresh_snapshot(latest).model_copy(
                update={
                    "status": ResearchLoopRunStatus.FAILED,
                    "stop_reason": ResearchLoopStopReason.FAILED,
                    "message": "后台研究循环执行失败。",
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_at": utc_now(),
                }
            )
            self._save_run(failed)
            self._append_event(
                failed,
                event_type="failed",
                stage=failed.current_stage,
                message=failed.error,
                action_index=failed.actions_completed,
            )

    def _run_action(self, run: ResearchLoopRun, stage: str) -> dict:
        if stage == "collector":
            remaining = run.max_total_sources - len(
                self.store.load_many(run.task_id, "sources")
            )
            service = self._collector_factory(self.store)
            try:
                return service.run_once(run.task_id, max_new_sources=remaining)
            finally:
                close = getattr(service, "close", None)
                if callable(close):
                    close()
        if stage == "extractor":
            return self._extractor_factory(self.store).run_once(run.task_id)
        if stage == "coverage":
            return self._coverage_factory(self.store).run_once(run.task_id)
        raise ValueError(f"未知研究循环阶段：{stage}")

    def _next_stage(self, task_id: str) -> str | None:
        ready = self.board_store.ready_records(task_id)
        ordered_types = (
            (TaskType.EXTRACT_SOURCE_EVIDENCE.value, "extractor"),
            (TaskType.EVALUATE_EVIDENCE_COVERAGE.value, "coverage"),
        )
        for task_type, stage in ordered_types:
            if any(status_value(item.task_type) == task_type for item in ready):
                return stage
        research_task_ids = {
            item.id
            for item in (
                ResearchTask(**raw)
                for raw in self.store.load_many(task_id, "research_tasks")
            )
            if item.status == "waiting_for_collector"
        }
        if any(item.task_key in research_task_ids for item in ready):
            return "collector"
        return None

    def _stop_decision(
        self,
        run: ResearchLoopRun,
    ) -> tuple[ResearchLoopRunStatus, ResearchLoopStopReason, str] | None:
        plan = self._latest_plan(run.task_id)
        if plan is None:
            return (
                ResearchLoopRunStatus.FAILED,
                ResearchLoopStopReason.FAILED,
                "ResearchPlan（研究计划）在循环过程中丢失。",
            )
        if plan.status == ResearchPlanStatus.READY_FOR_ANALYSIS:
            return (
                ResearchLoopRunStatus.COMPLETED,
                ResearchLoopStopReason.COVERAGE_SUFFICIENT,
                "所有必需的竞品 × 维度均已达到证据覆盖要求，可以进入后续分析。",
            )
        if run.source_count >= run.max_total_sources:
            return (
                ResearchLoopRunStatus.REQUIRES_HUMAN,
                ResearchLoopStopReason.BUDGET_EXHAUSTED,
                "仍有研究缺口，但已达到最大来源数量，需要人工处理。",
            )
        exhausted_gap_ids = list(plan.metadata.get("step6e4_exhausted_gap_ids") or [])
        if plan.status == ResearchPlanStatus.BLOCKED and exhausted_gap_ids:
            return (
                ResearchLoopRunStatus.REQUIRES_HUMAN,
                ResearchLoopStopReason.BUDGET_EXHAUSTED,
                "仍有研究缺口，但相关维度已经达到最大采集轮次，需要人工处理。",
            )
        if run.actions_completed >= run.max_actions:
            return (
                ResearchLoopRunStatus.REQUIRES_HUMAN,
                ResearchLoopStopReason.BUDGET_EXHAUSTED,
                "研究循环达到动作安全预算，为防止异常扩张已停止并转人工处理。",
            )
        return None

    def _refresh_snapshot(self, run: ResearchLoopRun) -> ResearchLoopRun:
        plan = self._latest_plan(run.task_id)
        coverage = self.store.load_many(run.task_id, "evidence_coverage")
        coverage_counts: dict[str, int] = {}
        for item in coverage:
            key = str(item.get("status") or "unknown")
            coverage_counts[key] = coverage_counts.get(key, 0) + 1
        research_tasks = [
            ResearchTask(**item)
            for item in self.store.load_many(run.task_id, "research_tasks")
        ]
        covered = coverage_counts.get(EvidenceCoverageStatus.SUFFICIENT.value, 0)
        covered += coverage_counts.get(EvidenceCoverageStatus.NOT_APPLICABLE.value, 0)
        if coverage:
            progress = 5 + int(90 * covered / len(coverage))
        else:
            progress = 5 + int(85 * run.actions_completed / max(run.max_actions, 1))
        updated = run.model_copy(
            update={
                "progress_percent": min(max(progress, 2), 95),
                "source_count": len(self.store.load_many(run.task_id, "sources")),
                "max_total_sources": (
                    plan.budget.max_total_sources if plan else run.max_total_sources
                ),
                "current_collection_round": max(
                    [item.collection_round for item in research_tasks],
                    default=0,
                ),
                "max_collection_rounds": (
                    plan.budget.max_collection_rounds
                    if plan
                    else run.max_collection_rounds
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
        run: ResearchLoopRun,
        *,
        status: ResearchLoopRunStatus,
        reason: ResearchLoopStopReason,
        message: str,
    ) -> ResearchLoopRun:
        final = self._refresh_snapshot(run).model_copy(
            update={
                "status": status,
                "stop_reason": reason,
                "current_stage": "",
                "progress_percent": 100,
                "message": message,
                "completed_at": utc_now(),
            }
        )
        self._save_run(final)
        self._append_event(
            final,
            event_type=(
                "completed"
                if status == ResearchLoopRunStatus.COMPLETED
                else "requires_human"
            ),
            message=message,
            action_index=final.actions_completed,
            data={"stop_reason": status_value(reason)},
        )
        return final

    def _save_run(self, run: ResearchLoopRun) -> None:
        with self._lock:
            self.store.save_many(run.task_id, "research_loop_runs", [run])

    def _append_event(
        self,
        run: ResearchLoopRun,
        *,
        event_type: str,
        message: str,
        stage: str = "",
        action_index: int = 0,
        data: dict | None = None,
    ) -> ResearchLoopEvent:
        with self._lock:
            existing = self.store.load_many(run.task_id, "research_loop_events")
            event = ResearchLoopEvent(
                task_id=run.task_id,
                research_loop_run_id=run.id,
                sequence=len(existing) + 1,
                event_type=event_type,
                status=run.status,
                stage=stage,
                message=message,
                progress_percent=run.progress_percent,
                action_index=action_index,
                data=data or {},
            )
            self.store.append_many(run.task_id, "research_loop_events", [event])
            return event

    def _latest_plan(self, task_id: str) -> ResearchPlan | None:
        items = self.store.load_many(task_id, "research_plans")
        return ResearchPlan(**items[-1]) if items else None

    @staticmethod
    def _stage_message(stage: str, *, running: bool) -> str:
        labels = {
            "collector": "Collector（采集智能体）",
            "extractor": "Extractor（抽取智能体）",
            "coverage": "Analyst（证据覆盖分析智能体）",
        }
        action = "正在执行" if running else "已完成本次任务"
        return f"{labels.get(stage, stage)}{action}。"


_DEFAULT_RESEARCH_LOOP_RUNNER: ResearchLoopRunner | None = None
_DEFAULT_RESEARCH_LOOP_RUNNER_LOCK = threading.Lock()


def get_research_loop_runner() -> ResearchLoopRunner:
    global _DEFAULT_RESEARCH_LOOP_RUNNER
    with _DEFAULT_RESEARCH_LOOP_RUNNER_LOCK:
        if _DEFAULT_RESEARCH_LOOP_RUNNER is None:
            _DEFAULT_RESEARCH_LOOP_RUNNER = ResearchLoopRunner()
        return _DEFAULT_RESEARCH_LOOP_RUNNER
