__all__ = ["ToolDefinition", "ToolRegistry"]


def __getattr__(name: str):
    if name in __all__:
        from app.tools.registry import ToolDefinition, ToolRegistry

        return {"ToolDefinition": ToolDefinition, "ToolRegistry": ToolRegistry}[name]
    raise AttributeError(name)
