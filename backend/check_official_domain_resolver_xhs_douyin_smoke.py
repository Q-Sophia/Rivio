from __future__ import annotations

import json
from pathlib import Path

from app.collection import CollectorQueueService
from app.harness.artifacts import ArtifactStore
from app.schemas import ResearchTask
from app.tools.search_provider import build_search_provider_from_env
from app.tools.web_collector import URLSafetyPolicy, WebCollectorTool


def reset(store: ArtifactStore, task_id: str) -> None:
    for artifact_type in (
        "evidence_coverage",
        "official_domain_contexts",
        "search_attempts",
        "source_selection_runs",
        "sources",
        "tool_calls",
        "web_search_results",
    ):
        store.save_many(task_id, artifact_type, [])


def research_task(task_id: str) -> ResearchTask:
    return ResearchTask(
        id=f"research_{task_id}",
        task_id=task_id,
        information_need_id=f"need_{task_id}",
        title="核实小红书与抖音的产品定位",
        objective="采集并核实小红书、抖音的官方产品定位与核心产品能力",
        competitor="小红书、抖音",
        dimension="positioning",
        query_hints=["小红书 抖音 官方 官网 产品定位"],
        preferred_source_types=["official_site", "docs"],
        status="waiting_for_collector",
        stop_condition="优先取得双方官方资料，官方不足时保留第三方身份继续研究",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "official_domain_resolver_xhs_douyin_smoke"
    )
    store = ArtifactStore(root / "runs")
    ArtifactStore(root / "cache").save_many(
        "official_domains",
        "official_domain_contexts",
        [],
    )
    provider = build_search_provider_from_env()
    if provider is None or provider.name != "tavily":
        raise RuntimeError("该 smoke test 要求当前 SEARCH_PROVIDER=tavily")
    collector = CollectorQueueService(
        store=store,
        web_tool=WebCollectorTool(
            url_policy=URLSafetyPolicy(resolve_dns=False),
            enable_browser_fallback=False,
        ),
        search_provider=provider,
    )
    factual = research_task("task_xhs_douyin_official_smoke")
    reset(store, factual.task_id)
    collector.search_agent_query(
        task_id=factual.task_id,
        research_task=factual,
        query=factual.query_hints[0],
        limit=5,
    )
    contexts = store.load_many(factual.task_id, "official_domain_contexts")
    if any(item["competitor"] == factual.competitor for item in contexts):
        raise AssertionError("组合竞品被作为单一 competitor 解析")
    confirmed = [item for item in contexts if item["confidence"] == "confirmed"]
    if not confirmed:
        raise AssertionError("真实 Tavily 检索没有确认任何官方域")
    factual_results = store.load_many(factual.task_id, "web_search_results")
    selected_official = [
        item
        for item in factual_results
        if item["selected_for_collection"]
        and item["metadata"].get("first_party") is True
    ]
    if not selected_official:
        raise AssertionError("confirmed 官方域没有进入 official-first 选择结果")

    fallback = research_task("task_xhs_douyin_fallback_smoke").model_copy(
        update={
            "competitor": "小红书",
            "title": "核实小红书的第三方行业定位资料",
            "objective": "官方资料不足时，采集小红书的权威第三方产品定位资料",
            "query_hints": ["小红书 产品定位 行业分析"],
        }
    )
    reset(store, fallback.task_id)
    collector.search_agent_query(
        task_id=fallback.task_id,
        research_task=fallback,
        query=fallback.query_hints[0],
        limit=5,
        source_preference="general",
    )
    collector.close()
    fallback_results = store.load_many(fallback.task_id, "web_search_results")
    selected_third_party = [
        item
        for item in fallback_results
        if item["selected_for_collection"]
        and item["metadata"].get("first_party") is False
    ]
    if not selected_third_party:
        raise AssertionError("显式 general fallback 没有保留第三方来源继续研究")

    print("check_official_domain_resolver_xhs_douyin_smoke: PASS")
    print(
        "confirmed="
        + json.dumps(
            [
                {
                    "competitor": item["competitor"],
                    "domain": item["domain"],
                }
                for item in confirmed
            ],
            ensure_ascii=False,
        )
    )
    print(
        "selected_official_domains="
        + json.dumps(
            sorted(
                {
                    item["metadata"].get("domain")
                    for item in selected_official
                }
            ),
            ensure_ascii=False,
        )
    )
    print(
        "fallback_third_party_domains="
        + json.dumps(
            sorted(
                {
                    item["metadata"].get("domain")
                    for item in selected_third_party
                }
            ),
            ensure_ascii=False,
        )
    )
    print("real_network_used=true")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
