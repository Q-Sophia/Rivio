from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.schemas import AnalysisTask, SourceDocument, SourceEvidence


class BaseCollector(ABC):
    @abstractmethod
    def collect(
        self,
        task: AnalysisTask,
    ) -> tuple[list[SourceDocument], list[SourceEvidence]]:
        """Collect source documents and normalized evidence for a task."""


class LocalSnapshotCollector(BaseCollector):
    """Collector for v0.1 Snapshot Mode."""

    def __init__(self, snapshot_root: Path | str | None = None):
        if snapshot_root is None:
            snapshot_root = (
                Path(__file__).resolve().parent / "data" / "snapshots"
            )
        self.snapshot_root = Path(snapshot_root)

    def collect(
        self,
        task: AnalysisTask,
    ) -> tuple[list[SourceDocument], list[SourceEvidence]]:
        snapshot_id = task.metadata.get("snapshot_id", "online_education")
        snapshot_dir = self.snapshot_root / snapshot_id

        sources = [
            SourceDocument(**self._with_task_id(item, task.task_id))
            for item in self._load_json_list(snapshot_dir / "sources.json")
        ]
        evidence = [
            SourceEvidence(**self._with_task_id(item, task.task_id))
            for item in self._load_json_list(snapshot_dir / "evidence.json")
        ]
        return sources, evidence

    @staticmethod
    def _with_task_id(item: dict[str, Any], task_id: str) -> dict[str, Any]:
        data = dict(item)
        data["task_id"] = task_id
        return data

    @staticmethod
    def _load_json_list(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            raise FileNotFoundError(f"Snapshot artifact not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"Snapshot artifact must be a list: {path}")
        return data
