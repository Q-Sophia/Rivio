from __future__ import annotations

from app.context import build_context_memory_artifacts
from app.agents.snapshot import (
    AnalystAgent,
    ExtractorAgent,
    SnapshotAgent,
    WriterAgent,
)
from app.llm import LLMClient
from app.llm.structured import (
    parse_analysis_claims,
    parse_competitive_analysis_portfolio_v2,
    parse_competitive_report,
    parse_product_cards,
    portfolio_v2_to_legacy_claims,
    validate_non_empty_evidence_ids,
    validate_portfolio_v2_refs,
    validate_product_card_refs,
    validate_report_claim_ids,
)
from app.schemas import (
    AgentContext,
    AgentResult,
    AnalysisClaim,
    AnalysisClaimV2,
    AnalysisTask,
    BriefAssessment,
    CitationCheck,
    ComparabilityNote,
    CompetitiveReport,
    CompetitorProfile,
    ContextBundle,
    EvidenceCoverage,
    LLMMode,
    ProductCard,
    ResearchGap,
    ReportStatement,
    SourceDocument,
    SourceEvidence,
)
from app.prompts import PromptRegistry
from app.reporting import (
    build_report_statements,
    resolve_report_title,
    validate_professional_report,
)
from build_claims_demo import validate_product_card_evidence_links
from build_product_cards_demo import validate_source_evidence_links
from build_report_demo import validate_claim_citation_links


_WRITER_INTERNAL_REFERENCE_FIELDS = {
    "source_ids",
    "evidence_ids",
    "counter_evidence_ids",
    "related_evidence_ids",
    "supporting_artifact_ids",
}


def _strip_writer_internal_references(value):
    """Keep governed semantics while hiding source/evidence identifier names from LLMs."""

    if isinstance(value, list):
        return [_strip_writer_internal_references(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_writer_internal_references(item)
            for key, item in value.items()
            if key not in _WRITER_INTERNAL_REFERENCE_FIELDS
        }
    return value


class LLMSnapshotAgent(SnapshotAgent):
    """Snapshot agent mixin that can call LLMClient and fallback to rule logic."""

    def __init__(self, *, llm_client: LLMClient, **kwargs):
        super().__init__(**kwargs)
        self.llm_client = llm_client

    def load_context_bundle(self, context: AgentContext) -> ContextBundle | None:
        build_context_memory_artifacts(
            task_id=context.task_id,
            store=self.llm_client.store,
        )
        raw = self.load_many(context, ["context_bundles"])
        for item in raw.get("context_bundles", []):
            bundle = ContextBundle(**item)
            if bundle.agent_role == self.role:
                return bundle
        return None

    def should_fallback(self) -> bool:
        return self.llm_client.config.mode == LLMMode.LLM_WITH_FALLBACK

    def mark_fallback(self, call_id: str, reason: str) -> None:
        calls = self.llm_client.trace.load_calls(self.tools.recorder.task_id)
        updated = []
        for call in calls:
            if call.id == call_id:
                call.used_fallback = True
                call.fallback_reason = reason
            updated.append(call)
        self.llm_client.trace.store.save_many(
            self.tools.recorder.task_id,
            "llm_calls",
            updated,
        )


class LLMExtractorAgent(LLMSnapshotAgent, ExtractorAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(context, ["sources", "evidence"])
        sources = [SourceDocument(**item) for item in raw["sources"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        if not sources:
            raise ValueError(f"No sources found for task_id={context.task_id}")
        if not evidence:
            raise ValueError(f"No evidence found for task_id={context.task_id}")
        validate_source_evidence_links(sources, evidence)
        self.mark_refs_validated(context, "已验证 evidence.source_id（证据来源编号）引用")

        try:
            bundle = self.load_context_bundle(context)
            llm_raw, call, _output = self.llm_client.generate_structured(
                task_id=context.task_id,
                agent_role=self.role,
                agent_run_id=self.agent_run_id(context),
                node_id=context.node_id,
                context_bundle=bundle,
                output_schema="ProductCard[]",
                prompt_id="extract_product_cards_zh_cn_v1",
                prompt_summary=(
                    "仅使用 SourceDocument（来源文档）和 SourceEvidence（来源证据）生成简体中文 "
                    "ProductCard[]（产品卡片）；保留全部 source_id 和 evidence_id。"
                ),
                artifacts=raw,
            )
            product_cards = parse_product_cards(llm_raw)
            validate_product_card_refs(product_cards)
            self.save_many(
                context,
                "product_cards",
                product_cards,
                f"已保存 {len(product_cards)} 个 mock LLM（模拟大模型）产品卡片",
            )
            return self.make_result(
                context,
                output_summary=f"通过 mock LLM（模拟大模型）生成 {len(product_cards)} 个产品卡片",
                output_artifacts={"product_cards": [item.id for item in product_cards]},
            )
        except Exception as exc:
            if not self.should_fallback():
                raise
            fallback_result = ExtractorAgent.execute(self, context)
            if "call" in locals():
                self.mark_fallback(call.id, f"{type(exc).__name__}: {exc}")
            return fallback_result.model_copy(
                update={
                    "output_summary": fallback_result.output_summary
                    + "（LLM 抽取失败后使用规则回退）"
                }
            )


class LLMAnalystAgent(LLMSnapshotAgent, AnalystAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(context, ["product_cards", "evidence"])
        product_cards = [ProductCard(**item) for item in raw["product_cards"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        if not product_cards:
            raise ValueError(f"No product_cards found for task_id={context.task_id}")
        if not evidence:
            raise ValueError(f"No evidence found for task_id={context.task_id}")
        validate_product_card_evidence_links(product_cards, evidence)
        self.mark_refs_validated(
            context,
            "已验证 product_cards.evidence_ids（产品卡片证据编号）引用",
        )

        try:
            bundle = self.load_context_bundle(context)
            llm_raw, call, _output = self.llm_client.generate_structured(
                task_id=context.task_id,
                agent_role=self.role,
                agent_run_id=self.agent_run_id(context),
                node_id=context.node_id,
                context_bundle=bundle,
                output_schema="AnalysisClaim[]",
                prompt_id="build_claims_zh_cn_v1",
                prompt_summary=(
                    "仅使用 ProductCard（产品卡片）和 SourceEvidence（来源证据）生成简体中文 "
                    "AnalysisClaim[]（分析结论）；保留全部 evidence_id。"
                ),
                artifacts=raw,
            )
            claims = parse_analysis_claims(llm_raw)
            validate_non_empty_evidence_ids(claims)
            claims = [
                claim.model_copy(
                    update={
                        "produced_by_agent_run_id": self.agent_run_id(context),
                    }
                )
                for claim in claims
            ]
            self.save_many(
                context,
                "claims",
                claims,
                f"已保存 {len(claims)} 个 mock LLM（模拟大模型）分析结论",
            )
            return self.make_result(
                context,
                output_summary=f"通过 mock LLM（模拟大模型）生成 {len(claims)} 个分析结论",
                output_artifacts={"claims": [item.id for item in claims]},
            )
        except Exception as exc:
            if not self.should_fallback():
                raise
            fallback_result = AnalystAgent.execute(self, context)
            if "call" in locals():
                self.mark_fallback(call.id, f"{type(exc).__name__}: {exc}")
            return fallback_result.model_copy(
                update={
                    "output_summary": fallback_result.output_summary
                    + "（LLM 分析失败后使用规则回退）"
                }
            )


class LLMProfessionalAnalystAgent(LLMSnapshotAgent, AnalystAgent):
    """Experimental Step6C analyst using the versioned professional prompt."""

    def __init__(self, *, prompt_registry: PromptRegistry | None = None, **kwargs):
        super().__init__(**kwargs)
        self.prompt_registry = prompt_registry or PromptRegistry()

    def execute(self, context: AgentContext) -> AgentResult:
        preserve_research_artifacts = bool(
            context.metadata.get("preserve_research_artifacts")
        )
        artifact_types = ["sources", "evidence", "product_cards"]
        if preserve_research_artifacts:
            artifact_types.extend(
                ["research_plans", "evidence_coverage", "research_gaps"]
            )
        raw = self.load_many(context, artifact_types)
        sources = [SourceDocument(**item) for item in raw["sources"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        product_cards = [ProductCard(**item) for item in raw["product_cards"]]
        if not sources or not evidence or not product_cards:
            raise ValueError("Step6C 分析需要 sources、evidence 和 product_cards")
        validate_product_card_evidence_links(product_cards, evidence)
        self.mark_refs_validated(
            context,
            "已验证 Step6C 输入中的 source_id 与 evidence_id 引用",
        )

        prompt = self.prompt_registry.load(
            "competitive_analyst",
            allow_candidate=True,
        )
        task = AnalysisTask(**context.task.model_dump(mode="json"))
        runtime_prompt = prompt.build_runtime_prompt(task)
        llm_artifacts = {
            **raw,
            "analysis_task": [task.model_dump(mode="json")],
        }
        try:
            bundle = self.load_context_bundle(context)
            llm_raw, call, _output = self.llm_client.generate_structured(
                task_id=context.task_id,
                agent_role=self.role,
                agent_run_id=self.agent_run_id(context),
                node_id=context.node_id,
                context_bundle=bundle,
                output_schema="CompetitiveAnalysisPortfolioV2",
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                prompt_hash=prompt.content_hash,
                prompt_summary=runtime_prompt,
                artifacts=llm_artifacts,
            )
            portfolio = parse_competitive_analysis_portfolio_v2(llm_raw)
            portfolio = portfolio.model_copy(
                update={
                    "prompt_id": prompt.prompt_id,
                    "prompt_version": prompt.version,
                    "items": [
                        item.model_copy(
                            update={
                                "produced_by_agent_run_id": self.agent_run_id(context)
                            }
                        )
                        for item in portfolio.items
                    ],
                    "metadata": {
                        **portfolio.metadata,
                        "prompt_hash": prompt.content_hash,
                        "prompt_status": prompt.status,
                        "source": (
                            "step6f_research_analysis"
                            if preserve_research_artifacts
                            else portfolio.metadata.get("source", "")
                        ),
                    },
                }
            )
            validate_portfolio_v2_refs(
                portfolio,
                known_source_ids={item.id for item in sources},
                known_evidence_ids={item.id for item in evidence},
                known_competitors={item.name for item in product_cards},
                evidence_competitors={item.id: item.competitor for item in evidence},
            )
            claims = portfolio_v2_to_legacy_claims(portfolio)
            validate_non_empty_evidence_ids(claims)

            artifacts_to_save = {
                "analysis_portfolios": [portfolio],
                "brief_assessments": [portfolio.brief_assessment],
                "competitor_profiles": portfolio.competitor_profiles,
                "intelligence_questions": portfolio.key_intelligence_questions,
                "information_needs": portfolio.information_needs,
                "comparability_notes": portfolio.comparability_notes,
                "claims_v2": portfolio.items,
                "claims": claims,
            }
            if preserve_research_artifacts:
                artifacts_to_save["analysis_evidence_coverage"] = (
                    portfolio.evidence_coverage
                )
                artifacts_to_save["analysis_research_gaps"] = portfolio.research_gaps
            else:
                artifacts_to_save["evidence_coverage"] = portfolio.evidence_coverage
                artifacts_to_save["research_gaps"] = portfolio.research_gaps
            for artifact_type, items in artifacts_to_save.items():
                self.save_many(
                    context,
                    artifact_type,
                    items,
                    f"Step6C 已保存 {len(items)} 个 {artifact_type} 结构化产物",
                )
            return self.make_result(
                context,
                output_summary=(
                    f"通过 {prompt.prompt_id}@{prompt.version} 生成专业分析组合："
                    f"{len(portfolio.competitor_profiles)} 个竞品画像、"
                    f"{len(portfolio.items)} 个 V2 结论、"
                    f"{len(portfolio.research_gaps)} 个研究缺口"
                ),
                output_artifacts={
                    artifact_type: [item.id for item in items]
                    for artifact_type, items in artifacts_to_save.items()
                },
            )
        except Exception as exc:
            if not self.should_fallback():
                raise
            fallback_result = AnalystAgent.execute(self, context)
            if "call" in locals():
                self.mark_fallback(call.id, f"{type(exc).__name__}: {exc}")
            return fallback_result.model_copy(
                update={
                    "output_summary": fallback_result.output_summary
                    + "（Step6C 专业分析失败后使用 V1 规则回退）"
                }
            )


class LLMWriterAgent(LLMSnapshotAgent, WriterAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(
            context,
            ["product_cards", "claims", "citation_checks", "sources", "evidence"],
        )
        product_cards = [ProductCard(**item) for item in raw["product_cards"]]
        claims = [AnalysisClaim(**item) for item in raw["claims"]]
        citation_checks = [CitationCheck(**item) for item in raw["citation_checks"]]
        sources = [SourceDocument(**item) for item in raw["sources"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        if not product_cards:
            raise ValueError(f"No product_cards found for task_id={context.task_id}")
        if not claims:
            raise ValueError(f"No claims found for task_id={context.task_id}")
        if not citation_checks:
            raise ValueError(f"No citation_checks found for task_id={context.task_id}")
        if not sources:
            raise ValueError(f"No sources found for task_id={context.task_id}")
        if not evidence:
            raise ValueError(f"No evidence found for task_id={context.task_id}")
        validate_claim_citation_links(claims, citation_checks)

        try:
            bundle = self.load_context_bundle(context)
            llm_raw, call, _output = self.llm_client.generate_structured(
                task_id=context.task_id,
                agent_role=self.role,
                agent_run_id=self.agent_run_id(context),
                node_id=context.node_id,
                context_bundle=bundle,
                output_schema="CompetitiveReport",
                prompt_id="write_report_zh_cn_v1",
                prompt_summary=(
                    "仅使用已验证的 AnalysisClaim（分析结论）和 CitationCheck（引用检查）生成"
                    "简体中文 CompetitiveReport（竞品报告）；正文必须包含全部 claim_id 引用。"
                ),
                artifacts=raw,
            )
            report = parse_competitive_report(llm_raw)
            validate_report_claim_ids(report)
            report = report.model_copy(
                update={"created_by_agent_run_id": self.agent_run_id(context)}
            )
            self.save_many(
                context,
                "reports",
                [report],
                "已保存 1 份 mock LLM（模拟大模型）竞品报告",
            )
            return self.make_result(
                context,
                output_summary=(
                    "通过 mock LLM（模拟大模型）生成 1 份报告，"
                    f"包含 {len(report.claim_ids)} 个 claim_id（结论编号）"
                ),
                output_artifacts={"reports": [report.id]},
            )
        except Exception as exc:
            if not self.should_fallback():
                raise
            fallback_result = WriterAgent.execute(self, context)
            if "call" in locals():
                self.mark_fallback(call.id, f"{type(exc).__name__}: {exc}")
            return fallback_result.model_copy(
                update={
                    "output_summary": fallback_result.output_summary
                    + "（LLM 写作失败后使用规则回退）"
                }
            )


class LLMProfessionalWriterAgent(LLMSnapshotAgent, WriterAgent):
    """Step6C writer that consumes governed analysis artifacts, not raw pages."""

    def __init__(self, *, prompt_registry: PromptRegistry | None = None, **kwargs):
        super().__init__(**kwargs)
        self.prompt_registry = prompt_registry or PromptRegistry()

    def execute(self, context: AgentContext) -> AgentResult:
        artifact_types = [
            "brief_assessments",
            "competitor_profiles",
            "evidence_coverage",
            "comparability_notes",
            "claims_v2",
            "research_gaps",
            "claims",
            "citation_checks",
        ]
        raw = self.load_many(context, artifact_types)
        briefs = [BriefAssessment(**item) for item in raw["brief_assessments"]]
        profiles = [CompetitorProfile(**item) for item in raw["competitor_profiles"]]
        coverage = [EvidenceCoverage(**item) for item in raw["evidence_coverage"]]
        notes = [ComparabilityNote(**item) for item in raw["comparability_notes"]]
        claims_v2 = [AnalysisClaimV2(**item) for item in raw["claims_v2"]]
        gaps = [ResearchGap(**item) for item in raw["research_gaps"]]
        claims = [AnalysisClaim(**item) for item in raw["claims"]]
        citation_checks = [CitationCheck(**item) for item in raw["citation_checks"]]
        if not briefs or not profiles or not claims_v2 or not claims or not citation_checks:
            raise ValueError(
                "Step6C Writer 需要 brief、profiles、claims_v2、claims 和 citation_checks"
            )
        validate_claim_citation_links(claims, citation_checks)

        task = AnalysisTask(**context.task.model_dump(mode="json"))
        brief = briefs[-1]
        resolved_title, title_source = resolve_report_title(task, brief)
        prompt = self.prompt_registry.load(
            "competitive_writer",
            allow_candidate=True,
        )
        runtime_prompt = prompt.build_writer_runtime_prompt(
            task,
            resolved_title=resolved_title,
        )
        llm_artifacts = {
            **_strip_writer_internal_references(raw),
            "analysis_task": [task.model_dump(mode="json")],
            "report_context": [
                {
                    "resolved_title": resolved_title,
                    "title_source": title_source,
                    "writer_prompt_id": prompt.prompt_id,
                    "writer_prompt_version": prompt.version,
                    "internal_reference_fields_hidden": True,
                }
            ],
        }
        bundle = self.load_context_bundle(context)
        llm_raw, _call, _output = self.llm_client.generate_structured(
            task_id=context.task_id,
            agent_role=self.role,
            agent_run_id=self.agent_run_id(context),
            node_id=context.node_id,
            context_bundle=bundle,
            output_schema="CompetitiveReport",
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
            prompt_summary=runtime_prompt,
            artifacts=llm_artifacts,
        )
        report = parse_competitive_report(llm_raw)
        report = report.model_copy(
            update={
                "created_by_agent_run_id": self.agent_run_id(context),
                "sections": {
                    **report.sections,
                    "report_version": prompt.version,
                    "prompt_id": prompt.prompt_id,
                    "prompt_hash": prompt.content_hash,
                    "prompt_status": prompt.status,
                    "title_source": title_source,
                    "research_gap_ids": [item.id for item in gaps],
                },
                "metadata": {
                    **report.metadata,
                    "writer_prompt_id": prompt.prompt_id,
                    "writer_prompt_version": prompt.version,
                    "writer_prompt_hash": prompt.content_hash,
                    "internal_reference_fields_hidden": True,
                },
            }
        )
        validate_report_claim_ids(report)
        validate_professional_report(
            report,
            task=task,
            brief=brief,
            known_claim_ids={item.id for item in claims_v2},
            known_research_gap_ids={item.id for item in gaps},
        )
        report_statements: list[ReportStatement] = build_report_statements(
            report=report,
            claims_v2=claims_v2,
            legacy_claims=claims,
            citation_checks=citation_checks,
            profiles=profiles,
            comparability_notes=notes,
            research_gaps=gaps,
        )
        report = report.model_copy(
            update={
                "sections": {
                    **report.sections,
                    "report_statement_ids": [item.id for item in report_statements],
                    "report_statement_count": len(report_statements),
                }
            }
        )
        self.save_many(
            context,
            "reports",
            [report],
            f"Step6C Writer 已保存任务化专业报告：{report.title}",
        )
        self.save_many(
            context,
            "report_statements",
            report_statements,
            f"已保存 {len(report_statements)} 条报告论点与证据映射",
        )
        return self.make_result(
            context,
            output_summary=(
                f"通过 {prompt.prompt_id}@{prompt.version} 生成《{report.title}》，"
                f"引用 {len(report.claim_ids)} 条结论并披露 {len(gaps)} 个研究缺口"
            ),
            output_artifacts={
                "reports": [report.id],
                "report_statements": [item.id for item in report_statements],
            },
        )
