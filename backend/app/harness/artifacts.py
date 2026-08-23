from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Iterable

from app.schemas import SchemaModel


DEFAULT_ARTIFACT_TYPES = {
    "sources",
    "evidence",
    "product_cards",
    "claims",
    "reports",
    "agent_runs",
    "tool_calls",
    "citation_checks",
    "review_feedback",
    "feature_comparisons",
    "swot_analysis",
    "market_positions",
    "scorecards",
    "task_board",
    "task_records",
    "quality_gates",
    "feedback_tasks",
    "context_bundles",
    "working_memory",
    "memory_items",
    "guardrail_checks",
    "llm_calls",
    "llm_outputs",
    "analysis_portfolios",
    "brief_assessments",
    "competitor_profiles",
    "intelligence_questions",
    "information_needs",
    "evidence_coverage",
    "analysis_evidence_coverage",
    "comparability_notes",
    "claims_v2",
    "research_gaps",
    "analysis_research_gaps",
    "report_statements",
    "task_drafts",
    "analysis_tasks",
    "dataset_compatibility_assessments",
    "execution_plans",
    "execution_authorizations",
    "execution_runs",
    "execution_events",
    "research_loop_runs",
    "research_loop_events",
    "research_plans",
    "research_kiqs",
    "research_information_needs",
    "research_tasks",
    "web_pages",
    "collection_attempts",
    "search_attempts",
    "web_search_results",
    "evidence_extraction_attempts",
}


_ARTIFACT_IO_LOCK = threading.RLock()


class ArtifactStore:
    """JSON artifact store with stable task/type boundaries."""

    def __init__(self, root_dir: Path | str | None = None):
        if root_dir is None:
            root_dir = Path(__file__).resolve().parents[1] / "data" / "runs"
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_many(
        self,
        task_id: str,
        artifact_type: str,
        items: list[SchemaModel],
    ) -> None:
        task_dir = self._task_dir(task_id)
        payload = [item.model_dump(mode="json") for item in items]
        with _ARTIFACT_IO_LOCK:
            self._write_json_atomic(
                self._artifact_path(task_dir, artifact_type),
                payload,
            )

    def append_many(
        self,
        task_id: str,
        artifact_type: str,
        items: Iterable[SchemaModel],
    ) -> None:
        with _ARTIFACT_IO_LOCK:
            existing = self.load_many(task_id, artifact_type)
            existing.extend(item.model_dump(mode="json") for item in items)
            task_dir = self._task_dir(task_id)
            self._write_json_atomic(
                self._artifact_path(task_dir, artifact_type),
                existing,
            )

    def load_many(self, task_id: str, artifact_type: str) -> list[dict]:
        path = self._artifact_path(self._task_dir(task_id), artifact_type)
        if not path.exists():
            return []
        with _ARTIFACT_IO_LOCK:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"Artifact {artifact_type} for {task_id} must be a list")
        return data

    def _task_dir(self, task_id: str) -> Path:
        task_dir = self.root_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    @staticmethod
    def _artifact_path(task_dir: Path, artifact_type: str) -> Path:
        if not artifact_type or any(ch in artifact_type for ch in "\\/:*?\"<>|"):
            raise ValueError(f"Invalid artifact type: {artifact_type!r}")
        return task_dir / f"{artifact_type}.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: list[dict]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
