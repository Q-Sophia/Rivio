from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server import MCPServer

from app.tools.search_provider import SearchProvider, build_search_provider_from_env
from app.tools.search_transport import WebSearchResultItem, WebSearchToolResult


LOGGER = logging.getLogger("research-tools")


def create_research_tools_server(
    *,
    search_provider: SearchProvider | None = None,
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING",
) -> MCPServer:
    state: dict[str, SearchProvider | None] = {"provider": search_provider}

    def provider() -> SearchProvider:
        current = state["provider"]
        if current is None:
            current = build_search_provider_from_env()
            state["provider"] = current
        if current is None:
            raise RuntimeError("research-tools MCP Server 未配置可用 SearchProvider。")
        return current

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            current = state["provider"]
            close = getattr(current, "close", None)
            if callable(close):
                close()

    server = MCPServer(
        name="research-tools",
        description="Competitive-intelligence research tools",
        lifespan=lifespan,
        log_level=log_level,
    )

    @server.tool(
        name="web_search",
        description="Search the web through the configured existing SearchProvider.",
        structured_output=True,
    )
    def web_search(
        query: str,
        limit: int = 5,
        domain_filter: str = "",
    ) -> WebSearchToolResult:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("query 不能为空")
        bounded_limit = max(1, min(limit, 50))
        active_provider = provider()
        hits = active_provider.search(
            normalized_query,
            count=bounded_limit,
            domain_filter=domain_filter,
        )
        return WebSearchToolResult(
            provider=active_provider.name,
            query=normalized_query,
            results=[
                WebSearchResultItem(
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                    site_name=item.site_name,
                    published_at=item.published_at,
                    source_type=item.source_type,
                    metadata=dict(item.metadata or {}),
                )
                for item in hits
            ],
        )

    return server


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    create_research_tools_server().run("stdio")


if __name__ == "__main__":
    main()
