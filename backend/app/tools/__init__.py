"""Research Tool 核心公共接口。"""

__all__ = [
    "MCPToolAdapter",
    "ResearchTool",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "resolve_tools",
    "research_intent_for_dimension",
]


def __getattr__(name: str):
    if name == "MCPToolAdapter":
        from app.tools.mcp_adapter import MCPToolAdapter

        return MCPToolAdapter

    if name in {"ToolDefinition", "ToolRegistry"}:
        from app.tools.registry import (
            ToolDefinition,
            ToolRegistry,
        )

        return {
            "ToolDefinition": ToolDefinition,
            "ToolRegistry": ToolRegistry,
        }[name]

    if name in {
        "resolve_tools",
        "research_intent_for_dimension",
    }:
        from app.tools.router import (
            research_intent_for_dimension,
            resolve_tools,
        )

        return {
            "resolve_tools": resolve_tools,
            "research_intent_for_dimension": research_intent_for_dimension,
        }[name]

    if name == "ResearchTool":
        from app.tools.base import ResearchTool

        return ResearchTool

    if name == "ToolResult":
        from app.tools.schemas import ToolResult

        return ToolResult

    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )