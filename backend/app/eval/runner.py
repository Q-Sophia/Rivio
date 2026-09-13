from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.eval.adapters import CurrentResearchAgentAdapter, LegacyResearchAdapter
from app.eval.cases import EvalCase, load_eval_cases, select_eval_cases
from app.eval.frozen import FrozenInputManager, read_json, write_json
from app.eval.metrics import build_search_audit, collect_e2_metrics, collect_e3_metrics
from app.eval.reporting import generate_outputs
from app.harness.artifacts import ArtifactStore
from app.schemas import utc_now


DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2] / "eval_workspace"


class EvaluationRunner:
    def __init__(
        self,
        *,
        workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
        frozen_manager: FrozenInputManager | None = None,
        legacy_adapter: LegacyResearchAdapter | None = None,
        agent_adapter: CurrentResearchAgentAdapter | None = None,
    ):
        self.workspace_root = Path(workspace_root)
        self.frozen = frozen_manager or FrozenInputManager(
            workspace_root=self.workspace_root
        )
        self.legacy_adapter = legacy_adapter or LegacyResearchAdapter()
        self.agent_adapter = agent_adapter or CurrentResearchAgentAdapter()

    @property
    def output_root(self) -> Path:
        return self.workspace_root / "eval_outputs"

    def prepare(
        self,
        *,
        case_id: str = "",
        limit: int | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        cases = select_eval_cases(
            load_eval_cases(),
            case_id=case_id,
            limit=limit,
        )
        return [self.frozen.prepare_case(item, force=force) for item in cases]

    def create_run(self, *, command: str, cases: list[EvalCase]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.output_root / f"run_{stamp}_{uuid.uuid4().hex[:6]}"
        run_dir.mkdir(parents=True, exist_ok=False)
        write_json(
            run_dir / "config.json",
            {
                "schema_version": "rivio_eval_r1_run_v1",
                "command": command,
                "created_at": utc_now().isoformat(),
                "cases": [item.id for item in cases],
                "LIVE_WEB_NONDETERMINISM": True,
                "frozen_planning_reused": True,
            },
        )
        write_json(
            self.output_root / "latest_run.json",
            {"run_dir": str(run_dir.resolve()), "updated_at": utc_now().isoformat()},
        )
        return run_dir

    def _selected_prepared_cases(
        self,
        *,
        case_id: str,
        limit: int | None,
    ) -> list[EvalCase]:
        cases = select_eval_cases(
            load_eval_cases(),
            case_id=case_id,
            limit=limit,
        )
        for item in cases:
            self.frozen.validate_case(item.id)
        return cases

    def run_e2(
        self,
        *,
        case_id: str = "",
        limit: int | None = None,
        force: bool = False,
        run_dir: Path | None = None,
    ) -> Path:
        cases = self._selected_prepared_cases(case_id=case_id, limit=limit)
        run_dir = run_dir or self.create_run(command="e2", cases=cases)
        for case in cases:
            for variant in ("legacy", "agent"):
                variant_dir = run_dir / "e2" / case.id / variant
                metrics_path = variant_dir / "metrics.json"
                if metrics_path.is_file() and not force:
                    continue
                self._run_e2_variant(
                    case=case,
                    variant=variant,
                    variant_dir=variant_dir,
                )
        generate_outputs(run_dir=run_dir, cases=load_eval_cases())
        return run_dir

    def _run_e2_variant(
        self,
        *,
        case: EvalCase,
        variant: str,
        variant_dir: Path,
    ) -> None:
        task_id = self._task_id(case.id, f"e2_{variant}")
        store = ArtifactStore(variant_dir / "artifacts")
        materialized = self.frozen.materialize(
            case.id,
            store=store,
            task_id=task_id,
            variant=f"e2_{variant}",
        )
        write_json(
            variant_dir / "config.json",
            {
                **materialized,
                "case": case.id,
                "variant": variant,
                "LIVE_WEB_NONDETERMINISM": True,
            },
        )
        started_at = utc_now()
        started_perf = time.perf_counter()
        execution_error = ""
        payload: dict[str, Any] = {}
        try:
            payload = (
                self.legacy_adapter.run(store=store, task_id=task_id)
                if variant == "legacy"
                else self.agent_adapter.run(
                    store=store,
                    task_id=task_id,
                    supplement_enabled=True,
                )
            )
        except Exception as exc:
            execution_error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
        execution = {
            "case": case.id,
            "variant": variant,
            "task_id": task_id,
            "started_at": started_at.isoformat(),
            "completed_at": utc_now().isoformat(),
            "elapsed_ms": elapsed_ms,
            "status": "failed" if execution_error else "completed",
            "error": execution_error,
            "payload": payload,
        }
        write_json(variant_dir / "execution.json", execution)
        metrics = collect_e2_metrics(
            store=store,
            task_id=task_id,
            case_id=case.id,
            variant=variant,
            elapsed_ms=elapsed_ms,
            execution_error=execution_error,
        )
        write_json(variant_dir / "metrics.json", metrics)
        write_json(
            variant_dir / "search_audit.json",
            build_search_audit(store, task_id),
        )

    def run_e3(
        self,
        *,
        case_id: str = "",
        limit: int | None = None,
        force: bool = False,
        run_dir: Path | None = None,
    ) -> Path:
        cases = self._selected_prepared_cases(case_id=case_id, limit=limit)
        run_dir = run_dir or self.create_run(command="e3", cases=cases)
        for case in cases:
            for variant, enabled in (
                ("supplement_off", False),
                ("supplement_on", True),
            ):
                variant_dir = run_dir / "e3" / case.id / variant
                metrics_path = variant_dir / "metrics.json"
                if metrics_path.is_file() and not force:
                    continue
                self._run_e3_variant(
                    case=case,
                    variant=variant,
                    supplement_enabled=enabled,
                    variant_dir=variant_dir,
                )
        generate_outputs(run_dir=run_dir, cases=load_eval_cases())
        return run_dir

    def _run_e3_variant(
        self,
        *,
        case: EvalCase,
        variant: str,
        supplement_enabled: bool,
        variant_dir: Path,
    ) -> None:
        task_id = self._task_id(case.id, f"e3_{variant}")
        store = ArtifactStore(variant_dir / "artifacts")
        materialized = self.frozen.materialize(
            case.id,
            store=store,
            task_id=task_id,
            variant=f"e3_{variant}",
        )
        write_json(
            variant_dir / "config.json",
            {
                **materialized,
                "case": case.id,
                "variant": variant,
                "supplement_enabled": supplement_enabled,
                "LIVE_WEB_NONDETERMINISM": True,
            },
        )
        started_at = utc_now()
        started_perf = time.perf_counter()
        execution_error = ""
        payload: dict[str, Any] = {}
        try:
            payload = self.agent_adapter.run(
                store=store,
                task_id=task_id,
                supplement_enabled=supplement_enabled,
            )
        except Exception as exc:
            execution_error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
        write_json(
            variant_dir / "execution.json",
            {
                "case": case.id,
                "variant": variant,
                "task_id": task_id,
                "supplement_enabled": supplement_enabled,
                "started_at": started_at.isoformat(),
                "completed_at": utc_now().isoformat(),
                "elapsed_ms": elapsed_ms,
                "status": "failed" if execution_error else "completed",
                "error": execution_error,
                "payload": payload,
            },
        )
        metrics = collect_e3_metrics(
            store=store,
            task_id=task_id,
            case_id=case.id,
            variant=variant,
            elapsed_ms=elapsed_ms,
            execution_error=execution_error,
        )
        validation_errors = list(metrics["supplement_provenance_errors"])
        if not supplement_enabled and metrics["supplement_task_count"] != 0:
            validation_errors.append("supplement_off_created_research_tasks")
        if validation_errors:
            metrics["research_success"] = False
            metrics["evaluation_validation_errors"] = validation_errors
        else:
            metrics["evaluation_validation_errors"] = []
        write_json(variant_dir / "metrics.json", metrics)
        write_json(
            variant_dir / "search_audit.json",
            build_search_audit(store, task_id),
        )

    def run_all(
        self,
        *,
        case_id: str = "",
        limit: int | None = None,
        force: bool = False,
    ) -> Path:
        cases = select_eval_cases(
            load_eval_cases(),
            case_id=case_id,
            limit=limit,
        )
        self.prepare(case_id=case_id, limit=limit, force=force)
        run_dir = self.create_run(command="all", cases=cases)
        self.run_e2(
            case_id=case_id,
            limit=limit,
            force=force,
            run_dir=run_dir,
        )
        self.run_e3(
            case_id=case_id,
            limit=limit,
            force=force,
            run_dir=run_dir,
        )
        return run_dir

    def latest_run_dir(self) -> Path:
        pointer = self.output_root / "latest_run.json"
        if pointer.is_file():
            run_dir = Path(read_json(pointer)["run_dir"])
            if run_dir.is_dir():
                return run_dir
        candidates = sorted(self.output_root.glob("run_*"))
        if not candidates:
            raise FileNotFoundError("尚无 Evaluation run")
        return candidates[-1]

    def summary(self) -> dict[str, Any]:
        run_dir = self.latest_run_dir()
        return generate_outputs(run_dir=run_dir, cases=load_eval_cases())

    @staticmethod
    def _task_id(case_id: str, variant: str) -> str:
        return f"eval_{case_id}_{variant}_{uuid.uuid4().hex[:12]}"
