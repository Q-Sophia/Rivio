from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.context.memory import (
    MemoryStore,
    build_memory_items_from_artifacts,
    build_working_memory_from_artifacts,
)
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AgentRole,
    AnalysisClaim,
    CitationCheck,
    ContextBundle,
    DAGNode,
    MemoryItem,
    ProductCard,
    QualityGateDecision,
    ReviewFeedback,
    SourceDocument,
    SourceEvidence,
    TaskRecord,
    WorkingMemory,
)


CONTEXT_BUNDLES_ARTIFACT = "context_bundles"
DEFAULT_CONTEXT_AGENT_ORDER = [
    AgentRole.COLLECTOR,
    AgentRole.EXTRACTOR,
    AgentRole.ANALYST,
    AgentRole.CITATION,
    AgentRole.WRITER,
    AgentRole.REVIEWER,
]


class ContextBuilder:
    """Build role-specific context bundles from artifacts and memory."""

    def __init__(self, *, store: ArtifactStore):
        self.store = store
        self.memory_store = MemoryStore(store=store)

    def build_all(self, task_id: str) -> dict[str, Any]:
        working_memory = build_working_memory_from_artifacts(
            task_id=task_id,
            store=self.store,
        )
        working_memory = self.memory_store.save_working_memory(working_memory)
        memory_items = build_memory_items_from_artifacts(
            task_id=task_id,
            store=self.store,
            working_memory=working_memory,
        )
        memory_items = self.memory_store.save_memory_items(task_id, memory_items)
        bundles = [
            self.build_for_role(
                task_id=task_id,
                agent_role=role,
                working_memory=working_memory,
                memory_items=memory_items,
            )
            for role in DEFAULT_CONTEXT_AGENT_ORDER
        ]
        self.store.save_many(task_id, CONTEXT_BUNDLES_ARTIFACT, bundles)
        return {
            "working_memory": working_memory,
            "memory_items": memory_items,
            "context_bundles": bundles,
        }

    def build_for_role(
        self,
        *,
        task_id: str,
        agent_role: AgentRole,
        working_memory: WorkingMemory,
        memory_items: list[MemoryItem],
    ) -> ContextBundle:
        artifacts = self._load_artifacts(task_id)
        task_key = self._task_key_for_role(agent_role)
        node_id = self._node_id_for_task_key(artifacts["dag_nodes"], task_key)
        selected = self._select_context_for_role(agent_role, artifacts, working_memory)
        role_value = agent_role.value if hasattr(agent_role, "value") else str(agent_role)
        bundle = ContextBundle(
            id=f"ctxbundle_{role_value}",
            task_id=task_id,
            agent_role=agent_role,
            node_id=node_id,
            task_key=task_key,
            system_context=self._system_context_for_role(agent_role),
            task_context=self._task_context(artifacts, working_memory),
            working_context=selected["working_context"],
            artifact_refs=selected["artifact_refs"],
            source_ids=selected["source_ids"],
            evidence_ids=selected["evidence_ids"],
            product_card_ids=selected["product_card_ids"],
            claim_ids=selected["claim_ids"],
            citation_check_ids=selected["citation_check_ids"],
            report_ids=selected["report_ids"],
            task_record_ids=selected["task_record_ids"],
            memory_item_ids=self._memory_ids_for_role(agent_role, memory_items),
            guardrail_policy=self._guardrail_policy_for_role(agent_role),
            compression_level=1,
            token_budget=self._token_budget_for_role(agent_role),
            metadata={
                "context_builder": "deterministic_artifact_context_builder_v1",
                "context_layers": ["system", "task", "working"],
                "id_preservation_required": True,
            },
        )
        return bundle.model_copy(
            update={"estimated_tokens": self._estimate_tokens(bundle)}
        )

    def _load_artifacts(self, task_id: str) -> dict[str, list]:
        return {
            "sources": [
                SourceDocument(**item)
                for item in self.store.load_many(task_id, "sources")
            ],
            "evidence": [
                SourceEvidence(**item)
                for item in self.store.load_many(task_id, "evidence")
            ],
            "product_cards": [
                ProductCard(**item)
                for item in self.store.load_many(task_id, "product_cards")
            ],
            "claims": [
                AnalysisClaim(**item)
                for item in self.store.load_many(task_id, "claims")
            ],
            "citation_checks": [
                CitationCheck(**item)
                for item in self.store.load_many(task_id, "citation_checks")
            ],
            "review_feedback": [
                ReviewFeedback(**item)
                for item in self.store.load_many(task_id, "review_feedback")
            ],
            "quality_gates": [
                QualityGateDecision(**item)
                for item in self.store.load_many(task_id, "quality_gates")
            ],
            "task_records": [
                TaskRecord(**item)
                for item in self.store.load_many(task_id, "task_records")
            ],
            "feedback_tasks": [
                TaskRecord(**item)
                for item in self.store.load_many(task_id, "feedback_tasks")
            ],
            "dag_nodes": [
                DAGNode(**item)
                for item in self.store.load_many(task_id, "dag_nodes")
            ],
        }

    def _select_context_for_role(
        self,
        agent_role: AgentRole,
        artifacts: dict[str, list],
        working_memory: WorkingMemory,
    ) -> dict[str, Any]:
        sources: list[SourceDocument] = artifacts["sources"]
        evidence: list[SourceEvidence] = artifacts["evidence"]
        product_cards: list[ProductCard] = artifacts["product_cards"]
        claims: list[AnalysisClaim] = artifacts["claims"]
        citation_checks: list[CitationCheck] = artifacts["citation_checks"]
        reviews: list[ReviewFeedback] = artifacts["review_feedback"]
        quality_gates: list[QualityGateDecision] = artifacts["quality_gates"]
        task_records: list[TaskRecord] = artifacts["task_records"]
        feedback_tasks: list[TaskRecord] = artifacts["feedback_tasks"]
        reports = self.store.load_many(working_memory.task_id, "reports")
        report_ids = [str(item.get("id", "")) for item in reports]
        selected_product_cards = product_cards

        if agent_role == AgentRole.COLLECTOR:
            selected_sources = sources
            selected_evidence = evidence[:12]
            working_context = {
                "source_count": len(sources),
                "evidence_count": len(evidence),
                "competitors": working_memory.active_competitors,
                "source_types": sorted({source.source_type for source in sources}),
                "next_expected_output": ["SourceDocument", "SourceEvidence"],
            }
        elif agent_role == AgentRole.EXTRACTOR:
            selected_sources = sources
            selected_evidence = evidence
            working_context = {
                "evidence_by_competitor": self._count_by(evidence, "competitor"),
                "product_card_contract": "ProductCard fields must keep source_ids and evidence_ids.",
                "next_expected_output": ["ProductCard"],
            }
        elif agent_role == AgentRole.ANALYST:
            # Analyst reasoning is Evidence-only. SourceDocument/ProductCard
            # remain in the store for provenance and compatibility validation,
            # but are not exposed in the Analyst bundle.
            selected_sources = []
            selected_evidence = evidence
            selected_product_cards = []
            working_context = {
                "evidence_by_dimension": self._count_by(evidence, "dimension"),
                "weak_claim_ids": working_memory.weak_claim_ids,
                "next_expected_output": ["AnalysisClaim"],
            }
        elif agent_role == AgentRole.CITATION:
            selected_sources = sources
            selected_evidence = evidence
            working_context = {
                "claims": [self._claim_summary(claim) for claim in claims],
                "citation_policy": "Every claim.evidence_ids item must resolve to SourceEvidence and SourceDocument.",
                "next_expected_output": ["CitationCheck"],
            }
        elif agent_role == AgentRole.WRITER:
            selected_sources = sources[:6]
            selected_evidence = evidence[:20]
            working_context = {
                "supported_claim_ids": [
                    claim.id for claim in claims if claim.citation_status == "supported"
                ],
                "weak_claim_ids": working_memory.weak_claim_ids,
                "citation_checks": [self._citation_summary(check) for check in citation_checks],
                "writer_rule": "Do not introduce strong conclusions without claim_id references.",
                "next_expected_output": ["CompetitiveReport"],
            }
        else:
            selected_sources = sources[:6]
            selected_evidence = evidence[:20]
            latest_review = reviews[-1] if reviews else None
            latest_gate = quality_gates[-1] if quality_gates else None
            working_context = {
                "report_ids": report_ids,
                "review_issue_ids": [issue.id for issue in latest_review.issues] if latest_review else [],
                "quality_gate_status": latest_gate.status if latest_gate else "not_run",
                "feedback_task_keys": [task.task_key for task in feedback_tasks],
                "next_expected_output": ["ReviewFeedback", "QualityGateDecision", "Feedback TaskRecord"],
            }

        return {
            "working_context": working_context,
            "artifact_refs": self._artifact_refs(
                selected_sources=selected_sources,
                selected_evidence=selected_evidence,
                product_cards=selected_product_cards,
                claims=claims,
                citation_checks=citation_checks,
                report_ids=report_ids,
                task_records=task_records,
            ),
            "source_ids": [source.id for source in selected_sources],
            "evidence_ids": [item.id for item in selected_evidence],
            "product_card_ids": [card.id for card in selected_product_cards],
            "claim_ids": [claim.id for claim in claims],
            "citation_check_ids": [check.id for check in citation_checks],
            "report_ids": report_ids,
            "task_record_ids": [task.id for task in task_records],
        }

    @staticmethod
    def _system_context_for_role(agent_role: AgentRole) -> list[str]:
        return [
            f"Agent role（智能体角色）：{agent_role.value if hasattr(agent_role, 'value') else agent_role}。",
            "默认使用简体中文（zh-CN）生成业务产物；英文术语首次出现时补充中文注释。",
            "只能使用 ContextBundle（上下文包）提供的结构化产物。",
            "必须保留 source_id、evidence_id、claim_id、citation_check_id 和 task_record_id。",
            "不得生成缺少证据支持的产品事实或报告结论。",
            "LLM（大模型）输出必须通过 Pydantic（数据校验模型）结构校验和语言一致性校验。",
        ]

    @staticmethod
    def _task_context(
        artifacts: dict[str, list],
        working_memory: WorkingMemory,
    ) -> dict[str, Any]:
        return {
            "task_id": working_memory.task_id,
            "current_goal": working_memory.current_goal,
            "task_summary": working_memory.task_summary,
            "competitors": working_memory.active_competitors,
            "focus_areas": working_memory.focus_areas,
            "completed_task_keys": working_memory.completed_task_keys,
            "skipped_task_keys": working_memory.skipped_task_keys,
            "quality_gate_ids": working_memory.quality_gate_ids,
            "artifact_counts": {
                key: len(value)
                for key, value in artifacts.items()
                if key != "dag_nodes"
            },
        }

    @staticmethod
    def _artifact_refs(
        *,
        selected_sources: list[SourceDocument],
        selected_evidence: list[SourceEvidence],
        product_cards: list[ProductCard],
        claims: list[AnalysisClaim],
        citation_checks: list[CitationCheck],
        report_ids: list[str],
        task_records: list[TaskRecord],
    ) -> dict[str, list[str]]:
        return {
            "sources": [source.id for source in selected_sources],
            "evidence": [item.id for item in selected_evidence],
            "product_cards": [card.id for card in product_cards],
            "claims": [claim.id for claim in claims],
            "citation_checks": [check.id for check in citation_checks],
            "reports": report_ids,
            "task_records": [task.id for task in task_records],
        }

    @staticmethod
    def _memory_ids_for_role(
        agent_role: AgentRole,
        memory_items: list[MemoryItem],
    ) -> list[str]:
        if agent_role in {AgentRole.COLLECTOR, AgentRole.CITATION, AgentRole.REVIEWER}:
            allowed = {"user_preference", "source_policy", "quality_issue", "run_summary"}
        elif agent_role == AgentRole.WRITER:
            allowed = {"user_preference", "domain_framework", "approved_claim", "run_summary"}
        else:
            allowed = {"user_preference", "domain_framework", "analysis_pattern", "run_summary"}
        return [item.id for item in memory_items if item.kind in allowed]

    @staticmethod
    def _guardrail_policy_for_role(agent_role: AgentRole) -> list[str]:
        common = [
            "preserve_artifact_ids",
            "schema_validate_outputs",
            "do_not_bypass_citation_checks",
        ]
        if agent_role == AgentRole.WRITER:
            return common + ["report_conclusions_require_claim_ids"]
        if agent_role == AgentRole.ANALYST:
            return common + [
                "claims_require_evidence_ids",
                "analyst_evidence_only_no_source_content",
            ]
        if agent_role == AgentRole.COLLECTOR:
            return common + ["raw_web_results_must_be_structured"]
        return common

    @staticmethod
    def _token_budget_for_role(agent_role: AgentRole) -> int:
        if agent_role in {AgentRole.ANALYST, AgentRole.WRITER}:
            return 6000
        if agent_role == AgentRole.REVIEWER:
            return 5000
        return 4000

    @staticmethod
    def _task_key_for_role(agent_role: AgentRole) -> str:
        return {
            AgentRole.COLLECTOR: "collect_sources",
            AgentRole.EXTRACTOR: "build_product_cards",
            AgentRole.ANALYST: "build_claims",
            AgentRole.CITATION: "check_citations",
            AgentRole.WRITER: "build_report",
            AgentRole.REVIEWER: "review_report",
        }.get(agent_role, "")

    @staticmethod
    def _node_id_for_task_key(nodes: list[DAGNode], task_key: str) -> str:
        for node in nodes:
            if node.id == task_key or node.label == task_key:
                return node.id
        return task_key

    @staticmethod
    def _count_by(items: list, attr: str) -> dict[str, int]:
        return dict(Counter(str(getattr(item, attr)) for item in items))

    @staticmethod
    def _product_card_summary(card: ProductCard) -> dict[str, Any]:
        return {
            "id": card.id,
            "name": card.name,
            "positioning": card.positioning,
            "evidence_ids": card.evidence_ids,
        }

    @staticmethod
    def _claim_summary(claim: AnalysisClaim) -> dict[str, Any]:
        return {
            "id": claim.id,
            "dimension": claim.dimension,
            "citation_status": claim.citation_status,
            "evidence_ids": claim.evidence_ids,
        }

    @staticmethod
    def _citation_summary(check: CitationCheck) -> dict[str, Any]:
        return {
            "id": check.id,
            "claim_id": check.claim_id,
            "status": check.status,
            "evidence_ids": check.evidence_ids,
        }

    @staticmethod
    def _estimate_tokens(bundle: ContextBundle) -> int:
        payload = bundle.model_dump(mode="json")
        return max(len(json.dumps(payload, ensure_ascii=False)) // 4, 1)


def build_context_memory_artifacts(
    *,
    task_id: str,
    store: ArtifactStore,
) -> dict[str, Any]:
    return ContextBuilder(store=store).build_all(task_id)
