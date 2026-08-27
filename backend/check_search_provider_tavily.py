from __future__ import annotations

import json
from unittest.mock import patch

import httpx

from app.tools.search_provider import (
    TavilySearchProvider,
    build_search_provider_from_env,
    get_search_provider_status,
)
from app.tools.search_transport import _search_provider_subprocess_env


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "query": "Trae official documentation",
                "results": [
                    {
                        "title": "Trae Documentation",
                        "url": "https://docs.trae.ai/features",
                        "content": "Official Trae product documentation and features.",
                        "score": 0.98,
                    },
                    {"title": "invalid without URL", "content": "ignored"},
                ],
            },
            request=request,
        )

    provider = TavilySearchProvider(
        "fake-tavily-key",
        transport=httpx.MockTransport(handler),
    )
    hits = provider.search(
        "  Trae   official documentation  ",
        count=50,
        domain_filter="docs.trae.ai",
    )
    provider.close()
    body = captured["body"]
    require(captured["url"] == "https://api.tavily.com/search", "Tavily endpoint 错误")
    require(captured["authorization"] == "Bearer fake-tavily-key", "Bearer auth 错误")
    require(body["query"] == "Trae official documentation", "query 映射错误")
    require(body["max_results"] == 20, "count 未按 Tavily 上限映射")
    require(body["include_domains"] == ["docs.trae.ai"], "domain_filter 映射错误")
    require(len(hits) == 1, "无 URL 的 Tavily result 未过滤")
    require(hits[0].title == "Trae Documentation", "title 映射错误")
    require(hits[0].snippet.startswith("Official Trae"), "content 未映射为 snippet")
    require(hits[0].site_name == "docs.trae.ai", "site_name 未从 URL 恢复")

    values = {
        "SEARCH_PROVIDER": "auto",
        "TAVILY_API_KEY": "fake-tavily-key",
        "ZHIPU_API_KEY": "fake-zhipu-key",
        "BOCHA_API_KEY": "fake-bocha-key",
    }
    with patch(
        "app.tools.search_provider._read_user_environment",
        side_effect=lambda key: values.get(key, ""),
    ):
        selected = build_search_provider_from_env()
        status = get_search_provider_status()
    require(isinstance(selected, TavilySearchProvider), "auto/default 没有优先 Tavily")
    selected.close()
    require(status["provider"] == "tavily" and status["configured"], "Tavily status 错误")

    with patch(
        "app.tools.search_transport._read_user_environment",
        side_effect=lambda key: values.get(key, ""),
    ):
        child_env = _search_provider_subprocess_env()
    require(child_env == values, "MCP subprocess env 未显式传递 Tavily key")
    require("PATH" not in child_env and "HOME" not in child_env, "MCP env allowlist 过宽")

    print("check_search_provider_tavily: PASS")
    print("request_mapping=true")
    print("response_mapping=true")
    print("auto_prefers_tavily=true")
    print("mcp_env_allowlist_tavily=true")
    print("real_network_used=false")


if __name__ == "__main__":
    main()
