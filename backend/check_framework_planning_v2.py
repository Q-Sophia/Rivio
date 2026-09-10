from __future__ import annotations

from pathlib import Path

import app.api.main  # noqa: F401 - initialize application packages in production order
from app.agents.research_planner import ResearchPlannerAgent
from app.frameworks import load_framework
from app.harness.artifacts import ArtifactStore
from app.schemas import AgentContext, AnalysisTask, DatasetCompatibilityAssessment, ResearchTask


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    framework = load_framework("competitive_intelligence", "1.0.0")
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "framework_planning_v2"
    root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root)
    task = AnalysisTask(
        id="task_framework_planning_v2",
        query="比较 Acme 的定位与客户体验",
        competitors=["Acme"],
        focus_areas=["market_positioning", "customer_experience"],
        mode="live",
    )
    for artifact_type in (
        "research_plans",
        "research_kiqs",
        "research_information_needs",
        "research_tasks",
    ):
        store.save_many(task.id, artifact_type, [])
    assessment = DatasetCompatibilityAssessment(
        task_id=task.id,
        dataset_id="framework_test",
        dataset_label="framework test",
        requested_competitors=task.competitors,
        missing_competitors=task.competitors,
        unsupported_focus_areas=task.focus_areas,
    )
    ResearchPlannerAgent(store=store).execute(
        AgentContext(
            task_id=task.id,
            task=task,
            node_id="framework_planning_v2",
            metadata={
                "dataset_assessment": assessment.model_dump(mode="json"),
                "framework_definition": framework.model_dump(mode="json"),
            },
        )
    )

    tasks = [
        ResearchTask(**item)
        for item in store.load_many(task.id, "research_tasks")
    ]
    require(len(tasks) == 2, "Planner 没有按 Framework focus dimensions 生成任务")
    require(
        [item.framework_dimension_id for item in tasks]
        == ["market_positioning", "customer_experience"],
        "Planner 输出缺少 Framework dimension provenance",
    )
    require(
        [item.dimension for item in tasks] == ["positioning", "customer"],
        "Planner 改坏了下游既有 evidence dimension 协议",
    )
    require(
        all(
            item.schema_version == "v2"
            and item.framework_id == framework.framework_id
            and item.framework_version == framework.version
            and item.framework_content_hash == framework.content_hash
            for item in tasks
        ),
        "ResearchTask v2 provenance 不完整",
    )
    require(
        tasks[1].query_hints == [
            "Acme 使用体验",
            "Acme 真实感受 用户评价",
        ],
        "Planner 没有使用 Framework query_templates",
    )
    plan = store.load_many(task.id, "research_plans")[-1]
    require(
        plan["metadata"]["planning_method"] == "framework_registry_v1",
        "ResearchPlan 没有记录 Framework-driven planning",
    )

    print("check_framework_planning_v2: PASS")
    print("research_tasks=2")
    print("framework_provenance=true")
    print("legacy_evidence_dimension_preserved=true")


if __name__ == "__main__":
    main()
