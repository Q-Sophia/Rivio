from __future__ import annotations

import re
import time
from typing import Any

from app.harness.artifacts import ArtifactStore
from app.llm.config import LLMConfig
from app.llm.language import contains_chinese, validate_structured_output_language
from app.llm.provider import (
    LLMProviderOutputTruncatedError,
    MockStructuredProvider,
    ProviderResult,
    StructuredLLMProvider,
    build_provider,
)
from app.llm.structured import (
    normalize_report_claim_references,
    reject_unaligned_claims_v2,
    reject_unaligned_portfolio_v2_claims,
)
from app.reporting import build_professional_mock_report
from app.schemas import (
    AgentRole,
    AnalystBriefProfilesStage,
    AnalystClaimsStage,
    AnalysisClaim,
    AnalysisClaimV2,
    AnalysisTask,
    AnalysisTaskDraft,
    BriefAssessment,
    CitationCheck,
    CitationStatus,
    ComparabilityNote,
    CompetitiveAnalysisPortfolioV2,
    CompetitorProfile,
    CompetitiveReport,
    ContextBundle,
    EvidenceCoverage,
    InformationNeed,
    KeyIntelligenceQuestion,
    LLMCall,
    LLMMode,
    LLMOutput,
    ProductCard,
    ResearchGap,
    RunStatus,
    SourceDocument,
    SourceEvidence,
    TaskPriority,
    utc_now,
)


LLM_CALLS_ARTIFACT = "llm_calls"
LLM_OUTPUTS_ARTIFACT = "llm_outputs"
_INTERNAL_REFERENCE_FIELD_NAMES = {
    "source_ids",
    "evidence_ids",
    "counter_evidence_ids",
    "related_evidence_ids",
    "supporting_artifact_ids",
}


class LLMOutputTruncatedError(ValueError):
    """A named LLM sub-stage exhausted its output-token budget."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"LLM 输出被截断；stage={stage}；{message}")
        self.stage = stage
        self.finish_reason = "length"


def _find_internal_reference_fields(value: Any) -> set[str]:
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_find_internal_reference_fields(item))
        return found
    if isinstance(value, dict):
        found = set(value).intersection(_INTERNAL_REFERENCE_FIELD_NAMES)
        for item in value.values():
            found.update(_find_internal_reference_fields(item))
        return found
    return set()


class LLMTraceStore:
    def __init__(self, *, store: ArtifactStore):
        self.store = store

    def append_call(self, task_id: str, call: LLMCall) -> None:
        calls = [
            LLMCall(**item)
            for item in self.store.load_many(task_id, LLM_CALLS_ARTIFACT)
        ]
        calls.append(call)
        self.store.save_many(task_id, LLM_CALLS_ARTIFACT, calls)

    def append_output(self, task_id: str, output: LLMOutput) -> None:
        outputs = [
            LLMOutput(**item)
            for item in self.store.load_many(task_id, LLM_OUTPUTS_ARTIFACT)
        ]
        outputs.append(output)
        self.store.save_many(task_id, LLM_OUTPUTS_ARTIFACT, outputs)

    def load_calls(self, task_id: str) -> list[LLMCall]:
        return [
            LLMCall(**item)
            for item in self.store.load_many(task_id, LLM_CALLS_ARTIFACT)
        ]

    def load_outputs(self, task_id: str) -> list[LLMOutput]:
        return [
            LLMOutput(**item)
            for item in self.store.load_many(task_id, LLM_OUTPUTS_ARTIFACT)
        ]


class LLMClient:
    """Local LLM boundary.

    Step6B.1 supports mock, OpenAI, and OpenAI-compatible provider boundaries.
    Real calls remain disabled unless LLM_ENABLE_REAL_CALLS=true is explicit.
    """

    def __init__(
        self,
        *,
        config: LLMConfig,
        store: ArtifactStore,
        provider: StructuredLLMProvider | None = None,
    ):
        self.config = config
        self.store = store
        self.trace = LLMTraceStore(store=store)
        self.provider = provider or build_provider(config=config)

    def generate_structured(
        self,
        *,
        task_id: str,
        agent_role: AgentRole,
        agent_run_id: str,
        node_id: str,
        context_bundle: ContextBundle | None,
        output_schema: str,
        prompt_id: str,
        prompt_summary: str,
        artifacts: dict[str, list],
        prompt_version: str = "",
        prompt_hash: str = "",
    ) -> tuple[dict[str, Any], LLMCall, LLMOutput]:
        started_at = utc_now()
        start = time.perf_counter()
        call_id = f"llmcall_{node_id}"
        used_fallback = False
        fallback_reason = ""
        provider_result: ProviderResult | None = None
        raw_output: dict[str, Any] = {}
        report_claim_refs_added: list[str] = []
        rejected_portfolio_claims: list[dict[str, Any]] = []
        caught_exception: Exception | None = None
        try:
            if isinstance(self.provider, MockStructuredProvider):
                raw_output = self._mock_generate(
                    task_id=task_id,
                    agent_role=agent_role,
                    output_schema=output_schema,
                    artifacts=artifacts,
                )
            else:
                provider_result = self.provider.generate(
                    task_id=task_id,
                    agent_role=agent_role,
                    output_schema=output_schema,
                    prompt_summary=prompt_summary,
                    system_context=(
                        context_bundle.system_context if context_bundle else []
                    ),
                    artifacts=artifacts,
                )
                raw_output = provider_result.raw_output
            if output_schema == "CompetitiveAnalysisPortfolioV2":
                raw_output, rejected_portfolio_claims = (
                    self._filter_unaligned_portfolio_claims(
                        raw_output=raw_output,
                        artifacts=artifacts,
                    )
                )
            if output_schema == "AnalystClaimsStage":
                raw_output, rejected_portfolio_claims = (
                    self._filter_unaligned_stage_claims(
                        raw_output=raw_output,
                        artifacts=artifacts,
                    )
                )
            if output_schema == "CompetitiveReport":
                raw_output, report_claim_refs_added = normalize_report_claim_references(
                    raw_output,
                    [str(item.get("id", "")) for item in artifacts.get("claims", [])],
                )
            parsed_ids = self._validate_output(
                task_id=task_id,
                output_schema=output_schema,
                raw_output=raw_output,
                artifacts=artifacts,
            )
            status = RunStatus.COMPLETED
            error = ""
            validation_status = "passed"
            validation_errors: list[str] = []
        except Exception as exc:
            caught_exception = exc
            provider_error = f"{type(exc).__name__}: {exc}"
            if self.config.mode == LLMMode.LLM_WITH_FALLBACK:
                used_fallback = True
                fallback_reason = provider_error
                raw_output = self._mock_generate(
                    task_id=task_id,
                    agent_role=agent_role,
                    output_schema=output_schema,
                    artifacts=artifacts,
                )
                if output_schema == "CompetitiveAnalysisPortfolioV2":
                    raw_output, rejected_portfolio_claims = (
                        self._filter_unaligned_portfolio_claims(
                            raw_output=raw_output,
                            artifacts=artifacts,
                        )
                    )
                if output_schema == "CompetitiveReport":
                    raw_output, report_claim_refs_added = normalize_report_claim_references(
                        raw_output,
                        [str(item.get("id", "")) for item in artifacts.get("claims", [])],
                    )
                parsed_ids = self._validate_output(
                    task_id=task_id,
                    output_schema=output_schema,
                    raw_output=raw_output,
                    artifacts=artifacts,
                )
                status = RunStatus.COMPLETED
                error = ""
                validation_status = "passed"
                validation_errors = []
            else:
                parsed_ids = []
                status = RunStatus.FAILED
                error = provider_error
                validation_status = "failed"
                validation_errors = [error]

        completed_at = utc_now()
        duration_ms = int((time.perf_counter() - start) * 1000)
        call = LLMCall(
            id=call_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            node_id=node_id,
            agent_role=agent_role,
            provider=self.config.provider,
            model=self.config.model,
            mode=self.config.mode,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            prompt_summary=prompt_summary,
            context_bundle_id=context_bundle.id if context_bundle else "",
            input_artifact_refs=context_bundle.artifact_refs if context_bundle else {},
            output_schema=output_schema,
            output_summary=self._output_summary(output_schema, raw_output, parsed_ids),
            status=status,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            duration_ms=duration_ms,
            error=error,
            created_at=started_at,
            completed_at=completed_at,
            metadata={
                "mock_provider": self.config.provider == "mock",
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "output_language": self.config.output_language,
                "real_calls_enabled": self.config.enable_real_calls,
                "api_surface": self.config.api_style,
                "structured_output_mode": self.config.structured_output_mode,
                "thinking_mode": self.config.thinking_mode,
                "report_claim_refs_added": report_claim_refs_added,
                "rejected_portfolio_claims_count": len(
                    rejected_portfolio_claims
                ),
                "rejected_portfolio_claims": rejected_portfolio_claims,
                "input_payload_artifact_types": sorted(artifacts),
                "input_internal_reference_fields": sorted(
                    _find_internal_reference_fields(artifacts)
                ),
                "request_id": provider_result.request_id if provider_result else "",
                "attempts": provider_result.attempts if provider_result else 0,
                "input_tokens": provider_result.input_tokens if provider_result else 0,
                "output_tokens": provider_result.output_tokens if provider_result else 0,
                "finish_reason": (
                    provider_result.metadata.get("finish_reason", "unknown")
                    if provider_result
                    else getattr(caught_exception, "finish_reason", "unknown")
                ),
            },
        )
        output = LLMOutput(
            id=f"llmout_{node_id}",
            task_id=task_id,
            llm_call_id=call.id,
            agent_role=agent_role,
            output_schema=output_schema,
            raw_output=raw_output,
            parsed_object_ids=parsed_ids,
            validation_status=validation_status,
            validation_errors=validation_errors,
        )
        self.trace.append_call(task_id, call)
        self.trace.append_output(task_id, output)
        if status == RunStatus.FAILED:
            if isinstance(caught_exception, LLMProviderOutputTruncatedError):
                raise LLMOutputTruncatedError(node_id, error) from caught_exception
            raise ValueError(error)
        return raw_output, call, output

    @staticmethod
    def _filter_unaligned_portfolio_claims(
        *,
        raw_output: dict[str, Any],
        artifacts: dict[str, list],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        portfolio = CompetitiveAnalysisPortfolioV2(**raw_output.get("item", {}))
        filtered, rejected = reject_unaligned_portfolio_v2_claims(
            portfolio,
            evidence_competitors={
                str(item.get("id", "")): str(item.get("competitor", ""))
                for item in artifacts.get("evidence", [])
            },
            known_competitors={
                str(item.get("name", ""))
                for item in artifacts.get("product_cards", [])
            },
        )
        if not rejected:
            return raw_output, []
        return {
            **raw_output,
            "item": filtered.model_dump(mode="json"),
        }, rejected

    @staticmethod
    def _filter_unaligned_stage_claims(
        *,
        raw_output: dict[str, Any],
        artifacts: dict[str, list],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        stage = AnalystClaimsStage(**raw_output.get("item", {}))
        filtered, rejected = reject_unaligned_claims_v2(
            stage.items,
            evidence_competitors={
                str(item.get("id", "")): str(item.get("competitor", ""))
                for item in artifacts.get("evidence", [])
            },
            known_competitors={
                str(item.get("name", ""))
                for item in artifacts.get("product_cards", [])
            },
        )
        if not rejected:
            return raw_output, []
        return {
            **raw_output,
            "item": stage.model_copy(update={"items": filtered}).model_dump(
                mode="json"
            ),
        }, rejected

    def _mock_generate(
        self,
        *,
        task_id: str,
        agent_role: AgentRole,
        output_schema: str,
        artifacts: dict[str, list],
    ) -> dict[str, Any]:
        if output_schema == "ProductCard[]":
            return {
                "items": [
                    self._mock_product_card(task_id, competitor, items)
                    for competitor, items in self._evidence_by_competitor(artifacts).items()
                ],
                "generated_by": str(agent_role),
                "output_language": self.config.output_language,
            }
        if output_schema == "AnalysisClaim[]":
            return {
                "items": self._mock_claims(task_id, artifacts),
                "generated_by": str(agent_role),
                "output_language": self.config.output_language,
            }
        if output_schema == "CompetitiveReport":
            return {
                "item": self._mock_report(task_id, artifacts),
                "generated_by": str(agent_role),
                "output_language": self.config.output_language,
            }
        if output_schema == "CompetitiveAnalysisPortfolioV2":
            return {
                "item": self._mock_analysis_portfolio_v2(task_id, artifacts),
                "generated_by": str(agent_role),
                "output_language": self.config.output_language,
            }
        if output_schema in {
            "AnalystBriefProfilesStage",
            "AnalystClaimsStage",
        }:
            portfolio = self._mock_analysis_portfolio_v2(task_id, artifacts)
            if output_schema == "AnalystBriefProfilesStage":
                item = AnalystBriefProfilesStage(
                    task_id=task_id,
                    brief_assessment=portfolio["brief_assessment"],
                    competitor_profiles=portfolio["competitor_profiles"],
                )
            else:
                item = AnalystClaimsStage(
                    task_id=task_id,
                    comparability_notes=portfolio["comparability_notes"],
                    items=portfolio["items"],
                )
            return {
                "item": item.model_dump(mode="json"),
                "generated_by": str(agent_role),
                "output_language": self.config.output_language,
            }
        if output_schema == "AnalysisTaskDraft":
            return {
                "item": self._mock_task_draft(task_id, artifacts),
                "generated_by": str(agent_role),
                "output_language": self.config.output_language,
            }
        raise ValueError(f"Unsupported mock output_schema={output_schema}")

    def _validate_output(
        self,
        *,
        task_id: str,
        output_schema: str,
        raw_output: dict[str, Any],
        artifacts: dict[str, list],
    ) -> list[str]:
        language_errors = validate_structured_output_language(
            output_schema,
            raw_output,
            output_language=self.config.output_language,
        )
        if language_errors:
            raise ValueError("；".join(language_errors))
        if output_schema == "ProductCard[]":
            items = [ProductCard(**item) for item in raw_output.get("items", [])]
            if not items:
                raise ValueError("ProductCard[] output is empty")
            self._ensure_non_empty_refs(items, "evidence_ids")
            self._ensure_task_ids(items, task_id)
            self._ensure_known_refs(
                items,
                "source_ids",
                {str(item.get("id", "")) for item in artifacts.get("sources", [])},
            )
            self._ensure_known_refs(
                items,
                "evidence_ids",
                {str(item.get("id", "")) for item in artifacts.get("evidence", [])},
            )
            return [item.id for item in items]
        if output_schema == "AnalysisClaim[]":
            items = [AnalysisClaim(**item) for item in raw_output.get("items", [])]
            if not items:
                raise ValueError("AnalysisClaim[] output is empty")
            self._ensure_non_empty_refs(items, "evidence_ids")
            self._ensure_task_ids(items, task_id)
            self._ensure_known_refs(
                items,
                "evidence_ids",
                {str(item.get("id", "")) for item in artifacts.get("evidence", [])},
            )
            return [item.id for item in items]
        if output_schema == "CompetitiveReport":
            item = CompetitiveReport(**raw_output.get("item", {}))
            if not item.markdown.strip():
                raise ValueError("CompetitiveReport.markdown is empty")
            if not item.claim_ids:
                raise ValueError("CompetitiveReport.claim_ids is empty")
            self._ensure_task_ids([item], task_id)
            self._ensure_known_refs(
                [item],
                "claim_ids",
                {str(claim.get("id", "")) for claim in artifacts.get("claims", [])},
            )
            missing_markdown_refs = [
                claim_id
                for claim_id in item.claim_ids
                if f"[{claim_id}]" not in item.markdown
            ]
            if missing_markdown_refs:
                raise ValueError(
                    "CompetitiveReport.markdown missing claim ids: "
                    + ", ".join(missing_markdown_refs)
                )
            return [item.id]
        if output_schema == "CompetitiveAnalysisPortfolioV2":
            from app.llm.structured import validate_portfolio_v2_refs

            item = CompetitiveAnalysisPortfolioV2(**raw_output.get("item", {}))
            self._ensure_task_ids([item], task_id)
            validate_portfolio_v2_refs(
                item,
                known_source_ids={
                    str(source.get("id", ""))
                    for source in artifacts.get("sources", [])
                },
                known_evidence_ids={
                    str(evidence.get("id", ""))
                    for evidence in artifacts.get("evidence", [])
                },
                known_competitors={
                    str(card.get("name", ""))
                    for card in artifacts.get("product_cards", [])
                },
                evidence_competitors={
                    str(evidence.get("id", "")): str(
                        evidence.get("competitor", "")
                    )
                    for evidence in artifacts.get("evidence", [])
                },
            )
            return [item.id]
        if output_schema == "AnalystBriefProfilesStage":
            item = AnalystBriefProfilesStage(**raw_output.get("item", {}))
            self._ensure_task_ids(
                [item, item.brief_assessment, *item.competitor_profiles],
                task_id,
            )
            known_competitors = {
                str(card.get("name", ""))
                for card in artifacts.get("product_cards", [])
            }
            invalid_competitors = sorted(
                profile.name
                for profile in item.competitor_profiles
                if profile.name not in known_competitors
            )
            if invalid_competitors:
                raise ValueError(
                    "AnalystBriefProfilesStage contains unknown competitors: "
                    + ", ".join(invalid_competitors)
                )
            self._ensure_known_refs(
                item.competitor_profiles,
                "source_ids",
                {str(source.get("id", "")) for source in artifacts.get("sources", [])},
            )
            self._ensure_known_refs(
                item.competitor_profiles,
                "evidence_ids",
                {str(ev.get("id", "")) for ev in artifacts.get("evidence", [])},
            )
            return [item.id]
        if output_schema == "AnalystClaimsStage":
            item = AnalystClaimsStage(**raw_output.get("item", {}))
            self._ensure_task_ids(
                [item, *item.comparability_notes, *item.items],
                task_id,
            )
            self._ensure_non_empty_refs(item.items, "evidence_ids")
            known_evidence_ids = {
                str(ev.get("id", "")) for ev in artifacts.get("evidence", [])
            }
            self._ensure_known_refs(item.items, "evidence_ids", known_evidence_ids)
            self._ensure_known_refs(
                item.items,
                "counter_evidence_ids",
                known_evidence_ids,
            )
            known_competitors = {
                str(card.get("name", ""))
                for card in artifacts.get("product_cards", [])
            }
            invalid_competitors = sorted(
                {
                    competitor
                    for claim in item.items
                    for competitor in claim.competitors
                    if competitor not in known_competitors
                }
            )
            if invalid_competitors:
                raise ValueError(
                    "AnalystClaimsStage contains unknown competitors: "
                    + ", ".join(invalid_competitors)
                )
            return [item.id]
        if output_schema == "AnalysisTaskDraft":
            item = AnalysisTaskDraft(**raw_output.get("item", {}))
            self._ensure_task_ids([item], task_id)
            request_items = artifacts.get("intake_request", [])
            expected_text = str(request_items[0].get("request_text", "")) if request_items else ""
            if expected_text and item.request_text != expected_text:
                raise ValueError("AnalysisTaskDraft.request_text 与用户原文不一致")
            return [item.id]
        raise ValueError(f"Unsupported output_schema={output_schema}")

    @staticmethod
    def _mock_task_draft(task_id: str, artifacts: dict[str, list]) -> dict[str, Any]:
        request_items = artifacts.get("intake_request", [])
        request_text = str(request_items[0].get("request_text", "")).strip() if request_items else ""
        comparison = re.search(r"(?:对比|比较|分析)([^，。；]{3,100})", request_text)
        segment = comparison.group(1) if comparison else ""
        segment = re.split(
            r"\s+(?:在|用于|面向)|(?:在|的)(?:功能|价格|定价|产品|市场|生态|客户|商业)",
            segment,
        )[0]
        competitors = [
            item.strip(" ：:、，,和与及")
            for item in re.split(r"[、,，]|和|与|及", segment)
            if item.strip(" ：:、，,和与及")
        ]
        competitors = [item for item in competitors if 1 < len(item) <= 30][:6]
        if "在线教育" in request_text or "教学" in request_text:
            industry = "在线教育与教学云服务"
        elif "电商" in request_text:
            industry = "电子商务"
        elif "医疗" in request_text:
            industry = "医疗健康"
        else:
            industry = ""
        dimensions = [
            label
            for token, label in (
                ("功能", "产品能力"), ("价格", "定价与成本"),
                ("生态", "生态与集成"), ("客户", "目标客户"),
                ("商业模式", "商业模式"), ("风险", "风险与限制"),
            )
            if token in request_text
        ]
        target_match = re.search(r"面向([^，。；]{2,30})", request_text)
        targets = [target_match.group(1).strip()] if target_match else []
        missing = []
        if not request_text:
            missing.append("决策问题")
        if not industry:
            missing.append("所属行业")
        if len(competitors) < 2:
            missing.append("至少两个比较对象")
        questions = []
        if "决策问题" in missing:
            questions.append("这份分析最终要帮助你做什么决策？")
        if "所属行业" in missing:
            questions.append("这些产品属于哪个行业或业务领域？")
        if "至少两个比较对象" in missing:
            questions.append("请至少提供两个需要比较的产品、品牌或方案。")
        ready = not missing
        subject_parts = [industry, "、".join(competitors)]
        subject = "：".join(part for part in subject_parts if part) or "待确认的竞品分析主题"
        return AnalysisTaskDraft(
            id=task_id,
            task_id=task_id,
            request_text=request_text,
            decision_question=request_text,
            industry=industry,
            competitors=competitors,
            target_customers=targets,
            focus_areas=dimensions,
            report_subject=subject,
            missing_fields=missing,
            clarification_questions=questions,
            ready_for_confirmation=ready,
            status="ready" if ready else "needs_clarification",
            metadata={"generation_mode": "mock_llm"},
        ).model_dump(mode="json")

    @staticmethod
    def _ensure_non_empty_refs(items: list, attr: str) -> None:
        missing = [item.id for item in items if not getattr(item, attr)]
        if missing:
            raise ValueError(f"Items missing {attr}: {', '.join(missing)}")

    @staticmethod
    def _ensure_task_ids(items: list, task_id: str) -> None:
        mismatched = [item.id for item in items if item.task_id != task_id]
        if mismatched:
            raise ValueError(
                f"Items have mismatched task_id={task_id}: {', '.join(mismatched)}"
            )

    @staticmethod
    def _ensure_known_refs(items: list, attr: str, allowed_ids: set[str]) -> None:
        invalid = [
            f"{item.id}:{ref_id}"
            for item in items
            for ref_id in getattr(item, attr)
            if ref_id not in allowed_ids
        ]
        if invalid:
            raise ValueError(
                f"Items contain unknown {attr}: {', '.join(invalid)}"
            )

    @staticmethod
    def _evidence_by_competitor(
        artifacts: dict[str, list],
    ) -> dict[str, list[SourceEvidence]]:
        evidence = [SourceEvidence(**item) for item in artifacts.get("evidence", [])]
        grouped: dict[str, list[SourceEvidence]] = {}
        for item in evidence:
            grouped.setdefault(item.competitor, []).append(item)
        return grouped

    def _mock_product_card(
        self,
        task_id: str,
        competitor: str,
        evidence: list[SourceEvidence],
    ) -> dict[str, Any]:
        source_ids = sorted({item.source_id for item in evidence})
        evidence_ids = [item.id for item in evidence]
        dimensions = sorted({str(item.dimension) for item in evidence})
        dimension_labels = [self._dimension_label(item) for item in dimensions]
        feature_facts = [
            self._localized_fact(item)
            for item in evidence
            if item.dimension in {"feature", "ecosystem"}
        ][:5]
        pricing_facts = [
            self._localized_fact(item)
            for item in evidence
            if item.dimension == "pricing"
        ][:2]
        return ProductCard(
            id=f"prod_llm_{self._slug(competitor)}",
            task_id=task_id,
            name=competitor,
            company=competitor,
            positioning=(
                f"{competitor} 的产品卡片基于 {len(evidence)} 条结构化证据整理，"
                f"覆盖{'、'.join(dimension_labels)}。"
            ),
            target_users=["目标客户与业务负责人", "产品、服务与战略评估人员"],
            pricing_summary=(
                "；".join(pricing_facts)
                if pricing_facts
                else "当前快照中的定价证据有限，需要后续调研补充。"
            ),
            core_features=feature_facts or [self._localized_fact(item) for item in evidence[:3]],
            strengths=[f"现有证据覆盖 {len(dimensions)} 个分析维度，具备基础可追溯性。"],
            weaknesses=[
                "当前为 mock LLM（模拟大模型）输出，保留证据编号，但未增加实时市场调研。"
            ],
            source_ids=source_ids,
            evidence_ids=evidence_ids,
            confidence=0.72,
            metadata={
                "generation_mode": "mock_llm",
                "source": "LLMClient._mock_product_card",
                "output_language": self.config.output_language,
            },
        ).model_dump(mode="json")

    def _mock_claims(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> list[dict[str, Any]]:
        evidence = [SourceEvidence(**item) for item in artifacts.get("evidence", [])]
        by_dimension: dict[str, list[SourceEvidence]] = {}
        for item in evidence:
            by_dimension.setdefault(str(item.dimension), []).append(item)
        dimensions = ["positioning", "feature", "pricing", "ecosystem", "risk"]
        claims = []
        for dimension in dimensions:
            items = by_dimension.get(dimension, [])
            if not items:
                continue
            competitors = sorted({item.competitor for item in items})
            competitors_text = "、".join(competitors)
            evidence_ids = [item.id for item in items[:6]]
            claims.append(
                AnalysisClaim(
                    id=f"cl_llm_online_education_{dimension}",
                    task_id=task_id,
                    dimension=dimension,
                    claim_text=self._claim_text(dimension, competitors_text),
                    competitors=competitors,
                    evidence_ids=evidence_ids,
                    confidence=0.74,
                    produced_by_agent_run_id="mock_llm_analyst",
                    citation_status=CitationStatus.PENDING,
                    metadata={
                        "generation_mode": "mock_llm",
                        "source": "LLMClient._mock_claims",
                        "output_language": self.config.output_language,
                    },
                ).model_dump(mode="json")
            )
        return claims

    def _mock_analysis_portfolio_v2(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> dict[str, Any]:
        task_data = (artifacts.get("analysis_task") or [{}])[0]
        query = str(task_data.get("query") or "完成当前竞品分析决策")
        industry = str(task_data.get("industry") or "待确认行业")
        focus_areas = [str(item) for item in task_data.get("focus_areas", [])]
        task_metadata = dict(task_data.get("metadata") or {})
        research_brief = dict(task_metadata.get("research_brief") or {})
        product_cards = [
            ProductCard(**item) for item in artifacts.get("product_cards", [])
        ]
        sources = [
            SourceDocument(**item) for item in artifacts.get("sources", [])
        ]
        evidence = [
            SourceEvidence(**item) for item in artifacts.get("evidence", [])
        ]
        source_by_id = {item.id: item for item in sources}
        evidence_by_competitor: dict[str, list[SourceEvidence]] = {}
        for item in evidence:
            evidence_by_competitor.setdefault(item.competitor, []).append(item)

        evidence_dimensions = sorted({str(item.dimension) for item in evidence})
        selected_dimensions = evidence_dimensions
        target_customers = [str(item) for item in research_brief.get("target_customers", [])]
        core_scenarios = [str(item) for item in research_brief.get("core_scenarios", [])]
        missing_fields = []
        if not query.strip() or task_metadata.get("goal_underspecified"):
            missing_fields.append("决策问题")
        if not target_customers:
            missing_fields.append("目标客户")
        if not core_scenarios:
            missing_fields.append("核心场景")
        brief = BriefAssessment(
            id=f"brief_{self._slug(task_id)}",
            task_id=task_id,
            decision_question=query,
            industry=industry,
            target_customers=target_customers,
            core_scenarios=core_scenarios,
            selected_dimensions=selected_dimensions,
            missing_fields=missing_fields,
            sufficient_for_analysis=not missing_fields,
            metadata={"generation_mode": "mock_llm"},
        )

        profiles: list[CompetitorProfile] = []
        for card in product_cards:
            card_evidence = evidence_by_competitor.get(card.name, [])
            dimensions = sorted({str(item.dimension) for item in card_evidence})
            card_metadata = dict(card.metadata or {})
            inferred_path, inferred_delivery = self._infer_solution_path(card)
            profiles.append(
                CompetitorProfile(
                    id=f"competitor_{self._slug(card.name)}",
                    task_id=task_id,
                    name=card.name,
                    role=card_metadata.get("competitor_role", "direct"),
                    selection_reason=card_metadata.get(
                        "selection_reason",
                        f"{card.name} 是任务预选研究对象，现有资料覆盖"
                        f"{len(dimensions)} 个证据维度。",
                    ),
                    represented_path=card_metadata.get(
                        "represented_path",
                        inferred_path,
                    ),
                    comparable_dimensions=dimensions,
                    non_comparable_dimensions=[
                        str(item)
                        for item in card_metadata.get("non_comparable_dimensions", [])
                    ],
                    target_customers=card.target_users,
                    core_scenarios=[],
                    value_proposition=card.positioning,
                    offering_scope="；".join(card.core_features[:3]) or "供给范围待补充。",
                    delivery_model=card_metadata.get(
                        "delivery_model",
                        inferred_delivery,
                    ),
                    business_model=card.pricing_summary,
                    source_ids=card.source_ids,
                    evidence_ids=card.evidence_ids,
                    confidence=card.confidence,
                    metadata={"generation_mode": "mock_llm"},
                )
            )

        questions = [
            KeyIntelligenceQuestion(
                id=f"kiq_{self._slug(task_id)}_customer",
                task_id=task_id,
                question="不同解决路径分别适合哪些目标客户、任务场景与组织约束？",
                decision_link=query,
                dimensions=["customer", "positioning"],
                priority=TaskPriority.HIGH,
            ),
            KeyIntelligenceQuestion(
                id=f"kiq_{self._slug(task_id)}_tradeoff",
                task_id=task_id,
                question="各方案在供给范围、交付速度、控制权、总体成本与持续责任上如何取舍？",
                decision_link=query,
                dimensions=["feature", "pricing", "ecosystem", "risk"],
                priority=TaskPriority.HIGH,
            ),
            KeyIntelligenceQuestion(
                id=f"kiq_{self._slug(task_id)}_evidence",
                task_id=task_id,
                question="哪些重要判断已有同口径证据，哪些仍会阻塞当前决策？",
                decision_link=query,
                dimensions=selected_dimensions,
                priority=TaskPriority.MEDIUM,
            ),
        ]
        needs = [
            InformationNeed(
                id=f"need_{self._slug(task_id)}_{index}",
                task_id=task_id,
                question_id=question.id,
                dimension=question.dimensions[0] if question.dimensions else "other",
                required_facts=["客户与场景", "责任边界", "可比口径与限制"],
                preferred_source_types=["官方资料", "客户资料", "可信第三方资料"],
                comparability_basis="采用相同客户任务、时间范围和责任边界进行比较。",
                decision_link=question.decision_link,
            )
            for index, question in enumerate(questions, start=1)
        ]

        dimensions = evidence_dimensions
        coverage: list[EvidenceCoverage] = []
        for profile in profiles:
            competitor_evidence = evidence_by_competitor.get(profile.name, [])
            for dimension in dimensions:
                matches = [
                    item for item in competitor_evidence if str(item.dimension) == dimension
                ]
                source_types = {
                    str(source_by_id[item.source_id].source_type)
                    for item in matches
                    if item.source_id in source_by_id
                }
                has_conflict = any(
                    bool(item.metadata.get("conflicting")) for item in matches
                )
                only_weak = bool(matches) and (
                    source_types <= {"social"}
                    or all(item.confidence < 0.5 for item in matches)
                )
                if has_conflict:
                    coverage_status = "conflicting"
                    limitation = "现有资料存在口径、时间或数值冲突，需要优先核实。"
                elif only_weak:
                    coverage_status = "weak"
                    limitation = "当前只有较弱来源，不能支撑高置信度决策。"
                elif len(matches) >= 2:
                    coverage_status = "sufficient"
                    limitation = "证据数量已形成基础覆盖，仍需结合决策风险复核。"
                elif matches:
                    coverage_status = "partial"
                    limitation = "当前只有单条直接资料，证据覆盖仍不完整。"
                else:
                    coverage_status = "missing"
                    limitation = "当前输入没有该维度证据。"
                coverage.append(
                    EvidenceCoverage(
                        id=(
                            f"coverage_{self._slug(profile.name)}_"
                            f"{self._slug(dimension)}"
                        ),
                        task_id=task_id,
                        competitor=profile.name,
                        dimension=dimension,
                        status=coverage_status,
                        source_ids=sorted({item.source_id for item in matches}),
                        evidence_ids=[item.id for item in matches],
                        limitations=limitation,
                    )
                )

        notes: list[ComparabilityNote] = []
        claims: list[AnalysisClaimV2] = []
        competitor_names = [profile.name for profile in profiles]
        profile_by_name = {profile.name: profile for profile in profiles}
        card_by_name = {card.name: card for card in product_cards}
        for dimension in dimensions:
            dimension_items = [item for item in evidence if str(item.dimension) == dimension]
            by_competitor = {
                competitor: [
                    item for item in dimension_items if item.competitor == competitor
                ]
                for competitor in competitor_names
            }
            covered_competitors = [
                competitor for competitor, items in by_competitor.items() if items
            ]
            comparison_tiers = {
                str(card_by_name[name].metadata.get("comparison_tier", "default"))
                for name in covered_competitors
            }
            service_boundaries = {
                str(card_by_name[name].metadata.get("service_boundary", "default"))
                for name in covered_competitors
            }
            explicitly_non_comparable = any(
                dimension in profile_by_name[name].non_comparable_dimensions
                for name in covered_competitors
            )
            comparable = (
                len(covered_competitors) >= 2
                and len(comparison_tiers) == 1
                and len(service_boundaries) == 1
                and not explicitly_non_comparable
            )
            if comparable:
                comparison_basis = "各参与对象均有该维度的结构化证据，可进行初步横向观察。"
            elif len(covered_competitors) < 2:
                comparison_basis = "只有一个对象具备该维度证据，不能横向比较。"
            else:
                comparison_basis = "对象的版本层级、服务边界或责任范围不同，不能直接排名。"
            notes.append(
                ComparabilityNote(
                    id=f"compare_{self._slug(task_id)}_{self._slug(dimension)}",
                    task_id=task_id,
                    competitors=covered_competitors,
                    dimension=dimension,
                    comparable=comparable,
                    basis=comparison_basis,
                    limitations="版本、地区、规模和服务边界仍需在正式决策前统一。",
                )
            )
            if not covered_competitors:
                continue
            selected_evidence = [
                item
                for competitor in covered_competitors
                for item in by_competitor[competitor][:2]
            ]
            label = self._dimension_label(dimension)
            coverage_for_dimension = [
                item for item in coverage if item.dimension == dimension
            ]
            has_conflict = any(item.status == "conflicting" for item in coverage_for_dimension)
            has_weak = any(item.status == "weak" for item in coverage_for_dimension)
            baseline_candidate = (
                dimension == "feature"
                and comparable
                and all(
                    any(bool(item.metadata.get("baseline_candidate")) for item in by_competitor[name])
                    for name in covered_competitors
                )
            )
            if dimension == "risk":
                claim_type = "risk"
            elif baseline_candidate:
                claim_type = "baseline"
            elif comparable:
                claim_type = "comparison"
            else:
                claim_type = "fact"
            counter_evidence_ids = [
                item.id
                for item in selected_evidence
                if bool(item.metadata.get("counter_evidence"))
                or bool(item.metadata.get("conflicting"))
            ]
            if has_conflict:
                uncertainty = "现有资料存在冲突，必须统一时间、版本和计量口径后再判断。"
            elif has_weak:
                uncertainty = "当前仅有较弱来源，该发现只能作为检索线索。"
            elif not comparable and len(covered_competitors) >= 2:
                uncertainty = "对象层级或责任边界不同，不能形成直接优劣排名。"
            else:
                uncertainty = "缺少统一版本、真实采用效果或客户验证资料。"
            claims.append(
                AnalysisClaimV2(
                    id=f"clv2_{self._slug(task_id)}_{self._slug(dimension)}",
                    task_id=task_id,
                    dimension=dimension,
                    claim_type=claim_type,
                    claim_text=(
                        f"现有{label}资料存在冲突，暂时不能形成稳定比较结论。"
                        if has_conflict else
                        f"现有证据表明，{'、'.join(covered_competitors)}在{label}方面"
                        "存在可观察差异，但差异是否构成客户价值仍需结合目标客户和场景验证。"
                        if comparable else
                        f"现有输入可确认 {'、'.join(covered_competitors)} 的{label}信息，"
                        "但因证据覆盖或可比口径限制，暂不支持直接排名。"
                    ),
                    competitors=covered_competitors,
                    evidence_ids=[item.id for item in selected_evidence],
                    counter_evidence_ids=counter_evidence_ids,
                    reasoning_summary=(
                        f"按对象各选取一条{label}结构化证据，并先检查证据覆盖范围。"
                    ),
                    uncertainty=uncertainty,
                    decision_impact=(
                        f"该发现可用于确定下一轮{label}验证重点，不能单独形成正式路线图。"
                    ),
                    confidence=min(
                        min(item.confidence for item in selected_evidence),
                        0.45 if has_weak else 0.55 if has_conflict else 1.0,
                    ),
                    produced_by_agent_run_id="mock_llm_analyst_v2",
                    citation_status=CitationStatus.PENDING,
                    metadata={"generation_mode": "mock_llm"},
                )
            )

        if task_metadata.get("include_fact_claim") and evidence:
            fact_evidence = evidence[0]
            claims.append(
                AnalysisClaimV2(
                    id=f"clv2_{self._slug(task_id)}_confirmed_fact",
                    task_id=task_id,
                    dimension=str(fact_evidence.dimension),
                    claim_type="fact",
                    claim_text=(
                        f"现有直接资料可确认 {fact_evidence.competitor} 在"
                        f"{self._dimension_label(str(fact_evidence.dimension))}方面的公开信息。"
                    ),
                    competitors=[fact_evidence.competitor],
                    evidence_ids=[fact_evidence.id],
                    counter_evidence_ids=[],
                    reasoning_summary="该事实仅复述一条已知结构化证据，不扩展为跨对象判断。",
                    uncertainty="该事实的客户价值与实际效果仍需独立验证。",
                    decision_impact="作为对象画像的已确认输入，不单独决定产品方向。",
                    confidence=fact_evidence.confidence,
                    produced_by_agent_run_id="mock_llm_analyst_v2",
                    citation_status=CitationStatus.PENDING,
                    metadata={"generation_mode": "mock_llm"},
                )
            )

        if task_metadata.get("include_recommendation_candidate"):
            comparison_claim = next(
                (
                    item
                    for item in claims
                    if item.claim_type in {"comparison", "baseline"}
                    and len(item.competitors) >= 2
                ),
                None,
            )
            if comparison_claim is not None:
                claims.append(
                    AnalysisClaimV2(
                        id=f"clv2_{self._slug(task_id)}_recommendation_candidate",
                        task_id=task_id,
                        dimension="strategy",
                        claim_type="recommendation",
                        claim_text=(
                            "当前可先把已确认的共同属性作为基线候选，并围绕责任边界和"
                            "适配条件开展客户验证；这是一项带条件的建议候选，不是正式决策。"
                        ),
                        competitors=comparison_claim.competitors,
                        evidence_ids=comparison_claim.evidence_ids,
                        counter_evidence_ids=[],
                        reasoning_summary=(
                            "建议候选来自已通过可比性检查的跨对象发现，未把竞品共同投入"
                            "直接转换为产品路线图。"
                        ),
                        uncertainty="仍需客户需求、自身能力、成本和风险验证。",
                        decision_impact="进入客户访谈或小范围验证，不直接承诺正式路线图。",
                        confidence=min(comparison_claim.confidence, 0.65),
                        produced_by_agent_run_id="mock_llm_analyst_v2",
                        citation_status=CitationStatus.PENDING,
                        metadata={"generation_mode": "mock_llm"},
                    )
                )

        gaps: list[ResearchGap] = []
        if missing_fields:
            gaps.append(ResearchGap(
                id=f"gap_{self._slug(task_id)}_brief",
                task_id=task_id,
                competitors=competitor_names,
                dimension="research_brief",
                missing_information="目标客户、核心任务场景和组织约束尚未明确。",
                decision_blocked="无法判断观察到的差异是否对目标客户形成真实价值。",
                why_existing_evidence_is_insufficient=(
                    "当前证据主要描述竞品自身，不能代替目标客户研究。"
                ),
                suggested_queries=["访谈目标客户的任务、痛点、替代做法和购买约束"],
                preferred_source_types=["客户访谈", "用户研究", "内部战略资料"],
                priority=TaskPriority.HIGH,
                stop_condition="目标客户与核心场景已由任务负责人确认并形成研究简报。",
                related_evidence_ids=[],
            ))
        if len(profiles) < 2:
            gaps.append(
                ResearchGap(
                    id=f"gap_{self._slug(task_id)}_comparison_scope",
                    task_id=task_id,
                    competitors=competitor_names,
                    dimension="comparison_scope",
                    missing_information="当前只有一个竞争对象，缺少横向比较基准。",
                    decision_blocked="不能判断观察结果是对象特征还是行业共性。",
                    why_existing_evidence_is_insufficient="单对象资料不能支撑跨竞品比较。",
                    suggested_queries=["补充至少一个直接竞品或替代方案"],
                    preferred_source_types=["官方资料", "可信第三方资料"],
                    priority=TaskPriority.HIGH,
                    stop_condition="已补充一个与决策问题相关且可说明选择理由的比较对象。",
                    related_evidence_ids=[],
                )
            )
        for profile in profiles:
            missing = [
                dimension
                for dimension in dimensions
                if not any(
                    str(item.dimension) == dimension
                    for item in evidence_by_competitor.get(profile.name, [])
                )
            ]
            if not missing:
                continue
            for missing_dimension in missing:
                gaps.append(ResearchGap(
                    id=(
                        f"gap_{self._slug(profile.name)}_"
                        f"{self._slug(missing_dimension)}_coverage"
                    ),
                    task_id=task_id,
                    competitors=[profile.name],
                    dimension=missing_dimension,
                    missing_information=(
                        f"{profile.name} 缺少{self._dimension_label(missing_dimension)}资料。"
                    ),
                    decision_blocked="相关维度不能进行同口径横向比较。",
                    why_existing_evidence_is_insufficient="当前输入没有该对象对应维度的结构化证据。",
                    suggested_queries=[f"查找 {profile.name} 的官方资料和可信第三方资料"],
                    preferred_source_types=["官方资料", "可信第三方资料"],
                    priority=TaskPriority.MEDIUM,
                    stop_condition="已找到直接资料，或明确记录该维度无法公开确认。",
                    related_evidence_ids=[],
                ))
        for coverage_item in coverage:
            if coverage_item.status not in {"conflicting", "weak"}:
                continue
            status_label = "冲突" if coverage_item.status == "conflicting" else "较弱"
            gaps.append(
                ResearchGap(
                    id=(
                        f"gap_{self._slug(coverage_item.competitor)}_"
                        f"{self._slug(coverage_item.dimension)}_{coverage_item.status}"
                    ),
                    task_id=task_id,
                    competitors=[coverage_item.competitor],
                    dimension=coverage_item.dimension,
                    missing_information=(
                        f"{coverage_item.competitor} 的{self._dimension_label(coverage_item.dimension)}"
                        f"资料存在{status_label}证据，需要补充核验。"
                    ),
                    decision_blocked="当前证据强度不足以支持高置信度判断。",
                    why_existing_evidence_is_insufficient=coverage_item.limitations,
                    suggested_queries=["优先查找同版本、同地区的官方或独立直接资料"],
                    preferred_source_types=["官方资料", "可信第三方资料"],
                    priority=TaskPriority.HIGH,
                    stop_condition="冲突已解释或获得至少一条适用的强直接证据。",
                    related_evidence_ids=coverage_item.evidence_ids,
                )
            )
        for note in notes:
            if note.comparable or len(note.competitors) < 2:
                continue
            gaps.append(
                ResearchGap(
                    id=f"gap_{self._slug(task_id)}_{self._slug(note.dimension)}_comparability",
                    task_id=task_id,
                    competitors=note.competitors,
                    dimension="comparability",
                    missing_information=(
                        f"{self._dimension_label(note.dimension)}缺少统一版本、服务边界或责任口径。"
                    ),
                    decision_blocked="不能进行直接优劣排名或总体成本比较。",
                    why_existing_evidence_is_insufficient=note.basis,
                    suggested_queries=["补充同版本、同规模和相同责任边界的资料"],
                    preferred_source_types=["官方定价与条款", "产品或服务说明"],
                    priority=TaskPriority.HIGH,
                    stop_condition="对象已统一到相同版本层级和责任边界，或明确保持不可比。",
                    related_evidence_ids=[],
                )
            )

        portfolio = CompetitiveAnalysisPortfolioV2(
            id=f"portfolio_{self._slug(task_id)}",
            task_id=task_id,
            prompt_id="competitive_analyst",
            prompt_version="2.2.1-candidate",
            brief_assessment=brief,
            competitor_profiles=profiles,
            key_intelligence_questions=questions,
            information_needs=needs,
            evidence_coverage=coverage,
            comparability_notes=notes,
            items=claims,
            research_gaps=gaps,
            metadata={
                "generation_mode": "mock_llm",
                "industry": industry,
                "source_count": len(source_by_id),
            },
        )
        return portfolio.model_dump(mode="json")

    def _mock_report(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> dict[str, Any]:
        if artifacts.get("report_context"):
            return self._mock_professional_report(task_id, artifacts)
        claims = [AnalysisClaim(**item) for item in artifacts.get("claims", [])]
        product_cards = [
            ProductCard(**item)
            for item in artifacts.get("product_cards", [])
        ]
        claim_ids = [claim.id for claim in claims]
        claim_lines = "\n".join(
            f"- {claim.claim_text} [{claim.id}]"
            for claim in claims
        )
        product_names = "、".join(card.name for card in product_cards)
        markdown = f"""# 通用竞品分析报告

## 任务背景

本报告基于结构化 SourceEvidence（来源证据）、ProductCard（产品卡片）、AnalysisClaim（分析结论）和 CitationCheck（引用检查）生成，覆盖 {product_names}。

## 竞争对象与解决路径概览

{claim_lines}

## 供给属性基线观察

决策团队应优先用客户任务、供给属性、采用成本和证据充分性进行比较。{self._claim_ref(claim_ids, 1)}

## 生产、交付与生态分析

生产方式、交付责任、生态连接和外部依赖需要按任务所属行业分别评价。{self._claim_ref(claim_ids, 3)}

## 商业模式与成本结构

商业模式判断必须回到定价证据和部署成本证据。{self._claim_ref(claim_ids, 2)}

## 风险与限制

弱引用和缺证据结论不能直接作为最终决策依据。{self._claim_ref(claim_ids, -1)}

## 产品研发建议

建议先明确目标客户、客户任务和希望承担的责任边界，再选择与任务行业相符的比较维度。{self._claim_ref(claim_ids, 0)}

## AnalysisClaim（分析结论）引用列表

{claim_lines}
"""
        return CompetitiveReport(
            id=f"report_llm_{self._slug(task_id)}",
            task_id=task_id,
            title="通用竞品分析报告",
            markdown=markdown,
            claim_ids=claim_ids,
            created_by_agent_run_id="mock_llm_writer",
            sections={
                "mode": "mock_llm",
                "product_count": len(product_cards),
                "claim_count": len(claims),
                "output_language": self.config.output_language,
            },
            metadata={
                "generation_mode": "mock_llm",
                "source": "LLMClient._mock_report",
                "output_language": self.config.output_language,
            },
        ).model_dump(mode="json")

    def _mock_professional_report(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> dict[str, Any]:
        try:
            task = AnalysisTask(**artifacts["analysis_task"][0])
            brief = BriefAssessment(**artifacts["brief_assessments"][0])
        except (IndexError, KeyError) as exc:
            raise ValueError(
                "Professional report requires analysis_task and brief_assessments"
            ) from exc
        report = build_professional_mock_report(
            task_id=task_id,
            task=task,
            brief=brief,
            profiles=[
                CompetitorProfile(**item)
                for item in artifacts.get("competitor_profiles", [])
            ],
            coverage=[
                EvidenceCoverage(**item)
                for item in artifacts.get("evidence_coverage", [])
            ],
            comparability_notes=[
                ComparabilityNote(**item)
                for item in artifacts.get("comparability_notes", [])
            ],
            claims_v2=[
                AnalysisClaimV2(**item)
                for item in artifacts.get("claims_v2", [])
            ],
            legacy_claims=[
                AnalysisClaim(**item) for item in artifacts.get("claims", [])
            ],
            citation_checks=[
                CitationCheck(**item)
                for item in artifacts.get("citation_checks", [])
            ],
            research_gaps=[
                ResearchGap(**item)
                for item in artifacts.get("research_gaps", [])
            ],
            output_language=self.config.output_language,
        )
        return report.model_dump(mode="json")

    @staticmethod
    def _claim_ref(claim_ids: list[str], index: int) -> str:
        if not claim_ids:
            return ""
        return f"[{claim_ids[index]}]"

    @staticmethod
    def _output_summary(
        output_schema: str,
        raw_output: dict[str, Any],
        parsed_ids: list[str],
    ) -> str:
        if output_schema.endswith("[]"):
            return f"已生成 {len(raw_output.get('items', []))} 个 {output_schema} 结构化对象"
        return f"已生成 {output_schema}，对象编号={','.join(parsed_ids)}"

    @staticmethod
    def _dimension_label(dimension: str) -> str:
        return {
            "positioning": "产品定位",
            "feature": "功能能力",
            "pricing": "定价与成本",
            "ecosystem": "生态与集成",
            "risk": "风险与限制",
            "customer": "目标客户",
            "other": "其他",
        }.get(dimension, dimension)

    @staticmethod
    def _infer_solution_path(card: ProductCard) -> tuple[str, str]:
        text = " ".join(
            [
                card.name,
                card.positioning,
                card.pricing_summary,
                *card.core_features,
                *card.strengths,
                *card.weaknesses,
                *card.source_ids,
                *card.evidence_ids,
            ]
        ).lower()
        if any(
            token in text
            for token in ["开源", "自托管", "self-host", "self_host", "github"]
        ):
            return (
                "开源自托管的虚拟教室方案",
                "由采用方自行部署并承担基础设施、维护与升级责任",
            )
        if any(token in text for token in ["低代码", "sdk", "open api", "云服务"]):
            return (
                "供教育平台集成的云端实时互动能力",
                "通过云服务、SDK（软件开发工具包）或 API（应用程序编程接口）接入现有业务系统",
            )
        return (
            "面向教育机构的开箱即用在线教学产品",
            "以完整产品和客户端能力交付，具体服务责任仍需结合合同确认",
        )

    def _localized_fact(self, evidence: SourceEvidence) -> str:
        if self.config.output_language.lower() == "zh-cn":
            if contains_chinese(evidence.snippet):
                return evidence.snippet
            if contains_chinese(evidence.normalized_fact):
                return evidence.normalized_fact
            return f"证据说明：{evidence.normalized_fact or evidence.snippet}"
        return evidence.normalized_fact or evidence.snippet

    @staticmethod
    def _claim_text(dimension: str, competitors: str) -> str:
        templates = {
            "positioning": (
                "现有证据显示，{competitors}在产品定位上采用不同路线；产品研发选型应结合"
                "目标用户、交付方式和可控性要求进行权衡。"
            ),
            "feature": (
                "现有证据显示，{competitors}在供给能力、任务覆盖范围和可定制方式上存在差异，"
                "这些属性是否形成客户价值仍需进一步验证。"
            ),
            "pricing": (
                "现有定价证据显示，{competitors}的计费方式和成本边界不同，比较时需要"
                "同时考虑软件费用、使用规模与部署运维成本。"
            ),
            "ecosystem": (
                "现有证据显示，{competitors}在生态连接、外部依赖和扩展方式上各有侧重，"
                "采用与转换成本应结合任务所属行业单独评估。"
            ),
            "risk": (
                "现有风险证据显示，{competitors}在服务依赖、部署维护或证据充分性方面存在"
                "不同限制，关键结论仍需结合引用强度审慎使用。"
            ),
        }
        template = templates.get(
            dimension,
            "现有证据显示，{competitors}在该分析维度上存在差异，需要结合产品研发目标进行权衡。",
        )
        return template.format(competitors=competitors)

    @staticmethod
    def _slug(value: str) -> str:
        return (
            value.lower()
            .replace(" ", "_")
            .replace("/", "_")
            .replace("-", "_")
            .replace("?", "")
        )[:40]
