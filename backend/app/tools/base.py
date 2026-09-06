from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.tools.schemas import ToolResult, WebSearchToolInput


class ResearchTool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        raise NotImplementedError


class ResearchSearchProvider(Protocol):
    name: str

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[ToolResult]: ...


class WebSearchTool(ResearchTool):
    name = "web_search"
    description = "Search for candidate research URLs through one provider."
    input_schema = WebSearchToolInput.model_json_schema()

    def __init__(self, provider: Any):
        self.provider = provider

    def execute(
        self,
        *,
        query: str,
        count: int = 5,
        domain_filter: str = "",
        task_id: str = "",
        **_kwargs: Any,
    ) -> list[ToolResult]:
        del task_id
        request = WebSearchToolInput(
            query=" ".join(query.split()),
            count=count,
            domain_filter=domain_filter.strip(),
        )
        if not request.query:
            raise ValueError("搜索词不能为空")
        values = self.provider.search(
            request.query,
            count=request.count,
            domain_filter=request.domain_filter,
        )
        results: list[ToolResult] = []
        for item in values:
            if isinstance(item, ToolResult):
                results.append(item)
                continue
            url = str(getattr(item, "url", "") or "").strip()
            if not url:
                continue
            metadata = dict(getattr(item, "metadata", {}) or {})
            site_name = str(getattr(item, "site_name", "") or "").strip()
            published_at = str(
                getattr(item, "published_at", "") or ""
            ).strip()
            if site_name:
                metadata.setdefault("site_name", site_name)
            if published_at:
                metadata.setdefault("published_at", published_at)
            results.append(
                ToolResult(
                    title=str(getattr(item, "title", "") or "").strip(),
                    url=url,
                    content=str(
                        getattr(item, "content", "")
                        or getattr(item, "snippet", "")
                        or ""
                    ).strip(),
                    provider=str(
                        getattr(item, "provider", "")
                        or getattr(self.provider, "name", "unknown")
                    ),
                    source_type=str(
                        getattr(item, "source_type", "")
                        or "general_third_party"
                    ),
                    metadata={
                        "domain": (urlsplit(url).hostname or "").casefold(),
                        **metadata,
                    },
                )
            )
        return results

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()
