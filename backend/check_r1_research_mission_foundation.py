from __future__ import annotations

from pathlib import Path

from app.execution.research_agent import ResearchEvidenceAgentService
from app.execution.research_mission import ResearchMissionService
from app.agents.web_evidence import verify_candidate_evidence
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    InformationNeed,
    OfficialDomainContext,
    ResearchAgentAction,
    ResearchTask,
    SourceChunk,
    SourceDocument,
    SourceEvidence,
    SourceTaskAssociation,
    WebPageContent,
    WebSearchResult,
)


URL = "https://docs.acme.example/product"
FEATURE_QUOTE = "Acme supports shared workspaces and role-based collaboration."
PRICING_QUOTE = "Acme bills business plans annually per active workspace."
PAGE_TEXT = f"{FEATURE_QUOTE}\n{PRICING_QUOTE}"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class SequenceDecider:
    def __init__(self, actions: list[ResearchAgentAction]):
        self.actions = list(actions)
        self.mission_contexts: list[dict] = []

    def decide(self, **kwargs) -> ResearchAgentAction:
        self.mission_contexts.append(dict(kwargs.get("mission_context") or {}))
        if not self.actions:
            raise AssertionError("Mission fixture actions exhausted")
        return self.actions.pop(0)


class OfflineMissionTools:
    def __init__(self, store: ArtifactStore):
        self.store = store
        self.fetch_count = 0

    def search(self, *, task_id, research_task, query, search_scope, **_kwargs):
        result = WebSearchResult(
            id=f"result_{research_task.id}_{len(self.store.load_many(task_id, 'web_search_results'))}",
            task_id=task_id,
            research_task_id=research_task.id,
            search_attempt_id=f"attempt_{research_task.id}",
            provider="offline",
            query=query,
            rank=1,
            title="Acme official product documentation",
            url=URL,
            snippet="Acme workspace collaboration and annual business billing.",
            selected_for_collection=True,
        )
        existing = self.store.load_many(task_id, "web_search_results")
        self.store.save_many(task_id, "web_search_results", [*existing, result])
        self.store.save_many(
            task_id,
            "official_domain_contexts",
            [
                OfficialDomainContext(
                    task_id=task_id,
                    competitor=research_task.competitor,
                    domain="acme.example",
                    confidence="confirmed",
                    research_task_ids=[research_task.id],
                    search_result_ids=[result.id],
                )
            ],
        )
        return {
            "query": query,
            "search_scope": search_scope,
            "results": [
                {
                    "title": result.title,
                    "url": result.url,
                    "snippet": result.snippet,
                    "source_level": "first_party",
                    "official_confidence": "confirmed",
                    "freshness": "unknown",
                }
            ],
        }

    def fetch(self, *, task_id, research_task, url):
        self.fetch_count += 1
        source = SourceDocument(
            id="source_acme_product",
            task_id=task_id,
            title="Acme product documentation",
            url=url,
            source_type="official_site",
            competitor=research_task.competitor,
            content_excerpt=PAGE_TEXT,
            metadata={"research_task_id": research_task.id},
        )
        page = WebPageContent(
            id="page_acme_product",
            task_id=task_id,
            source_id=source.id,
            requested_url=url,
            final_url=url,
            title=source.title,
            text=PAGE_TEXT,
            content_hash="acme_product_v1",
        )
        self.store.save_many(task_id, "sources", [source])
        self.store.save_many(task_id, "web_pages", [page])
        return {
            "source_id": source.id,
            "web_page_id": page.id,
            "url": url,
            "reused": False,
        }

    def read(self, *, task_id, research_task, source_id):
        page = WebPageContent(**self.store.load_many(task_id, "web_pages")[0])
        chunk = SourceChunk(
            id="chunk_acme_product",
            task_id=task_id,
            source_id=source_id,
            web_page_id=page.id,
            content_hash=page.content_hash,
            chunk_index=0,
            source_text_start=0,
            source_text_end=len(page.text),
            text=page.text,
            title=page.title,
            competitor=research_task.competitor,
            origin_research_task_id=str(
                SourceDocument(**self.store.load_many(task_id, "sources")[0]).metadata.get(
                    "research_task_id"
                )
                or ""
            ),
        )
        self.store.save_many(task_id, "source_chunks", [chunk])
        return {
            "source_id": source_id,
            "retrieval_run_id": "offline_mission_retrieval",
            "chunks": [
                {
                    "chunk_id": chunk.id,
                    "source_id": source_id,
                    "text": chunk.text,
                }
            ],
        }

    def submit_evidence(
        self,
        *,
        task_id,
        research_task,
        source_id,
        chunk_id,
        exact_quote,
        supports,
    ):
        source = SourceDocument(**self.store.load_many(task_id, "sources")[0])
        page = WebPageContent(**self.store.load_many(task_id, "web_pages")[0])
        chunk = SourceChunk(**self.store.load_many(task_id, "source_chunks")[0])
        association = next(
            (
                SourceTaskAssociation(**raw)
                for raw in self.store.load_many(
                    task_id, "source_task_associations"
                )
                if raw.get("research_task_id") == research_task.id
                and raw.get("source_id") == source_id
            ),
            None,
        )
        evidence = verify_candidate_evidence(
            task_id=task_id,
            research_task=research_task,
            source=source,
            page=page,
            chunk=chunk,
            exact_quote=exact_quote,
            supports=supports,
            source_association=association,
        )
        existing = [
            SourceEvidence(**raw)
            for raw in self.store.load_many(task_id, "evidence")
        ]
        self.store.save_many(task_id, "evidence", [*existing, evidence])
        return {
            "evidence_id": evidence.id,
            "source_id": source.id,
            "chunk_id": chunk.id,
            "quote_verified": True,
        }


def make_action(task_id: str, research_task_id: str, action: str, **values):
    return ResearchAgentAction(
        task_id=task_id,
        research_task_id=research_task_id,
        action=action,
        rationale=f"offline {action}",
        **values,
    )


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "checks"
        / "r1_research_mission_foundation"
    )
    store = ArtifactStore(root)
    task_id = "task_r1_research_mission_foundation"
    needs = [
        InformationNeed(
            id="need_feature",
            task_id=task_id,
            question_id="kiq_feature",
            dimension="feature",
            required_facts=["current features"],
            comparability_basis="same product scope",
            decision_link="product decision",
        ),
        InformationNeed(
            id="need_pricing",
            task_id=task_id,
            question_id="kiq_pricing",
            dimension="pricing",
            required_facts=["current pricing"],
            comparability_basis="same billing period",
            decision_link="product decision",
        ),
    ]
    tasks = [
        ResearchTask(
            id="worker_feature",
            task_id=task_id,
            information_need_id="need_feature",
            title="Acme feature",
            objective="Verify Acme feature",
            competitor="Acme",
            dimension="feature",
            status="waiting_for_collector",
            stop_condition="verified feature evidence",
        ),
        ResearchTask(
            id="worker_pricing",
            task_id=task_id,
            information_need_id="need_pricing",
            title="Acme pricing",
            objective="Verify Acme pricing",
            competitor="Acme",
            dimension="pricing",
            status="waiting_for_collector",
            stop_condition="verified pricing evidence",
        ),
    ]
    for artifact_type in (
        "sources",
        "web_pages",
        "source_chunks",
        "evidence",
        "web_search_results",
        "source_task_associations",
        "official_domain_contexts",
        "research_agent_runs",
        "research_agent_actions",
        "research_agent_observations",
        "dag_nodes",
        "agent_runs",
        "tool_calls",
        "product_cards",
        "evidence_coverage",
        "research_missions",
        "research_mission_states",
        "research_worker_contexts",
        "research_worker_results",
        "research_mission_decisions",
    ):
        store.save_many(task_id, artifact_type, [])
    store.save_many(task_id, "research_information_needs", needs)
    store.save_many(task_id, "research_tasks", tasks)

    mission_service = ResearchMissionService(store=store)
    missions = mission_service.ensure_missions(task_id)
    require(len(missions) == 1, "同 competitor 未聚合成一个 Mission")
    require(
        set(missions[0].information_need_ids)
        == {"need_feature", "need_pricing"},
        "Mission 未持有两个真实 InformationNeed",
    )

    tools = OfflineMissionTools(store)
    feature_decider = SequenceDecider(
        [
            make_action(
                task_id,
                "worker_feature",
                "SEARCH",
                query="Acme product official",
            ),
            make_action(task_id, "worker_feature", "FETCH", url=URL),
            make_action(
                task_id,
                "worker_feature",
                "READ",
                source_id="source_acme_product",
            ),
            make_action(
                task_id,
                "worker_feature",
                "SUBMIT_EVIDENCE",
                source_id="source_acme_product",
                chunk_id="chunk_acme_product",
                exact_quote=FEATURE_QUOTE,
                supports="feature",
            ),
            make_action(
                task_id,
                "worker_feature",
                "FINISH",
                finish_status="COMPLETE",
            ),
        ]
    )
    service = ResearchEvidenceAgentService(store=store)
    service.run_once(
        task_id,
        research_task_id="worker_feature",
        decider=feature_decider,
        tools=tools,
    )

    pricing_decider = SequenceDecider(
        [
            make_action(
                task_id,
                "worker_pricing",
                "SEARCH",
                query="Acme product official",
            ),
            make_action(task_id, "worker_pricing", "FETCH", url=URL),
            make_action(
                task_id,
                "worker_pricing",
                "READ",
                source_id="source_acme_product",
            ),
            make_action(
                task_id,
                "worker_pricing",
                "SUBMIT_EVIDENCE",
                source_id="source_acme_product",
                chunk_id="chunk_acme_product",
                exact_quote=PRICING_QUOTE,
                supports="pricing",
            ),
            make_action(
                task_id,
                "worker_pricing",
                "FINISH",
                finish_status="COMPLETE",
            ),
        ]
    )
    service.run_once(
        task_id,
        research_task_id="worker_pricing",
        decider=pricing_decider,
        tools=tools,
    )
    mission_service.refresh_coverage(task_id)

    pricing_context = pricing_decider.mission_contexts[0]
    require(
        pricing_context["related_sources"][0]["source_id"]
        == "source_acme_product",
        "第二 Worker 未收到 shared Source",
    )
    require(
        pricing_context["related_verified_evidence"],
        "第二 Worker 未收到 shared verified Evidence summary",
    )
    require(
        pricing_context["confirmed_official_domains"] == ["acme.example"],
        "第二 Worker 未收到 confirmed official domain",
    )
    require(
        pricing_context["conversation_history_shared"] is False
        and "recent_observations" not in pricing_context,
        "Worker 继承了完整对话/Observation history",
    )
    observations = store.load_many(task_id, "research_agent_observations")
    pricing_search = next(
        item
        for item in observations
        if item["research_task_id"] == "worker_pricing"
        and item["action"] == "SEARCH"
    )
    pricing_fetch = next(
        item
        for item in observations
        if item["research_task_id"] == "worker_pricing"
        and item["action"] == "FETCH"
    )
    require(
        pricing_search["status"] == "rejected",
        "Mission duplicate query 未阻止",
    )
    require(
        pricing_fetch["status"] == "rejected" and tools.fetch_count == 1,
        "Mission visited URL 未去重",
    )
    associations = store.load_many(task_id, "source_task_associations")
    require(
        any(
            item["research_task_id"] == "worker_pricing"
            and item["source_id"] == "source_acme_product"
            for item in associations
        ),
        "Mission shared Source 未建立 task-local association",
    )
    source = SourceDocument(**store.load_many(task_id, "sources")[0])
    require(
        source.metadata["research_task_id"] == "worker_feature",
        "Source 首次发现 provenance 被 Mission 覆盖",
    )
    evidence = store.load_many(task_id, "evidence")
    require(
        {item["metadata"]["research_task_id"] for item in evidence}
        == {"worker_feature", "worker_pricing"},
        "Worker Evidence submit provenance 未分别保留",
    )
    state = store.load_many(task_id, "research_mission_states")[0]
    require(
        len(state["attempted_queries"]) == 1
        and len(state["visited_urls"]) == 1
        and state["source_ids"] == ["source_acme_product"]
        and len(state["verified_evidence_ids"]) == 2,
        "MissionState merge/dedup 错误",
    )
    require(
        set(state["outcome_by_need"])
        == {"need_feature", "need_pricing"},
        "MissionState 未按真实 InformationNeed merge outcome",
    )
    require(
        len(state["worker_result_ids"]) == 2
        and len(store.load_many(task_id, "research_worker_results")) == 2,
        "Worker 未输出并 merge 结构化 ResearchWorkerResult",
    )
    require(
        set(state["coverage_status_by_need"])
        == {"need_feature", "need_pricing"},
        "MissionState 未 merge Coverage state",
    )
    print("check_r1_research_mission_foundation: PASS")
    print("deepseek_calls=0")
    print("tavily_calls=0")


if __name__ == "__main__":
    main()
