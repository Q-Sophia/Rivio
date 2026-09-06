from __future__ import annotations

import asyncio
import os

from mcp import ClientSession
from mcp.client.sse import sse_client


ZHIHU_MCP_SSE_URL = (
    "https://developer.zhihu.com/api/mcp/zhihu_search/v1/sse"
)
ZHIHU_API_KEY_ENV = "ZHIHU_API_KEY"


async def main() -> None:
    access_secret = os.getenv(
        ZHIHU_API_KEY_ENV , ""
    ).strip()

    if not access_secret:
        raise RuntimeError(
            f"缺少环境变量 {ZHIHU_API_KEY_ENV }"
        )

    headers = {
        "Authorization": f"Bearer {access_secret}",
    }

    print("1. connecting to Zhihu Remote MCP...")

    async with sse_client(
        ZHIHU_MCP_SSE_URL,
        headers=headers,
        timeout=10.0,
        sse_read_timeout=60.0,
    ) as (read_stream, write_stream):

        print("2. SSE connected")

        async with ClientSession(
            read_stream,
            write_stream,
        ) as session:

            print("3. initializing MCP session...")
            initialize_result = await session.initialize()

            print(
                "   server =",
                getattr(
                    initialize_result,
                    "serverInfo",
                    None,
                ),
            )

            print("4. listing tools...")
            tools_result = await session.list_tools()

            tool_names = [
                tool.name
                for tool in tools_result.tools
            ]

            print("   tools =", tool_names)

            if "zhihu_search" not in tool_names:
                raise RuntimeError(
                    "知乎 MCP Server 未注册 zhihu_search Tool"
                )

            print("5. calling zhihu_search...")

            result = await session.call_tool(
                "zhihu_search",
                arguments={
                    "query": "ClassIn 用户评价",
                    "count": 3,
                },
            )

            print("6. tool call completed")
            print("   is_error =", result.is_error)
            print("   structured_content =", result.structured_content)

            print("\n===== MCP CONTENT =====")

            for index, item in enumerate(
                result.content,
                start=1,
            ):
                print(
                    f"[{index}] type="
                    f"{type(item).__name__}"
                )

                text = getattr(item, "text", None)
                if text is not None:
                    print(text)
                else:
                    print(item)

            if result.is_error:
                raise RuntimeError(
                    "zhihu_search 返回 MCP Tool Error"
                )

    print("\ncheck_zhihu_remote_mcp_smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())