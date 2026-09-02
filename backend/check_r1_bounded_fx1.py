from __future__ import annotations

from pathlib import Path

from app.execution.research_agent_coordinator import ResearchAgentCoordinator
from app.harness.artifacts import ArtifactStore
from app.intake.step6e4 import build_step6e4_research_tasks
from app.schemas import ResearchGap, ResearchTask, TaskPriority
from check_r1_bounded_research_r1 import FakeR1Service, run_case, seed_case


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_gap(*, task_id: str, gap_id: str, dimension: str) -> ResearchGap:
    return ResearchGap(
        id=gap_id,
        task_id=task_id,
        competitors=["小红书"],
        dimension=dimension,
        missing_information=f"{dimension} evidence missing",
        decision_blocked="bounded fixture",
        why_existing_evidence_is_insufficient="bounded fixture",
        suggested_queries=[],
        preferred_source_types=["official_site"],
        priority=TaskPriority.MEDIUM,
        stop_condition="sufficient or bounded stop",
        metadata={
            "source": "step6e4_coverage_gap_v1",
            "coverage_id": f"coverage_{gap_id}",
            "coverage_status": "partial",
        },
    )


def check_generation_dedup_and_round_two() -> None:
    task_id = "task_r1_bounded_fx1_generation"
    initial = ResearchTask(
        id="initial_xhs_other_round_1",
        task_id=task_id,
        information_need_id="need_xhs_other",
        title="initial",
        objective="initial",
        competitor="小红书",
        dimension="other",
        status="evidence_exhausted",
        stop_condition="bounded",
        collection_round=1,
    )
    tasks = build_step6e4_research_tasks(
        task_id,
        [
            make_gap(task_id=task_id, gap_id="gap_growth", dimension="增长"),
            make_gap(task_id=task_id, gap_id="gap_market", dimension="市场"),
        ],
        existing_tasks=[initial],
        max_collection_rounds=3,
    )
    require(len(tasks) == 1, "同一 canonical competitor×dimension 生成了重复补采任务")
    supplement = tasks[0]
    require(supplement.collection_round == 2, "首次补采不是 Round2")
    require(supplement.dimension == "other", "dimension 未 canonicalize")
    require(
        supplement.information_need_id == initial.information_need_id,
        "补采 ResearchTask 新增了 synthetic dangling InformationNeed",
    )
    require(
        supplement.metadata.get("trigger_gap_ids")
        == ["gap_growth", "gap_market"],
        "合并补采任务未保留全部 gap provenance",
    )
    require(
        len(supplement.metadata.get("gap_provenance") or []) == 2,
        "gap provenance 明细不完整",
    )


def check_coordinator_defensive_dedup(root: Path) -> None:
    task_id = "task_r1_bounded_fx1_defensive"
    store = ArtifactStore(root / "defensive_dedup")
    seed_case(
        store,
        task_id=task_id,
        competitors=["小红书"],
        dimensions=["other"],
        max_rounds=3,
    )
    tasks = [
        ResearchTask(**item)
        for item in store.load_many(task_id, "research_tasks")
    ]
    for suffix in ("a", "b"):
        tasks.append(
            ResearchTask(
                id=f"duplicate_xhs_other_round_2_{suffix}",
                task_id=task_id,
                information_need_id="need_xhs_other",
                title="duplicate supplement",
                objective="duplicate supplement",
                competitor="小红书",
                dimension="other",
                status="waiting_for_collector",
                stop_condition="bounded",
                collection_round=2,
                research_gap_id=f"gap_{suffix}",
                metadata={"source": "r1_bounded_coverage_supplement"},
            )
        )
    store.save_many(task_id, "research_tasks", tasks)
    fake = FakeR1Service(
        store,
        {
            ("小红书", "other", 1): (1, True),
            ("小红书", "other", 2): (2, False),
        },
    )
    coordinator = ResearchAgentCoordinator(
        store=store,
        research_service_factory=lambda _store: fake,
    )
    coordinator.submit(task_id, acknowledge_real_llm_call=True)
    completed = coordinator.wait(task_id, timeout=20)
    round_two_calls = [
        item for item in fake.calls if item["collection_round"] == 2
    ]
    require(len(round_two_calls) == 1, "Coordinator 执行了重复 Round2 supplement")
    require(completed.total_tasks == 2, "动态总任务数未按语义去重")
    require(completed.completed_tasks == 2, "累计完成数错误")


def check_rounds_events_and_dynamic_progress(root: Path) -> None:
    task_id, store, fake, completed = run_case(
        root,
        name="rounds_and_progress",
        competitors=["抖音"],
        dimensions=["feature"],
        max_rounds=3,
        evidence_plan={
            ("抖音", "feature", 1): (1, True),
            ("抖音", "feature", 2): (1, True),
            ("抖音", "feature", 3): (1, True),
        },
    )
    require(
        [item["collection_round"] for item in fake.calls] == [1, 2, 3],
        "ResearchTask round 不是 Round1→Round2→Round3",
    )
    events = store.load_many(task_id, "research_agent_coordinator_events")
    started_rounds = [
        item["data"]["collection_round"]
        for item in events
        if item["event_type"] == "collection_round_started"
    ]
    refreshed_rounds = [
        item["data"]["collection_round"]
        for item in events
        if item["event_type"] == "coverage_refreshed"
    ]
    require(started_rounds == [1, 2, 3], "collection_round_started round 错误")
    require(refreshed_rounds == [1, 2, 3], "coverage_refreshed round 错误")
    require(completed.stop_reason == "max_collection_rounds", "max round 未停止")
    require(
        all(0 <= int(item["data"]["progress_percent"]) <= 100 for item in events),
        "动态补采进度超过 100%",
    )
    round_one_refresh = next(
        item
        for item in events
        if item["event_type"] == "coverage_refreshed"
        and item["data"]["collection_round"] == 1
    )
    require(
        round_one_refresh["data"]["total_tasks"] == 2,
        "Round2 生成后事件仍使用 initial_total",
    )
    require(
        all(
            field in round_one_refresh["data"]
            for field in (
                "active_collection_round",
                "current_round_completed",
                "current_round_total",
                "cumulative_completed",
                "outcomes",
            )
        ),
        "Coordinator 事件缺少动态轮次进度字段",
    )


def check_frontend_dynamic_progress_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "src" / "app.js"
    ).read_text(encoding="utf-8")
    for marker in (
        "clampResearchProgressPercent",
        "eventData.total_tasks",
        '"Active round"',
        '"Current round"',
        '"Cumulative completed"',
        '"FAILED"',
    ):
        require(marker in source, f"前端动态进度契约缺少：{marker}")
    require(
        "Math.max(\n                Number(\n                  current.progress_percent"
        not in source,
        "前端仍保留 initial_total 单调进度算法",
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_bounded_fx1"
    )
    check_generation_dedup_and_round_two()
    check_coordinator_defensive_dedup(root)
    check_rounds_events_and_dynamic_progress(root)
    check_frontend_dynamic_progress_contract()
    print("check_r1_bounded_fx1: PASS")
    print("supplement_generation_dedup=true")
    print("supplement_gap_provenance_merged=true")
    print("coordinator_defensive_dedup=true")
    print("rounds_1_2_3_consistent=true")
    print("max_collection_rounds_stop=true")
    print("dynamic_progress_lte_100=true")
    print("deepseek_calls=0")
    print("tavily_calls=0")


if __name__ == "__main__":
    main()
