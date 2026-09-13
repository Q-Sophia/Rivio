from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.harness.artifacts import ArtifactStore
from app.harness.pipeline import get_research_pipeline_harness
from app.execution import (
    ResearchAnalysisOutputTruncatedError,
    get_execution_runner,
    get_research_evidence_agent_service,
    get_research_analysis_service,
    get_research_reporting_service,
    get_research_loop_runner,
)
from app.execution.research_agent_coordinator import (
    get_research_agent_coordinator,
)
from app.execution.evidence_feed import build_evidence_feed
from app.collection import CollectorQueueService
from app.extraction import ExtractorQueueService
from app.intake import (
    ExecutionPlanningService,
    IntentDraftService,
    ResearchPlanningService,
    Step6E4QueueService,
    build_intent_llm_config,
)
from app.tools.search_provider import get_search_provider_status
from app.schemas import (
    AuthorizeExecutionRequest,
    ConfirmAnalysisTaskRequest,
    IntentParseRequest,
    RunResearchAgentRequest,
    RunResearchAnalysisRequest,
    RunResearchReportingRequest,
    StartExecutionRequest,
)


ARTIFACT_ENDPOINTS = {
    "sources": "sources",
    "source-chunks": "source_chunks",
    "evidence": "evidence",
    "product-cards": "product_cards",
    "claims": "claims",
    "citation-checks": "citation_checks",
    "task-board": "task_board",
    "task-records": "task_records",
    "quality-gates": "quality_gates",
    "feedback-tasks": "feedback_tasks",
    "context-bundles": "context_bundles",
    "working-memory": "working_memory",
    "memory-items": "memory_items",
    "guardrail-checks": "guardrail_checks",
    "llm-calls": "llm_calls",
    "llm-outputs": "llm_outputs",
    "analysis-portfolios": "analysis_portfolios",
    "brief-assessments": "brief_assessments",
    "competitor-profiles": "competitor_profiles",
    "intelligence-questions": "intelligence_questions",
    "information-needs": "information_needs",
    "evidence-coverage": "evidence_coverage",
    "comparability-notes": "comparability_notes",
    "claims-v2": "claims_v2",
    "research-gaps": "research_gaps",
    "report-statements": "report_statements",
    "research-plans": "research_plans",
    "research-kiqs": "research_kiqs",
    "research-information-needs": "research_information_needs",
    "research-tasks": "research_tasks",
    "web-pages": "web_pages",
    "collection-attempts": "collection_attempts",
    "search-attempts": "search_attempts",
    "web-search-results": "web_search_results",
    "evidence-extraction-attempts": "evidence_extraction_attempts",
    "research-loop-runs": "research_loop_runs",
    "research-loop-events": "research_loop_events",
    "analysis-evidence-coverage": "analysis_evidence_coverage",
    "analysis-research-gaps": "analysis_research_gaps",
    "analysis-assessments": "analysis_assessments",
    "research-agent-runs": "research_agent_runs",
    "research-agent-actions": "research_agent_actions",
    "research-agent-observations": "research_agent_observations",
    "research-agent-coordinator-runs": "research_agent_coordinator_runs",
    "research-agent-coordinator-events": "research_agent_coordinator_events",
    "research-task-failures": "research_task_failures",
    "research-batch-results": "research_batch_results",
    "agent-handoffs": "agent_handoffs",
    "pipeline-runs": "pipeline_runs",
    "pipeline-checkpoints": "pipeline_checkpoints",
    "pipeline-events": "pipeline_events",
}

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
STEP6C_EXPERIMENT_ROOT = Path(__file__).resolve().parents[1] / "data" / "ab_tests"
DEFAULT_RUN_ID = "default"


app = FastAPI(
    title="Competitive Intel Agents API",
    version="0.1.0",
    description=(
        "Evidence-first workflow artifact API plus a bounded task-intake boundary. "
        "Only the explicit intent-parse endpoint may call an LLM; confirming a draft "
        "does not start analysis, crawling, or report generation."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

class StartResearchAgentCoordinatorRequest(BaseModel):
    mode: str = "deepseek"
    acknowledge_real_llm_call: bool = False


class StartResearchPipelineRequest(BaseModel):
    mode: str = "deepseek"
    acknowledge_real_llm_call: bool = False

def get_store() -> ArtifactStore:
    return ArtifactStore()


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def validate_path_segment(value: str, label: str) -> str:
    if not value or any(ch in value for ch in "\\/:*?\"<>|") or value in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value!r}")
    return value


def resolve_run_task_dir(run_id: str, task_id: str) -> Path:
    validate_path_segment(run_id, "run_id")
    validate_path_segment(task_id, "task_id")
    root_dir = get_store().root_dir.resolve()
    if run_id == DEFAULT_RUN_ID:
        task_dir = root_dir / task_id
    else:
        task_dir = root_dir / run_id / task_id
    resolved = task_dir.resolve()
    if resolved != root_dir and root_dir not in resolved.parents:
        raise HTTPException(status_code=400, detail="Run path escapes artifact root")
    if not resolved.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Run task not found: {run_id}/{task_id}",
        )
    return resolved


def load_json_from_dir(task_dir: Path, artifact_type: str) -> Any:
    validate_path_segment(artifact_type, "artifact_type")
    path = task_dir / f"{artifact_type}.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Artifact not found: {task_dir.name}/{artifact_type}.json",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Artifact is not valid JSON: {artifact_type}.json",
        ) from exc


def load_list_from_dir(task_dir: Path, artifact_type: str) -> list[dict[str, Any]]:
    data = load_json_from_dir(task_dir, artifact_type)
    if not isinstance(data, list):
        raise HTTPException(
            status_code=500,
            detail=f"Artifact must be a list: {artifact_type}.json",
        )
    return data


def iter_run_task_dirs() -> list[tuple[str, str, Path]]:
    root_dir = get_store().root_dir
    discovered: list[tuple[str, str, Path]] = []
    for candidate in root_dir.iterdir():
        if not candidate.is_dir():
            continue
        if (candidate / "pipeline_summary.json").exists():
            discovered.append((DEFAULT_RUN_ID, candidate.name, candidate))
            continue
        try:
            nested_dirs = list(candidate.iterdir())
        except OSError:
            continue
        for task_dir in nested_dirs:
            if task_dir.is_dir() and (task_dir / "pipeline_summary.json").exists():
                discovered.append((candidate.name, task_dir.name, task_dir))
    return discovered


def infer_stage(metadata: dict[str, Any]) -> tuple[str, str, int]:
    runtime = str(metadata.get("runtime") or "").lower()
    provider = str(metadata.get("llm_provider") or "unknown")
    real_calls_enabled = bool(metadata.get("real_calls_enabled", False))
    if bool(metadata.get("user_task_execution")) or "step6d4" in runtime:
        mode = "真实 DeepSeek" if real_calls_enabled else "mock 模拟"
        return (
            "Step 6D.4",
            f"用户任务后台执行与实时进度 · {mode}",
            74,
        )
    if bool(metadata.get("analyst_writer_real")):
        model = str(metadata.get("analyst_llm_model") or "真实模型")
        return (
            "Step 6C.3",
            f"真实 Analyst + Writer（分析与写作智能体）闭环 · {model}",
            68,
        )
    if bool(metadata.get("writer_only_real")):
        model = str(metadata.get("writer_llm_model") or "真实模型")
        return "Step 6C.2D", f"真实 Writer（写作智能体）试运行 · {model}", 64
    if bool(metadata.get("professional_analysis")) or "step6c" in runtime:
        mode = "真实模型" if real_calls_enabled and provider != "mock" else "mock 模拟"
        detail = (
            "专业报告与证据追溯工作流"
            if int(metadata.get("report_statements_count", 0)) > 0
            else "专业竞品分析工作流"
        )
        return "Step 6C", f"{detail} · {mode}", 60
    if real_calls_enabled and provider != "mock":
        return "Step 6B.2", "真实模型结构化运行", 52
    if metadata.get("llm_calls_count") is not None or metadata.get("llm_model"):
        return "Step 6A.5", "mock LLM（模拟大模型）结构化运行", 45
    return "M1-M6", "Evidence-first（证据优先）基础工作流", 30


def build_run_descriptor(run_id: str, task_id: str, task_dir: Path) -> dict[str, Any]:
    summaries = load_list_from_dir(task_dir, "pipeline_summary")
    summary = latest_item(summaries, "pipeline_summary")
    metadata = summary.get("metadata") or {}
    modified_at = datetime.fromtimestamp(
        (task_dir / "pipeline_summary.json").stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()
    provider = str(metadata.get("llm_provider") or "unknown")
    real_calls_enabled = bool(metadata.get("real_calls_enabled", False))
    stage, stage_detail, stage_rank = infer_stage(metadata)
    return {
        "run_id": run_id,
        "task_id": task_id,
        "provider": provider,
        "model": str(metadata.get("llm_model") or "unknown"),
        "mode": str(metadata.get("llm_mode") or "unknown"),
        "real_calls_enabled": real_calls_enabled,
        "is_real_llm": real_calls_enabled and provider != "mock",
        "stage": stage,
        "stage_detail": stage_detail,
        "stage_rank": stage_rank,
        "workflow_runtime": str(metadata.get("runtime") or "unknown"),
        "professional_analysis": bool(metadata.get("professional_analysis", False)),
        "analysis_portfolios_count": int(metadata.get("analysis_portfolios_count", 0)),
        "competitor_profiles_count": int(metadata.get("competitor_profiles_count", 0)),
        "claims_v2_count": int(metadata.get("claims_v2_count", 0)),
        "research_gaps_count": int(metadata.get("research_gaps_count", 0)),
        "report_statements_count": int(
            metadata.get("report_statements_count", 0)
        ),
        "analyst_writer_real": bool(metadata.get("analyst_writer_real", False)),
        "analyst_llm_model": str(metadata.get("analyst_llm_model") or ""),
        "real_analyst_calls_count": int(
            metadata.get("real_analyst_calls_count", 0)
        ),
        "real_writer_calls_count": int(
            metadata.get("real_writer_calls_count", 0)
        ),
        "analyst_rejected_claims_count": int(
            metadata.get("analyst_rejected_claims_count", 0)
        ),
        "writer_only_real": bool(metadata.get("writer_only_real", False)),
        "writer_llm_model": str(metadata.get("writer_llm_model") or ""),
        "pipeline_status": summary.get("pipeline_status", "unknown"),
        "approved": bool(summary.get("approved", False)),
        "claims_count": int(summary.get("claims_count", 0)),
        "review_score": float(summary.get("review_score", 0.0)),
        "modified_at": modified_at,
    }


def optional_list_from_dir(task_dir: Path, artifact_type: str) -> list[dict[str, Any]]:
    path = task_dir / f"{artifact_type}.json"
    if not path.exists():
        return []
    return load_list_from_dir(task_dir, artifact_type)


def optional_object_from_dir(task_dir: Path, artifact_type: str) -> dict[str, Any] | None:
    path = task_dir / f"{artifact_type}.json"
    if not path.exists():
        return None
    data = load_json_from_dir(task_dir, artifact_type)
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Artifact must be an object: {artifact_type}.json",
        )
    return data


def optional_latest_from_dir(
    task_dir: Path,
    artifact_type: str,
) -> dict[str, Any] | None:
    items = optional_list_from_dir(task_dir, artifact_type)
    return items[-1] if items else None


def infer_workspace_stage(artifacts: dict[str, Any]) -> tuple[str, str]:
    if artifacts["review"] is not None:
        return "reviewed", "审查产物已经生成"
    if artifacts["report"] is not None:
        return "reported", "分析报告已经生成，等待或正在进行审查"
    if (
        artifacts["analysisPortfolios"]
        or artifacts["claims"]
        or artifacts["claimsV2"]
        or artifacts["citationChecks"]
    ):
        return "analyzed", "分析结论已经生成"
    if artifacts["evidenceCoverage"] or artifacts["researchGaps"] or artifacts["productCards"]:
        return "coverage_evaluated", "证据覆盖与研究缺口已经更新"
    if artifacts["evidence"]:
        return "evidence_extracted", "网页内容已经转为结构化证据"
    if artifacts["sources"] or artifacts["webPages"]:
        return "sources_collected", "来源或网页内容已经采集"
    if artifacts["researchPlan"] is not None or artifacts["researchTasks"]:
        return "research_planned", "研究计划已经生成"
    return "confirmed", "AnalysisTask 已确认，等待后续研究产物"


def build_task_navigation_item(task_dir: Path) -> dict[str, Any] | None:
    analysis_tasks = optional_list_from_dir(task_dir, "analysis_tasks")
    if not analysis_tasks:
        return None
    analysis_task = analysis_tasks[-1]
    stage_artifacts = {
        "review": optional_latest_from_dir(task_dir, "review_feedback"),
        "report": optional_latest_from_dir(task_dir, "reports"),
        "analysisPortfolios": optional_list_from_dir(task_dir, "analysis_portfolios"),
        "claims": optional_list_from_dir(task_dir, "claims"),
        "claimsV2": optional_list_from_dir(task_dir, "claims_v2"),
        "citationChecks": optional_list_from_dir(task_dir, "citation_checks"),
        "evidenceCoverage": optional_list_from_dir(task_dir, "evidence_coverage"),
        "researchGaps": optional_list_from_dir(task_dir, "research_gaps"),
        "productCards": optional_list_from_dir(task_dir, "product_cards"),
        "evidence": optional_list_from_dir(task_dir, "evidence"),
        "sources": optional_list_from_dir(task_dir, "sources"),
        "webPages": optional_list_from_dir(task_dir, "web_pages"),
        "researchPlan": optional_latest_from_dir(task_dir, "research_plans"),
        "researchTasks": optional_list_from_dir(task_dir, "research_tasks"),
    }
    workspace_stage, stage_detail = infer_workspace_stage(stage_artifacts)
    research_loop_run = optional_latest_from_dir(task_dir, "research_loop_runs")
    loop_status = str((research_loop_run or {}).get("status") or "")
    progress_by_stage = {
        "confirmed": 0,
        "research_planned": 10,
        "sources_collected": 35,
        "evidence_extracted": 55,
        "coverage_evaluated": 70,
        "analyzed": 85,
        "reported": 95,
        "reviewed": 100,
    }
    status = str(analysis_task.get("status") or "pending")
    current_stage = workspace_stage
    progress_percent = progress_by_stage.get(workspace_stage, 0)
    if loop_status in {"queued", "running"}:
        status = loop_status
        current_stage = str(
            (research_loop_run or {}).get("current_stage") or "research_loop"
        )
        stage_detail = str(
            (research_loop_run or {}).get("message") or stage_detail
        )
        progress_percent = int(
            (research_loop_run or {}).get("progress_percent") or 0
        )
    elif workspace_stage in {"reported", "reviewed"}:
        status = "reported"
    elif workspace_stage == "analyzed":
        status = "analyzed"
    elif loop_status in {"failed", "requires_human"}:
        status = loop_status
        current_stage = str(
            (research_loop_run or {}).get("current_stage") or "research_loop"
        )
        stage_detail = str(
            (research_loop_run or {}).get("message") or stage_detail
        )
        progress_percent = int(
            (research_loop_run or {}).get("progress_percent") or 0
        )
    elif workspace_stage != "confirmed":
        status = "in_progress"

    modified_timestamps = [
        path.stat().st_mtime
        for path in task_dir.glob("*.json")
        if path.is_file()
    ]
    modified_at = datetime.fromtimestamp(
        max(modified_timestamps, default=task_dir.stat().st_mtime),
        tz=timezone.utc,
    ).isoformat()
    task_metadata = analysis_task.get("metadata") or {}
    if not isinstance(task_metadata, dict):
        task_metadata = {}
    request_text = str(
        task_metadata.get("request_text")
        or analysis_task.get("query")
        or ""
    )
    title = str(
        analysis_task.get("preferred_title")
        or analysis_task.get("report_subject")
        or analysis_task.get("query")
        or task_dir.name
    )
    return {
        "task_id": task_dir.name,
        "title": title,
        "request_text": request_text or title,
        "workspace_origin": str(task_metadata.get("workspace_origin") or ""),
        "analysis_task_status": str(analysis_task.get("status") or "pending"),
        "status": status,
        "stage": workspace_stage,
        "current_stage": current_stage,
        "stage_detail": stage_detail,
        "progress_percent": max(0, min(progress_percent, 100)),
        "updated_at": modified_at,
    }


def build_task_workspace(task_id: str) -> dict[str, Any]:
    """Read the current artifacts for one task without requiring a completed run."""
    validate_path_segment(task_id, "task_id")
    root_dir = get_store().root_dir.resolve()
    task_dir = (root_dir / task_id).resolve()
    if root_dir not in task_dir.parents or not task_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Analysis task not found: {task_id}")

    analysis_tasks = optional_list_from_dir(task_dir, "analysis_tasks")
    if not analysis_tasks:
        raise HTTPException(status_code=404, detail=f"Analysis task not found: {task_id}")

    workspace: dict[str, Any] = {
        "workspaceVersion": "v1",
        "taskId": task_id,
        "analysisTask": analysis_tasks[-1],
        "run": None,
        "summary": optional_latest_from_dir(task_dir, "pipeline_summary"),
        "sources": optional_list_from_dir(task_dir, "sources"),
        "sourceChunks": optional_list_from_dir(task_dir, "source_chunks"),
        "evidence": optional_list_from_dir(task_dir, "evidence"),
        "productCards": optional_list_from_dir(task_dir, "product_cards"),
        "claims": optional_list_from_dir(task_dir, "claims"),
        "citationChecks": optional_list_from_dir(task_dir, "citation_checks"),
        "report": optional_latest_from_dir(task_dir, "reports"),
        "review": optional_latest_from_dir(task_dir, "review_feedback"),
        "researchPlan": optional_latest_from_dir(task_dir, "research_plans"),
        "researchTasks": optional_list_from_dir(task_dir, "research_tasks"),
        "evidenceCoverage": optional_list_from_dir(task_dir, "evidence_coverage"),
        "researchGaps": optional_list_from_dir(task_dir, "research_gaps"),
        "analysisPortfolios": optional_list_from_dir(task_dir, "analysis_portfolios"),
        "briefAssessments": optional_list_from_dir(task_dir, "brief_assessments"),
        "competitorProfiles": optional_list_from_dir(task_dir, "competitor_profiles"),
        "intelligenceQuestions": optional_list_from_dir(task_dir, "intelligence_questions"),
        "informationNeeds": optional_list_from_dir(task_dir, "information_needs"),
        "comparabilityNotes": optional_list_from_dir(task_dir, "comparability_notes"),
        "claimsV2": optional_list_from_dir(task_dir, "claims_v2"),
        "analysisEvidenceCoverage": optional_list_from_dir(task_dir, "analysis_evidence_coverage"),
        "analysisResearchGaps": optional_list_from_dir(task_dir, "analysis_research_gaps"),
        "taskBoard": optional_latest_from_dir(task_dir, "task_board"),
        "taskRecords": optional_list_from_dir(task_dir, "task_records"),
        "trace": {
            "task_id": task_id,
            "dag_nodes": optional_list_from_dir(task_dir, "dag_nodes"),
            "agent_runs": optional_list_from_dir(task_dir, "agent_runs"),
            "tool_calls": optional_list_from_dir(task_dir, "tool_calls"),
        },
        "evalSummary": optional_object_from_dir(task_dir, "eval_summary"),
        "qualityGates": optional_list_from_dir(task_dir, "quality_gates"),
        "feedbackTasks": optional_list_from_dir(task_dir, "feedback_tasks"),
        "contextBundles": optional_list_from_dir(task_dir, "context_bundles"),
        "workingMemory": optional_latest_from_dir(task_dir, "working_memory"),
        "memoryItems": optional_list_from_dir(task_dir, "memory_items"),
        "guardrailChecks": optional_list_from_dir(task_dir, "guardrail_checks"),
        "llmCalls": optional_list_from_dir(task_dir, "llm_calls"),
        "llmOutputs": optional_list_from_dir(task_dir, "llm_outputs"),
        "reportStatements": optional_list_from_dir(task_dir, "report_statements"),
        "webPages": optional_list_from_dir(task_dir, "web_pages"),
        "collectionAttempts": optional_list_from_dir(task_dir, "collection_attempts"),
        "searchAttempts": optional_list_from_dir(task_dir, "search_attempts"),
        "webSearchResults": optional_list_from_dir(task_dir, "web_search_results"),
        "researchAgentRuns": optional_list_from_dir(task_dir, "research_agent_runs"),
        "researchAgentActions": optional_list_from_dir(task_dir, "research_agent_actions"),
        "researchAgentObservations": optional_list_from_dir(
            task_dir,
            "research_agent_observations",
        ),
        "evidenceExtractionAttempts": optional_list_from_dir(
            task_dir,
            "evidence_extraction_attempts",
        ),
    }
    workspace["stage"], workspace["stageDetail"] = infer_workspace_stage(workspace)
    return workspace


def load_step6c_experiment_detail(
    summary_path: Path,
    summary: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(result.get("task_id") or "")
    validate_path_segment(task_id, "experiment task_id")
    task_dir = (STEP6C_EXPERIMENT_ROOT / task_id).resolve()
    experiment_root = STEP6C_EXPERIMENT_ROOT.resolve()
    if experiment_root not in task_dir.parents or not task_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Experiment task not found: {task_id}")

    return {
        "experiment": summary,
        "result": result,
        "taskId": task_id,
        "modifiedAt": datetime.fromtimestamp(
            summary_path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat(),
        "sources": optional_list_from_dir(task_dir, "sources"),
        "evidence": optional_list_from_dir(task_dir, "evidence"),
        "productCards": optional_list_from_dir(task_dir, "product_cards"),
        "analysisPortfolios": optional_list_from_dir(task_dir, "analysis_portfolios"),
        "briefAssessments": optional_list_from_dir(task_dir, "brief_assessments"),
        "competitorProfiles": optional_list_from_dir(task_dir, "competitor_profiles"),
        "intelligenceQuestions": optional_list_from_dir(task_dir, "intelligence_questions"),
        "informationNeeds": optional_list_from_dir(task_dir, "information_needs"),
        "evidenceCoverage": optional_list_from_dir(task_dir, "evidence_coverage"),
        "comparabilityNotes": optional_list_from_dir(task_dir, "comparability_notes"),
        "claimsV2": optional_list_from_dir(task_dir, "claims_v2"),
        "researchGaps": optional_list_from_dir(task_dir, "research_gaps"),
        "reportStatements": optional_list_from_dir(task_dir, "report_statements"),
        "llmCalls": optional_list_from_dir(task_dir, "llm_calls"),
        "llmOutputs": optional_list_from_dir(task_dir, "llm_outputs"),
    }


def iter_step6c_experiment_summaries() -> list[tuple[Path, dict[str, Any]]]:
    if not STEP6C_EXPERIMENT_ROOT.is_dir():
        return []
    summaries: list[tuple[Path, dict[str, Any]]] = []
    for summary_path in STEP6C_EXPERIMENT_ROOT.glob("*_summary.json"):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            summaries.append((summary_path, payload))
    summaries.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
    return summaries


def artifact_path(task_id: str, artifact_type: str) -> Path:
    if not task_id or any(ch in task_id for ch in "\\/:*?\"<>|"):
        raise HTTPException(status_code=400, detail=f"Invalid task_id: {task_id!r}")
    if not artifact_type or any(ch in artifact_type for ch in "\\/:*?\"<>|"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid artifact_type: {artifact_type!r}",
        )
    return get_store().root_dir / task_id / f"{artifact_type}.json"


def load_json_artifact(task_id: str, artifact_type: str) -> Any:
    path = artifact_path(task_id, artifact_type)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Artifact not found: {task_id}/{artifact_type}.json",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Artifact is not valid JSON: {artifact_type}.json",
        ) from exc


def load_list_artifact(task_id: str, artifact_type: str) -> list[dict[str, Any]]:
    data = load_json_artifact(task_id, artifact_type)
    if not isinstance(data, list):
        raise HTTPException(
            status_code=500,
            detail=f"Artifact must be a list: {artifact_type}.json",
        )
    return data


def latest_item(items: list[dict[str, Any]], artifact_type: str) -> dict[str, Any]:
    if not items:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact is empty: {artifact_type}.json",
        )
    return items[-1]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def get_dashboard() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found")
    return FileResponse(index_path)


@app.get("/api/tasks")
def list_tasks() -> dict[str, Any]:
    root_dir = get_store().root_dir
    tasks = sorted(path.name for path in root_dir.iterdir() if path.is_dir())
    return {"tasks": tasks}


@app.get("/api/analysis-tasks")
def list_analysis_tasks(limit: int = 20) -> dict[str, Any]:
    root_dir = get_store().root_dir
    tasks: list[dict[str, Any]] = []
    for task_dir in root_dir.iterdir():
        if not task_dir.is_dir() or not (task_dir / "analysis_tasks.json").exists():
            continue
        try:
            item = build_task_navigation_item(task_dir)
        except (HTTPException, OSError):
            continue
        if item is not None:
            tasks.append(item)
    tasks.sort(key=lambda item: item["updated_at"], reverse=True)
    bounded_limit = max(1, min(limit, 100))
    return {"tasks": tasks[:bounded_limit]}


@app.get("/api/runs")
def list_runs() -> dict[str, Any]:
    runs = [
        build_run_descriptor(run_id, task_id, task_dir)
        for run_id, task_id, task_dir in iter_run_task_dirs()
    ]
    runs.sort(
        key=lambda item: (
            item["stage_rank"],
            item["pipeline_status"] == "completed",
            item["is_real_llm"],
            item["modified_at"],
        ),
        reverse=True,
    )
    return {"runs": runs}


@app.get("/api/task-drafts")
def list_task_drafts(limit: int = 10) -> dict[str, Any]:
    drafts = IntentDraftService().list_drafts(limit=limit)
    return {"drafts": [item.model_dump(mode="json") for item in drafts]}


@app.get("/api/integrations/status")
def integration_status() -> dict[str, Any]:
    """Expose non-secret runtime readiness; this endpoint never calls a provider."""
    intent_config = build_intent_llm_config()
    readiness_errors = intent_config.real_call_readiness_errors()
    return {
        "deepseek": {
            "configured": not readiness_errors,
            "provider": str(intent_config.provider),
            "model": intent_config.model,
            "endpoint": intent_config.base_url,
            "purpose": "intent_analysis_writer",
            "transport": (
                "environment_proxy"
                if intent_config.trust_env_proxy
                else "direct"
            ),
            "errors": readiness_errors,
        },
        "search": get_search_provider_status(),
        "network_called": False,
        "message": "这里只检查后端配置；点击相应业务按钮时才会真实调用外部 API。",
    }


@app.post("/api/task-drafts/parse")
def parse_task_draft(request: IntentParseRequest) -> dict[str, Any]:
    try:
        draft, llm_call = IntentDraftService().parse_request(request.request_text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"意图解析失败：{exc}") from exc
    return {
        "draft": draft.model_dump(mode="json"),
        "llm_call": llm_call,
        "execution_started": False,
    }


@app.get("/api/task-drafts/{draft_id}")
def get_task_draft(draft_id: str) -> dict[str, Any]:
    validate_path_segment(draft_id, "draft_id")
    draft = IntentDraftService().get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Task draft not found: {draft_id}")
    return {"draft": draft.model_dump(mode="json")}


@app.post("/api/task-drafts/{draft_id}/confirm")
def confirm_task_draft(
    draft_id: str,
    request: ConfirmAnalysisTaskRequest,
) -> dict[str, Any]:
    validate_path_segment(draft_id, "draft_id")
    try:
        draft, task = IntentDraftService().confirm_draft(draft_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "draft": draft.model_dump(mode="json"),
        "analysis_task": task.model_dump(mode="json"),
        "execution_started": False,
        "message": "任务已确认并保存，尚未开始分析。",
    }


@app.get("/api/analysis-tasks/{task_id}/workspace")
def get_analysis_task_workspace(task_id: str) -> dict[str, Any]:
    return build_task_workspace(task_id)


@app.get("/tasks/{task_id}/evidence-feed")
@app.get("/api/tasks/{task_id}/evidence-feed")
def get_evidence_feed(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    store = get_store()
    if not store.load_many(task_id, "analysis_tasks"):
        raise HTTPException(
            status_code=404,
            detail=f"Analysis task not found: {task_id}",
        )
    return {"items": build_evidence_feed(store, task_id)}


def execution_plan_payload(
    service: ExecutionPlanningService,
    task_id: str,
) -> dict[str, Any]:
    task = service.get_task(task_id)
    plan = service.get_latest_plan(task_id)
    assessment = service.get_latest_assessment(task_id)
    authorization = service.get_latest_authorization(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Analysis task not found: {task_id}")
    if plan is None or assessment is None:
        raise HTTPException(status_code=404, detail="尚未生成资料兼容评估与执行计划")
    return {
        "analysis_task": task.model_dump(mode="json"),
        "compatibility_assessment": assessment.model_dump(mode="json"),
        "execution_plan": plan.model_dump(mode="json"),
        "execution_authorization": (
            authorization.model_dump(mode="json") if authorization else None
        ),
        "execution_started": bool(task.metadata.get("execution_started", False)),
    }


@app.get("/api/analysis-tasks/{task_id}/plan")
def get_execution_plan(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    return execution_plan_payload(ExecutionPlanningService(), task_id)


@app.post("/api/analysis-tasks/{task_id}/plan")
def build_execution_plan(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    service = ExecutionPlanningService()
    try:
        service.build_plan(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return execution_plan_payload(service, task_id)


@app.post("/api/analysis-tasks/{task_id}/authorize")
def authorize_execution(
    task_id: str,
    request: AuthorizeExecutionRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    service = ExecutionPlanningService()
    try:
        task, plan, authorization, task_board = service.authorize(task_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "analysis_task": task.model_dump(mode="json"),
        "execution_plan": plan.model_dump(mode="json"),
        "execution_authorization": authorization.model_dump(mode="json"),
        "task_board": task_board,
        "execution_started": False,
        "message": "执行计划已获授权并加入任务队列，后台执行尚未开始。",
    }


@app.get("/api/analysis-tasks/{task_id}/research-plan")
def get_research_plan(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return ResearchPlanningService().get_payload(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/analysis-tasks/{task_id}/research-plan")
def build_research_plan(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return ResearchPlanningService().build(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/analysis-tasks/{task_id}/collector/run-once")
def run_collector_once(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    service = CollectorQueueService()
    try:
        return service.run_once(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        service.close()


@app.post("/api/analysis-tasks/{task_id}/extractor/run-once")
def run_extractor_once(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return ExtractorQueueService().run_once(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/analysis-tasks/{task_id}/coverage/run-once")
def run_evidence_coverage_once(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return Step6E4QueueService().run_once(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def research_loop_status_payload(task_id: str) -> dict[str, Any]:
    runner = get_research_loop_runner()
    run = runner.reconcile_interrupted(task_id)
    events = runner.get_events(task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="该任务尚未启动研究循环。")
    return {
        "research_loop_run": run.model_dump(mode="json"),
        "events": [item.model_dump(mode="json") for item in events],
        "terminal": run.status in {"completed", "requires_human", "failed"},
    }


@app.post("/api/analysis-tasks/{task_id}/research-loop", status_code=202)
def start_research_loop(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        run = get_research_loop_runner().submit(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "research_loop_run": run.model_dump(mode="json"),
        "research_loop_started": True,
        "message": "有限研究循环已启动；请通过 SSE（服务器发送事件）观察实时进度。",
    }


@app.get("/api/analysis-tasks/{task_id}/research-loop")
def get_research_loop(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    return research_loop_status_payload(task_id)


@app.get("/api/analysis-tasks/{task_id}/research-loop/events")
def get_research_loop_events(task_id: str, after: int = 0) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    events = get_research_loop_runner().get_events(task_id, after=max(after, 0))
    return {"events": [item.model_dump(mode="json") for item in events]}


@app.get("/api/analysis-tasks/{task_id}/research-loop/events/stream")
async def stream_research_loop_events(
    task_id: str,
    after: int = 0,
) -> StreamingResponse:
    validate_path_segment(task_id, "task_id")
    runner = get_research_loop_runner()

    async def generate():
        cursor = max(after, 0)
        idle_ticks = 0
        runner.reconcile_interrupted(task_id)
        while True:
            events = runner.get_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: research-loop\ndata: {payload}\n\n"
                idle_ticks = 0
            latest = runner.get_latest_run(task_id)
            if (
                latest
                and latest.status in {"completed", "requires_human", "failed"}
                and not events
            ):
                break
            if latest is None and idle_ticks >= 4:
                payload = json.dumps(
                    {"message": "该任务尚未启动研究循环。"},
                    ensure_ascii=False,
                )
                yield f"event: unavailable\ndata: {payload}\n\n"
                break
            idle_ticks += 1
            if idle_ticks % 30 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

def research_agent_coordinator_status_payload(
    task_id: str,
) -> dict[str, Any]:
    coordinator = get_research_agent_coordinator()
    run = coordinator.reconcile_interrupted(task_id)
    events = coordinator.get_events(task_id)

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="该任务尚未启动 Research Agent R1 批量研究。",
        )

    return {
        "research_agent_coordinator_run": run.model_dump(mode="json"),
        "events": [
            item.model_dump(mode="json")
            for item in events
        ],
        "terminal": run.status in {"completed", "failed", "stopped"},
    }


@app.post(
    "/api/analysis-tasks/{task_id}/research-agent/run",
    status_code=202,
)
def start_research_agent_coordinator(
    task_id: str,
    request: StartResearchAgentCoordinatorRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")

    try:
        planning_service = ResearchPlanningService()
        existing_plan = planning_service.get_latest_plan(task_id)
        planning_payload = planning_service.build(task_id)
        run = get_research_agent_coordinator().submit(
            task_id,
            mode=request.mode,
            acknowledge_real_llm_call=(
                request.acknowledge_real_llm_call
            ),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return {
        "research_plan": planning_payload,
        "research_plan_created": existing_plan is None,
        "research_agent_coordinator_run": (
            run.model_dump(mode="json")
        ),
        "research_agent_started": True,
        "message": (
            "研究计划已准备，Research Agent R1 已进入后台执行；"
            "可通过 SSE 观察研究进度。"
        ),
    }


@app.get(
    "/api/analysis-tasks/{task_id}/research-agent/run"
)
def get_research_agent_coordinator_run(
    task_id: str,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    return research_agent_coordinator_status_payload(task_id)


@app.post(
    "/api/analysis-tasks/{task_id}/research-agent/run/stop"
)
def stop_research_agent_coordinator(
    task_id: str,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        run = get_research_agent_coordinator().request_stop(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "research_agent_coordinator_run": run.model_dump(mode="json"),
        "stop_requested": run.status in {"stopping", "stopped"},
        "terminal": run.status in {"completed", "failed", "stopped"},
    }


@app.get(
    "/api/analysis-tasks/{task_id}/research-agent/run/events"
)
def get_research_agent_coordinator_events(
    task_id: str,
    after: int = 0,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")

    events = get_research_agent_coordinator().get_events(
        task_id,
        after=max(after, 0),
    )

    return {
        "events": [
            item.model_dump(mode="json")
            for item in events
        ]
    }


@app.get(
    "/api/analysis-tasks/{task_id}/research-agent/run/events/stream"
)
async def stream_research_agent_coordinator_events(
    task_id: str,
    after: int = 0,
) -> StreamingResponse:
    validate_path_segment(task_id, "task_id")
    coordinator = get_research_agent_coordinator()

    async def generate():
        cursor = max(after, 0)
        idle_ticks = 0

        coordinator.reconcile_interrupted(task_id)

        while True:
            coordinator.sync_evidence_events(task_id)
            events = coordinator.get_events(
                task_id,
                after=cursor,
            )

            for event in events:
                cursor = event.sequence

                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                )

                yield (
                    f"id: {event.sequence}\n"
                    f"event: research-agent\n"
                    f"data: {payload}\n\n"
                )

                idle_ticks = 0

            latest = coordinator.get_latest_run(task_id)

            if (
                latest
                and latest.status in {"completed", "failed", "stopped"}
                and not events
            ):
                break

            if latest is None and idle_ticks >= 4:
                payload = json.dumps(
                    {
                        "message": (
                            "该任务尚未启动 "
                            "Research Agent R1 批量研究。"
                        )
                    },
                    ensure_ascii=False,
                )

                yield (
                    "event: unavailable\n"
                    f"data: {payload}\n\n"
                )
                break

            idle_ticks += 1

            if idle_ticks % 30 == 0:
                yield ": heartbeat\n\n"

            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def research_pipeline_status_payload(task_id: str) -> dict[str, Any]:
    harness = get_research_pipeline_harness()
    run = harness.reconcile_interrupted(task_id)
    run = harness.reconcile_completed_artifacts(task_id) or run
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="该任务尚未启动统一多 Agent Pipeline。",
        )

    coordinator = get_research_agent_coordinator()
    research_run = coordinator.get_latest_run(task_id)
    compatibility_run = (
        research_run.model_dump(mode="json")
        if research_run is not None
        else {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "outcomes": {},
            "current_collection_round": 0,
            "max_collection_rounds": 0,
            "current_round_total": 0,
            "current_round_completed": 0,
            "current_research_task_id": "",
        }
    )
    compatibility_run.update(
        {
            "id": run.id,
            "task_id": run.task_id,
            "status": enum_value(run.status),
            "current_stage": enum_value(run.current_stage),
            "progress_percent": run.progress_percent,
            "completed_stages": list(run.completed_stages),
            "message": run.message,
            "error": run.error,
            "stop_reason": run.stop_reason,
            "resume_count": run.resume_count,
            "last_checkpoint_id": run.last_checkpoint_id,
        }
    )
    events = harness.get_events(task_id)
    return {
        "pipeline_run": run.model_dump(mode="json"),
        # Compatibility projection keeps the existing research progress UI and
        # API clients working while lifecycle ownership moves to the Harness.
        "research_agent_coordinator_run": compatibility_run,
        "events": [item.model_dump(mode="json") for item in events],
        "terminal": enum_value(run.status)
        in {"completed", "failed", "stopped", "interrupted"},
    }


@app.post("/api/analysis-tasks/{task_id}/pipeline/run", status_code=202)
def start_research_pipeline(
    task_id: str,
    request: StartResearchPipelineRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        run = get_research_pipeline_harness().submit(
            task_id,
            mode=request.mode,
            acknowledge_real_llm_call=request.acknowledge_real_llm_call,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = research_pipeline_status_payload(task_id)
    planning_service = ResearchPlanningService()
    plan = planning_service.get_latest_plan(task_id)
    payload.update(
        {
            "research_plan": (
                planning_service.get_payload(task_id) if plan is not None else None
            ),
            "pipeline_started": enum_value(run.status) != "completed",
            "message": (
                "统一 Harness 已接管自动流程：Planner → Research → Analyst → "
                "Citation → Writer → Reviewer → QualityGate。"
            ),
        }
    )
    return payload


@app.get("/api/analysis-tasks/{task_id}/pipeline/run")
def get_research_pipeline_run(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    return research_pipeline_status_payload(task_id)


@app.post("/api/analysis-tasks/{task_id}/pipeline/run/resume", status_code=202)
def resume_research_pipeline(
    task_id: str,
    request: StartResearchPipelineRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        get_research_pipeline_harness().submit(
            task_id,
            mode=request.mode,
            acknowledge_real_llm_call=request.acknowledge_real_llm_call,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return research_pipeline_status_payload(task_id)


@app.post("/api/analysis-tasks/{task_id}/pipeline/run/stop")
def stop_research_pipeline(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        run = get_research_pipeline_harness().request_stop(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = research_pipeline_status_payload(task_id)
    payload["stop_requested"] = enum_value(run.status) in {"stopping", "stopped"}
    return payload


@app.get("/api/analysis-tasks/{task_id}/pipeline/run/events")
def get_research_pipeline_events(
    task_id: str,
    after: int = 0,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    events = get_research_pipeline_harness().get_events(
        task_id,
        after=max(after, 0),
    )
    return {"events": [item.model_dump(mode="json") for item in events]}


@app.get("/api/analysis-tasks/{task_id}/pipeline/run/events/stream")
async def stream_research_pipeline_events(
    task_id: str,
    after: int = 0,
) -> StreamingResponse:
    validate_path_segment(task_id, "task_id")
    harness = get_research_pipeline_harness()

    async def generate():
        cursor = max(after, 0)
        idle_ticks = 0
        harness.reconcile_interrupted(task_id)
        while True:
            harness.sync_research_events(task_id)
            events = harness.get_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                yield (
                    f"id: {event.sequence}\n"
                    "event: pipeline\n"
                    f"data: {payload}\n\n"
                )
                idle_ticks = 0

            latest = harness.get_latest_run(task_id)
            if (
                latest
                and enum_value(latest.status)
                in {"completed", "failed", "stopped", "interrupted"}
                and not events
            ):
                break
            if latest is None and idle_ticks >= 4:
                payload = json.dumps(
                    {"message": "该任务尚未启动统一多 Agent Pipeline。"},
                    ensure_ascii=False,
                )
                yield f"event: unavailable\ndata: {payload}\n\n"
                break
            idle_ticks += 1
            if idle_ticks % 30 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/api/analysis-tasks/{task_id}/research-analysis")
def get_research_analysis(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    return get_research_analysis_service().get_payload(task_id)


@app.get("/api/analysis-tasks/{task_id}/research-agent")
def get_research_agent(
    task_id: str,
    research_task_id: str = "",
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    if research_task_id:
        validate_path_segment(research_task_id, "research_task_id")
    return get_research_evidence_agent_service().get_payload(
        task_id,
        research_task_id,
    )


@app.post("/api/analysis-tasks/{task_id}/research-agent")
def run_research_agent(
    task_id: str,
    request: RunResearchAgentRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    validate_path_segment(request.research_task_id, "research_task_id")
    try:
        return get_research_evidence_agent_service().run_once(
            task_id,
            research_task_id=request.research_task_id,
            mode=request.mode,
            acknowledge_real_llm_call=request.acknowledge_real_llm_call,
            budget=request.budget,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/analysis-tasks/{task_id}/research-analysis")
def run_research_analysis(
    task_id: str,
    request: RunResearchAnalysisRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return get_research_analysis_service().run_once(
            task_id,
            mode=request.mode,
            acknowledge_real_llm_call=request.acknowledge_real_llm_call,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ResearchAnalysisOutputTruncatedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/analysis-tasks/{task_id}/research-reporting")
def get_research_reporting(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return get_research_reporting_service().get_payload(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/analysis-tasks/{task_id}/research-reporting")
def run_research_reporting(
    task_id: str,
    request: RunResearchReportingRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        return get_research_reporting_service().run(
            task_id,
            mode=request.mode,
            acknowledge_real_llm_call=request.acknowledge_real_llm_call,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def execution_status_payload(task_id: str) -> dict[str, Any]:
    runner = get_execution_runner()
    run = runner.reconcile_interrupted(task_id)
    events = runner.get_events(task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="该任务尚未启动后台执行。")
    return {
        "execution_run": run.model_dump(mode="json"),
        "events": [item.model_dump(mode="json") for item in events],
        "terminal": run.status in {"completed", "failed"},
    }


@app.post("/api/analysis-tasks/{task_id}/execute", status_code=202)
def start_task_execution(
    task_id: str,
    request: StartExecutionRequest,
) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    try:
        run = get_execution_runner().submit(task_id, mode=request.mode)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "execution_run": run.model_dump(mode="json"),
        "execution_started": True,
        "message": "后台执行已启动；请通过 SSE（服务器发送事件）观察实时进度。",
    }


@app.get("/api/analysis-tasks/{task_id}/execution")
def get_task_execution(task_id: str) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    return execution_status_payload(task_id)


@app.get("/api/analysis-tasks/{task_id}/events")
def get_task_execution_events(task_id: str, after: int = 0) -> dict[str, Any]:
    validate_path_segment(task_id, "task_id")
    events = get_execution_runner().get_events(task_id, after=max(after, 0))
    return {"events": [item.model_dump(mode="json") for item in events]}


@app.get("/api/analysis-tasks/{task_id}/events/stream")
async def stream_task_execution_events(
    task_id: str,
    after: int = 0,
) -> StreamingResponse:
    validate_path_segment(task_id, "task_id")
    runner = get_execution_runner()

    async def generate():
        cursor = max(after, 0)
        idle_ticks = 0
        runner.reconcile_interrupted(task_id)
        while True:
            events = runner.get_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                yield f"id: {event.sequence}\nevent: execution\ndata: {payload}\n\n"
                idle_ticks = 0
            latest = runner.get_latest_run(task_id)
            if latest and latest.status in {"completed", "failed"} and not events:
                break
            if latest is None and idle_ticks >= 4:
                payload = json.dumps(
                    {"message": "该任务尚未启动后台执行。"},
                    ensure_ascii=False,
                )
                yield f"event: unavailable\ndata: {payload}\n\n"
                break
            idle_ticks += 1
            if idle_ticks % 30 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/experiments/step6c/latest")
def get_latest_step6c_experiment() -> dict[str, Any]:
    summaries = iter_step6c_experiment_summaries()
    for summary_path, summary in summaries:
        results = summary.get("results") or []
        if not isinstance(results, list):
            continue
        preferred = str(
            summary.get("selected_variant")
            or summary.get("preferred_variant_for_next_pilot")
            or "v2_candidate"
        )
        ordered = sorted(
            (item for item in results if isinstance(item, dict)),
            key=lambda item: item.get("variant") == preferred,
            reverse=True,
        )
        for result in ordered:
            task_id = str(result.get("task_id") or "")
            if task_id and (STEP6C_EXPERIMENT_ROOT / task_id).is_dir():
                return load_step6c_experiment_detail(summary_path, summary, result)
    raise HTTPException(status_code=404, detail="No readable Step 6C experiment found")


@app.get("/api/runs/{run_id}/tasks/{task_id}/dashboard")
def get_run_dashboard(run_id: str, task_id: str) -> dict[str, Any]:
    task_dir = resolve_run_task_dir(run_id, task_id)

    def list_artifact(name: str) -> list[dict[str, Any]]:
        return load_list_from_dir(task_dir, name)

    def optional_list_artifact(name: str) -> list[dict[str, Any]]:
        return optional_list_from_dir(task_dir, name)

    summary = latest_item(list_artifact("pipeline_summary"), "pipeline_summary")
    reports = list_artifact("reports")
    reviews = list_artifact("review_feedback")
    working_memories = list_artifact("working_memory")
    eval_summary = load_json_from_dir(task_dir, "eval_summary")
    if not isinstance(eval_summary, dict):
        raise HTTPException(
            status_code=500,
            detail="Artifact must be an object: eval_summary.json",
        )
    return {
        "run": build_run_descriptor(run_id, task_id, task_dir),
        "taskId": task_id,
        "summary": summary,
        "sources": list_artifact("sources"),
        "evidence": list_artifact("evidence"),
        "productCards": list_artifact("product_cards"),
        "claims": list_artifact("claims"),
        "citationChecks": list_artifact("citation_checks"),
        "report": latest_item(reports, "reports"),
        "review": latest_item(reviews, "review_feedback"),
        "trace": {
            "task_id": task_id,
            "dag_nodes": list_artifact("dag_nodes"),
            "agent_runs": list_artifact("agent_runs"),
            "tool_calls": list_artifact("tool_calls"),
        },
        "evalSummary": eval_summary,
        "taskBoard": latest_item(list_artifact("task_board"), "task_board"),
        "taskRecords": list_artifact("task_records"),
        "qualityGates": list_artifact("quality_gates"),
        "feedbackTasks": list_artifact("feedback_tasks"),
        "contextBundles": list_artifact("context_bundles"),
        "workingMemory": latest_item(working_memories, "working_memory"),
        "memoryItems": list_artifact("memory_items"),
        "guardrailChecks": list_artifact("guardrail_checks"),
        "llmCalls": list_artifact("llm_calls"),
        "llmOutputs": list_artifact("llm_outputs"),
        "analysisPortfolios": optional_list_artifact("analysis_portfolios"),
        "briefAssessments": optional_list_artifact("brief_assessments"),
        "competitorProfiles": optional_list_artifact("competitor_profiles"),
        "intelligenceQuestions": optional_list_artifact("intelligence_questions"),
        "informationNeeds": optional_list_artifact("information_needs"),
        "evidenceCoverage": optional_list_artifact("evidence_coverage"),
        "comparabilityNotes": optional_list_artifact("comparability_notes"),
        "claimsV2": optional_list_artifact("claims_v2"),
        "researchGaps": optional_list_artifact("research_gaps"),
        "reportStatements": optional_list_artifact("report_statements"),
    }


@app.get("/api/tasks/{task_id}/artifacts")
def list_task_artifacts(task_id: str) -> dict[str, Any]:
    task_dir = get_store().root_dir / task_id
    if not task_dir.exists() or not task_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    artifacts = []
    for path in sorted(task_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        count = len(data) if isinstance(data, list) else 1
        artifacts.append(
            {
                "artifact_type": path.stem,
                "file_name": path.name,
                "count": count,
            }
        )
    return {"task_id": task_id, "artifacts": artifacts}


@app.get("/api/tasks/{task_id}/summary")
def get_summary(task_id: str) -> dict[str, Any]:
    summaries = load_list_artifact(task_id, "pipeline_summary")
    return latest_item(summaries, "pipeline_summary")


@app.get("/api/tasks/{task_id}/sources")
def get_sources(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "sources")


@app.get("/api/tasks/{task_id}/evidence")
def get_evidence(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "evidence")


@app.get("/api/tasks/{task_id}/product-cards")
def get_product_cards(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "product_cards")


@app.get("/api/tasks/{task_id}/claims")
def get_claims(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "claims")


@app.get("/api/tasks/{task_id}/citation-checks")
def get_citation_checks(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "citation_checks")


@app.get("/api/tasks/{task_id}/task-board")
def get_task_board(task_id: str) -> dict[str, Any]:
    boards = load_list_artifact(task_id, "task_board")
    return latest_item(boards, "task_board")


@app.get("/api/tasks/{task_id}/task-records")
def get_task_records(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "task_records")


@app.get("/api/tasks/{task_id}/quality-gates")
def get_quality_gates(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "quality_gates")


@app.get("/api/tasks/{task_id}/feedback-tasks")
def get_feedback_tasks(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "feedback_tasks")


@app.get("/api/tasks/{task_id}/context-bundles")
def get_context_bundles(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "context_bundles")


@app.get("/api/tasks/{task_id}/working-memory")
def get_working_memory(task_id: str) -> dict[str, Any]:
    memories = load_list_artifact(task_id, "working_memory")
    return latest_item(memories, "working_memory")


@app.get("/api/tasks/{task_id}/memory-items")
def get_memory_items(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "memory_items")


@app.get("/api/tasks/{task_id}/guardrail-checks")
def get_guardrail_checks(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "guardrail_checks")


@app.get("/api/tasks/{task_id}/llm-calls")
def get_llm_calls(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "llm_calls")


@app.get("/api/tasks/{task_id}/llm-outputs")
def get_llm_outputs(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "llm_outputs")


@app.get("/api/tasks/{task_id}/analysis-portfolios")
def get_analysis_portfolios(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "analysis_portfolios")


@app.get("/api/tasks/{task_id}/brief-assessments")
def get_brief_assessments(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "brief_assessments")


@app.get("/api/tasks/{task_id}/competitor-profiles")
def get_competitor_profiles(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "competitor_profiles")


@app.get("/api/tasks/{task_id}/intelligence-questions")
def get_intelligence_questions(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "intelligence_questions")


@app.get("/api/tasks/{task_id}/information-needs")
def get_information_needs(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "information_needs")


@app.get("/api/tasks/{task_id}/evidence-coverage")
def get_evidence_coverage(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "evidence_coverage")


@app.get("/api/tasks/{task_id}/comparability-notes")
def get_comparability_notes(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "comparability_notes")


@app.get("/api/tasks/{task_id}/claims-v2")
def get_claims_v2(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "claims_v2")


@app.get("/api/tasks/{task_id}/research-gaps")
def get_research_gaps(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "research_gaps")


@app.get("/api/tasks/{task_id}/report-statements")
def get_report_statements(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "report_statements")


@app.get("/api/tasks/{task_id}/web-pages")
def get_web_pages(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "web_pages")


@app.get("/api/tasks/{task_id}/collection-attempts")
def get_collection_attempts(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "collection_attempts")


@app.get("/api/tasks/{task_id}/search-attempts")
def get_search_attempts(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "search_attempts")


@app.get("/api/tasks/{task_id}/web-search-results")
def get_web_search_results(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "web_search_results")


@app.get("/api/tasks/{task_id}/evidence-extraction-attempts")
def get_evidence_extraction_attempts(task_id: str) -> list[dict[str, Any]]:
    return load_list_artifact(task_id, "evidence_extraction_attempts")


@app.get("/api/tasks/{task_id}/report")
def get_report(task_id: str) -> dict[str, Any]:
    reports = load_list_artifact(task_id, "reports")
    return latest_item(reports, "reports")


@app.get("/api/tasks/{task_id}/review")
def get_review(task_id: str) -> dict[str, Any]:
    reviews = load_list_artifact(task_id, "review_feedback")
    return latest_item(reviews, "review_feedback")


@app.get("/api/tasks/{task_id}/trace")
def get_trace(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "dag_nodes": load_list_artifact(task_id, "dag_nodes"),
        "agent_runs": load_list_artifact(task_id, "agent_runs"),
        "tool_calls": load_list_artifact(task_id, "tool_calls"),
    }


@app.get("/api/tasks/{task_id}/eval")
def get_eval(task_id: str) -> dict[str, Any]:
    data = load_json_artifact(task_id, "eval_summary")
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="Artifact must be an object: eval_summary.json",
        )
    return data


if (FRONTEND_DIR / "src").exists():
    app.mount("/src", StaticFiles(directory=FRONTEND_DIR / "src"), name="frontend-src")
