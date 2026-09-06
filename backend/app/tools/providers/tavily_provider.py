from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.tools.schemas import ToolResult


class TavilyProvider:
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com/search",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key.strip():
            raise ValueError("TAVILY_API_KEY 不能为空")
        self.client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
        )
        self.base_url = base_url

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[ToolResult]:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("搜索词不能为空")
        request_body: dict[str, object] = {
            "query": normalized_query,
            "search_depth": "basic",
            "max_results": max(1, min(count, 20)),
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if domain_filter.strip():
            request_body["include_domains"] = [domain_filter.strip()]
        response = self.client.post(self.base_url, json=request_body)
        response.raise_for_status()
        results: list[ToolResult] = []
        for item in response.json().get("results") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            url = str(item["url"]).strip()
            published_at = str(item.get("published_date") or "").strip()
            metadata = {
                "domain": (urlsplit(url).hostname or "").casefold(),
                "site_name": (urlsplit(url).hostname or "").strip(),
            }
            if published_at:
                metadata["published_at"] = published_at
            if item.get("score") is not None:
                metadata["provider_score"] = item["score"]
            results.append(
                ToolResult(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    content=str(item.get("content") or "").strip(),
                    provider=self.name,
                    source_type="general_third_party",
                    metadata=metadata,
                )
            )
        return results

    def close(self) -> None:
        self.client.close()
