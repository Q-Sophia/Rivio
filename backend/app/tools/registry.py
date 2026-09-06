from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.schemas import RunStatus, SchemaModel, utc_now
from app.tools.base import ResearchTool

if TYPE_CHECKING:
    from app.workflow.trace import TraceRecorder

ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: ToolHandler
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def execute(self, **kwargs: Any) -> Any:
        return self.handler(**kwargs)


class ToolRegistry:
    """Small tool registry with ToolCall trace recording.

    The shape follows the reference projects' tool dispatch pattern, but this
    version is deliberately local-only and deterministic.
    """

    def __init__(self, *, recorder: TraceRecorder | None = None):
        self.recorder = recorder
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        tool_name: str | ResearchTool,
        handler: ToolHandler | None = None,
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(tool_name, ResearchTool):
            tool = tool_name
            definition = ToolDefinition(
                name=tool.name,
                handler=tool.execute,
                description=tool.description,
                input_schema=dict(tool.input_schema),
            )
        else:
            if handler is None:
                raise ValueError("注册函数型 Tool 时 handler 不能为空")
            definition = ToolDefinition(
                name=tool_name,
                handler=handler,
                description=description,
                input_schema=input_schema or {},
            )
        self._tools[definition.name] = definition

    def get(self, tool_name: str) -> ToolDefinition:
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ValueError(f"Unknown tool: {tool_name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def call(
        self,
        tool_name: str,
        *,
        agent_run_id: str,
        task_id: str,
        output_summary: str = "",
        **kwargs: Any,
    ) -> Any:
        started_at = utc_now()
        start = time.perf_counter()
        tool = self.get(tool_name)
        try:
            result = tool.execute(task_id=task_id, **kwargs)
        except Exception as exc:
            completed_at = utc_now()
            error = f"{type(exc).__name__}: {exc}"
            self._record(
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                input_data={"task_id": task_id, **self._summarize_kwargs(kwargs)},
                output_summary=output_summary,
                status=RunStatus.FAILED,
                error=error,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            raise

        completed_at = utc_now()
        self._record(
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            input_data={"task_id": task_id, **self._summarize_kwargs(kwargs)},
            output_summary=output_summary or self._summarize_result(result),
            status=RunStatus.COMPLETED,
            error="",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return result

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.list_tools()
        ]

    def _record(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        output_summary: str,
        status: RunStatus,
        error: str,
        started_at,
        completed_at,
        duration_ms: int,
    ) -> None:
        if not self.recorder:
            return
        self.recorder.record_tool_call(
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            input_data=input_data,
            output_summary=output_summary,
            status=status,
            error=error,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    @classmethod
    def _summarize_kwargs(cls, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {key: cls._summarize_value(value) for key, value in kwargs.items()}

    @classmethod
    def _summarize_value(cls, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, SchemaModel):
            return {"schema": type(value).__name__, "id": getattr(value, "id", "")}
        if isinstance(value, list):
            if not value:
                return {"type": "list", "count": 0}
            return {
                "type": "list",
                "count": len(value),
                "item_type": type(value[0]).__name__,
            }
        if isinstance(value, tuple):
            return {
                "type": "tuple",
                "count": len(value),
            }
        if isinstance(value, dict):
            if all(isinstance(item, list) for item in value.values()):
                return {
                    key: {"type": "list", "count": len(item)}
                    for key, item in value.items()
                }
            return {"type": "dict", "keys": sorted(str(key) for key in value.keys())}
        return value

    @classmethod
    def _summarize_result(cls, result: Any) -> str:
        if isinstance(result, dict):
            if all(isinstance(item, list) for item in result.values()):
                total = sum(len(item) for item in result.values())
                return (
                    f"Returned {len(result)} artifact groups, total_items={total}"
                )
            return "Returned dict with keys: " + ", ".join(
                sorted(str(key) for key in result.keys())
            )
        if isinstance(result, list):
            return f"Returned {len(result)} items"
        if isinstance(result, tuple):
            return f"Returned tuple with {len(result)} items"
        if isinstance(result, SchemaModel):
            return f"Returned {type(result).__name__} {getattr(result, 'id', '')}"
        return str(result)[:200]
