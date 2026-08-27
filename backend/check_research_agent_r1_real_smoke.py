from __future__ import annotations

import json
from pathlib import Path

from app.execution.research_agent import (
    LLMResearchActionDecider,
    ResearchEvidenceAgentService,
    build_research_agent_llm_config,
)
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient
from app.schemas import (
    InformationNeed,
    ResearchAgentBudget,
    ResearchTask,
)
from check_research_agent_r1 import FakeResearchTools


def main() -> None:
    task_id = "task_research_agent_r1_real_smoke"
    research_task_id = "research_task_research_agent_r1_real_smoke"
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "research_agent_r1_real_smoke"
    )
    store = ArtifactStore(root)
    task = ResearchTask(
        id=research_task_id,
        task_id=task_id,
        information_need_id="need_research_agent_r1_real_smoke",
        title="核实 ClassIn 定价与收费模式",
        objective="找到 ClassIn 定价与收费模式的可验证官方原文",
        competitor="ClassIn",
        dimension="pricing",
        query_hints=["ClassIn 官方 定价 收费"],
        status="waiting_for_collector",
        stop_condition="提交至少一条逐字可验证的官方定价证据",
    )
    need = InformationNeed(
        id=task.information_need_id,
        task_id=task_id,
        question_id="kiq_research_agent_r1_real_smoke",
        dimension="pricing",
        required_facts=["收费模式", "价格依据"],
        comparability_basis="相同计费周期",
        decision_link="判断采购成本",
    )
    store.save_many(task_id, "research_tasks", [task])
    store.save_many(task_id, "research_information_needs", [need])
    for artifact_type in (
        "sources", "web_pages", "source_chunks", "evidence", "web_search_results",
        "research_agent_runs", "research_agent_actions", "research_agent_observations",
        "dag_nodes", "agent_runs", "tool_calls", "llm_calls", "llm_outputs",
    ):
        store.save_many(task_id, artifact_type, [])

    config = build_research_agent_llm_config()
    errors = config.real_call_readiness_errors()
    if errors:
        raise RuntimeError("；".join(errors))
    tools = FakeResearchTools(store, task_id, task)
    payload = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=research_task_id,
        decider=LLMResearchActionDecider(
            llm_client=LLMClient(config=config, store=store),
        ),
        tools=tools,
        budget=ResearchAgentBudget(
            max_steps=3,
            max_searches=2,
            max_sources=2,
            max_failed_actions=3,
        ),
    )
    actions = payload["actions"]
    observations = payload["observations"]
    if len(actions) < 2 or not observations:
        raise AssertionError("真实 LLM 未形成 Action → Observation → 新 Action")
    if actions[1]["created_at"] < observations[0]["created_at"]:
        raise AssertionError("第二个 LLM Action 不是在首个 Observation 之后产生")
    llm_calls = store.load_many(task_id, "llm_calls")
    if len(llm_calls) < 2 or any(item.get("provider") == "mock" for item in llm_calls):
        raise AssertionError("Smoke 未真实调用 DeepSeek 至少两轮")
    trace = [
        {
            "step": index,
            "action": item["action"],
            "query": item.get("query", ""),
            "url": item.get("url", ""),
            "source_id": item.get("source_id", ""),
            "observation": (
                observations[index - 1]["summary"]
                if index - 1 < len(observations)
                else "terminal/no observation"
            ),
        }
        for index, item in enumerate(actions, start=1)
    ]
    print("check_research_agent_r1_real_smoke: PASS")
    print(f"deepseek_calls={len(llm_calls)}")
    print("network_tools_used=false")
    print(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
