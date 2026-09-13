from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from app.context.research_action import ResearchActionContextViewBuilder
from app.execution.research_agent import (
    LLMResearchActionDecider,
    ResearchEvidenceAgentService,
    get_research_evidence_agent_service,
)
from app.execution.research_agent_coordinator import ResearchAgentCoordinator
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    InformationNeed,
    LLMCall,
    ObservedResearchTerm,
    ResearchAgentObservation,
    ResearchAgentRun,
    ResearchTask,
)


TASK_ID = "task_context_governance_offline"
RESEARCH_TASK_ID = "research_task_context_governance"
EXACT_READ_TEXT = (
    "Acme Enterprise includes audit logs and role-based access control. "
    "This exact source chunk must remain available for verbatim evidence."
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeLLMClient:
    def __init__(self, *, input_tokens: int, duration_ms: int):
        self.input_tokens = input_tokens
        self.duration_ms = duration_ms
        self.requests: list[dict] = []

    def generate_structured(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        call = LLMCall(
            task_id=kwargs["task_id"],
            agent_run_id=kwargs["agent_run_id"],
            node_id=kwargs["node_id"],
            agent_role=kwargs["agent_role"],
            prompt_id=kwargs["prompt_id"],
            prompt_version=kwargs["prompt_version"],
            prompt_summary=kwargs["prompt_summary"],
            output_schema=kwargs["output_schema"],
            duration_ms=self.duration_ms,
            metadata={
                "input_tokens": self.input_tokens,
                "output_tokens": 23,
            },
        )
        return (
            {
                "item": {
                    "task_id": TASK_ID,
                    "research_task_id": RESEARCH_TASK_ID,
                    "action": "SEARCH",
                    "rationale": "Use another relevant source.",
                    "query": "Acme enterprise audit logs",
                }
            },
            call,
            None,
        )


def _research_task() -> ResearchTask:
    return ResearchTask(
        id=RESEARCH_TASK_ID,
        task_id=TASK_ID,
        information_need_id="need_context_governance",
        title="Acme enterprise capabilities",
        objective="Verify Acme enterprise security capabilities.",
        competitor="Acme",
        dimension="feature",
        query_hints=["Acme enterprise audit logs"],
        preferred_source_types=["official"],
        stop_condition="A verified source supports the capability claim.",
    )


def _information_need() -> InformationNeed:
    return InformationNeed(
        id="need_context_governance",
        task_id=TASK_ID,
        question_id="kiq_context_governance",
        dimension="feature",
        required_facts=["audit logs", "role-based access control"],
        preferred_source_types=["official"],
        comparability_basis="Published enterprise product capabilities",
        decision_link="Compare enterprise administration features",
    )


def _state() -> ResearchAgentRun:
    terms = [
        ObservedResearchTerm(
            id=f"observed_term_{index}",
            task_id=TASK_ID,
            research_task_id=RESEARCH_TASK_ID,
            term=f"Term {index}",
            discovered_from="SEARCH",
            provenance_id=f"observation_{index}",
        )
        for index in range(30)
    ]
    terms.append(
        ObservedResearchTerm(
            id="observed_term_latest_duplicate",
            task_id=TASK_ID,
            research_task_id=RESEARCH_TASK_ID,
            term="term 10",
            discovered_from="READ",
            provenance_id="observation_latest",
        )
    )
    return ResearchAgentRun(
        id="research_run_context_governance",
        task_id=TASK_ID,
        research_task_id=RESEARCH_TASK_ID,
        attempted_queries=[f"query {index}" for index in range(8)],
        visited_urls=[f"https://example.com/{index}" for index in range(7)],
        rejected_sources=[f"https://rejected.example/{index}" for index in range(6)],
        observed_terms=terms,
        verified_evidence_ids=["evidence_1", "evidence_2"],
        failed_actions=["FETCH:https://failed.example|ValueError"],
        remaining_need="Confirm enterprise availability.",
        step_count=7,
    )


def _observations() -> list[ResearchAgentObservation]:
    actions = ["SEARCH", "FETCH", "SEARCH", "FETCH", "READ", "SUBMIT_EVIDENCE"]
    observations: list[ResearchAgentObservation] = []
    for index, action in enumerate(actions):
        payload: dict = {
            "schema_version": "v1",
            "task_id": TASK_ID,
            "research_task_id": RESEARCH_TASK_ID,
            "metadata": {"debug": "not model-visible in governed mode"},
            "result": f"result {index}",
        }
        if action == "SEARCH":
            payload.update(
                {
                    "query": f"Acme query {index}",
                    "search_scope": "auto",
                    "result_count": 1,
                    "results": [
                        {
                            "id": f"search_result_{index}",
                            "task_id": TASK_ID,
                            "title": "Acme documentation",
                            "url": "https://docs.example.com/acme",
                            "snippet": "Published enterprise capabilities.",
                            "selected_for_collection": True,
                            "metadata": {"raw": "discard"},
                        }
                    ],
                }
            )
        elif action == "READ":
            payload.update(
                {
                    "source_id": "source_acme",
                    "retrieval_run_id": "retrieval_acme",
                    "chunks": [
                        {
                            "chunk_id": "chunk_acme",
                            "source_id": "source_acme",
                            "source_text_start": 12,
                            "source_text_end": 151,
                            "text": EXACT_READ_TEXT,
                            "metadata": {"embedding": [0.1, 0.2]},
                        }
                    ],
                }
            )
        observations.append(
            ResearchAgentObservation(
                id=f"observation_{index}",
                task_id=TASK_ID,
                research_task_id=RESEARCH_TASK_ID,
                action_id=f"action_{index}",
                action=action,
                status="completed",
                summary=f"Observation {index}",
                payload=payload,
            )
        )
    return observations


def _expected_legacy_artifacts(
    *,
    task: ResearchTask,
    need: InformationNeed,
    state: ResearchAgentRun,
    observations: list[ResearchAgentObservation],
    mission_context: dict,
) -> dict[str, list]:
    research_state = state.model_dump(mode="json")
    for field in ("attempted_queries", "visited_urls", "rejected_sources"):
        values = list(getattr(state, field))
        research_state[field] = {
            "count": len(values),
            "recent": values[-3:],
        }
    return {
        "research_task": [task.model_dump(mode="json")],
        "information_need": [need.model_dump(mode="json")],
        "research_state": [research_state],
        "recent_observations": [
            item.model_dump(mode="json") for item in observations[-4:]
        ],
        "mission_context": [mission_context],
    }


def main() -> None:
    task = _research_task()
    need = _information_need()
    state = _state()
    observations = _observations()
    mission_context = {
        "mission_id": "mission_acme",
        "mission_goal": "Understand Acme enterprise capabilities.",
        "related_verified_evidence": [
            {
                "evidence_id": "evidence_shared",
                "source_id": "source_shared",
                "fact": "Acme supports enterprise administration.",
            }
        ],
        "budget": {"actions_used": 7, "max_actions": 12},
    }
    available_artifacts = {
        "research_source_candidates": [
            {"id": "candidate_1", "url": "https://example.com/1"},
            {"id": "candidate_2", "url": "https://example.com/2"},
        ]
    }
    state_before = state.model_dump(mode="json")
    observations_before = [
        item.model_dump(mode="json") for item in observations
    ]

    legacy_view = ResearchActionContextViewBuilder(enabled=False).build(
        research_task=task,
        information_need=need,
        state=state,
        recent_observations=observations,
        mission_context=mission_context,
        available_artifacts=available_artifacts,
    )
    require(legacy_view.context_mode == "legacy", "默认视图必须是 legacy")
    require(
        legacy_view.artifacts
        == _expected_legacy_artifacts(
            task=task,
            need=need,
            state=state,
            observations=observations,
            mission_context=mission_context,
        ),
        "context_governance_enabled=false 必须逐字段复现 R1 前上下文",
    )

    governed_view = ResearchActionContextViewBuilder(enabled=True).build(
        research_task=task,
        information_need=need,
        state=state,
        recent_observations=observations,
        mission_context=mission_context,
        available_artifacts=available_artifacts,
    )
    governed_state = governed_view.artifacts["research_state"][0]
    visible_terms = governed_state["observed_terms"]
    require(governed_view.context_mode == "governed", "治理视图模式错误")
    require(len(visible_terms) == 24, "observed_terms 模型视图上限必须为 24")
    require(
        len({item["term"].casefold() for item in visible_terms}) == 24,
        "observed_terms 必须按 term 去重",
    )
    require(
        all(set(item) == {"term", "discovered_from"} for item in visible_terms),
        "observed_terms 不得携带 ID、时间或 provenance 包装",
    )
    latest_duplicate = next(
        item for item in visible_terms if item["term"].casefold() == "term 10"
    )
    require(
        latest_duplicate["discovered_from"] == "READ",
        "observed_terms 去重后必须保留最新发现来源",
    )
    visible_observations = governed_view.artifacts["recent_observations"]
    require(len(visible_observations) == 4, "只允许最近 4 条 Observation")
    require(
        all(
            set(item) == {"action", "status", "summary", "payload"}
            for item in visible_observations
        ),
        "治理后的 Observation 只应保留决策必要包装",
    )
    require(
        all(
            "metadata" not in item["payload"]
            and "task_id" not in item["payload"]
            and "research_task_id" not in item["payload"]
            for item in visible_observations
        ),
        "治理后的 Observation payload 不应重复携带 metadata/task IDs",
    )
    read_observation = next(
        item for item in visible_observations if item["action"] == "READ"
    )
    require(
        read_observation["payload"]["chunks"][0]["text"]
        == EXACT_READ_TEXT,
        "READ chunk 原文必须完整保留以支持逐字引用",
    )
    require(
        governed_view.artifacts["mission_context"] == [mission_context],
        "Mission Context 本轮不得裁剪",
    )
    require(
        "research_source_candidates" not in governed_view.artifacts
        and governed_view.candidate_count == 0
        and governed_view.available_candidate_count == 2,
        "source candidates 只能记录可用数量，不能在 R1 中新增到模型上下文",
    )
    require(
        governed_view.total_chars < legacy_view.total_chars,
        "治理后的模型视图应小于旧视图",
    )
    require(
        state.model_dump(mode="json") == state_before
        and [item.model_dump(mode="json") for item in observations]
        == observations_before,
        "构建模型视图不得修改完整 Research State/Observation",
    )

    checks_root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "research_context_governance_r1"
    )
    task_root = checks_root / TASK_ID
    if task_root.exists():
        shutil.rmtree(task_root)
    checks_root.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(checks_root)
    try:
        legacy_client = FakeLLMClient(input_tokens=654, duration_ms=41)
        governed_client = FakeLLMClient(input_tokens=321, duration_ms=37)
        legacy_decider = LLMResearchActionDecider(
            llm_client=legacy_client,
            store=store,
        )
        governed_decider = LLMResearchActionDecider(
            llm_client=governed_client,
            store=store,
            context_governance_enabled=True,
        )
        common = {
            "task_id": TASK_ID,
            "research_task": task,
            "information_need": need,
            "state": state,
            "recent_observations": observations,
            "mission_context": mission_context,
            "artifacts": available_artifacts,
        }
        legacy_decider.decide(**common)
        governed_decider.decide(**common)
        require(
            legacy_client.requests[0]["prompt_summary"]
            == governed_client.requests[0]["prompt_summary"],
            "上下文治理不得修改 Research Agent prompt policy",
        )
        require(
            legacy_client.requests[0]["output_schema"]
            == governed_client.requests[0]["output_schema"]
            == "ResearchAgentAction",
            "上下文治理不得修改 Action Schema",
        )
        traces = store.load_many(TASK_ID, "research_action_context_traces")
        require(len(traces) == 2, "每次 Action 决策必须追加一条 Context Trace")
        require(
            [item["context_mode"] for item in traces]
            == ["legacy", "governed"],
            "Context Trace 必须区分 legacy/governed",
        )
        require(
            traces[1]["input_tokens"] == 321
            and traces[1]["llm_latency_ms"] == 37,
            "Context Trace 必须记录 provider 的实际 input tokens 和 LLM latency",
        )
        require(
            traces[1]["observation_count"] == 4
            and traces[1]["candidate_count"] == 0
            and traces[1]["available_candidate_count"] == 2
            and traces[1]["evidence_count"] == 3
            and traces[1]["observed_term_count"] == 24,
            "Context Trace 数量字段错误",
        )
        require(
            set(traces[1]["section_chars"])
            == {
                "research_task",
                "information_need",
                "research_state",
                "recent_observations",
                "mission_context",
            },
            "Context Trace 必须按主要上下文部分记录大小",
        )
        require(
            "prompt_summary" not in traces[1]
            and "artifacts" not in traces[1],
            "Context Trace 不得保存完整 prompt 或模型上下文",
        )

        default_service = ResearchEvidenceAgentService(store=store)
        governed_service = ResearchEvidenceAgentService(
            store=store,
            context_governance_enabled=True,
        )
        require(
            default_service.context_governance_enabled is False
            and governed_service.context_governance_enabled is True,
            "Research service 开关默认值或传递错误",
        )
        require(
            get_research_evidence_agent_service(
                context_governance_enabled=True
            ).context_governance_enabled
            is True,
            "默认 Research service factory 未传递上下文治理开关",
        )
        coordinator = ResearchAgentCoordinator(store=store)
        governed_coordinator = ResearchAgentCoordinator(
            store=store,
            context_governance_enabled=True,
        )
        require(
            coordinator.context_governance_enabled is False
            and governed_coordinator.context_governance_enabled is True,
            "Coordinator 开关默认值或传递错误",
        )
        require(
            governed_coordinator
            ._research_service_factory(store)
            .context_governance_enabled
            is True,
            "Coordinator 默认 factory 未把开关传给 Research service",
        )
        coordinator._pool.shutdown(wait=False)
        governed_coordinator._pool.shutdown(wait=False)
    finally:
        if task_root.exists():
            shutil.rmtree(task_root)

    print("Research Agent Context Governance R1 offline checks passed.")


if __name__ == "__main__":
    main()
