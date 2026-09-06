from __future__ import annotations

import asyncio
import os
import re
import html
from typing import Any, Callable
import logging

from mcp import ClientSession
from mcp.client.sse import sse_client

from app.tools.schemas import ToolResult
from app.tools.mcp_adapter import MCPToolAdapter
from anyio import BrokenResourceError, ClosedResourceError



ZHIHU_MCP_SSE_URL = (
    "https://developer.zhihu.com/api/mcp/zhihu_search/v1/sse"
)

ZHIHU_API_KEY_ENV = "ZHIHU_API_KEY"

from app.tools.router import ZHIHU_SEARCH_TOOL


_ZHIHU_COMMUNITY_DIMENSIONS = {
    "customer",
    "user_feedback",
    "customer_feedback",
    "experience",
    "usage",
}
_ZHIHU_OFFICIAL_FIRST_DIMENSIONS = {
    "positioning",
    "feature",
    "pricing",
    "ecosystem",
}
_ZHIHU_COMMUNITY_MARKERS = (
    "用户评价",
    "用户反馈",
    "客户反馈",
    "用户体验",
    "使用体验",
)
_ZHIHU_QUERY_SUFFIXES = (
    "好用吗",
    "使用体验",
    "真实感受",
    "怎么样",
    "用户评价",
)


def _normalize_research_label(value: str) -> str:
    return "_".join(
        str(value or "")
        .strip()
        .casefold()
        .replace("-", "_")
        .split()
    )


def should_expand_zhihu_query(
    *,
    dimension: str = "",
    research_intent: str = "",
) -> bool:
    """Return whether a task should use community-oriented Zhihu queries."""

    normalized_dimension = _normalize_research_label(dimension)
    normalized_intent = _normalize_research_label(research_intent)

    if normalized_dimension in _ZHIHU_OFFICIAL_FIRST_DIMENSIONS:
        return False

    if (
        normalized_dimension in _ZHIHU_COMMUNITY_DIMENSIONS
        or normalized_intent in _ZHIHU_COMMUNITY_DIMENSIONS
    ):
        return True

    compact_dimension = normalized_dimension.replace("_", "")
    return any(
        marker in compact_dimension
        for marker in _ZHIHU_COMMUNITY_MARKERS
    )


def expand_zhihu_search_queries(
    original_query: str,
    *,
    competitor: str = "",
    dimension: str = "",
    research_intent: str = "",
) -> list[str]:
    """Deterministically adapt community tasks without changing Web queries."""

    normalized_query = " ".join(str(original_query or "").split())
    if not normalized_query:
        raise ValueError("知乎搜索 query 不能为空。")

    if not should_expand_zhihu_query(
        dimension=dimension,
        research_intent=research_intent,
    ):
        return [normalized_query]

    subject = " ".join(str(competitor or "").split())
    if not subject:
        return [normalized_query]

    return list(
        dict.fromkeys(
            f"{subject} {suffix}"
            for suffix in _ZHIHU_QUERY_SUFFIXES
        )
    )

class _BenignMCPSSETeardownFilter(logging.Filter):
    """Suppress only known MCP SSE shutdown noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "mcp.client.sse":
            return True

        if record.getMessage() != "Error in sse_reader":
            return True

        exc_info = record.exc_info
        if not exc_info:
            return True

        exc = exc_info[1]

        return not isinstance(
            exc,
            (BrokenResourceError, ClosedResourceError),
        )

# 就放这里：Filter 类定义完以后，ZhihuRemoteMCPClient 类之前
_mcp_sse_logger = logging.getLogger("mcp.client.sse")

if not any(
    isinstance(item, _BenignMCPSSETeardownFilter)
    for item in _mcp_sse_logger.filters
):
    _mcp_sse_logger.addFilter(
        _BenignMCPSSETeardownFilter()
    )


class ZhihuRemoteMCPClient:
    """
    知乎官方 Remote MCP Client。

    职责仅包括：
    1. 建立 SSE MCP Session
    2. initialize
    3. tools/list 验证 zhihu_search
    4. tools/call
    5. 将知乎 XML TextContent 归一化为 ToolResult

    不负责：
    - Research Agent 路由
    - Source Quality
    - Evidence Verification
    - Web Search
    """

    server_name = "zhihu"

    def __init__(
        self,
        *,
        access_key: str | None = None,
        sse_url: str = ZHIHU_MCP_SSE_URL,
        timeout: float = 10.0,
        sse_read_timeout: float = 60.0,
    ):
        self.access_key = (
            access_key
            or os.getenv(ZHIHU_API_KEY_ENV, "")
        ).strip()

        if not self.access_key:
            raise RuntimeError(
                f"缺少环境变量 {ZHIHU_API_KEY_ENV}"
            )

        self.sse_url = sse_url
        self.timeout = timeout
        self.sse_read_timeout = sse_read_timeout
        self.closed = False

    def search(
        self,
        query: str,
        *,
        count: int = 5,
    ) -> list[ToolResult]:
        if self.closed:
            raise RuntimeError("Zhihu Remote MCP Client 已关闭。")

        normalized_query = " ".join(query.split())

        if not normalized_query:
            raise ValueError("知乎搜索 query 不能为空。")

        try:
            return asyncio.run(
                self._search_async(
                    query=normalized_query,
                    count=max(1, count),
                )
            )
        except BaseExceptionGroup as exc:
            leaf = _first_exception(exc)
            raise RuntimeError(
                f"Zhihu Remote MCP 调用失败：{leaf}"
            ) from exc

    def invoke(
        self,
        arguments: dict[str, Any],
    ) -> list[ToolResult]:
        """
        给 MCPToolAdapter 使用的统一入口。
        """

        return self.search(
            str(arguments.get("query") or ""),
            count=int(arguments.get("count") or 5),
        )

    async def _search_async(
            self,
            *,
            query: str,
            count: int,
    ) -> list[ToolResult]:
        headers = {
            "Authorization": f"Bearer {self.access_key}",
        }

        parsed_results: list[ToolResult] | None = None

        try:
            async with sse_client(
                    self.sse_url,
                    headers=headers,
                    timeout=self.timeout,
                    sse_read_timeout=self.sse_read_timeout,
            ) as (read_stream, write_stream):

                async with ClientSession(
                        read_stream,
                        write_stream,
                ) as session:

                    await session.initialize()

                    tools_result = await session.list_tools()

                    if not any(
                            tool.name == ZHIHU_SEARCH_TOOL
                            for tool in tools_result.tools
                    ):
                        raise RuntimeError(
                            "知乎 MCP Server 未注册 zhihu_search Tool。"
                        )

                    result = await session.call_tool(
                        ZHIHU_SEARCH_TOOL,
                        arguments={
                            "query": query,
                            "count": count,
                        },
                    )

                    if result.is_error:
                        raise RuntimeError(
                            "知乎 zhihu_search Tool 返回错误："
                            + _tool_error_summary(result.content)
                        )

                    text_parts: list[str] = []

                    for item in result.content:
                        text = getattr(item, "text", None)
                        if text:
                            text_parts.append(str(text))

                    if not text_parts:
                        parsed_results = []
                    else:
                        parsed_results = _parse_zhihu_search_xml(
                            "\n".join(text_parts)
                        )

        except BaseExceptionGroup as exc:
            if (
                    parsed_results is not None
                    and _only_benign_sse_teardown(exc)
            ):
                return parsed_results

            raise

        return parsed_results or []

    def close(self) -> None:
        # 每次 search 都拥有独立的 bounded SSE / ClientSession context，
        # 离开 async with 后连接已经释放。
        self.closed = True


def build_zhihu_mcp_tool_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "需要在知乎检索的研究问题或关键词。",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "default": 5,
                "description": "返回结果数量。",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


class ZhihuSearchMCPAdapter(MCPToolAdapter):
    """Zhihu adapter with deterministic community-query expansion."""

    def __init__(
        self,
        *,
        invoke: Callable[[dict[str, Any]], list[ToolResult]],
    ):
        super().__init__(
            name=ZHIHU_SEARCH_TOOL,
            description=(
                "Search Zhihu through the official remote MCP service "
                "for third-party discussions, articles, opinions, "
                "and experience-related research material."
            ),
            input_schema=build_zhihu_mcp_tool_input_schema(),
            invoke=invoke,
        )

    def execute(
        self,
        *,
        query: str,
        count: int = 5,
        competitor: str = "",
        dimension: str = "",
        research_intent: str = "",
        task_id: str = "",
        **_kwargs: Any,
    ) -> list[ToolResult]:
        del task_id
        original_query = " ".join(str(query or "").split())
        expanded_queries = expand_zhihu_search_queries(
            original_query,
            competitor=competitor,
            dimension=dimension,
            research_intent=research_intent,
        )

        merged: list[ToolResult] = []
        seen_urls: set[str] = set()
        expansion_applied = expanded_queries != [original_query]

        for expanded_query in expanded_queries:
            results = super().execute(
                query=expanded_query,
                count=count,
            )
            for item in results:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                merged.append(
                    item.model_copy(
                        update={
                            "metadata": {
                                **dict(item.metadata),
                                "zhihu_original_query": original_query,
                                "zhihu_expanded_query": expanded_query,
                                "zhihu_query_expansion_applied": (
                                    expansion_applied
                                ),
                            }
                        }
                    )
                )

        return merged


def _parse_zhihu_search_xml(
    raw_text: str,
) -> list[ToolResult]:
    """
    解析知乎 Remote MCP 返回的 pseudo-XML。

    注意：
    知乎返回内容虽然使用 <zhihu_search>/<search_item> 标签，
    但属性值中可能包含未转义的 '&' 等字符，因此不能使用
    xml.etree.ElementTree 作为严格 XML 解析器。

    这里仅解析我们真正需要的 search_item 边界与属性。
    """

    item_pattern = re.compile(
        r"<search_item\b(?P<attrs>[^>]*)>"
        r"(?P<body>.*?)"
        r"</search_item>",
        flags=re.DOTALL,
    )

    attr_pattern = re.compile(
        r'([A-Za-z_][\w:-]*)="([^"]*)"'
    )

    results: list[ToolResult] = []

    for match in item_pattern.finditer(raw_text):
        raw_attrs = match.group("attrs")
        raw_body = match.group("body")

        attrs = {
            key: html.unescape(value).strip()
            for key, value in attr_pattern.findall(raw_attrs)
        }

        url = attrs.get("url", "").strip()

        if not url:
            continue

        title = attrs.get("title", "").strip()

        content = html.unescape(raw_body).strip()

        metadata = {
            "channel": "zhihu",
            "content_type": attrs.get("content_type", ""),
            "author_name": attrs.get("author_name", ""),
            "author_avatar": attrs.get("author_avatar", ""),
            "author_badge_text": attrs.get("author_badge_text", ""),
            "edit_time": attrs.get("edit_time", ""),
            "authority_level": attrs.get("authority_level", ""),
            "ranking_score": attrs.get("ranking_score", ""),
            "transport": "remote_mcp_sse",
            "mcp_server": "zhihu",
            "mcp_tool": ZHIHU_SEARCH_TOOL,
        }

        results.append(
            ToolResult(
                title=title,
                url=url,
                content=content,
                provider="zhihu_mcp",
                source_type="general_third_party",
                metadata=metadata,
            )
        )

    if not results:
        raise RuntimeError(
            "知乎 MCP 返回内容中未解析出任何 search_item。"
        )

    return results

def _tool_error_summary(
    content: list[object],
) -> str:
    values = [
        str(getattr(item, "text", "")).strip()
        for item in content
    ]

    summary = "; ".join(
        value for value in values if value
    )

    return summary[:500] or "unknown MCP tool error"


def _first_exception(
    error: BaseException,
) -> BaseException:
    if isinstance(error, BaseExceptionGroup):
        for item in error.exceptions:
            return _first_exception(item)

    return error

def build_zhihu_remote_mcp_client_from_env(
) -> ZhihuRemoteMCPClient | None:
    access_key = os.getenv(
        ZHIHU_API_KEY_ENV,
        "",
    ).strip()

    if not access_key:
        return None

    return ZhihuRemoteMCPClient(
        access_key=access_key,
    )

def _only_benign_sse_teardown(
    error: BaseException,
) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _only_benign_sse_teardown(item)
            for item in error.exceptions
        )

    return isinstance(
        error,
        (
            BrokenResourceError,
            ClosedResourceError,
        ),
    )
