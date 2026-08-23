from __future__ import annotations

from collections import Counter

from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    CompetitiveReport,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    ProductCard,
    QualityGateDecision,
    ReviewFeedback,
    SourceDocument,
    SourceEvidence,
    TaskRecord,
    WorkingMemory,
    utc_now,
)


WORKING_MEMORY_ARTIFACT = "working_memory"
MEMORY_ITEMS_ARTIFACT = "memory_items"


class MemoryStore:
    """Artifact-backed memory boundary.

    The current implementation is intentionally JSON-based. It separates
    short-lived working memory, run memory, and long-term memory candidates
    without introducing vector databases before RAG is needed.
    """

    def __init__(self, *, store: ArtifactStore):
        self.store = store

    def save_working_memory(self, memory: WorkingMemory) -> WorkingMemory:
        validated = WorkingMemory(**memory.model_dump(mode="json"))
        self.store.save_many(validated.task_id, WORKING_MEMORY_ARTIFACT, [validated])
        return validated

    def save_memory_items(self, task_id: str, items: list[MemoryItem]) -> list[MemoryItem]:
        validated = [MemoryItem(**item.model_dump(mode="json")) for item in items]
        self.store.save_many(task_id, MEMORY_ITEMS_ARTIFACT, validated)
        return validated

    def load_working_memory(self, task_id: str) -> WorkingMemory | None:
        items = self.store.load_many(task_id, WORKING_MEMORY_ARTIFACT)
        if not items:
            return None
        return WorkingMemory(**items[-1])

    def load_memory_items(self, task_id: str) -> list[MemoryItem]:
        return [
            MemoryItem(**item)
            for item in self.store.load_many(task_id, MEMORY_ITEMS_ARTIFACT)
        ]


def build_working_memory_from_artifacts(
    *,
    task_id: str,
    store: ArtifactStore,
) -> WorkingMemory:
    sources = [SourceDocument(**item) for item in store.load_many(task_id, "sources")]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    product_cards = [
        ProductCard(**item)
        for item in store.load_many(task_id, "product_cards")
    ]
    claims = [AnalysisClaim(**item) for item in store.load_many(task_id, "claims")]
    citation_checks = [
        CitationCheck(**item)
        for item in store.load_many(task_id, "citation_checks")
    ]
    reviews = [
        ReviewFeedback(**item)
        for item in store.load_many(task_id, "review_feedback")
    ]
    task_records = [
        TaskRecord(**item)
        for item in store.load_many(task_id, "task_records")
    ]
    quality_gates = [
        QualityGateDecision(**item)
        for item in store.load_many(task_id, "quality_gates")
    ]
    feedback_tasks = [
        TaskRecord(**item)
        for item in store.load_many(task_id, "feedback_tasks")
    ]

    latest_review = reviews[-1] if reviews else None
    dimension_counts = Counter(str(item.dimension) for item in evidence)
    weak_claim_ids = [
        check.claim_id for check in citation_checks if check.status == "weak"
    ]
    invalid_claim_ids = [
        check.claim_id
        for check in citation_checks
        if check.status in {"invalid_evidence", "missing_evidence", "unsupported"}
    ]

    return WorkingMemory(
        id="wm_snapshot_online_education",
        task_id=task_id,
        current_goal="Generate an evidence-first competitive analysis report for online classroom real-time interaction solutions.",
        task_summary=(
            f"Loaded {len(sources)} sources, {len(evidence)} evidence items, "
            f"{len(product_cards)} product cards, {len(claims)} claims, "
            f"{len(citation_checks)} citation checks, and "
            f"{len(feedback_tasks)} feedback tasks."
        ),
        active_competitors=[card.name for card in product_cards],
        focus_areas=sorted(dimension_counts.keys()),
        current_task_key="review_report",
        completed_task_keys=[
            record.task_key for record in task_records if record.status == "completed"
        ],
        pending_task_keys=[
            record.task_key for record in task_records if record.status == "pending"
        ],
        blocked_task_keys=[
            record.task_key for record in task_records if record.status == "blocked"
        ],
        skipped_task_keys=[
            record.task_key for record in task_records if record.status == "skipped"
        ],
        selected_source_ids=[source.id for source in sources],
        selected_evidence_ids=[item.id for item in evidence],
        selected_product_card_ids=[card.id for card in product_cards],
        selected_claim_ids=[claim.id for claim in claims],
        weak_claim_ids=weak_claim_ids,
        invalid_claim_ids=invalid_claim_ids,
        review_issue_ids=[issue.id for issue in latest_review.issues] if latest_review else [],
        feedback_task_ids=[task.id for task in feedback_tasks],
        quality_gate_ids=[gate.id for gate in quality_gates],
        notes=[
            "Short-term memory is scoped to this task_id and should not be reused as a current market fact in another run.",
            "Long-term memory can store frameworks, preferences, and source policies; volatile product facts must keep evidence_ids and verification timestamps.",
        ],
        metadata={
            "memory_scope": "task_run",
            "dimension_counts": dict(dimension_counts),
            "approved": latest_review.approved if latest_review else False,
            "review_score": latest_review.overall_score if latest_review else 0.0,
        },
    )


def build_memory_items_from_artifacts(
    *,
    task_id: str,
    store: ArtifactStore,
    working_memory: WorkingMemory,
) -> list[MemoryItem]:
    sources = [SourceDocument(**item) for item in store.load_many(task_id, "sources")]
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
    ]
    claims = [AnalysisClaim(**item) for item in store.load_many(task_id, "claims")]
    citation_checks = [
        CitationCheck(**item)
        for item in store.load_many(task_id, "citation_checks")
    ]
    reports = [
        CompetitiveReport(**item)
        for item in store.load_many(task_id, "reports")
    ]
    latest_report = reports[-1] if reports else None
    now = utc_now()
    supported_claim_ids = [
        claim.id for claim in claims if claim.citation_status == "supported"
    ]
    weak_claim_ids = [
        check.claim_id for check in citation_checks if check.status == "weak"
    ]

    return [
        MemoryItem(
            id="mem_user_pref_evidence_first",
            task_id=task_id,
            scope=MemoryScope.LONG_TERM,
            kind=MemoryKind.USER_PREFERENCE,
            key="user_pref.evidence_first_report",
            content=(
                "The user wants a mature evidence-first multi-agent competitive "
                "analysis system, not a toy LLM report generator."
            ),
            summary="Project preference: keep evidence-first workflow and traceability.",
            confidence=0.95,
            metadata={
                "volatile": False,
                "reuse_policy": "safe_cross_task_design_preference",
            },
        ),
        MemoryItem(
            id="mem_framework_competitive_analysis",
            task_id=task_id,
            scope=MemoryScope.LONG_TERM,
            kind=MemoryKind.DOMAIN_FRAMEWORK,
            key="framework.competitive_analysis_dimensions",
            content=(
                "Competitive analysis should cover positioning, feature baseline, "
                "technical access, ecosystem, pricing/business model, risks, and "
                "product R&D recommendations."
            ),
            summary="Reusable analysis framework for competitive intelligence reports.",
            confidence=0.9,
            metadata={
                "volatile": False,
                "reuse_policy": "safe_cross_task_framework",
            },
        ),
        MemoryItem(
            id="mem_policy_source_traceability",
            task_id=task_id,
            scope=MemoryScope.LONG_TERM,
            kind=MemoryKind.SOURCE_POLICY,
            key="policy.source_traceability",
            content=(
                "Web, MCP, RAG, or LLM outputs must not bypass SourceDocument, "
                "SourceEvidence, AnalysisClaim, CitationCheck, and ReviewFeedback."
            ),
            summary="Reusable source and citation policy.",
            confidence=0.95,
            metadata={
                "volatile": False,
                "reuse_policy": "safe_cross_task_governance",
            },
        ),
        MemoryItem(
            id="mem_run_snapshot_summary",
            task_id=task_id,
            scope=MemoryScope.RUN,
            kind=MemoryKind.RUN_SUMMARY,
            key="run.snapshot_online_education.summary",
            content=working_memory.task_summary,
            summary="Run-level artifact count summary.",
            source_ids=[source.id for source in sources],
            evidence_ids=[item.id for item in evidence],
            claim_ids=[claim.id for claim in claims],
            citation_check_ids=[check.id for check in citation_checks],
            report_ids=[latest_report.id] if latest_report else [],
            confidence=0.9,
            last_verified_at=now,
            metadata={"volatile": True, "reuse_policy": "task_scoped_only"},
        ),
        MemoryItem(
            id="mem_run_supported_claims",
            task_id=task_id,
            scope=MemoryScope.RUN,
            kind=MemoryKind.APPROVED_CLAIM,
            key="run.snapshot_online_education.supported_claims",
            content=(
                f"Supported claim ids: {', '.join(supported_claim_ids)}. "
                f"Weak claim ids: {', '.join(weak_claim_ids) or 'none'}."
            ),
            summary="Run-level claim quality summary.",
            claim_ids=[claim.id for claim in claims],
            citation_check_ids=[check.id for check in citation_checks],
            confidence=0.9,
            last_verified_at=now,
            metadata={"volatile": True, "reuse_policy": "task_scoped_only"},
        ),
    ]
