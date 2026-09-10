from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from app.schemas import ExecutionMode, SchemaModel, new_id, utc_now


PIPELINE_PROTOCOL_VERSION = "1.0"


class PipelineRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PipelineStage(str, Enum):
    PLANNING = "planning"
    RESEARCHING = "researching"
    ANALYZING = "analyzing"
    REPORTING = "reporting"
    COMPLETED = "completed"


class HandoffMessageType(str, Enum):
    ARTIFACT_HANDOFF = "artifact_handoff"
    CONTROL = "control"
    COMPLETION = "completion"
    FEEDBACK = "feedback"


class ArtifactReference(SchemaModel):
    """Immutable reference to a persisted ArtifactStore collection revision."""

    artifact_type: str
    artifact_ids: list[str] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    content_hash: str = ""


class AgentHandoff(SchemaModel):
    """Versioned JSON envelope used between pipeline stages.

    The envelope carries references, not copied webpage/evidence payloads. The
    ArtifactStore remains the source of truth for cross-agent data.
    """

    id: str = Field(default_factory=lambda: new_id("handoff"))
    protocol_version: str = PIPELINE_PROTOCOL_VERSION
    task_id: str
    pipeline_run_id: str
    sender: str
    recipient: str
    message_type: HandoffMessageType = HandoffMessageType.ARTIFACT_HANDOFF
    artifact_refs: list[ArtifactReference] = Field(default_factory=list)
    correlation_id: str = ""
    causation_id: str = ""
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class PipelineRun(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("pipeline"))
    protocol_version: str = PIPELINE_PROTOCOL_VERSION
    task_id: str
    mode: ExecutionMode = ExecutionMode.DEEPSEEK
    real_llm_call_authorized: bool = False
    status: PipelineRunStatus = PipelineRunStatus.QUEUED
    current_stage: PipelineStage = PipelineStage.PLANNING
    progress_percent: int = Field(default=0, ge=0, le=100)
    completed_stages: list[str] = Field(default_factory=list)
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    research_coordinator_run_id: str = ""
    research_event_cursor: int = Field(default=0, ge=0)
    stop_requested: bool = False
    stop_reason: str = ""
    resume_count: int = Field(default=0, ge=0)
    last_checkpoint_id: str = ""
    current_handoff_id: str = ""
    message: str = ""
    error: str = ""
    result_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PipelineCheckpoint(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("checkpoint"))
    protocol_version: str = PIPELINE_PROTOCOL_VERSION
    task_id: str
    pipeline_run_id: str
    sequence: int = Field(ge=1)
    stage: PipelineStage
    status: str = "completed"
    completed_stages: list[str] = Field(default_factory=list)
    artifact_refs: list[ArtifactReference] = Field(default_factory=list)
    state_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class PipelineEvent(SchemaModel):
    id: str = Field(default_factory=lambda: new_id("pipelineevent"))
    protocol_version: str = PIPELINE_PROTOCOL_VERSION
    task_id: str
    pipeline_run_id: str
    sequence: int = Field(ge=1)
    event_type: str
    stage: str = ""
    status: str = ""
    research_task_id: str = ""
    message: str = ""
    progress_percent: int = Field(default=0, ge=0, le=100)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
