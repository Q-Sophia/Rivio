from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp.client import Client

from app.collection import CollectorQueueService
from app.harness.artifacts import ArtifactStore
from app.mcp.research_tools_server import create_research_tools_server
from app.schemas import (
    AgentRole,
    ResearchBudget,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    TaskBoard,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from app.tools.search_provider import SearchHit
from app.tools.search_transport import (
    MCPWebSearchTransport,
    _search_provider_subprocess_env,
    build_search_tool_transport_from_env,
)
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool
from app.workflow.taskboard import TaskBoardStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class InMemoryFakeSearchProvider:
    name = "fake-in-memory-search"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.closed = False

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("in-memory fake failure")
        return [
            SearchHit(
                title="Nebula Feature Documentation",
                url="https://docs.nebula.example/product/features",
                snippet="Nebula feature and API documentation.",
                site_name="Nebula Docs",
                published_at="2026-01-01",
            )
        ][:count]

    def close(self) -> None:
        self.closed = True


class NativeFakeSearchProvider:
    name = "fake-native-search"

    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def search(
        self,
        query: str,
        *,
        count: int = 5,
        domain_filter: str = "",
    ) -> list[SearchHit]:
        self.calls += 1
        return [
            SearchHit(
                title="Nebula 用户实测与踩坑记录",
                url="https://forum.example.com/nebula/native-experience",
                snippet="用户记录 Nebula 实际使用体验和功能限制。",
                site_name="Developer Forum",
            )
        ][:count]

    def close(self) -> None:
        self.closed = True


async def check_in_memory_protocol() -> None:
    provider = InMemoryFakeSearchProvider()
    server = create_research_tools_server(
        search_provider=provider,
        log_level="CRITICAL",
    )
    async with Client(server) as client:
        tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "web_search")
        properties = tool.input_schema["properties"]
        require("query" in properties and "limit" in properties, "Tool input schema 缺少 query/limit")
        require(tool.output_schema is not None, "web_search 缺少 structured output schema")
        result = await client.call_tool(
            "web_search",
            {"query": "Nebula feature", "limit": 2},
        )
        require(not result.is_error, "in-memory web_search 返回 MCP error")
        require(isinstance(result.structured_content, dict), "Tool 没有 structuredContent")
        require(result.structured_content["provider"] == provider.name, "provider 字段丢失")
        require(result.structured_content["results"][0]["title"], "结构化结果字段丢失")
    require(provider.calls == 1, "单次 MCP Tool 请求重复调用 SearchProvider")
    require(provider.closed, "in-memory MCP Server lifespan 未关闭 provider")

    failing = InMemoryFakeSearchProvider(fail=True)
    async with Client(
        create_research_tools_server(
            search_provider=failing,
            log_level="CRITICAL",
        )
    ) as client:
        result = await client.call_tool("web_search", {"query": "FAIL", "limit": 1})
        require(result.is_error, "MCP Tool provider 异常未设置 result.is_error")
    require(failing.calls == 1, "错误路径重复调用 provider")


def reset_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def stdio_env(call_log: Path, close_marker: Path) -> dict[str, str]:
    return {
        "MCP_R1_FAKE_REQUIRED_ENV": "explicit-test-value",
        "MCP_R1_FAKE_CALL_LOG": str(call_log),
        "MCP_R1_FAKE_CLOSE_MARKER": str(close_marker),
    }


def read_call_log(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def new_stdio_transport(root: Path, label: str) -> tuple[MCPWebSearchTransport, Path, Path]:
    call_log = root / f"{label}_calls.jsonl"
    close_marker = root / f"{label}_closed.txt"
    reset_log(call_log)
    reset_log(close_marker)
    transport = MCPWebSearchTransport(
        server_module="check_mcp_r1_fake_server",
        server_env=stdio_env(call_log, close_marker),
    )
    require(transport.server_parameters.command == sys.executable, "stdio Server 未使用 sys.executable")
    require(
        set(transport.server_parameters.env or {})
        == {
            "MCP_R1_FAKE_REQUIRED_ENV",
            "MCP_R1_FAKE_CALL_LOG",
            "MCP_R1_FAKE_CLOSE_MARKER",
        },
        "stdio adapter 无差别传入了父进程环境",
    )
    return transport, call_log, close_marker


def check_stdio_adapter(root: Path) -> None:
    transport, call_log, close_marker = new_stdio_transport(root, "adapter_success")
    hits = transport.search("Nebula feature", count=2)
    require(len(hits) == 1 and hits[0].title, "Adapter 未恢复 SearchHit 契约")
    require(transport.provider_name == "fake-stdio-search", "Adapter provider 解析错误")
    require(len(read_call_log(call_log)) == 1, "stdio adapter 重复调用 provider")
    require(close_marker.read_text(encoding="utf-8") == "closed", "stdio subprocess 未正常关闭")
    transport.close()

    failing, fail_log, fail_close = new_stdio_transport(root, "adapter_failure")
    try:
        failing.search("FAIL", count=1)
    except RuntimeError as exc:
        require("MCP web_search Tool 调用失败" in str(exc), "MCP error 语义不明确")
    else:
        raise AssertionError("MCP result.is_error 被静默 fallback")
    require(len(read_call_log(fail_log)) == 1, "失败请求重复调用 provider")
    require(fail_close.read_text(encoding="utf-8") == "closed", "失败路径 subprocess 未关闭")
    failing.close()


def build_store(root: Path, *, task_id: str, research_task: ResearchTask) -> ArtifactStore:
    store = ArtifactStore(root)
    for artifact_type in (
        "sources",
        "web_pages",
        "collection_attempts",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "tool_calls",
        "task_records",
    ):
        store.save_many(task_id, artifact_type, [])
    store.save_many(
        task_id,
        "research_plans",
        [
            ResearchPlan(
                task_id=task_id,
                decision_question=research_task.objective,
                status=ResearchPlanStatus.NEEDS_COLLECTION,
                research_task_ids=[research_task.id],
                budget=ResearchBudget(max_sources_per_task=1, max_total_sources=1),
            )
        ],
    )
    store.save_many(task_id, "research_tasks", [research_task])
    TaskBoardStore(store).save_board(
        TaskBoard(
            task_id=task_id,
            status=TaskStatus.READY,
            tasks=[
                TaskRecord(
                    task_id=task_id,
                    task_key=research_task.id,
                    task_type=TaskType.SUPPLEMENT_COLLECTION,
                    target_agent_role=AgentRole.COLLECTOR,
                    status=TaskStatus.READY,
                )
            ],
        )
    )
    return store


def build_web_tool() -> WebCollectorTool:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Nebula experience</title></head><body>"
                "Nebula users report practical feature behavior, workflow limits, "
                "integration experience and reproducible product observations."
                "</body></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    return WebCollectorTool(
        transport=httpx.MockTransport(handler),
        url_policy=URLSafetyPolicy(resolve_dns=False),
        enable_browser_fallback=False,
    )


def research_task(task_id: str, suffix: str) -> ResearchTask:
    return ResearchTask(
        id=f"research_nebula_experience_{suffix}",
        task_id=task_id,
        information_need_id=f"need_nebula_experience_{suffix}",
        title="收集 Nebula 用户体验",
        objective="收集 Nebula 用户实际使用体验、踩坑和社区反馈",
        competitor="Nebula",
        dimension="feature",
        query_hints=["Nebula 用户实测"],
        status="waiting_for_collector",
        stop_condition="获得一条真实用户经验来源",
    )


def check_mcp_collector(root: Path) -> None:
    task_id = "task_mcp_r1_collector"
    task = research_task(task_id, "mcp")
    store = build_store(root / "mcp_collector", task_id=task_id, research_task=task)
    transport, call_log, close_marker = new_stdio_transport(root, "collector")
    previous = os.environ.get("SEARCH_TOOL_TRANSPORT")
    try:
        os.environ["SEARCH_TOOL_TRANSPORT"] = "mcp"
        with patch(
            "app.tools.search_transport.MCPWebSearchTransport",
            return_value=transport,
        ):
            collector = CollectorQueueService(
                store=store,
                web_tool=build_web_tool(),
            )
    finally:
        if previous is None:
            os.environ.pop("SEARCH_TOOL_TRANSPORT", None)
        else:
            os.environ["SEARCH_TOOL_TRANSPORT"] = previous
    result = collector.run_once(task_id)
    collector.close()
    require(result["status"] == "completed", str(result))
    require(len(read_call_log(call_log)) == 1, "Collector MCP 请求重复调用 provider")
    require(close_marker.read_text(encoding="utf-8") == "closed", "Collector stdio 未关闭")
    attempts = store.load_many(task_id, "search_attempts")
    require(attempts[0]["provider"] == "fake-stdio-search", "SearchAttempt provider 未恢复")
    require(attempts[0]["metadata"]["acquisition_stage"] == "general", "体验任务阶段回退")
    require(store.load_many(task_id, "sources"), "MCP 搜索结果未进入 WebCollector")
    selection_runs = store.load_many(task_id, "source_selection_runs")
    require(
        selection_runs[0]["ranking_version"] == "source_quality_v1_fix2",
        "MCP 路径没有继续经过 Source Quality FIX2",
    )
    tool_calls = store.load_many(task_id, "tool_calls")
    require(len(tool_calls) == 1 and tool_calls[0]["tool_name"] == "web_search", "ToolCall 未记录")
    trace_input = tool_calls[0]["input"]
    require(trace_input["transport"] == "mcp", "ToolCall 未标记 transport=mcp")
    require(trace_input["server"] == "research-tools", "ToolCall 未标记 MCP Server")
    require(trace_input["provider"] == "fake-stdio-search", "ToolCall 未记录 provider")
    serialized_trace = json.dumps(tool_calls, ensure_ascii=False)
    require("explicit-test-value" not in serialized_trace, "secret 泄露到 Trace")
    require("MCP_R1_FAKE_REQUIRED_ENV" not in serialized_trace, "env 名单泄露到 Trace")


def check_native_collector(root: Path) -> None:
    task_id = "task_mcp_r1_native"
    task = research_task(task_id, "native")
    store = build_store(root / "native_collector", task_id=task_id, research_task=task)
    provider = NativeFakeSearchProvider()
    collector = CollectorQueueService(
        store=store,
        web_tool=build_web_tool(),
        search_provider=provider,
    )
    result = collector.run_once(task_id)
    collector.close()
    require(result["status"] == "completed", str(result))
    require(provider.calls == 1, "native SearchProvider 调用次数回退")
    require(provider.closed, "native provider 未正常关闭")
    tool_call = store.load_many(task_id, "tool_calls")[0]
    require(tool_call["input"]["transport"] == "native", "native ToolCall 标记错误")
    require(tool_call["input"]["server"] == "", "native 路径伪装 MCP Server")


def check_transport_switch() -> None:
    previous = os.environ.get("SEARCH_TOOL_TRANSPORT")
    try:
        os.environ["SEARCH_TOOL_TRANSPORT"] = "mcp"
        transport = build_search_tool_transport_from_env()
        require(isinstance(transport, MCPWebSearchTransport), "mcp switch 未构造 MCP transport")
        transport.close()
        os.environ["SEARCH_TOOL_TRANSPORT"] = "invalid"
        try:
            build_search_tool_transport_from_env()
        except ValueError as exc:
            require("native|mcp" in str(exc), "非法 transport 错误不明确")
        else:
            raise AssertionError("非法 SEARCH_TOOL_TRANSPORT 未失败")
    finally:
        if previous is None:
            os.environ.pop("SEARCH_TOOL_TRANSPORT", None)
        else:
            os.environ["SEARCH_TOOL_TRANSPORT"] = previous


def check_explicit_provider_env() -> None:
    values = {
        "SEARCH_PROVIDER": "tavily",
        "TAVILY_API_KEY": "fake-tavily-secret",
        "ZHIPU_API_KEY": "fake-zhipu-secret",
        "BOCHA_API_KEY": "fake-bocha-secret",
    }
    with patch(
        "app.tools.search_transport._read_user_environment",
        side_effect=lambda key: values.get(key, ""),
    ):
        child_env = _search_provider_subprocess_env()
    require(child_env == values, "MCP 子进程没有显式收到 SearchProvider 必需 env")
    require("PATH" not in child_env and "HOME" not in child_env, "子进程 env 复制范围过大")


def main() -> None:
    require(
        Path(sys.executable).resolve()
        == Path(r"C:\Users\qyn\anaconda3\envs\agent\python.exe").resolve(),
        f"MCP R1 测试必须使用 agent Conda Python，当前为 {sys.executable}",
    )
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "mcp_r1_production_integration"
    )
    asyncio.run(check_in_memory_protocol())
    check_stdio_adapter(root)
    check_mcp_collector(root)
    check_native_collector(root)
    check_transport_switch()
    check_explicit_provider_env()
    print("check_mcp_r1_production_integration: PASS")
    print(f"python={sys.executable}")
    print("mcp_sdk=2.1.0")
    print("in_memory_protocol=true")
    print("stdio_subprocess=true")
    print("collector_transport_mcp=true")
    print("native_transport_preserved=true")
    print("structured_content=true")
    print("provider_calls_per_request=1")
    print("secret_in_trace=false")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
