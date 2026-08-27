from __future__ import annotations

import json
import os
from pathlib import Path

from app.mcp.research_tools_server import create_research_tools_server
from app.tools.search_provider import SearchHit


class FakeSearchProvider:
    name = "fake-stdio-search"

    def __init__(self) -> None:
        if os.getenv("MCP_R1_FAKE_REQUIRED_ENV") != "explicit-test-value":
            raise RuntimeError("MCP stdio 子进程未收到显式必需 env。")
        self.call_log = Path(os.environ["MCP_R1_FAKE_CALL_LOG"])
        self.close_marker = Path(os.environ["MCP_R1_FAKE_CLOSE_MARKER"])

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        with self.call_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "query": query,
                        "count": count,
                        "domain_filter": domain_filter,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if query == "FAIL":
            raise RuntimeError("fake provider failure")
        if "用户" in query or "体验" in query:
            return [
                SearchHit(
                    title="Nebula 用户实测与踩坑记录",
                    url="https://forum.example.com/nebula/experience",
                    snippet="用户记录 Nebula 实际使用体验、功能限制和踩坑反馈。",
                    site_name="Developer Forum",
                )
            ][:count]
        return [
            SearchHit(
                title="Nebula Feature Documentation",
                url="https://docs.nebula.example/product/features",
                snippet="Nebula product feature and API documentation.",
                site_name="Nebula Docs",
            )
        ][:count]

    def close(self) -> None:
        self.close_marker.write_text("closed", encoding="utf-8")


def main() -> None:
    create_research_tools_server(
        search_provider=FakeSearchProvider(),
        log_level="CRITICAL",
    ).run("stdio")


if __name__ == "__main__":
    main()
