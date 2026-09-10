from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.execution.research_reporting import ResearchReportingService
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient
from app.schemas import ExecutionMode


UPSTREAM_ARTIFACTS = (
    "brief_assessments",
    "competitor_profiles",
    "evidence_coverage",
    "comparability_notes",
    "claims_v2",
    "research_gaps",
    "claims",
    "citation_checks",
)
DOWNSTREAM_ARTIFACTS = (
    "reports",
    "report_statements",
    "review_feedback",
    "quality_gates",
    "feedback_tasks",
)
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class _DevelopmentReportingLLMClient(LLMClient):
    """Expose the complete gap checklist to this one-off Writer rerun."""

    def generate_structured(self, **kwargs: Any):
        if kwargs.get("output_schema") == "CompetitiveReport":
            gap_ids = [
                str(item.get("id") or "")
                for item in kwargs.get("artifacts", {}).get("research_gaps", [])
                if str(item.get("id") or "")
            ]
            if gap_ids:
                kwargs["prompt_summary"] = (
                    str(kwargs.get("prompt_summary") or "")
                    + "\n\n本次开发重跑的 ResearchGap 完整引用检查清单如下。"
                    "这些 gap_id 均来自输入，不得遗漏；可以合并重复缺口的说明，"
                    "但必须在‘后续研究缺口’章节中让每个 ID 至少出现一次：\n"
                    + "\n".join(f"- [{gap_id}]" for gap_id in gap_ids)
                )
        return super().generate_structured(**kwargs)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_path(task_dir: Path, artifact_type: str) -> Path:
    return task_dir / f"{artifact_type}.json"


def _upstream_hashes(task_dir: Path) -> dict[str, str]:
    return {
        artifact_type: _sha256(_artifact_path(task_dir, artifact_type))
        for artifact_type in UPSTREAM_ARTIFACTS
    }


def _require_upstream(store: ArtifactStore, task_id: str, task_dir: Path) -> None:
    missing = [
        artifact_type
        for artifact_type in UPSTREAM_ARTIFACTS
        if not _artifact_path(task_dir, artifact_type).is_file()
        or not store.load_many(task_id, artifact_type)
    ]
    if missing:
        raise ValueError("缺少或为空的上游 Artifact: " + ", ".join(missing))


def _backup_and_clear_downstream(
    store: ArtifactStore,
    task_id: str,
    task_dir: Path,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = task_dir / "_reporting_backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": {},
    }
    for artifact_type in DOWNSTREAM_ARTIFACTS:
        source = _artifact_path(task_dir, artifact_type)
        items = store.load_many(task_id, artifact_type)
        record = {
            "existed": source.is_file(),
            "item_count": len(items),
            "sha256": _sha256(source) if source.is_file() else "",
        }
        if source.is_file():
            shutil.copy2(source, backup_dir / source.name)
        manifest["artifacts"][artifact_type] = record
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for artifact_type in DOWNSTREAM_ARTIFACTS:
        store.save_many(task_id, artifact_type, [])
    return backup_dir


def rerun_reporting(task_id: str, *, acknowledge_real_llm_call: bool) -> dict[str, Any]:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("task_id 只能包含字母、数字、下划线和连字符")
    if not acknowledge_real_llm_call:
        raise ValueError("必须传入 --acknowledge-real-llm-call 才能调用 DeepSeek Writer")

    store = ArtifactStore()
    runs_root = store.root_dir.resolve()
    task_dir = (runs_root / task_id).resolve()
    if task_dir.parent != runs_root or not task_dir.is_dir():
        raise ValueError(f"任务目录不存在或超出 runs 根目录: {task_dir}")

    _require_upstream(store, task_id, task_dir)
    upstream_before = _upstream_hashes(task_dir)
    backup_dir = _backup_and_clear_downstream(store, task_id, task_dir)

    llm_client = _DevelopmentReportingLLMClient(
        config=ResearchReportingService._deepseek_config(),
        store=store,
    )
    result = ResearchReportingService(
        store=store,
        llm_client=llm_client,
    ).run(
        task_id,
        mode=ExecutionMode.DEEPSEEK,
        acknowledge_real_llm_call=True,
    )
    upstream_after = _upstream_hashes(task_dir)
    changed_upstream = sorted(
        key for key, value in upstream_before.items() if upstream_after[key] != value
    )
    if changed_upstream:
        raise RuntimeError(
            "Reporting 重跑意外修改上游 Artifact: " + ", ".join(changed_upstream)
        )

    writer_calls = result.get("writer_llm_calls") or []
    writer_call = writer_calls[-1] if writer_calls else {}
    report = result.get("report") or {}
    summary = {
        "task_id": task_id,
        "status": result.get("status"),
        "backup_dir": str(backup_dir),
        "real_llm_calls_this_run": result.get("real_llm_calls_this_run"),
        "writer_prompt_id": writer_call.get("prompt_id"),
        "writer_prompt_version": writer_call.get("prompt_version"),
        "writer_model": writer_call.get("model"),
        "report_id": report.get("id"),
        "report_statement_count": len(result.get("report_statements") or []),
        "reviewer": result.get("review"),
        "quality_gate": result.get("quality_gate"),
        "feedback_task_count": len(result.get("feedback_tasks") or []),
        "upstream_artifacts_unchanged": not changed_upstream,
    }
    return {"summary": summary, "markdown": str(report.get("markdown") or "")}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "基于现有 Research/Analyst/Citation Artifact 重跑 "
            "Writer -> ReportStatement -> Reviewer -> QualityGate。"
        )
    )
    parser.add_argument("task_id")
    parser.add_argument("--acknowledge-real-llm-call", action="store_true")
    args = parser.parse_args()
    output = rerun_reporting(
        args.task_id,
        acknowledge_real_llm_call=args.acknowledge_real_llm_call,
    )
    print("REPORTING_RERUN_SUMMARY")
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    print("REPORTING_RERUN_MARKDOWN_BEGIN")
    print(output["markdown"])
    print("REPORTING_RERUN_MARKDOWN_END")


if __name__ == "__main__":
    main()
