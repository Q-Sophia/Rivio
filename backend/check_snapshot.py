from __future__ import annotations

import argparse
from pathlib import Path

from app.collectors import LocalSnapshotCollector
from app.harness.artifacts import ArtifactStore
from app.schemas import AnalysisTask, SourceDocument, SourceEvidence, TaskMode


DEFAULT_SNAPSHOT_ID = "online_education"
DEFAULT_TASK_ID = "snapshot_online_education_check"


def build_snapshot_task(
    *,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    task_id: str = DEFAULT_TASK_ID,
) -> AnalysisTask:
    return AnalysisTask(
        id=task_id,
        task_id=task_id,
        query=(
            "比较 ClassIn、腾讯云实时互动 / TRTC 教育方案与 BigBlueButton，"
            "分析在线教育实时互动与虚拟教室方案的产品定位、核心能力、成本、"
            "生态、适用场景和风险，为产品研发选型提供依据。"
        ),
        competitors=[
            "ClassIn",
            "腾讯云实时互动 / TRTC 教育方案",
            "BigBlueButton",
        ],
        industry="online education",
        report_subject="在线教育实时互动与虚拟教室解决方案",
        focus_areas=[
            "positioning",
            "feature",
            "pricing",
            "ecosystem",
            "customer",
            "risk",
        ],
        mode=TaskMode.SNAPSHOT,
        metadata={"snapshot_id": snapshot_id},
    )


def validate_evidence_links(
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
) -> None:
    source_ids = [source.id for source in sources]
    duplicate_source_ids = sorted(
        source_id for source_id in set(source_ids) if source_ids.count(source_id) > 1
    )
    if duplicate_source_ids:
        raise ValueError(
            "Duplicate SourceDocument.id values: "
            + ", ".join(duplicate_source_ids)
        )

    source_id_set = set(source_ids)
    missing_refs = sorted(
        {
            item.source_id
            for item in evidence
            if item.source_id not in source_id_set
        }
    )
    if missing_refs:
        raise ValueError(
            "Evidence references missing SourceDocument.id values: "
            + ", ".join(missing_refs)
        )


def collect_snapshot(
    task: AnalysisTask,
    *,
    snapshot_root: Path | str | None = None,
) -> tuple[list[SourceDocument], list[SourceEvidence]]:
    collector = LocalSnapshotCollector(snapshot_root=snapshot_root)
    sources, evidence = collector.collect(task)
    validate_evidence_links(sources, evidence)
    return sources, evidence


def save_snapshot_artifacts(
    task: AnalysisTask,
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    *,
    artifact_root: Path | str | None = None,
) -> ArtifactStore:
    store = ArtifactStore(root_dir=artifact_root)
    store.save_many(task.task_id, "sources", sources)
    store.save_many(task.task_id, "evidence", evidence)
    return store


def load_and_validate_saved_artifacts(
    store: ArtifactStore,
    task_id: str,
) -> tuple[list[SourceDocument], list[SourceEvidence]]:
    saved_sources = [
        SourceDocument(**item)
        for item in store.load_many(task_id, "sources")
    ]
    saved_evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    validate_evidence_links(saved_sources, saved_evidence)
    return saved_sources, saved_evidence


def run_snapshot_check(
    *,
    snapshot_id: str = DEFAULT_SNAPSHOT_ID,
    task_id: str = DEFAULT_TASK_ID,
    snapshot_root: Path | str | None = None,
    artifact_root: Path | str | None = None,
) -> tuple[AnalysisTask, list[SourceDocument], list[SourceEvidence], ArtifactStore]:
    task = build_snapshot_task(snapshot_id=snapshot_id, task_id=task_id)
    sources, evidence = collect_snapshot(task, snapshot_root=snapshot_root)
    store = save_snapshot_artifacts(
        task,
        sources,
        evidence,
        artifact_root=artifact_root,
    )
    load_and_validate_saved_artifacts(store, task.task_id)
    return task, sources, evidence, store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a local snapshot through SourceDocument/SourceEvidence, "
            "check evidence.source_id links, and save sources/evidence artifacts."
        )
    )
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--snapshot-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task, sources, evidence, store = run_snapshot_check(
        snapshot_id=args.snapshot_id,
        task_id=args.task_id,
        snapshot_root=args.snapshot_root,
        artifact_root=args.artifact_root,
    )
    run_dir = store.root_dir / task.task_id
    print("Snapshot check passed")
    print(f"task_id={task.task_id}")
    print(f"sources={len(sources)}")
    print(f"evidence={len(evidence)}")
    print(f"artifact_dir={run_dir}")


if __name__ == "__main__":
    main()
