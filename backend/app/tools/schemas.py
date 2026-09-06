from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Provider-neutral research lead returned by a discovery tool."""

    title: str = ""
    url: str
    content: str = ""
    provider: str
    source_type: str = "general_third_party"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebSearchToolInput(BaseModel):
    query: str
    count: int = Field(default=5, ge=1, le=50)
    domain_filter: str = ""
