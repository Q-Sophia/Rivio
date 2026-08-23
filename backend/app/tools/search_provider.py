from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    site_name: str = ""
    published_at: str = ""


class SearchProvider(Protocol):
    name: str

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]: ...


class BochaSearchProvider:
    """Adapter for Bocha's official Web Search API."""

    name = "bocha"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.bochaai.com/v1/web-search",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key.strip():
            raise ValueError("BOCHA_API_KEY 不能为空")
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
    ) -> list[SearchHit]:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("搜索词不能为空")
        response = self.client.post(
            self.base_url,
            json={
                "query": normalized_query,
                "summary": True,
                "count": max(1, min(count, 10)),
            },
        )
        response.raise_for_status()
        payload = response.json()
        values = (payload.get("webPages") or {}).get("value") or []
        hits: list[SearchHit] = []
        for item in values:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            hits.append(
                SearchHit(
                    title=str(item.get("name") or "").strip(),
                    url=str(item["url"]).strip(),
                    snippet=str(item.get("summary") or item.get("snippet") or "").strip(),
                    site_name=str(item.get("siteName") or "").strip(),
                    published_at=str(item.get("datePublished") or "").strip(),
                )
            )
        return hits

    def close(self) -> None:
        self.client.close()


class ZhipuSearchProvider:
    """Adapter for Zhipu's official Web Search API."""

    name = "zhipu"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4/web_search",
        search_engine: str = "search_std",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key.strip():
            raise ValueError("ZHIPU_API_KEY 不能为空")
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
        self.search_engine = search_engine

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("搜索词不能为空")
        if len(normalized_query) > 70:
            normalized_query = normalized_query[:70]
        request_body = {
                "search_query": normalized_query,
                "search_engine": self.search_engine,
                "search_intent": False,
                "count": max(1, min(count, 50)),
                "search_recency_filter": "noLimit",
                "content_size": "medium",
        }
        if domain_filter.strip():
            request_body["search_domain_filter"] = domain_filter.strip()
        response = self.client.post(self.base_url, json=request_body)
        response.raise_for_status()
        values = response.json().get("search_result") or []
        hits: list[SearchHit] = []
        for item in values:
            if not isinstance(item, dict) or not item.get("link"):
                continue
            hits.append(
                SearchHit(
                    title=str(item.get("title") or "").strip(),
                    url=str(item["link"]).strip(),
                    snippet=str(item.get("content") or "").strip(),
                    site_name=str(item.get("media") or "").strip(),
                    published_at=str(item.get("publish_date") or "").strip(),
                )
            )
        return hits

    def close(self) -> None:
        self.client.close()


def _read_user_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value or sys.platform != "win32":
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            stored, _kind = winreg.QueryValueEx(key, name)
        return str(stored).strip()
    except (FileNotFoundError, OSError):
        return ""


def build_search_provider_from_env() -> SearchProvider | None:
    provider = _read_user_environment("SEARCH_PROVIDER").lower()
    bocha_key = _read_user_environment("BOCHA_API_KEY")
    zhipu_key = _read_user_environment("ZHIPU_API_KEY")
    if provider in {"", "auto"}:
        provider = "zhipu" if zhipu_key else ("bocha" if bocha_key else "disabled")
    if provider in {"disabled", "none", "off"}:
        return None
    if provider == "zhipu":
        return ZhipuSearchProvider(zhipu_key) if zhipu_key else None
    if provider == "bocha":
        return BochaSearchProvider(bocha_key) if bocha_key else None
    raise ValueError(f"不支持的 SEARCH_PROVIDER：{provider}")


def get_search_provider_status() -> dict[str, object]:
    """Return non-secret configuration status for the frontend."""
    provider = _read_user_environment("SEARCH_PROVIDER").lower()
    bocha_configured = bool(_read_user_environment("BOCHA_API_KEY"))
    zhipu_configured = bool(_read_user_environment("ZHIPU_API_KEY"))
    if provider in {"", "auto"}:
        provider = "zhipu" if zhipu_configured else ("bocha" if bocha_configured else "disabled")
    configured = (
        (provider == "zhipu" and zhipu_configured)
        or (provider == "bocha" and bocha_configured)
    )
    endpoint = {
        "zhipu": "https://open.bigmodel.cn/api/paas/v4/web_search",
        "bocha": "https://api.bochaai.com/v1/web-search",
    }.get(provider, "")
    return {
        "provider": provider,
        "configured": configured,
        "endpoint": endpoint,
        "purpose": "web_search",
        "transport": "direct",
    }
