from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from app.execution.research_agent import (
    LLMResearchActionDecider,
    ResearchEvidenceAgentService,
    _extract_observed_terms,
    build_research_agent_llm_config,
)
from app.agents.web_evidence import verify_candidate_evidence
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, OpenAIChatCompletionsProvider
from app.schemas import (
    InformationNeed,
    ResearchAgentAction,
    ResearchAgentBudget,
    ResearchAgentObservation,
    ResearchAgentRun,
    ResearchTask,
    SourceChunk,
    SourceDocument,
    SourceEvidence,
    WebPageContent,
    WebSearchResult,
    utc_now,
)


QUOTE = "ClassIn EDU教育版按年度收取服务费，具体价格以官方报价为准。"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class TrajectoryDecider:
    def __init__(self, actions: list[ResearchAgentAction]):
        self.actions = list(actions)
        self.seen_states: list[dict] = []

    def decide(self, **kwargs) -> ResearchAgentAction:
        self.seen_states.append(kwargs["state"].model_dump(mode="json"))
        if not self.actions:
            raise AssertionError("Fake LLM trajectory 已耗尽")
        return self.actions.pop(0)


class FakeResearchTools:
    def __init__(self, store: ArtifactStore, task_id: str, research_task: ResearchTask):
        self.store = store
        self.task_id = task_id
        self.research_task = research_task
        self.search_scopes: list[str] = []
        self.fetch_calls: list[str] = []
        self.read_calls: list[str] = []
        self.submit_calls = 0

    def search(self, *, query: str, search_scope: str, **_kwargs) -> dict:
        self.search_scopes.append(search_scope)
        index = len(self.search_scopes)
        official = search_scope == "auto"
        url = (
            "https://docs.classin.example/pricing"
            if official else "https://community.example/classin-pricing"
        )
        result = WebSearchResult(
            id=f"search_result_{index}",
            task_id=self.task_id,
            research_task_id=self.research_task.id,
            search_attempt_id=f"attempt_{index}",
            provider="fake",
            query=query,
            rank=1,
            title="ClassIn EDU教育版官方收费说明" if official else "ClassIn 用户收费体验",
            url=url,
            snippet="ClassIn EDU教育版按年度收费，用户讨论了实际付款体验。",
            selected_for_collection=True,
        )
        existing = [WebSearchResult(**item) for item in self.store.load_many(self.task_id, "web_search_results")]
        self.store.save_many(self.task_id, "web_search_results", existing + [result])
        return {
            "query": query,
            "search_scope": search_scope,
            "selected_urls": [url],
            "results": [
                {
                    "id": result.id,
                    "title": result.title,
                    "url": result.url,
                    "snippet": result.snippet,
                    "selected": True,
                    "rejection_reason": "",
                }
            ],
        }
    def fetch(self, *, url: str, **_kwargs) -> dict:
        self.fetch_calls.append(url)
        source = SourceDocument(
            id="src_classin_pricing",
            task_id=self.task_id,
            title="ClassIn EDU教育版收费说明",
            url=url,
            source_type="docs",
            competitor="ClassIn",
            content_excerpt=QUOTE,
            metadata={"research_task_id": self.research_task.id, "discovered_url": url},
        )
        text = f"ClassIn 官方收费页面。\n{QUOTE}\n页面同时说明退款规则。"
        page = WebPageContent(
            id="page_classin_pricing",
            task_id=self.task_id,
            source_id=source.id,
            requested_url=url,
            final_url=url,
            title=source.title,
            text=text,
            content_hash="hash_classin_pricing_v1",
        )
        self.store.save_many(self.task_id, "sources", [source])
        self.store.save_many(self.task_id, "web_pages", [page])
        return {"source_id": source.id, "web_page_id": page.id, "final_url": url}

    def read(self, *, source_id: str, **_kwargs) -> dict:
        self.read_calls.append(source_id)
        page = WebPageContent(**self.store.load_many(self.task_id, "web_pages")[0])
        chunk = SourceChunk(
            id="chunk_classin_pricing",
            task_id=self.task_id,
            source_id=source_id,
            web_page_id=page.id,
            content_hash=page.content_hash,
            chunk_index=0,
            source_text_start=0,
            source_text_end=len(page.text),
            text=page.text,
            title=page.title,
            competitor="ClassIn",
            origin_research_task_id=self.research_task.id,
        )
        self.store.save_many(self.task_id, "source_chunks", [chunk])
        return {
            "source_id": source_id,
            "retrieval_run_id": "retrieval_fake",
            "chunks": [{"chunk_id": chunk.id, "source_id": source_id, "text": chunk.text}],
        }

    def submit_evidence(
        self,
        *,
        source_id: str,
        chunk_id: str,
        exact_quote: str,
        supports: str,
        **_kwargs,
    ) -> dict:
        self.submit_calls += 1
        source = SourceDocument(**self.store.load_many(self.task_id, "sources")[0])
        page = WebPageContent(**self.store.load_many(self.task_id, "web_pages")[0])
        chunk = SourceChunk(**self.store.load_many(self.task_id, "source_chunks")[0])
        require(source.id == source_id and chunk.id == chunk_id, "Fake Tool 引用错误")
        evidence = verify_candidate_evidence(
            task_id=self.task_id,
            research_task=self.research_task,
            source=source,
            page=page,
            chunk=chunk,
            exact_quote=exact_quote,
            supports=supports,
        )
        existing = [SourceEvidence(**item) for item in self.store.load_many(self.task_id, "evidence")]
        if all(item.id != evidence.id for item in existing):
            self.store.save_many(self.task_id, "evidence", existing + [evidence])
        return {
            "evidence_id": evidence.id,
            "source_id": source.id,
            "chunk_id": chunk.id,
            "quote_verified": True,
            "source_url": source.url,
            "content_hash": page.content_hash,
            "absolute_start": evidence.source_text_start,
            "absolute_end": evidence.source_text_end,
        }


class FailedUrlRecoveryTools(FakeResearchTools):
    failed_url = "https://docs.classin.example/pricing"
    alternate_url = "https://docs.classin.example/pricing-alt"

    def search(self, **kwargs) -> dict:
        payload = super().search(**kwargs)
        result = WebSearchResult(
            id="search_result_alternate",
            task_id=self.task_id,
            research_task_id=self.research_task.id,
            search_attempt_id="attempt_alternate",
            provider="fake",
            query=kwargs["query"],
            rank=2,
            title="ClassIn 另一条官方收费线索",
            url=self.alternate_url,
            snippet="另一条可供 Research Agent 重新决策的官方线索。",
            selected_for_collection=True,
        )
        existing = [
            WebSearchResult(**item)
            for item in self.store.load_many(self.task_id, "web_search_results")
        ]
        self.store.save_many(
            self.task_id,
            "web_search_results",
            [*existing, result],
        )
        payload["selected_urls"].append(self.alternate_url)
        payload["results"].append(
            {
                "id": result.id,
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet,
                "selected": True,
                "rejection_reason": "",
            }
        )
        return payload

    def fetch(self, *, url: str, **kwargs) -> dict:
        if url == self.failed_url:
            self.fetch_calls.append(url)
            raise RuntimeError("fixture fetch failed")
        return super().fetch(url=url, **kwargs)


def action(task_id: str, research_task_id: str, kind: str, **values) -> ResearchAgentAction:
    defaults = {"rationale": f"Fake LLM chooses {kind}"}
    defaults.update(values)
    return ResearchAgentAction(
        task_id=task_id,
        research_task_id=research_task_id,
        action=kind,
        **defaults,
    )


def setup(root: Path, name: str, *, objective: str = "核实 ClassIn 定价与收费模式"):
    task_id = f"task_research_agent_{name}"
    research_task = ResearchTask(
        id=f"research_task_{name}",
        task_id=task_id,
        information_need_id=f"need_{name}",
        title="研究 ClassIn 定价",
        objective=objective,
        competitor="ClassIn",
        dimension="pricing",
        query_hints=["ClassIn 官方 定价 收费"],
        status="waiting_for_collector",
        stop_condition="取得至少一条官方可验证定价证据",
    )
    need = InformationNeed(
        id=research_task.information_need_id,
        task_id=task_id,
        question_id=f"kiq_{name}",
        dimension="pricing",
        required_facts=["收费模式", "价格依据"],
        comparability_basis="相同计费周期",
        decision_link="判断采购成本",
    )
    store = ArtifactStore(root / name)
    store.save_many(task_id, "research_tasks", [research_task])
    store.save_many(task_id, "research_information_needs", [need])
    for artifact_type in (
        "sources", "web_pages", "source_chunks", "evidence", "web_search_results",
        "research_agent_runs", "research_agent_actions", "research_agent_observations",
        "dag_nodes", "agent_runs", "tool_calls", "llm_calls", "llm_outputs",
    ):
        store.save_many(task_id, artifact_type, [])
    return task_id, research_task, store


def run_case(root: Path, name: str, actions: list[ResearchAgentAction], *, objective: str = "核实 ClassIn 定价与收费模式", budget=None):
    task_id, research_task, store = setup(root, name, objective=objective)
    decider = TrajectoryDecider(actions)
    tools = FakeResearchTools(store, task_id, research_task)
    payload = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=research_task.id,
        decider=decider,
        tools=tools,
        budget=budget or ResearchAgentBudget(max_steps=12, max_searches=4, max_sources=4, max_failed_actions=3),
    )
    run = payload["runs"][-1]
    return task_id, research_task, store, decider, tools, run


def main() -> None:
    root = Path(__file__).resolve().parent / "app" / "data" / "checks" / "research_agent_r1"

    production_config = build_research_agent_llm_config()
    production_client = LLMClient(
        config=production_config,
        store=ArtifactStore(root / "provider_wiring"),
    )
    require(
        isinstance(production_client.provider, OpenAIChatCompletionsProvider),
        "Research Agent production config 仍进入 MockStructuredProvider",
    )
    require(
        production_config.base_url == "https://api.deepseek.com/v1"
        and production_config.api_key_env == "DEEPSEEK_API_KEY",
        "Research Agent 未使用共享 DeepSeek-compatible 默认契约",
    )
    action_item_schema = production_client.provider.output_json_schema(
        "ResearchAgentAction"
    )["properties"]["item"]
    action_schema = action_item_schema["properties"]
    require(
        not {
            "id", "task_id", "research_task_id", "created_at",
            "schema_version", "metadata",
        }.intersection(action_schema),
        "LLM ResearchAgentAction schema 仍暴露系统 ownership 字段",
    )
    require(
        any(
            "finish_status" in item.get("then", {}).get("required", [])
            for item in action_item_schema.get("allOf", [])
        ),
        "ResearchAgentAction JSON Schema 未声明 FINISH 的 finish_status 条件必填",
    )

    repair_task_id, repair_task, _repair_store = setup(root, "finish_repair")

    class FakeRepairClient:
        def __init__(self, *, fail_twice: bool = False):
            self.fail_twice = fail_twice
            self.calls: list[dict] = []

        def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1 or self.fail_twice:
                raise ValueError(
                    "ValueError: FINISH 缺少字段：finish_status"
                )
            repaired = ResearchAgentAction(
                task_id=repair_task_id,
                research_task_id=repair_task.id,
                action="FINISH",
                rationale="证据仍不完整，有限修复后结束。",
                finish_status="PARTIAL",
            )
            return {"item": repaired.model_dump(mode="json")}, None, None

    repair_client = FakeRepairClient()
    repaired_action = LLMResearchActionDecider(
        llm_client=repair_client,
    ).decide(
        task_id=repair_task_id,
        research_task=repair_task,
        information_need=None,
        state=ResearchAgentRun(
            task_id=repair_task_id,
            research_task_id=repair_task.id,
        ),
        recent_observations=[],
    )
    require(
        repaired_action.finish_status == "PARTIAL"
        and len(repair_client.calls) == 2
        and repair_client.calls[-1]["node_id"].endswith("_finish_repair_1"),
        "FINISH 缺字段没有执行唯一一次 structured repair",
    )
    always_invalid = FakeRepairClient(fail_twice=True)
    try:
        LLMResearchActionDecider(llm_client=always_invalid).decide(
            task_id=repair_task_id,
            research_task=repair_task,
            information_need=None,
            state=ResearchAgentRun(
                task_id=repair_task_id,
                research_task_id=repair_task.id,
            ),
            recent_observations=[],
        )
    except ValueError:
        require(
            len(always_invalid.calls) == 2,
            "FINISH structured repair 未保持一次上限",
        )
    else:
        raise AssertionError("无效 FINISH 被默认成 COMPLETE 或伪装成功")

    env_name = "RESEARCH_AGENT_LLM_API_KEY_ENV"
    previous = os.environ.get(env_name)
    os.environ[env_name] = "DEEPSEEK_API_KEY_MISSING_RESEARCH_AGENT_TEST"
    try:
        missing_config = build_research_agent_llm_config()
        readiness_errors = missing_config.real_call_readiness_errors()
        require(
            any("DEEPSEEK_API_KEY_MISSING_RESEARCH_AGENT_TEST" in item for item in readiness_errors),
            "缺失 DeepSeek API Key 没有明确失败",
        )
        missing_task_id, missing_task, missing_store = setup(root, "missing_key")
        try:
            ResearchEvidenceAgentService(store=missing_store).run_once(
                missing_task_id,
                research_task_id=missing_task.id,
                mode="deepseek",
                acknowledge_real_llm_call=True,
            )
        except ValueError as exc:
            require(
                "DEEPSEEK_API_KEY_MISSING_RESEARCH_AGENT_TEST" in str(exc),
                "Research Agent Service 缺 Key 错误不明确",
            )
        else:
            raise AssertionError("Research Agent Service 缺 Key 时没有失败")
    finally:
        if previous is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = previous

    name = "system_field_ownership"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    before = utc_now()
    forged_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    _, _, store, _, _, _ = run_case(
        root,
        name,
        [
            ResearchAgentAction(
                id="action_1",
                task_id="task_forged",
                research_task_id="research_task_forged",
                action="SEARCH",
                rationale="Fake LLM forged system fields",
                query="ClassIn 官方 定价 收费",
                search_scope="auto",
                created_at=forged_time,
                schema_version="forged",
                metadata={"owner": "llm"},
            ),
            ResearchAgentAction(
                id="action_2",
                task_id="task_forged",
                research_task_id="research_task_forged",
                action="FINISH",
                rationale="stop",
                finish_status="EXHAUSTED",
                created_at=forged_time,
            ),
        ],
    )
    after = utc_now()
    persisted_action = store.load_many(task_id, "research_agent_actions")[0]
    persisted_created_at = datetime.fromisoformat(persisted_action["created_at"])
    require(
        persisted_action["id"].startswith("researchaction_")
        and persisted_action["id"] != "action_1",
        "LLM 伪造 action id 被持久化",
    )
    require(
        persisted_action["task_id"] == task_id
        and persisted_action["research_task_id"] == rid,
        "LLM 伪造 task identity 被持久化",
    )
    require(
        before <= persisted_created_at <= after
        and persisted_action["schema_version"] == "v1"
        and persisted_action["metadata"] == {},
        "created_at/schema_version/metadata 未由后端接管",
    )

    observation = ResearchAgentObservation(
        task_id=task_id,
        research_task_id=rid,
        action_id=persisted_action["id"],
        action="SEARCH",
        status="completed",
        summary="fixture",
        payload={
            "search_scope": "auto",
            "source_id": "src_should_not_be_seen",
            "url": "https://www.example.com/path",
            "results": [
                {
                    "id": "searchresult_should_be_filtered",
                    "title": "星桥协议 NebulaKey auto searchresult_fake src_fake",
                    "snippet": (
                        "星桥协议；NebulaKey；www com for and you；"
                        "chunk_fake retrieval_fake abcdef0123456789abcdef0123456789"
                    ),
                    "url": "https://www.example.com/path",
                }
            ],
        },
    )
    fixture_task = ResearchTask(**store.load_many(task_id, "research_tasks")[0])
    observed = _extract_observed_terms(
        observation,
        task=fixture_task,
        existing=[],
    )
    terms = {item.term.casefold() for item in observed}
    require({"星桥协议", "nebulakey"}.issubset(terms), "Observation 原样新词未保留")
    require(
        not terms.intersection(
            {
                "auto", "www", "com", "for", "and", "you",
                "searchresult_fake", "src_fake", "chunk_fake", "retrieval_fake",
                "abcdef0123456789abcdef0123456789",
            }
        ),
        f"结构垃圾进入 observed_terms：{sorted(terms)}",
    )
    require("幽灵术语" not in terms, "Observation 中未出现的词被生成")
    require(
        all(
            item.provenance_id == observation.id
            and item.term in f"{observation.payload['results'][0]['title']} {observation.payload['results'][0]['snippet']}"
            for item in observed
        ),
        "ObservedTerm exact presence/provenance 不完整",
    )

    name = "pricing_complete"
    task_id = f"task_research_agent_{name}"
    rid = f"research_task_{name}"
    complete_actions = [
        action(task_id, rid, "SEARCH", query="ClassIn 官方 定价 收费", search_scope="auto"),
        action(task_id, rid, "FETCH", url="https://docs.classin.example/pricing"),
        action(task_id, rid, "READ", source_id="src_classin_pricing"),
        action(task_id, rid, "SUBMIT_EVIDENCE", source_id="src_classin_pricing", chunk_id="chunk_classin_pricing", exact_quote=QUOTE, supports="ClassIn EDU教育版采用年度服务费模式"),
        action(task_id, rid, "FINISH", finish_status="COMPLETE", remaining_need=""),
    ]
    _task_id, _task, store, decider, tools, run = run_case(root, name, complete_actions)
    require(run["outcome"] == "COMPLETE", "pricing trajectory 未 COMPLETE")
    evidence = store.load_many(task_id, "evidence")
    require(len(evidence) == 1 and evidence[0]["metadata"]["quote_verified"], "证据未验证")
    require(decider.seen_states[-1]["verified_evidence_ids"], "下一轮 LLM 未看到 Verified Evidence")
    require(
        store.load_many(task_id, "research_tasks")[0]["status"] == "evidence_extracted",
        "ResearchTask 未推进到 evidence_extracted",
    )
    repeated = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=rid,
        decider=TrajectoryDecider([]),
        tools=tools,
    )
    require(repeated["status"] == "already_terminal", "终态任务发生重复 LLM/Tool 执行")

    name = "community"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    _, _, _, _, tools, _ = run_case(
        root,
        name,
        [
            action(task_id, rid, "SEARCH", query="ClassIn 用户 体验 社区 反馈", search_scope="community"),
            action(task_id, rid, "FINISH", finish_status="EXHAUSTED"),
        ],
        objective="收集 ClassIn 用户体验与社区反馈",
    )
    require(tools.search_scopes == ["community"], "用户反馈没有直接使用 community strategy")

    name = "official_fallback"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    _, _, _, decider, tools, _ = run_case(
        root,
        name,
        [
            action(task_id, rid, "SEARCH", query="ClassIn 官方 定价 收费", search_scope="auto"),
            action(task_id, rid, "FETCH", url="https://docs.classin.example/pricing"),
            action(task_id, rid, "READ", source_id="src_classin_pricing"),
            action(task_id, rid, "SEARCH", query="ClassIn EDU教育版 定价", search_scope="general"),
            action(task_id, rid, "FINISH", finish_status="EXHAUSTED"),
        ],
    )
    require(tools.search_scopes == ["auto", "general"], "official insufficient 未 general fallback")
    require(any(item["observed_terms"] for item in decider.seen_states[1:]), "Observation 新术语未进入状态")

    name = "unobserved_term"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    _, _, store, _, tools, run = run_case(
        root,
        name,
        [
            action(task_id, rid, "SEARCH", query="ClassIn 企业版 定价", search_scope="auto"),
            action(task_id, rid, "FINISH", finish_status="EXHAUSTED"),
        ],
    )
    require(
        tools.search_scopes == ["auto"],
        "保持 ClassIn 对象锚点的新产品术语被错误拒绝",
    )
    require(not run["failed_actions"], "合法的新探索词产生了失败 Observation")

    name = "dedupe"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    _, _, store, _, tools, run = run_case(
        root,
        name,
        [
            action(task_id, rid, "SEARCH", query="ClassIn 官方 定价 收费", search_scope="auto"),
            action(task_id, rid, "SEARCH", query="ClassIn 官方 定价 收费", search_scope="auto"),
            action(task_id, rid, "FETCH", url="https://docs.classin.example/pricing"),
            action(task_id, rid, "FETCH", url="https://docs.classin.example/pricing"),
            action(task_id, rid, "FINISH", finish_status="EXHAUSTED"),
        ],
        budget=ResearchAgentBudget(max_steps=8, max_searches=4, max_sources=4, max_failed_actions=4),
    )
    require(len(tools.search_scopes) == 1 and len(tools.fetch_calls) == 1, "Query/URL 去重失败")
    rejected = [
        item
        for item in store.load_many(task_id, "research_agent_observations")
        if item["status"] == "rejected"
    ]
    require(len(rejected) == 2, "重复动作未作为 rejected Observation 记录")
    require(not run["failed_actions"], "已知重复动作不应重复消耗 failed action 预算")

    name = "failed_url_recovery"
    task_id, research_task, store = setup(root, name)
    failed_tools = FailedUrlRecoveryTools(store, task_id, research_task)
    failed_decider = TrajectoryDecider(
        [
            action(
                task_id,
                research_task.id,
                "SEARCH",
                query="ClassIn 官方 定价 收费",
                search_scope="auto",
            ),
            action(
                task_id,
                research_task.id,
                "FETCH",
                url=failed_tools.failed_url,
            ),
            action(
                task_id,
                research_task.id,
                "FETCH",
                url=failed_tools.failed_url,
            ),
            action(
                task_id,
                research_task.id,
                "FETCH",
                url=failed_tools.alternate_url,
            ),
            action(
                task_id,
                research_task.id,
                "FINISH",
                finish_status="EXHAUSTED",
            ),
        ]
    )
    recovery_payload = ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=research_task.id,
        decider=failed_decider,
        tools=failed_tools,
        budget=ResearchAgentBudget(
            max_steps=8,
            max_searches=3,
            max_sources=3,
            max_failed_actions=2,
        ),
    )
    recovery_run = recovery_payload["runs"][-1]
    require(
        failed_tools.fetch_calls
        == [failed_tools.failed_url, failed_tools.alternate_url],
        "失败 URL 被重复调用工具，或 Agent 未改选其他 source",
    )
    require(
        len(recovery_run["failed_actions"]) == 1
        and failed_tools.failed_url in recovery_run["rejected_sources"],
        "失败 URL 未进入可见 Research State，或重复消耗失败预算",
    )
    fetch_observations = [
        item
        for item in store.load_many(task_id, "research_agent_observations")
        if item["action"] == "FETCH"
    ]
    require(
        [item["status"] for item in fetch_observations]
        == ["failed", "rejected", "completed"],
        "失败、重复拒绝、改选来源没有形成完整 Observation 轨迹",
    )

    name = "multi_task_run_persistence"
    task_id, first_task, store = setup(root, name)
    first_need = InformationNeed(
        **store.load_many(task_id, "research_information_needs")[0]
    )
    second_task = first_task.model_copy(
        update={
            "id": f"{first_task.id}_partial",
            "information_need_id": f"{first_need.id}_partial",
            "title": "第二个 ResearchTask",
        }
    )
    third_task = first_task.model_copy(
        update={
            "id": f"{first_task.id}_exhausted",
            "information_need_id": f"{first_need.id}_exhausted",
            "title": "第三个 ResearchTask",
        }
    )
    store.save_many(task_id, "research_tasks", [first_task, second_task, third_task])
    store.save_many(
        task_id,
        "research_information_needs",
        [
            first_need,
            first_need.model_copy(update={"id": second_task.information_need_id}),
            first_need.model_copy(update={"id": third_task.information_need_id}),
        ],
    )
    first_tools = FakeResearchTools(store, task_id, first_task)
    ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=first_task.id,
        decider=TrajectoryDecider(
            [
                action(
                    task_id,
                    first_task.id,
                    "SEARCH",
                    query="ClassIn 官方 定价 收费",
                    search_scope="auto",
                ),
                action(
                    task_id,
                    first_task.id,
                    "FETCH",
                    url="https://docs.classin.example/pricing",
                ),
                action(
                    task_id,
                    first_task.id,
                    "READ",
                    source_id="src_classin_pricing",
                ),
                action(
                    task_id,
                    first_task.id,
                    "SUBMIT_EVIDENCE",
                    source_id="src_classin_pricing",
                    chunk_id="chunk_classin_pricing",
                    exact_quote=QUOTE,
                    supports="ClassIn EDU教育版采用年度服务费模式",
                ),
                action(
                    task_id,
                    first_task.id,
                    "FINISH",
                    finish_status="COMPLETE",
                ),
            ]
        ),
        tools=first_tools,
    )
    for research_task, outcome in (
        (second_task, "PARTIAL"),
        (third_task, "EXHAUSTED"),
    ):
        ResearchEvidenceAgentService(store=store).run_once(
            task_id,
            research_task_id=research_task.id,
            decider=TrajectoryDecider(
                [
                    action(
                        task_id,
                        research_task.id,
                        "FINISH",
                        finish_status=outcome,
                    )
                ]
            ),
            tools=FakeResearchTools(store, task_id, research_task),
        )
    persisted_runs = [
        ResearchAgentRun(**item)
        for item in store.load_many(task_id, "research_agent_runs")
    ]
    require(
        len(persisted_runs) == 3
        and len({item.id for item in persisted_runs}) == 3
        and {item.outcome for item in persisted_runs}
        == {"COMPLETE", "PARTIAL", "EXHAUSTED"},
        "多 ResearchTask run 被覆盖，或同 run id 更新产生重复记录",
    )
    complete_run = next(item for item in persisted_runs if item.outcome == "COMPLETE")
    require(
        complete_run.verified_evidence_ids,
        "后续 ResearchTask 保存覆盖了先前 run 的 verified_evidence_ids",
    )
    before_terminal_read = [item.model_dump(mode="json") for item in persisted_runs]
    ResearchEvidenceAgentService(store=store).run_once(
        task_id,
        research_task_id=first_task.id,
        decider=TrajectoryDecider([]),
        tools=first_tools,
    )
    require(
        store.load_many(task_id, "research_agent_runs") == before_terminal_read,
        "读取终态任务改变了 run 历史，不满足幂等",
    )

    name = "fake_quote"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    _, _, store, _, _, run = run_case(
        root,
        name,
        [
            action(task_id, rid, "SEARCH", query="ClassIn 官方 定价 收费", search_scope="auto"),
            action(task_id, rid, "FETCH", url="https://docs.classin.example/pricing"),
            action(task_id, rid, "READ", source_id="src_classin_pricing"),
            action(task_id, rid, "SUBMIT_EVIDENCE", source_id="src_classin_pricing", chunk_id="chunk_classin_pricing", exact_quote="ClassIn 企业版永久免费。", supports="企业版免费"),
            action(task_id, rid, "FINISH", finish_status="PARTIAL"),
        ],
    )
    require(not store.load_many(task_id, "evidence") and not run["verified_evidence_ids"], "Fake quote 进入正式 Evidence")

    name = "max_steps"
    task_id, rid = f"task_research_agent_{name}", f"research_task_{name}"
    _, _, _, _, _, run = run_case(
        root,
        name,
        [
            action(task_id, rid, "SEARCH", query="ClassIn 官方 定价 收费", search_scope="auto"),
            action(task_id, rid, "FETCH", url="https://docs.classin.example/pricing"),
        ],
        budget=ResearchAgentBudget(max_steps=2, max_searches=2, max_sources=2, max_failed_actions=2),
    )
    require(run["outcome"] == "EXHAUSTED" and run["step_count"] == 2, "max_steps 未有限结束")

    print("check_research_agent_r1: PASS")
    print("production_provider=OpenAIChatCompletionsProvider")
    print("missing_deepseek_key_fails_explicitly=true")
    print("backend_owned_action_audit_fields=true")
    print("finish_status_schema_and_bounded_repair=true")
    print("domain_agnostic_observed_term_filter=true")
    print("pricing_official_first_complete=true")
    print("community_strategy=true")
    print("official_insufficient_general_fallback=true")
    print("observation_driven_query_expansion=true")
    print("object_anchored_query_expansion=true")
    print("query_url_deduped=true")
    print("failed_url_redecision=true")
    print("multi_research_task_runs_preserved=true")
    print("fake_quote_rejected=true")
    print("max_steps_bounded=true")
    print("provenance_invariants=true")
    print("real_network_used=false")
    print("real_llm_calls=0")


if __name__ == "__main__":
    main()
