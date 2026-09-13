from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from app.eval.cases import EvalCase
from app.frameworks import (
    DEFAULT_FRAMEWORK_ID,
    DEFAULT_FRAMEWORK_VERSION,
    get_framework_registry,
)
from app.harness.artifacts import ArtifactStore
from app.intake import IntentDraftService, ResearchPlanningService
from app.schemas import (
    AnalysisTask,
    ConfirmAnalysisTaskRequest,
    FrameworkDefinition,
    InformationNeed,
    KeyIntelligenceQuestion,
    ResearchPlan,
    ResearchTask,
    TaskBoard,
    utc_now,
)
from app.workflow.taskboard import TaskBoardStore


FROZEN_FILES = (
    "request.json",
    "intent_draft.json",
    "analysis_task.json",
    "research_brief.json",
    "research_plan.json",
    "kiqs.json",
    "information_needs.json",
    "research_tasks.json",
    "framework_definition.json",
    "task_board.json",
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PrepareAdapter(Protocol):
    def prepare(self, case: EvalCase, store: ArtifactStore) -> dict[str, Any]: ...


class ProductionPrepareAdapter:
    """Call the real Intent and deterministic Research Planner exactly once."""

    def prepare(self, case: EvalCase, store: ArtifactStore) -> dict[str, Any]:
        intent = IntentDraftService(store=store)
        draft, intent_call = intent.parse_request(case.request)
        if not draft.ready_for_confirmation:
            raise ValueError(
                f"{case.id} Intent 仍需澄清: {draft.clarification_questions}"
            )
        confirmation = ConfirmAnalysisTaskRequest(
            decision_question=draft.decision_question,
            industry=draft.industry,
            competitors=draft.competitors,
            target_customers=draft.target_customers,
            core_scenarios=draft.core_scenarios,
            focus_areas=draft.focus_areas,
            constraints=draft.constraints,
            report_subject=draft.report_subject,
            preferred_title=draft.preferred_title,
            workspace_origin="rivio_eval_r1_prepare",
        )
        confirmed_draft, task = intent.confirm_draft(draft.id, confirmation)
        planning = ResearchPlanningService(store=store)
        planning_payload = planning.build(task.id)
        research_tasks = [
            ResearchTask(**item) for item in planning_payload["research_tasks"]
        ]
        framework_id = (
            research_tasks[0].framework_id
            if research_tasks
            else str(task.metadata.get("framework_id") or DEFAULT_FRAMEWORK_ID)
        )
        framework_version = (
            research_tasks[0].framework_version
            if research_tasks
            else str(
                task.metadata.get("framework_version")
                or DEFAULT_FRAMEWORK_VERSION
            )
        )
        framework = get_framework_registry().load_framework(
            framework_id,
            framework_version,
        )
        return {
            "request": {"case": case.id, "request": case.request},
            "intent_draft": confirmed_draft.model_dump(mode="json"),
            "analysis_task": planning_payload["analysis_task"],
            "research_brief": dict(
                planning_payload["analysis_task"].get("metadata", {}).get(
                    "research_brief", {}
                )
            ),
            "research_plan": planning_payload["research_plan"],
            "kiqs": planning_payload["kiqs"],
            "information_needs": planning_payload["information_needs"],
            "research_tasks": planning_payload["research_tasks"],
            "framework_definition": framework.model_dump(mode="json"),
            "task_board": planning_payload["task_board"],
            "prepare_trace": {
                "intent_runs": 1,
                "planner_runs": 1,
                "intent_call": intent_call,
                "source_task_id": task.id,
            },
        }


class FrozenInputManager:
    def __init__(
        self,
        *,
        workspace_root: Path | str,
        prepare_adapter: PrepareAdapter | None = None,
    ):
        self.workspace_root = Path(workspace_root)
        self.input_root = self.workspace_root / "eval_inputs"
        self.prepare_adapter = prepare_adapter or ProductionPrepareAdapter()

    def case_dir(self, case_id: str) -> Path:
        return self.input_root / case_id

    def prepare_case(self, case: EvalCase, *, force: bool = False) -> dict[str, Any]:
        case_dir = self.case_dir(case.id)
        manifest_path = case_dir / "manifest.json"
        if manifest_path.is_file() and not force:
            manifest = read_json(manifest_path)
            self.validate_case(case.id, manifest=manifest)
            return {**manifest, "reused": True}

        prepare_store = ArtifactStore(case_dir / "_prepare_artifacts")
        payload = self.prepare_adapter.prepare(case, prepare_store)
        mapping = {
            "request.json": payload["request"],
            "intent_draft.json": payload["intent_draft"],
            "analysis_task.json": payload["analysis_task"],
            "research_brief.json": payload["research_brief"],
            "research_plan.json": payload["research_plan"],
            "kiqs.json": payload["kiqs"],
            "information_needs.json": payload["information_needs"],
            "research_tasks.json": payload["research_tasks"],
            "framework_definition.json": payload["framework_definition"],
            "task_board.json": payload["task_board"],
        }
        for file_name, value in mapping.items():
            write_json(case_dir / file_name, value)
        file_hashes = {
            file_name: canonical_hash(value)
            for file_name, value in mapping.items()
        }
        manifest = {
            "schema_version": "rivio_eval_r1_frozen_v1",
            "case": case.id,
            "request": case.request,
            "prepared_at": utc_now().isoformat(),
            "files": file_hashes,
            "frozen_input_hash": canonical_hash(file_hashes),
            "prepare_trace": payload.get("prepare_trace", {}),
            "live_web_nondeterminism": True,
        }
        write_json(manifest_path, manifest)
        return {**manifest, "reused": False}

    def validate_case(
        self,
        case_id: str,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        case_dir = self.case_dir(case_id)
        manifest = manifest or read_json(case_dir / "manifest.json")
        for file_name in FROZEN_FILES:
            path = case_dir / file_name
            if not path.is_file():
                raise FileNotFoundError(f"冻结输入缺少文件: {path}")
            expected = str(manifest.get("files", {}).get(file_name) or "")
            actual = canonical_hash(read_json(path))
            if not expected or actual != expected:
                raise ValueError(f"冻结输入 hash 不匹配: {path}")
        return manifest

    def load_case(self, case_id: str) -> dict[str, Any]:
        manifest = self.validate_case(case_id)
        case_dir = self.case_dir(case_id)
        return {
            "manifest": manifest,
            **{
                file_name.removesuffix(".json"): read_json(case_dir / file_name)
                for file_name in FROZEN_FILES
            },
        }

    def materialize(
        self,
        case_id: str,
        *,
        store: ArtifactStore,
        task_id: str,
        variant: str,
    ) -> dict[str, Any]:
        frozen = self.load_case(case_id)
        source_task = AnalysisTask(**frozen["analysis_task"])
        task = source_task.model_copy(
            update={
                "id": task_id,
                "task_id": task_id,
                "metadata": {
                    **source_task.metadata,
                    "eval_case": case_id,
                    "eval_variant": variant,
                    "frozen_input_hash": frozen["manifest"][
                        "frozen_input_hash"
                    ],
                    "source_frozen_task_id": source_task.id,
                },
            }
        )
        plan = ResearchPlan(**frozen["research_plan"]).model_copy(
            update={"task_id": task_id}
        )
        kiqs = [
            KeyIntelligenceQuestion(**item).model_copy(
                update={"task_id": task_id}
            )
            for item in frozen["kiqs"]
        ]
        needs = [
            InformationNeed(**item).model_copy(update={"task_id": task_id})
            for item in frozen["information_needs"]
        ]
        research_tasks = [
            ResearchTask(**item).model_copy(update={"task_id": task_id})
            for item in frozen["research_tasks"]
        ]
        framework = FrameworkDefinition(**frozen["framework_definition"])
        source_board = TaskBoard(**frozen["task_board"])
        board = source_board.model_copy(
            update={
                "task_id": task_id,
                "tasks": [
                    item.model_copy(update={"task_id": task_id})
                    for item in source_board.tasks
                ],
                "metadata": {
                    **source_board.metadata,
                    "eval_case": case_id,
                    "eval_variant": variant,
                    "frozen_input_hash": frozen["manifest"][
                        "frozen_input_hash"
                    ],
                },
            }
        )
        store.save_many(task_id, "analysis_tasks", [task])
        store.save_many(task_id, "research_plans", [plan])
        store.save_many(task_id, "research_kiqs", kiqs)
        store.save_many(task_id, "research_information_needs", needs)
        store.save_many(task_id, "research_tasks", research_tasks)
        store.save_many(task_id, "eval_framework_definitions", [framework])
        TaskBoardStore(store).save_board(board)
        return {
            "task_id": task_id,
            "frozen_input_hash": frozen["manifest"]["frozen_input_hash"],
            "research_task_count": len(research_tasks),
            "information_need_count": len(needs),
        }
