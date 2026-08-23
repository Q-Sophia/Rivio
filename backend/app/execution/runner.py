from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.intake import ExecutionPlanningService
from app.llm import LLMConfig
from app.schemas import (
    AnalysisTask,
    ExecutionEvent,
    ExecutionMode,
    ExecutionRun,
    ExecutionRunStatus,
    LLMMode,
    LLMProvider,
    RunStatus,
    utc_now,
)
from app.workflow.snapshot_pipeline import run_snapshot_llm_agent_workflow


TERMINAL_EXECUTION_STATUSES = {
    ExecutionRunStatus.COMPLETED.value,
    ExecutionRunStatus.FAILED.value,
}


class ExecutionRunner:
    """Single-worker Step6D.4 queue that executes an authorized user task."""

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        max_workers: int = 1,
    ):
        self.store = store or ArtifactStore()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="step6d4-execution",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}

    def get_latest_run(self, task_id: str) -> ExecutionRun | None:
        items = self.store.load_many(task_id, "execution_runs")
        return ExecutionRun(**items[-1]) if items else None

    def get_events(self, task_id: str, *, after: int = 0) -> list[ExecutionEvent]:
        return [
            ExecutionEvent(**item)
            for item in self.store.load_many(task_id, "execution_events")
            if int(item.get("sequence", 0)) > after
        ]

    def reconcile_interrupted(self, task_id: str) -> ExecutionRun | None:
        """Turn an orphaned active run into an explicit failure after process restart."""
        with self._lock:
            latest = self.get_latest_run(task_id)
            future = self._futures.get(task_id)
            if (
                latest
                and latest.status not in TERMINAL_EXECUTION_STATUSES
                and (future is None or future.done())
            ):
                failed = latest.model_copy(
                    update={
                        "status": ExecutionRunStatus.FAILED,
                        "message": "后端进程曾中断，原后台线程已不存在。",
                        "error": (
                            "ExecutionInterrupted: 后端重启导致本次执行中断；"
                            "系统不会把不完整结果标记为成功。"
                        ),
                        "completed_at": utc_now(),
                    }
                )
                self._save_run(failed)
                self._append_event(
                    failed,
                    event_type="failed",
                    step_key=failed.current_step,
                    message=failed.error,
                    progress_percent=failed.progress_percent,
                )
                return failed
            return latest

    def submit(self, task_id: str, *, mode: ExecutionMode | str) -> ExecutionRun:
        selected_mode = ExecutionMode(mode)
        planning = ExecutionPlanningService(store=self.store)
        task = planning.get_task(task_id)
        plan = planning.get_latest_plan(task_id)
        authorization = planning.get_latest_authorization(task_id)
        if task is None or plan is None or authorization is None:
            raise LookupError("任务尚未完成资料检查与执行授权。")
        if not authorization.authorized or plan.status != "authorized":
            raise ValueError("执行计划尚未获得用户授权。")
        if selected_mode == ExecutionMode.DEEPSEEK:
            self._validate_deepseek_readiness()

        with self._lock:
            latest = self.get_latest_run(task_id)
            if latest and latest.status not in TERMINAL_EXECUTION_STATUSES:
                return latest
            if latest and latest.status in TERMINAL_EXECUTION_STATUSES:
                raise ValueError("该任务已经有终态执行记录，不能重复启动；请新建任务。")

            run = ExecutionRun(
                task_id=task_id,
                plan_id=plan.id,
                authorization_id=authorization.id,
                mode=selected_mode,
                status=ExecutionRunStatus.QUEUED,
                progress_percent=0,
                message="任务已交给后台 Execution Runner（执行器）。",
            )
            self.store.save_many(task_id, "execution_runs", [run])
            self.store.save_many(task_id, "execution_events", [])
            self._append_event(
                run,
                event_type="queued",
                message="任务进入后台执行队列。",
                progress_percent=0,
            )
            self._futures[task_id] = self._pool.submit(
                self._execute,
                run,
                task,
            )
            return run

    def wait(self, task_id: str, timeout: float = 30.0) -> ExecutionRun:
        future = self._futures.get(task_id)
        if future is not None:
            future.result(timeout=timeout)
        latest = self.get_latest_run(task_id)
        if latest is None:
            raise LookupError(f"未找到 execution run: {task_id}")
        return latest

    def _execute(self, run: ExecutionRun, task: AnalysisTask) -> None:
        running = run.model_copy(
            update={
                "status": ExecutionRunStatus.RUNNING,
                "started_at": utc_now(),
                "progress_percent": 2,
                "message": "后台工作流已经开始。",
            }
        )
        self._save_run(running)
        self._save_task_state(task, status=RunStatus.RUNNING, run=running)
        self._append_event(
            running,
            event_type="started",
            message="开始执行用户确认的 AnalysisTask（分析任务）。",
            progress_percent=2,
        )

        def on_progress(payload: dict) -> None:
            latest = self.get_latest_run(task.id) or running
            status = str(payload.get("status") or "running")
            progress = int(payload.get("progress_percent") or latest.progress_percent)
            message = str(payload.get("message") or "步骤状态已更新。")
            step_key = str(payload.get("step_key") or "")
            updated = latest.model_copy(
                update={
                    "status": (
                        ExecutionRunStatus.FAILED
                        if status == "failed"
                        else ExecutionRunStatus.RUNNING
                    ),
                    "current_step": step_key,
                    "progress_percent": progress,
                    "message": message,
                }
            )
            self._save_run(updated)
            self._append_event(
                updated,
                event_type=f"step_{status}",
                step_key=step_key,
                message=message,
                progress_percent=progress,
                data={"step_status": status},
            )

        try:
            extractor, analyst, writer = self._configs(ExecutionMode(run.mode))
            summary, _recorder = run_snapshot_llm_agent_workflow(
                task_id=task.id,
                artifact_root=self.store.root_dir,
                professional_analysis=True,
                llm_config_override=extractor,
                analyst_llm_config=analyst,
                writer_llm_config=writer,
                runtime_name="taskboard_driven_step6d4_user_execution_v1",
                analysis_task=task,
                reuse_task_board=True,
                progress_callback=on_progress,
            )
            if summary.pipeline_status != "completed":
                raise RuntimeError("工作流未完成，请查看失败步骤与 trace（追踪记录）。")
            completed = (self.get_latest_run(task.id) or running).model_copy(
                update={
                    "status": ExecutionRunStatus.COMPLETED,
                    "current_step": "review_report",
                    "progress_percent": 100,
                    "message": "竞品分析工作流执行完成。",
                    "result_task_id": task.id,
                    "completed_at": utc_now(),
                    "error": "",
                    "metadata": {
                        **run.metadata,
                        "pipeline_status": summary.pipeline_status,
                        "approved": summary.approved,
                        "report_title": summary.metadata.get("report_title", ""),
                    },
                }
            )
            self._save_run(completed)
            self._save_task_state(task, status=RunStatus.COMPLETED, run=completed)
            self._append_event(
                completed,
                event_type="completed",
                message="报告、证据追溯与质量检查均已生成。",
                progress_percent=100,
                data={"approved": summary.approved},
            )
        except Exception as exc:
            failed = (self.get_latest_run(task.id) or running).model_copy(
                update={
                    "status": ExecutionRunStatus.FAILED,
                    "message": "后台工作流执行失败。",
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_at": utc_now(),
                }
            )
            self._save_run(failed)
            self._save_task_state(task, status=RunStatus.FAILED, run=failed)
            self._append_event(
                failed,
                event_type="failed",
                step_key=failed.current_step,
                message=failed.error,
                progress_percent=failed.progress_percent,
            )

    def _save_run(self, run: ExecutionRun) -> None:
        with self._lock:
            self.store.save_many(run.task_id, "execution_runs", [run])

    def _append_event(
        self,
        run: ExecutionRun,
        *,
        event_type: str,
        message: str,
        progress_percent: int,
        step_key: str = "",
        data: dict | None = None,
    ) -> ExecutionEvent:
        with self._lock:
            existing = self.store.load_many(run.task_id, "execution_events")
            event = ExecutionEvent(
                task_id=run.task_id,
                execution_run_id=run.id,
                sequence=len(existing) + 1,
                event_type=event_type,
                status=run.status,
                step_key=step_key,
                message=message,
                progress_percent=progress_percent,
                data=data or {},
            )
            self.store.append_many(run.task_id, "execution_events", [event])
            return event

    def _save_task_state(
        self,
        task: AnalysisTask,
        *,
        status: RunStatus,
        run: ExecutionRun,
    ) -> None:
        updated = task.model_copy(
            update={
                "status": status,
                "updated_at": utc_now(),
                "metadata": {
                    **task.metadata,
                    "execution_started": True,
                    "execution_status": str(run.status),
                    "execution_run_id": run.id,
                    "execution_mode": str(run.mode),
                    "execution_error": run.error,
                },
            }
        )
        self.store.save_many(task.id, "analysis_tasks", [updated])

    @staticmethod
    def _mock_config() -> LLMConfig:
        return LLMConfig(
            provider=LLMProvider.MOCK,
            model="mock-structured-v1",
            mode=LLMMode.LLM_WITH_FALLBACK,
            output_language="zh-CN",
            api_style="mock",
            structured_output_mode="json_schema",
        )

    @staticmethod
    def _deepseek_config(*, max_tokens: int) -> LLMConfig:
        return LLMConfig(
            provider=LLMProvider.COMPATIBLE,
            model=os.environ.get("STEP6D_LLM_MODEL", "deepseek-v4-flash"),
            mode=LLMMode.LLM,
            base_url=os.environ.get("STEP6D_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            api_key_env=os.environ.get("STEP6D_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"),
            timeout_seconds=int(os.environ.get("STEP6D_LLM_TIMEOUT_SECONDS", "120")),
            max_tokens=max_tokens,
            temperature=0.2,
            output_language="zh-CN",
            max_retries=2,
            retry_base_seconds=1.0,
            enable_real_calls=True,
            api_style="chat_completions",
            structured_output_mode="json_object",
            thinking_mode="disabled",
        )

    def _configs(self, mode: ExecutionMode) -> tuple[LLMConfig, LLMConfig, LLMConfig]:
        mock = self._mock_config()
        if mode == ExecutionMode.MOCK:
            return mock, mock, mock
        return mock, self._deepseek_config(max_tokens=16000), self._deepseek_config(max_tokens=12000)

    def _validate_deepseek_readiness(self) -> None:
        configs = [
            self._deepseek_config(max_tokens=16000),
            self._deepseek_config(max_tokens=12000),
        ]
        errors: list[str] = []
        for config in configs:
            config.validate()
            errors.extend(config.real_call_readiness_errors())
        if errors:
            raise ValueError("；".join(sorted(set(errors))))


_DEFAULT_RUNNER: ExecutionRunner | None = None
_DEFAULT_RUNNER_LOCK = threading.Lock()


def get_execution_runner() -> ExecutionRunner:
    global _DEFAULT_RUNNER
    with _DEFAULT_RUNNER_LOCK:
        if _DEFAULT_RUNNER is None:
            _DEFAULT_RUNNER = ExecutionRunner()
        return _DEFAULT_RUNNER
