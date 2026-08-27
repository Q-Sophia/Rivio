from __future__ import annotations

import json
from pathlib import Path

from app.collection import CollectorQueueService
from app.harness.artifacts import ArtifactStore
from app.schemas import ResearchTask
from app.tools.search_provider import build_search_provider_from_env
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool


def main() -> None:
    task_id = "task_official_host_detection_tavily_smoke"
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "official_host_detection_fix"
    )
    store = ArtifactStore(root)
    for artifact_type in (
        "sources",
        "search_attempts",
        "web_search_results",
        "source_selection_runs",
        "tool_calls",
    ):
        store.save_many(task_id, artifact_type, [])
    research_task = ResearchTask(
        id="research_kuaishou_official_host_smoke",
        task_id=task_id,
        information_need_id="need_kuaishou_official_host_smoke",
        title="核实快手开放平台能力",
        objective="查找快手官方开放平台和开发者文档中的产品能力事实",
        competitor="快手",
        dimension="feature",
        query_hints=["快手 开放平台 开发者文档"],
        preferred_source_types=["docs"],
        status="waiting_for_collector",
        stop_condition="取得快手官方开发者来源",
    )
    provider = build_search_provider_from_env()
    if provider is None or provider.name != "tavily":
        raise RuntimeError("真实 smoke 要求 SEARCH_PROVIDER=tavily")
    collector = CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        search_provider=provider,
    )
    try:
        collector._discover_seed_urls(
            task_id=task_id,
            research_task=research_task,
            limit=1,
        )
    finally:
        collector.close()

    attempts = store.load_many(task_id, "search_attempts")
    discovered_hosts = sorted(
        {
            host
            for item in attempts
            if item.get("metadata", {}).get("acquisition_stage")
            == "official_discovery"
            for host in item.get("metadata", {}).get("discovered_official_hosts", [])
        }
    )
    targeted = [
        item
        for item in attempts
        if item.get("metadata", {}).get("acquisition_stage") == "official_targeted"
    ]
    general = [
        item
        for item in attempts
        if item.get("metadata", {}).get("acquisition_stage") == "general"
    ]
    if not discovered_hosts:
        raise AssertionError("Tavily official discovery did not infer any probable host")
    if not targeted:
        raise AssertionError("probable host did not trigger official_targeted SearchAttempt")
    print("check_official_host_detection_tavily_smoke: PASS")
    print(f"discovered_official_hosts={json.dumps(discovered_hosts, ensure_ascii=False)}")
    print(f"official_targeted_attempts={len(targeted)}")
    print(f"official_targeted_completed={sum(item['status'] == 'completed' for item in targeted)}")
    print(f"general_attempts={len(general)}")


if __name__ == "__main__":
    main()
