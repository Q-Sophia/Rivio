from __future__ import annotations

from pathlib import Path

from app.agents.base import BaseAgent
from app.schemas import (
    AgentContext,
    AgentResult,
    AgentRole,
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    ProductCard,
    ReviewFeedback,
    SourceDocument,
    SourceEvidence,
)
from app.tools.registry import ToolRegistry
from build_claims_demo import build_claims, validate_product_card_evidence_links
from build_product_cards_demo import build_product_cards, validate_source_evidence_links
from build_report_demo import build_report, validate_claim_citation_links
from run_citation_check_demo import run_citation_checks
from run_review_demo import build_review_feedback


class SnapshotAgent(BaseAgent):
    def __init__(
        self,
        *,
        name: str,
        role: AgentRole,
        tools: ToolRegistry,
        input_artifacts: list[str] | None = None,
        output_artifacts: list[str] | None = None,
        snapshot_id: str = "",
        snapshot_root: Path | str | None = None,
    ):
        super().__init__(
            name=name,
            role=role,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
        )
        self.tools = tools
        self.snapshot_id = snapshot_id
        self.snapshot_root = snapshot_root

    @staticmethod
    def agent_run_id(context: AgentContext) -> str:
        return context.metadata["agent_run_id"]

    def load_many(self, context: AgentContext, artifact_types: list[str]) -> dict:
        return self.tools.call(
            "artifact_store.load_many",
            agent_run_id=self.agent_run_id(context),
            task_id=context.task_id,
            artifact_types=artifact_types,
        )

    def save_many(
        self,
        context: AgentContext,
        artifact_type: str,
        items: list,
        output_summary: str,
    ) -> None:
        self.tools.call(
            "artifact_store.save_many",
            agent_run_id=self.agent_run_id(context),
            task_id=context.task_id,
            artifact_type=artifact_type,
            items=items,
            output_summary=output_summary,
        )

    def mark_refs_validated(self, context: AgentContext, message: str) -> None:
        self.tools.call(
            "artifact_validator.check_refs",
            agent_run_id=self.agent_run_id(context),
            task_id=context.task_id,
            node_id=context.node_id,
            message=message,
            output_summary=message,
        )


class CollectorAgent(SnapshotAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        sources, evidence = self.tools.call(
            "snapshot_collector.collect",
            agent_run_id=self.agent_run_id(context),
            task_id=context.task_id,
            snapshot_id=self.snapshot_id,
            snapshot_root=self.snapshot_root,
        )
        self.save_many(context, "sources", sources, f"Saved {len(sources)} sources")
        self.save_many(
            context,
            "evidence",
            evidence,
            f"Saved {len(evidence)} evidence items",
        )
        return self.make_result(
            context,
            output_summary=(
                f"Loaded {len(sources)} sources and {len(evidence)} evidence items"
            ),
            output_artifacts={
                "sources": [item.id for item in sources],
                "evidence": [item.id for item in evidence],
            },
        )


class ExtractorAgent(SnapshotAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(context, ["sources", "evidence"])
        sources = [SourceDocument(**item) for item in raw["sources"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        if not sources:
            raise ValueError(f"No sources found for task_id={context.task_id}")
        if not evidence:
            raise ValueError(f"No evidence found for task_id={context.task_id}")
        validate_source_evidence_links(sources, evidence)
        self.mark_refs_validated(context, "Validated evidence.source_id references")

        product_cards = build_product_cards(context.task_id, sources, evidence)
        self.save_many(
            context,
            "product_cards",
            product_cards,
            f"Saved {len(product_cards)} product cards",
        )
        return self.make_result(
            context,
            output_summary=f"Generated {len(product_cards)} product cards",
            output_artifacts={
                "product_cards": [item.id for item in product_cards],
            },
        )


class AnalystAgent(SnapshotAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(context, ["product_cards", "evidence"])
        product_cards = [ProductCard(**item) for item in raw["product_cards"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        if not product_cards:
            raise ValueError(
                f"No product_cards found for task_id={context.task_id}"
            )
        if not evidence:
            raise ValueError(f"No evidence found for task_id={context.task_id}")
        validate_product_card_evidence_links(product_cards, evidence)
        self.mark_refs_validated(
            context,
            "Validated product_cards.evidence_ids references",
        )

        claims = build_claims(context.task_id, product_cards, evidence)
        self.save_many(context, "claims", claims, f"Saved {len(claims)} claims")
        return self.make_result(
            context,
            output_summary=f"Generated {len(claims)} claims",
            output_artifacts={"claims": [item.id for item in claims]},
        )


class CitationAgent(SnapshotAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(context, ["sources", "evidence", "claims"])
        sources = [SourceDocument(**item) for item in raw["sources"]]
        evidence = [SourceEvidence(**item) for item in raw["evidence"]]
        claims = [AnalysisClaim(**item) for item in raw["claims"]]
        if not sources:
            raise ValueError(f"No sources found for task_id={context.task_id}")
        if not evidence:
            raise ValueError(f"No evidence found for task_id={context.task_id}")
        if not claims:
            raise ValueError(f"No claims found for task_id={context.task_id}")
        validate_source_evidence_links(sources, evidence)

        updated_claims, citation_checks = self.tools.call(
            "citation_checker.check_claims",
            agent_run_id=self.agent_run_id(context),
            task_id=context.task_id,
            sources=sources,
            evidence=evidence,
            claims=claims,
        )
        supported = sum(1 for check in citation_checks if check.status == "supported")
        weak = sum(1 for check in citation_checks if check.status == "weak")
        self.mark_refs_validated(
            context,
            (
                f"Checked {len(citation_checks)} claim citation links, "
                f"supported={supported}, weak={weak}"
            ),
        )
        self.save_many(
            context,
            "claims",
            updated_claims,
            f"Saved {len(updated_claims)} claims with citation status",
        )
        self.save_many(
            context,
            "citation_checks",
            citation_checks,
            f"Saved {len(citation_checks)} citation checks",
        )
        return self.make_result(
            context,
            output_summary=(
                f"Created {len(citation_checks)} citation checks, "
                f"supported={supported}, weak={weak}"
            ),
            output_artifacts={
                "claims": [item.id for item in updated_claims],
                "citation_checks": [item.id for item in citation_checks],
            },
        )


class WriterAgent(SnapshotAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(
            context,
            ["product_cards", "claims", "citation_checks", "sources", "evidence"],
        )
        product_cards = [ProductCard(**item) for item in raw["product_cards"]]
        claims = [AnalysisClaim(**item) for item in raw["claims"]]
        citation_checks = [
            CitationCheck(**item) for item in raw["citation_checks"]
        ]
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

        report = build_report(
            context.task_id,
            product_cards,
            claims,
            citation_checks,
            sources,
            evidence,
        )
        self.save_many(context, "reports", [report], "Saved 1 competitive report")
        return self.make_result(
            context,
            output_summary=f"Generated 1 report with {len(report.claim_ids)} claim ids",
            output_artifacts={"reports": [report.id]},
        )


class ReviewerAgent(SnapshotAgent):
    def execute(self, context: AgentContext) -> AgentResult:
        raw = self.load_many(
            context,
            ["reports", "claims", "citation_checks", "product_cards"],
        )
        reports = [CompetitiveReport(**item) for item in raw["reports"]]
        claims = [AnalysisClaim(**item) for item in raw["claims"]]
        citation_checks = [
            CitationCheck(**item) for item in raw["citation_checks"]
        ]
        product_cards = [ProductCard(**item) for item in raw["product_cards"]]
        if not reports:
            raise ValueError(f"No reports found for task_id={context.task_id}")
        if not claims:
            raise ValueError(f"No claims found for task_id={context.task_id}")
        if not citation_checks:
            raise ValueError(f"No citation_checks found for task_id={context.task_id}")
        if not product_cards:
            raise ValueError(f"No product_cards found for task_id={context.task_id}")

        review = self.tools.call(
            "review_checker.check_report",
            agent_run_id=self.agent_run_id(context),
            task_id=context.task_id,
            report=reports[-1],
            claims=claims,
            citation_checks=citation_checks,
            product_cards=product_cards,
        )
        self.save_many(
            context,
            "review_feedback",
            [review],
            (
                f"Saved review feedback, approved={review.approved}, "
                f"score={review.overall_score}"
            ),
        )
        return self.make_result(
            context,
            output_summary=(
                f"Generated review feedback, approved={review.approved}, "
                f"score={review.overall_score}"
            ),
            output_artifacts={"review_feedback": [review.id]},
        )
