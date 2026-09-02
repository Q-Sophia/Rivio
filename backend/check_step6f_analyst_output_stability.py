from __future__ import annotations

from pathlib import Path

from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient, LLMConfig
from app.llm.client import LLMOutputTruncatedError, LLMStructuredOutputError
from app.llm.provider import (
    LLMProviderOutputTruncatedError,
    LLMProviderResponseError,
    OpenAIChatCompletionsProvider,
    ProviderResult,
    StructuredLLMProvider,
)
from app.llm.structured import validate_portfolio_v2_refs
from app.schemas import (
    AgentContext,
    AgentRole,
    AnalysisTask,
    CompetitiveAnalysisPortfolioV2,
    ContextBundle,
    EvidenceCoverage,
    InformationNeed,
    KeyIntelligenceQuestion,
    LLMMode,
    LLMProvider,
    ProductCard,
    ResearchAgentRun,
    ResearchGap,
    ResearchPlan,
    ResearchPlanStatus,
    ResearchTask,
    RunStatus,
    SourceDocument,
    SourceEvidence,
    TaskPriority,
)
from app.workflow.snapshot_pipeline import build_snapshot_tool_registry
from app.workflow.trace import TraceRecorder
from app.agents import LLMProfessionalAnalystAgent
from app.execution.research_analysis import ResearchAnalysisService


TASK_ID = "task_step6f_output_stability"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class StageFixtureProvider(StructuredLLMProvider):
    """Offline provider that can deterministically simulate length truncation."""

    def __init__(
        self,
        *,
        mock_client: LLMClient,
        truncate_counts=None,
        malformed_counts=None,
        invalid_schema_counts=None,
        generation_cards: list[ProductCard] | None = None,
    ):
        self.mock_client = mock_client
        self.truncate_counts = dict(truncate_counts or {})
        self.malformed_counts = dict(malformed_counts or {})
        self.invalid_schema_counts = dict(invalid_schema_counts or {})
        self.generation_cards = list(generation_cards or [])
        self.calls: list[str] = []
        self.requests: list[dict] = []

    def generate(self, **kwargs) -> ProviderResult:
        output_schema = kwargs["output_schema"]
        self.calls.append(output_schema)
        self.requests.append(kwargs)
        remaining = self.truncate_counts.get(output_schema, 0)
        if remaining:
            self.truncate_counts[output_schema] = remaining - 1
            raise LLMProviderOutputTruncatedError(
                "fixture JSON truncated；finish_reason=length",
            )
        malformed_remaining = self.malformed_counts.get(output_schema, 0)
        if malformed_remaining:
            self.malformed_counts[output_schema] = malformed_remaining - 1
            raise LLMProviderResponseError(
                "fixture malformed JSON；finish_reason=stop",
                raw_output_text='{"item": {"task_id": "broken",},}',
                finish_reason="stop",
            )
        invalid_remaining = self.invalid_schema_counts.get(output_schema, 0)
        if invalid_remaining:
            self.invalid_schema_counts[output_schema] = invalid_remaining - 1
            return ProviderResult(
                raw_output={"item": {"task_id": kwargs["task_id"]}},
                request_id=f"offline_invalid_{len(self.calls)}",
                metadata={"finish_reason": "stop"},
            )
        artifacts = kwargs["artifacts"]
        if self.generation_cards and not artifacts.get("product_cards"):
            artifacts = {
                **artifacts,
                "product_cards": [
                    item.model_dump(mode="json") for item in self.generation_cards
                ],
            }
        return ProviderResult(
            raw_output=self.mock_client._mock_generate(
                task_id=kwargs["task_id"],
                agent_role=kwargs["agent_role"],
                output_schema=output_schema,
                artifacts=artifacts,
            ),
            request_id=f"offline_{len(self.calls)}",
            input_tokens=100,
            output_tokens=200,
            metadata={"finish_reason": "stop"},
        )


def fixture_models():
    task = AnalysisTask(
        id=TASK_ID,
        task_id=TASK_ID,
        query="比较 ClassIn 与 BigBlueButton 的教学能力与采购取舍",
        industry="在线教育",
        competitors=["ClassIn", "BigBlueButton"],
        focus_areas=["feature", "pricing"],
        status=RunStatus.COMPLETED,
    )
    sources = [
        SourceDocument(
            id="src_classin",
            task_id=TASK_ID,
            title="ClassIn 官方功能",
            url="https://example.test/classin",
            source_type="official_site",
            competitor="ClassIn",
            content_excerpt="ClassIn 提供互动教学工具。",
            reliability_score=0.9,
        ),
        SourceDocument(
            id="src_bbb",
            task_id=TASK_ID,
            title="BigBlueButton 官方功能",
            url="https://example.test/bbb",
            source_type="official_site",
            competitor="BigBlueButton",
            content_excerpt="BigBlueButton 是开源虚拟课堂。",
            reliability_score=0.9,
        ),
    ]
    evidence = [
        SourceEvidence(
            id="ev_classin_feature",
            task_id=TASK_ID,
            source_id="src_classin",
            competitor="ClassIn",
            dimension="feature",
            snippet="提供互动教学工具",
            normalized_fact="ClassIn 提供互动教学工具。",
            confidence=0.9,
        ),
        SourceEvidence(
            id="ev_classin_pricing",
            task_id=TASK_ID,
            source_id="src_classin",
            competitor="ClassIn",
            dimension="pricing",
            snippet="采购价格需要询价",
            normalized_fact="ClassIn 的采购价格需要询价。",
            confidence=0.75,
        ),
        SourceEvidence(
            id="ev_bbb_feature",
            task_id=TASK_ID,
            source_id="src_bbb",
            competitor="BigBlueButton",
            dimension="feature",
            snippet="开源虚拟课堂",
            normalized_fact="BigBlueButton 是开源虚拟课堂。",
            confidence=0.9,
        ),
    ]
    cards = [
        ProductCard(
            id="prod_classin",
            task_id=TASK_ID,
            name="ClassIn",
            company="ClassIn",
            positioning="面向教学组织的互动课堂产品。",
            target_users=["学校与培训机构"],
            pricing_summary="采购价格需要询价。",
            core_features=["互动教学工具"],
            strengths=["教学互动能力有官方证据"],
            weaknesses=["公开定价资料有限"],
            source_ids=["src_classin"],
            evidence_ids=["ev_classin_feature", "ev_classin_pricing"],
            confidence=0.82,
        ),
        ProductCard(
            id="prod_bbb",
            task_id=TASK_ID,
            name="BigBlueButton",
            company="BigBlueButton",
            positioning="面向在线教学的开源虚拟课堂。",
            target_users=["具备技术运维能力的教学组织"],
            pricing_summary="当前没有同口径采购价格证据。",
            core_features=["开源虚拟课堂"],
            strengths=["部署控制权较高"],
            weaknesses=["需要评估运维责任"],
            source_ids=["src_bbb"],
            evidence_ids=["ev_bbb_feature"],
            confidence=0.8,
        ),
    ]
    question = KeyIntelligenceQuestion(
        id="kiq_feature",
        task_id=TASK_ID,
        question="两种方案的教学能力和责任边界如何影响采购？",
        decision_link=task.query,
        dimensions=["feature", "pricing"],
        priority=TaskPriority.HIGH,
    )
    need = InformationNeed(
        id="need_feature",
        task_id=TASK_ID,
        question_id=question.id,
        dimension="feature",
        required_facts=["教学能力", "责任边界"],
        preferred_source_types=["官方资料"],
        comparability_basis="相同教学场景和责任范围。",
        decision_link=task.query,
    )
    coverage = [
        EvidenceCoverage(
            id="coverage_classin_feature",
            task_id=TASK_ID,
            competitor="ClassIn",
            dimension="feature",
            status="partial",
            source_ids=["src_classin"],
            evidence_ids=["ev_classin_feature"],
            limitations="当前只有一条官方证据。",
        ),
        EvidenceCoverage(
            id="coverage_bbb_pricing",
            task_id=TASK_ID,
            competitor="BigBlueButton",
            dimension="pricing",
            status="missing",
            limitations="当前没有定价证据。",
        ),
    ]
    gaps = [
        ResearchGap(
            id="gap_bbb_pricing",
            task_id=TASK_ID,
            competitors=["BigBlueButton"],
            dimension="pricing",
            missing_information="BigBlueButton 缺少同口径总体成本资料。",
            decision_blocked="暂时不能比较总体拥有成本。",
            why_existing_evidence_is_insufficient="现有证据只说明产品形态。",
            suggested_queries=["查找部署和运维成本资料"],
            preferred_source_types=["官方文档"],
            priority=TaskPriority.HIGH,
            stop_condition="获得同场景部署与运维成本，或确认无公开资料。",
            related_evidence_ids=["ev_bbb_feature"],
        )
    ]
    plan = ResearchPlan(
        id="researchplan_fixture",
        task_id=TASK_ID,
        decision_question=task.query,
        status=ResearchPlanStatus.READY_FOR_ANALYSIS,
        kiq_ids=[question.id],
        information_need_ids=[need.id],
        covered_competitors=task.competitors,
        covered_dimensions=["feature"],
        missing_dimensions=["pricing"],
    )
    return task, sources, evidence, cards, question, need, coverage, gaps, plan


def prepare_store(root: Path) -> tuple[ArtifactStore, AnalysisTask]:
    store = ArtifactStore(root_dir=root)
    task, sources, evidence, cards, question, need, coverage, gaps, plan = (
        fixture_models()
    )
    artifacts = {
        "analysis_tasks": [task],
        "sources": sources,
        "evidence": [*evidence, evidence[0]],
        "product_cards": cards,
        "research_plans": [plan],
        "research_kiqs": [question],
        "research_information_needs": [need],
        "evidence_coverage": coverage,
        "research_gaps": gaps,
        "llm_calls": [],
        "llm_outputs": [],
        "analysis_portfolios": [],
        "analysis_evidence_coverage": [],
        "analysis_research_gaps": [],
        "brief_assessments": [],
        "competitor_profiles": [],
        "intelligence_questions": [],
        "information_needs": [],
        "comparability_notes": [],
        "claims_v2": [],
        "claims": [],
    }
    for artifact_type, items in artifacts.items():
        store.save_many(TASK_ID, artifact_type, items)
    return store, task


def config() -> LLMConfig:
    return LLMConfig(
        provider=LLMProvider.MOCK,
        model="offline-stage-fixture",
        mode=LLMMode.LLM,
        max_tokens=8000,
        output_language="zh-CN",
    )


def run_agent(
    store: ArtifactStore,
    task: AnalysisTask,
    provider,
    *,
    authorized_evidence_ids: list[str] | None = None,
):
    recorder = TraceRecorder(store=store, task_id=TASK_ID)
    tools = build_snapshot_tool_registry(store=store, recorder=recorder)
    client = LLMClient(config=config(), store=store, provider=provider)
    agent = LLMProfessionalAnalystAgent(
        name="offline_step6f_analyst",
        role=AgentRole.ANALYST,
        tools=tools,
        llm_client=client,
    )
    context = AgentContext(
        task_id=TASK_ID,
        task=task,
        node_id="step6f_fixture",
        metadata={
            "agent_run_id": "run_step6f_fixture",
            "preserve_research_artifacts": True,
            "authorized_evidence_ids": authorized_evidence_ids or [],
            "require_r1_evidence_authority": (
                authorized_evidence_ids is not None
            ),
        },
    )
    return agent.execute(context)


def build_provider(
    store: ArtifactStore,
    *,
    truncate_counts=None,
    malformed_counts=None,
    invalid_schema_counts=None,
    generation_cards: list[ProductCard] | None = None,
):
    mock_client = LLMClient(config=config(), store=store)
    return StageFixtureProvider(
        mock_client=mock_client,
        truncate_counts=truncate_counts,
        malformed_counts=malformed_counts,
        invalid_schema_counts=invalid_schema_counts,
        generation_cards=generation_cards,
    )


def check_successful_assembly(root: Path) -> None:
    store, task = prepare_store(root)
    original_coverage = store.load_many(TASK_ID, "evidence_coverage")
    provider = build_provider(store)
    result = run_agent(store, task, provider)
    require(result.status == RunStatus.COMPLETED, "两阶段 Analyst 应成功")
    require(
        provider.calls
        == ["AnalystBriefProfilesStage", "AnalystClaimsStage"],
        "Analyst 不应再依赖一次完整 Portfolio JSON",
    )
    portfolios = store.load_many(TASK_ID, "analysis_portfolios")
    require(len(portfolios) == 1, "应确定性组装一个 Portfolio")
    portfolio = CompetitiveAnalysisPortfolioV2(**portfolios[0])
    evidence = [SourceEvidence(**item) for item in store.load_many(TASK_ID, "evidence")]
    sources = [SourceDocument(**item) for item in store.load_many(TASK_ID, "sources")]
    validate_portfolio_v2_refs(
        portfolio,
        known_source_ids={item.id for item in sources},
        known_evidence_ids={item.id for item in evidence},
        known_competitors={"ClassIn", "BigBlueButton"},
        evidence_competitors={item.id: item.competitor for item in evidence},
    )
    require(portfolio.items, "部分资料不足时仍应形成当前证据支持的 Claims")
    require(
        store.load_many(TASK_ID, "evidence_coverage") == original_coverage,
        "上游 EvidenceCoverage 不得被覆盖",
    )
    require(
        store.load_many(TASK_ID, "analysis_evidence_coverage")
        == original_coverage,
        "Portfolio 应原样引用上游 EvidenceCoverage",
    )
    known_ids = {item.id for item in evidence}
    require(
        all(
            set(claim.evidence_ids + claim.counter_evidence_ids) <= known_ids
            for claim in portfolio.items
        ),
        "Claim evidence_id 不得越界",
    )
    require(
        portfolio.metadata["raw_evidence_count"] == 4
        and portfolio.metadata["deduped_evidence_count"] == 3,
        "同一 evidence_id 应在送入模型前去重",
    )


def check_research_agent_bridge_without_product_cards(root: Path) -> None:
    store, task = prepare_store(root)
    generation_cards = fixture_models()[3]
    store.save_many(TASK_ID, "product_cards", [])
    store.save_many(TASK_ID, "evidence_coverage", [])
    store.save_many(TASK_ID, "research_gaps", [])

    research_tasks = [
        ResearchTask(
            id="researchtask_complete",
            task_id=TASK_ID,
            information_need_id="need_feature",
            title="ClassIn 功能",
            objective="确认 ClassIn 的教学能力。",
            competitor="ClassIn",
            dimension="feature",
            stop_condition="取得可引用的功能证据。",
            status="evidence_extracted",
        ),
        ResearchTask(
            id="researchtask_exhausted",
            task_id=TASK_ID,
            information_need_id="need_feature",
            title="BigBlueButton 定价",
            objective="确认 BigBlueButton 的同口径成本。",
            competitor="BigBlueButton",
            dimension="pricing",
            query_hints=["BigBlueButton pricing"],
            preferred_source_types=["官方定价"],
            stop_condition="取得同口径成本，或确认公开资料已穷尽。",
            status="evidence_exhausted",
        ),
    ]
    runs = [
        ResearchAgentRun(
            id="researchagentrun_complete",
            task_id=TASK_ID,
            research_task_id="researchtask_complete",
            status=RunStatus.COMPLETED,
            outcome="COMPLETE",
            verified_evidence_ids=["ev_classin_feature"],
        ),
        ResearchAgentRun(
            id="researchagentrun_exhausted",
            task_id=TASK_ID,
            research_task_id="researchtask_exhausted",
            status=RunStatus.COMPLETED,
            outcome="EXHAUSTED",
            verified_evidence_ids=["ev_bbb_feature"],
            remaining_need="缺少同口径公开成本。",
        ),
    ]
    store.save_many(TASK_ID, "research_tasks", research_tasks)
    store.save_many(TASK_ID, "research_agent_runs", runs)

    service = ResearchAnalysisService(store=store)
    readiness = service.get_payload(TASK_ID)
    require(readiness["can_analyze"], "有 Verified Evidence 时应允许分析")
    require(readiness["product_card_count"] == 0, "测试不得依赖 ProductCard")
    service._validate_inputs(TASK_ID)
    service._ensure_research_agent_gaps(TASK_ID)
    gaps = store.load_many(TASK_ID, "research_gaps")
    require(len(gaps) == 1, "COMPLETE 不产缺口，EXHAUSTED 应形成 ResearchGap")
    require(
        gaps[0]["metadata"]["research_task_id"] == "researchtask_exhausted",
        "ResearchGap 应追溯到 EXHAUSTED ResearchTask",
    )

    provider = build_provider(store, generation_cards=generation_cards)
    result = run_agent(store, task, provider)
    require(result.status == RunStatus.COMPLETED, "无 ProductCard 时 Analyst 应可运行")
    require(store.load_many(TASK_ID, "claims_v2"), "应生成证据约束的 Claims")
    require(
        store.load_many(TASK_ID, "analysis_research_gaps") == gaps,
        "混合 COMPLETE/EXHAUSTED 的缺口应进入分析组合",
    )


def check_r1_verified_evidence_authority(root: Path) -> None:
    store, task = prepare_store(root)
    legacy = SourceEvidence(
        id="ev_legacy_unverified",
        task_id=TASK_ID,
        source_id="src_classin",
        competitor="ClassIn",
        dimension="feature",
        snippet="legacy unverified statement",
        normalized_fact="legacy unverified statement",
        confidence=0.99,
        metadata={"source": "legacy_extractor"},
    )
    store.save_many(
        TASK_ID,
        "evidence",
        [
            *(SourceEvidence(**item) for item in store.load_many(TASK_ID, "evidence")),
            legacy,
        ],
    )
    authorized = [
        "ev_classin_feature",
        "ev_classin_pricing",
        "ev_bbb_feature",
    ]
    store.save_many(
        TASK_ID,
        "research_agent_runs",
        [
            ResearchAgentRun(
                task_id=TASK_ID,
                research_task_id="researchtask_authority",
                status=RunStatus.COMPLETED,
                outcome="COMPLETE",
                verified_evidence_ids=authorized,
            )
        ],
    )
    store.save_many(
        TASK_ID,
        "product_cards",
        [
            ProductCard(**item).model_copy(
                update={
                    "metadata": {
                        **item.get("metadata", {}),
                        "verified_evidence_only": True,
                    }
                }
            )
            for item in store.load_many(TASK_ID, "product_cards")
        ],
    )
    service = ResearchAnalysisService(store=store)
    readiness = service._analysis_readiness(TASK_ID)
    require(
        readiness["authorized_evidence_ids"] == sorted(authorized),
        "Analyst readiness 未使用 R1 verified allowlist",
    )
    provider = build_provider(store)
    result = run_agent(
        store,
        task,
        provider,
        authorized_evidence_ids=authorized,
    )
    require(result.status == RunStatus.COMPLETED, "verified-only Analyst 执行失败")
    for request in provider.requests:
        evidence_ids = {
            item["id"] for item in request["artifacts"].get("evidence", [])
        }
        require(
            "ev_legacy_unverified" not in evidence_ids
            and evidence_ids <= set(authorized),
            "legacy/unverified Evidence 进入 Analyst 上下文",
        )
    analyst_calls = store.load_many(TASK_ID, "llm_calls")
    require(
        all(
            "ev_legacy_unverified"
            not in call.get("input_artifact_refs", {}).get("evidence", [])
            and set(
                call.get("input_artifact_refs", {}).get("evidence", [])
            )
            <= set(authorized)
            for call in analyst_calls
        ),
        "ContextBundle 泄漏 legacy/unverified Evidence",
    )
    analyst_bundle = next(
        item
        for item in store.load_many(TASK_ID, "context_bundles")
        if item["agent_role"] == "analyst"
    )
    require(
        "ev_legacy_unverified" not in analyst_bundle["evidence_ids"]
        and set(analyst_bundle["evidence_ids"]) <= set(authorized),
        "持久化 Analyst ContextBundle 未执行 verified-only gate",
    )


def check_no_evidence_is_blocked(root: Path) -> None:
    store, _task = prepare_store(root)
    store.save_many(TASK_ID, "evidence", [])
    service = ResearchAnalysisService(store=store)
    payload = service.get_payload(TASK_ID)
    require(not payload["can_analyze"], "完全无 Evidence 时必须禁止分析")
    require(
        "Verified Evidence" in payload["analysis_blocking_reason"],
        "阻塞原因必须明确说明缺少 Verified Evidence",
    )
    try:
        service._validate_inputs(TASK_ID)
    except ValueError as exc:
        require("Verified Evidence" in str(exc), "后端 gate 应返回相同明确原因")
    else:
        raise AssertionError("完全无 Evidence 不得通过后端 gate")


def check_frontend_uses_backend_readiness() -> None:
    frontend = (
        Path(__file__).resolve().parent.parent / "frontend" / "src" / "app.js"
    ).read_text(encoding="utf-8")
    require(
        "state.researchCanAnalyze = Boolean(payload?.can_analyze);" in frontend,
        "前端分析按钮必须使用后端 can_analyze",
    )
    require(
        "coverage.length > 0 && productCards.length > 0" not in frontend,
        "前端不得继续把 EvidenceCoverage + ProductCard 作为硬 gate",
    )


def check_bounded_retry(root: Path) -> None:
    store, task = prepare_store(root)
    provider = build_provider(
        store,
        truncate_counts={"AnalystClaimsStage": 1},
    )
    result = run_agent(store, task, provider)
    require(result.status == RunStatus.COMPLETED, "一次 length 后应有限重试成功")
    require(
        provider.calls
        == [
            "AnalystBriefProfilesStage",
            "AnalystClaimsStage",
            "AnalystClaimsStage",
        ],
        "仅被截断阶段可重试一次",
    )
    calls = store.load_many(TASK_ID, "llm_calls")
    require(calls[1]["status"] == "failed", "截断尝试必须留存失败审计")
    require(
        calls[1]["metadata"]["finish_reason"] == "length",
        "finish_reason=length 必须显式记录",
    )


def check_retry_cap(root: Path) -> None:
    store, task = prepare_store(root)
    provider = build_provider(
        store,
        truncate_counts={"AnalystBriefProfilesStage": 2},
    )
    try:
        run_agent(store, task, provider)
    except LLMOutputTruncatedError as exc:
        require("stage=brief_profiles" in str(exc), "错误必须包含被截断子阶段")
        require("2 次有限尝试" in str(exc), "错误必须说明重试已到上限")
    else:
        raise AssertionError("连续两次 length 应返回明确截断错误")
    require(
        provider.calls
        == ["AnalystBriefProfilesStage", "AnalystBriefProfilesStage"],
        "length retry 必须严格限制为 1 次",
    )
    require(
        not store.load_many(TASK_ID, "analysis_portfolios"),
        "阶段失败不得保存半成品 Portfolio",
    )


def check_structured_repair(root: Path) -> None:
    store, task = prepare_store(root / "malformed_success")
    provider = build_provider(
        store,
        malformed_counts={"AnalystBriefProfilesStage": 1},
    )
    result = run_agent(store, task, provider)
    require(result.status == RunStatus.COMPLETED, "malformed JSON 修复后应成功")
    require(
        provider.calls
        == [
            "AnalystBriefProfilesStage",
            "AnalystBriefProfilesStage",
            "AnalystClaimsStage",
        ],
        "malformed JSON 只能触发一次统一 structured retry",
    )
    require(
        provider.requests[0]["output_schema"]
        == provider.requests[1]["output_schema"]
        == "AnalystBriefProfilesStage",
        "统一 structured retry 没有复用原 Stage Schema",
    )
    require(
        provider.requests[0]["artifacts"] == provider.requests[1]["artifacts"]
        and "只返回符合原 json_schema 的合法 JSON"
        in provider.requests[1]["prompt_summary"],
        "统一 structured retry 没有保持输入或追加严格 JSON 指令",
    )
    outputs = store.load_many(TASK_ID, "llm_outputs")
    require(
        outputs[0]["validation_status"] == "passed"
        and outputs[0]["metadata"]["structured_retry_performed"] is True
        and outputs[0]["metadata"]["structured_retry_result"] == "succeeded"
        and outputs[0]["metadata"]["initial_diagnostics"]["parse_error"],
        "malformed 初次失败与统一 retry 结果未在单次逻辑调用中留痕",
    )

    schema_store, schema_task = prepare_store(root / "schema_success")
    schema_provider = build_provider(
        schema_store,
        invalid_schema_counts={"AnalystBriefProfilesStage": 1},
    )
    schema_result = run_agent(schema_store, schema_task, schema_provider)
    require(
        schema_result.status == RunStatus.COMPLETED
        and schema_provider.calls[:2]
        == ["AnalystBriefProfilesStage", "AnalystBriefProfilesStage"],
        "Stage Schema 失败没有执行唯一一次统一 structured retry",
    )

    failed_store, failed_task = prepare_store(root / "malformed_failed")
    failed_provider = build_provider(
        failed_store,
        malformed_counts={"AnalystBriefProfilesStage": 2},
    )
    try:
        run_agent(failed_store, failed_task, failed_provider)
    except LLMStructuredOutputError:
        require(
            failed_provider.calls
            == ["AnalystBriefProfilesStage", "AnalystBriefProfilesStage"],
            "统一 structured retry 失败后仍进行了额外调用",
        )
    else:
        raise AssertionError("第二次 malformed JSON 被伪装成成功")


def check_finish_reason_is_authoritative() -> None:
    try:
        OpenAIChatCompletionsProvider.extract_structured_output(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"item": {}}'},
                    }
                ]
            }
        )
    except LLMProviderOutputTruncatedError as exc:
        require(exc.finish_reason == "length", "截断异常必须保留 finish_reason")
    else:
        raise AssertionError("finish_reason=length 即使碰巧是合法 JSON 也必须识别")


def main() -> None:
    root = (
        Path(__file__).resolve().parent
        / "app"
        / "data"
        / "tmp"
        / "step6f_output_stability"
    )
    check_successful_assembly(root / "success")
    check_research_agent_bridge_without_product_cards(root / "ra_bridge")
    check_no_evidence_is_blocked(root / "no_evidence")
    check_r1_verified_evidence_authority(root / "verified_authority")
    check_frontend_uses_backend_readiness()
    check_bounded_retry(root / "retry")
    check_retry_cap(root / "retry_cap")
    check_structured_repair(root / "structured_repair")
    check_finish_reason_is_authoritative()
    print("STEP6F_ANALYST_OUTPUT_STABILITY_CHECK_PASS")
    print("stages=brief_profiles,claims")
    print("normal_calls=2")
    print("max_calls=4")
    print("finish_reason_length=recognized")
    print("malformed_json_central_retry=bounded_once")
    print("stage_schema_central_retry=bounded_once")
    print("research_agent_verified_evidence_bridge=pass")
    print("product_cards_optional_for_research_analysis=true")
    print("legacy_product_card_path_compatible=true")
    print("frontend_backend_readiness_aligned=true")
    print("real_llm_called=false")


if __name__ == "__main__":
    main()
