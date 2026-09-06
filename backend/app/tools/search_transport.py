from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from app.tools.search_provider import (
    SearchHit,
    SearchProvider,
    _read_user_environment,
    build_search_provider_from_env,
)



SEARCH_TOOL_TRANSPORT_ENV = "SEARCH_TOOL_TRANSPORT"
MCP_SERVER_NAME = "research-tools"
_SEARCH_PROVIDER_ENV_KEYS = (
    "SEARCH_PROVIDER",
    "TAVILY_API_KEY",
    "ZHIPU_API_KEY",
    "BOCHA_API_KEY",
)


class WebSearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    site_name: str = ""
    published_at: str = ""
    source_type: str = "general_third_party"
    metadata: dict[str, object] = Field(default_factory=dict)


class WebSearchToolResult(BaseModel):
    provider: str
    query: str
    results: list[WebSearchResultItem] = Field(default_factory=list)


class SearchToolTransport(Protocol):
    transport: str
    server_name: str

    @property
    def provider_name(self) -> str: ...

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]: ...

    def close(self) -> None: ...


class NativeSearchToolTransport:
    transport = "native"
    server_name = ""

    def __init__(
        self,
        provider: SearchProvider,
        *,
        tool_name: str = "web_search",
    ):
        self.provider = provider
        self.tool_name = tool_name

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def search(
            self,
            query: str,
            *,
            count: int = 5,
            domain_filter: str = "",
    ) -> list[SearchHit]:
        return self.provider.search(
            query,
            count=count,
            domain_filter=domain_filter,
        )

    def close(self) -> None:
        close = getattr(self.provider, "close", None)

        if callable(close):
            close()


class MCPWebSearchTransport:
    transport = "mcp"
    server_name = MCP_SERVER_NAME

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        server_module: str = "app.mcp.research_tools_server",
        cwd: str | Path | None = None,
        server_env: dict[str, str] | None = None,
    ):
        backend_dir = Path(__file__).resolve().parents[2]
        self.server_parameters = StdioServerParameters(
            command=python_executable or sys.executable,
            args=["-m", server_module],
            env=dict(server_env or _search_provider_subprocess_env()),
            cwd=Path(cwd) if cwd is not None else backend_dir,
        )
        self._provider_name = ""
        self.closed = False

    @property
    def provider_name(self) -> str:
        return self._provider_name or "mcp"

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        if self.closed:
            raise RuntimeError("MCP web_search transport 已关闭。")
        try:
            payload = asyncio.run(
                self._call_web_search(
                    query=query,
                    limit=count,
                    domain_filter=domain_filter,
                )
            )
        except BaseExceptionGroup as exc:
            leaf = _first_exception(exc)
            raise RuntimeError(str(leaf)) from exc
        self._provider_name = payload.provider
        return [
            SearchHit(
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                site_name=item.site_name,
                published_at=item.published_at,
                source_type=item.source_type,
                metadata=dict(item.metadata),
            )
            for item in payload.results
        ]

    async def _call_web_search(
        self,
        *,
        query: str,
        limit: int,
        domain_filter: str,
    ) -> WebSearchToolResult:
        async with stdio_client(self.server_parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                if not any(item.name == "web_search" for item in tools.tools):
                    raise RuntimeError(
                        f"MCP Server {self.server_name} 未注册 web_search Tool。"
                    )
                result = await session.call_tool(
                    "web_search",
                    arguments={
                        "query": query,
                        "limit": max(1, min(limit, 50)),
                        "domain_filter": domain_filter,
                    },
                )
                if result.is_error:
                    raise RuntimeError(
                        f"MCP web_search Tool 调用失败：{_tool_error_summary(result.content)}"
                    )
                if not isinstance(result.structured_content, dict):
                    raise RuntimeError("MCP web_search 未返回 structuredContent。")
                return WebSearchToolResult.model_validate(result.structured_content)

    def close(self) -> None:
        # Each synchronous search owns one bounded stdio async context.  Exiting
        # that context has already closed ClientSession and the subprocess.
        self.closed = True


def _tool_error_summary(content: list[object]) -> str:
    values = [str(getattr(item, "text", "")).strip() for item in content]
    summary = "; ".join(item for item in values if item)
    return summary[:500] or "unknown MCP tool error"


def _first_exception(error: BaseException) -> BaseException:
    if isinstance(error, BaseExceptionGroup):
        for item in error.exceptions:
            return _first_exception(item)
    return error


def _search_provider_subprocess_env() -> dict[str, str]:
    return {
        key: value
        for key in _SEARCH_PROVIDER_ENV_KEYS
        if (value := _read_user_environment(key))
    }


def build_search_tool_transport_from_env(
    *,
    tool_name: str = "web_search",
) -> SearchToolTransport | None:

    if tool_name != "web_search":
        raise ValueError(
            "SearchToolTransport 当前只兼容 web_search；"
            "其他独立 Tool 应通过 ToolRegistry + 独立 MCP Client 执行。"
        )

    mode = _read_user_environment(
        SEARCH_TOOL_TRANSPORT_ENV
    ).casefold() or "native"

    if mode == "native":
        provider = build_search_provider_from_env()

        return (
            NativeSearchToolTransport(
                provider,
                tool_name=tool_name,
            )
            if provider is not None
            else None
        )

    if mode == "mcp":
        return MCPWebSearchTransport()

    raise ValueError(
        f"不支持的 {SEARCH_TOOL_TRANSPORT_ENV}：{mode}；"
        "仅支持 native|mcp。"
    )
