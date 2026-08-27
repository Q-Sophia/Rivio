from __future__ import annotations

from pathlib import Path

from app.execution.research_agent import ResearchEvidenceAgentService
from app.schemas import ProductCard, SourceDocument, SourceEvidence
from check_research_agent_r1 import (
    QUOTE,
    FakeResearchTools,
    TrajectoryDecider,
    action,
    run_case,
    setup,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_verified_evidence_projection(root: Path) -> None:
    name = "verified_evidence"
    task_id = f"task_research_agent_{name}"
    research_task_id = f"research_task_{name}"
    _task_id, _task, store, _decider, tools, run = run_case(
        root,
        name,
        [
            action(
                task_id,
                research_task_id,
                "SEARCH",
                query="ClassIn 官方 定价 收费",
                search_scope="auto",
            ),
            action(
                task_id,
                research_task_id,
                "FETCH",
                url="https://docs.classin.example/pricing",
            ),
            action(
                task_id,
                research_task_id,
                "READ",
                source_id="src_classin_pricing",
            ),
            action(
                task_id,
                research_task_id,
                "SUBMIT_EVIDENCE",
                source_id="src_classin_pricing",
                chunk_id="chunk_classin_pricing",
                exact_quote=QUOTE,
                supports="ClassIn EDU教育版采用年度服务费模式",
            ),
            action(
                task_id,
                research_task_id,
                "FINISH",
                finish_status="COMPLETE",
            ),
        ],
    )
    require(run["outcome"] == "COMPLETE", "R1 fixture 未正常完成")
    evidence = store.load_many(task_id, "evidence")
    sources = store.load_many(task_id, "sources")
    cards = store.load_many(task_id, "product_cards")
    coverage = store.load_many(task_id, "evidence_coverage")
    require(len(cards) == 1, "R1 Verified Evidence 未生成 ProductCard")
    require(
        set(cards[0]["evidence_ids"]) == {evidence[0]["id"]},
        "ProductCard 未严格引用真实 R1 Verified Evidence",
    )
    require(
        set(cards[0]["source_ids"]) == {sources[0]["id"]},
        "ProductCard 的 Source 溯源断链",
    )
    require(
        cards[0]["metadata"]["source"]
        == "research_agent_r1_compat_projection"
        and cards[0]["metadata"]["verified_evidence_only"] is True,
        "ProductCard 未标记 R1 兼容投影来源",
    )
    require(
        coverage
        and evidence[0]["id"] in coverage[0]["evidence_ids"]
        and sources[0]["id"] in coverage[0]["source_ids"],
        "EvidenceCoverage 未保持 Evidence/Source 引用",
    )
    require(tools.submit_calls == 1, "兼容投影导致证据工具重复执行")

    legacy_evidence = SourceEvidence(
        id="ev_legacy_not_r1_verified",
        task_id=task_id,
        source_id=sources[0]["id"],
        competitor="ClassIn",
        dimension="pricing",
        snippet="legacy-only fact",
        normalized_fact="legacy-only fact",
    )
    store.save_many(
        task_id,
        "evidence",
        [*(SourceEvidence(**item) for item in evidence), legacy_evidence],
    )
    store.save_many(
        task_id,
        "product_cards",
        [
            ProductCard(
                id="prod_legacy_should_be_replaced",
                task_id=task_id,
                name="ClassIn",
                source_ids=[sources[0]["id"]],
                evidence_ids=[legacy_evidence.id],
            )
        ],
    )
    ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=research_task_id,
        decider=TrajectoryDecider([]),
        tools=tools,
    )
    rebuilt_cards = store.load_many(task_id, "product_cards")
    require(
        len(rebuilt_cards) == 1
        and set(rebuilt_cards[0]["evidence_ids"]) == {evidence[0]["id"]}
        and legacy_evidence.id not in rebuilt_cards[0]["evidence_ids"],
        "Projection 读取或合并了 legacy ProductCard/Evidence",
    )


def check_no_evidence_does_not_fabricate(root: Path) -> None:
    name = "no_evidence"
    task_id = f"task_research_agent_{name}"
    research_task_id = f"research_task_{name}"
    _task_id, _task, store, _decider, _tools, _run = run_case(
        root,
        name,
        [
            action(
                task_id,
                research_task_id,
                "FINISH",
                finish_status="EXHAUSTED",
            )
        ],
    )
    require(not store.load_many(task_id, "evidence"), "无证据 fixture 异常")
    require(
        not store.load_many(task_id, "product_cards"),
        "无 R1 Verified Evidence 时伪造了 ProductCard",
    )
    require(
        not store.load_many(task_id, "evidence_coverage"),
        "无 R1 Verified Evidence 时伪造了 EvidenceCoverage",
    )


def check_no_r1_evidence_clears_derived_projection(root: Path) -> None:
    name = "legacy_compat"
    task_id, research_task, store = setup(root, name)
    source = SourceDocument(
        id="src_legacy",
        task_id=task_id,
        title="Legacy source",
        url="https://legacy.example/source",
        competitor="ClassIn",
    )
    evidence = SourceEvidence(
        id="ev_legacy",
        task_id=task_id,
        source_id=source.id,
        competitor="ClassIn",
        dimension="pricing",
        snippet="Legacy verified quote",
        normalized_fact="Legacy verified fact",
    )
    legacy_card = ProductCard(
        id="prod_legacy",
        task_id=task_id,
        name="ClassIn",
        source_ids=[source.id],
        evidence_ids=[evidence.id],
        metadata={"source": "legacy_step6e4"},
    )
    store.save_many(task_id, "sources", [source])
    store.save_many(task_id, "evidence", [evidence])
    store.save_many(task_id, "product_cards", [legacy_card])
    tools = FakeResearchTools(store, task_id, research_task)
    payload = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=research_task.id,
        decider=TrajectoryDecider(
            [
                action(
                    task_id,
                    research_task.id,
                    "FINISH",
                    finish_status="EXHAUSTED",
                )
            ]
        ),
        tools=tools,
    )
    require(
        payload["compatibility_projection"]["projected"] is False,
        "无 R1 Verified Evidence 不应报告 projection 成功",
    )
    require(
        not store.load_many(task_id, "product_cards")
        and not store.load_many(task_id, "evidence_coverage"),
        "无 R1 Verified Evidence 时仍残留旧 derived projection",
    )
    require(
        store.load_many(task_id, "evidence")
        == [evidence.model_dump(mode="json")],
        "清空 projection 不应删除原始 Evidence",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_compat_bridge_r1"
    )
    check_verified_evidence_projection(root)
    check_no_evidence_does_not_fabricate(root)
    check_no_r1_evidence_clears_derived_projection(root)
    print("check_r1_compat_bridge_r1: PASS")
    print("r1_verified_evidence_to_product_card=true")
    print("product_card_evidence_source_provenance=true")
    print("coverage_projection=true")
    print("no_evidence_no_fabrication=true")
    print("legacy_product_card_not_used_or_merged=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
