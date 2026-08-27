from __future__ import annotations

import json

from app.tools.search_provider import build_search_provider_from_env


def main() -> None:
    provider = build_search_provider_from_env()
    if provider is None or provider.name != "tavily":
        raise RuntimeError("SEARCH_PROVIDER=tavily 且 TAVILY_API_KEY 必须已配置")
    try:
        hits = provider.search("Trae official documentation", count=5)
    finally:
        provider.close()
    if not hits:
        raise AssertionError("Tavily real smoke returned no SearchHit")
    print("check_search_provider_tavily_smoke: PASS")
    print(f"provider={provider.name}")
    print("query=Trae official documentation")
    print("requested_count=5")
    print(f"returned_count={len(hits)}")
    print(
        json.dumps(
            [
                {"title": item.title, "url": item.url}
                for item in hits
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
