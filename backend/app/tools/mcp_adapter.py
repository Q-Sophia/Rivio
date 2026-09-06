from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.tools.base import ResearchTool


class MCPToolAdapter(ResearchTool):
    """Metadata/invocation adapter only; transport and lifecycle stay external."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        invoke: Callable[[dict[str, Any]], Any],
    ):
        if not name.strip():
            raise ValueError("MCP tool name 不能为空")
        self.name = name.strip()
        self.description = description.strip()
        self.input_schema = dict(input_schema)
        self._invoke = invoke

    def execute(self, **kwargs: Any) -> Any:
        arguments = dict(kwargs)
        arguments.pop("task_id", None)
        missing = [
            str(key)
            for key in self.input_schema.get("required", [])
            if key not in arguments
        ]
        if missing:
            raise ValueError(
                "MCP tool 缺少必填参数: " + ", ".join(missing)
            )
        return self._invoke(arguments)
