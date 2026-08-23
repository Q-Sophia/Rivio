from __future__ import annotations

from app.context.builder import CONTEXT_BUNDLES_ARTIFACT
from app.context.memory import MEMORY_ITEMS_ARTIFACT, WORKING_MEMORY_ARTIFACT
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    ContextBundle,
    GuardrailCheck,
    GuardrailStatus,
    IssueSeverity,
    MemoryItem,
    ProductCard,
    QualityGateDecision,
    ReviewFeedback,
    SourceDocument,
    SourceEvidence,
    TaskRecord,
    WorkingMemory,
)


GUARDRAIL_CHECKS_ARTIFACT = "guardrail_checks"


class GuardrailChecker:
    """Deterministic guardrails for context, memory, and evidence integrity."""

    def __init__(self, *, store: ArtifactStore):
        self.store = store

    def run_all(self, task_id: str) -> list[GuardrailCheck]:
        artifacts = self._load_artifacts(task_id)
        checks = [
            self.check_evidence_source_refs(task_id, artifacts),
            self.check_claim_evidence_refs(task_id, artifacts),
            self.check_report_claim_refs(task_id, artifacts),
            self.check_context_id_preservation(task_id, artifacts),
            self.check_writer_context_claim_policy(task_id, artifacts),
            self.check_memory_policy(task_id, artifacts),
            self.check_weak_citation_visibility(task_id, artifacts),
        ]
        self.store.save_many(task_id, GUARDRAIL_CHECKS_ARTIFACT, checks)
        return checks

    def check_evidence_source_refs(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        sources: list[SourceDocument] = artifacts["sources"]
        evidence: list[SourceEvidence] = artifacts["evidence"]
        source_ids = {source.id for source in sources}
        missing = [item.id for item in evidence if item.source_id not in source_ids]
        return self._make_check(
            task_id=task_id,
            guardrail_name="evidence_source_refs_valid",
            passed=not missing,
            severity=IssueSeverity.HIGH,
            target_type="evidence",
            message=(
                "All SourceEvidence.source_id values resolve to SourceDocument.id."
                if not missing
                else "Evidence items reference missing SourceDocument ids: "
                + ", ".join(missing)
            ),
            evidence_ids=missing,
        )

    def check_claim_evidence_refs(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        claims: list[AnalysisClaim] = artifacts["claims"]
        evidence: list[SourceEvidence] = artifacts["evidence"]
        evidence_ids = {item.id for item in evidence}
        missing_claims = [
            claim.id
            for claim in claims
            if not claim.evidence_ids
            or any(evidence_id not in evidence_ids for evidence_id in claim.evidence_ids)
        ]
        return self._make_check(
            task_id=task_id,
            guardrail_name="claims_require_valid_evidence_ids",
            passed=not missing_claims,
            severity=IssueSeverity.HIGH,
            target_type="claim",
            message=(
                "Every AnalysisClaim keeps non-empty evidence_ids and all refs resolve."
                if not missing_claims
                else "Claims with missing or invalid evidence refs: "
                + ", ".join(missing_claims)
            ),
            claim_ids=missing_claims,
        )

    def check_report_claim_refs(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        claims: list[AnalysisClaim] = artifacts["claims"]
        reports = artifacts["reports"]
        if not reports:
            return self._make_check(
                task_id=task_id,
                guardrail_name="report_claim_ids_preserved",
                passed=False,
                severity=IssueSeverity.HIGH,
                target_type="report",
                message="No CompetitiveReport artifacts found.",
            )
        report = reports[-1]
        claim_ids = {claim.id for claim in claims}
        missing = [
            claim_id
            for claim_id in report.claim_ids
            if claim_id not in claim_ids or f"[{claim_id}]" not in report.markdown
        ]
        return self._make_check(
            task_id=task_id,
            guardrail_name="report_claim_ids_preserved",
            passed=not missing and bool(report.claim_ids),
            severity=IssueSeverity.HIGH,
            target_type="report",
            target_id=report.id,
            message=(
                "CompetitiveReport.claim_ids all resolve and appear in markdown."
                if not missing and report.claim_ids
                else "Report is missing claim ids in markdown or claims artifact: "
                + ", ".join(missing)
            ),
            claim_ids=missing,
        )

    def check_context_id_preservation(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        bundles: list[ContextBundle] = artifacts["context_bundles"]
        source_ids = {source.id for source in artifacts["sources"]}
        evidence_ids = {item.id for item in artifacts["evidence"]}
        product_card_ids = {card.id for card in artifacts["product_cards"]}
        claim_ids = {claim.id for claim in artifacts["claims"]}
        citation_check_ids = {check.id for check in artifacts["citation_checks"]}
        task_record_ids = {task.id for task in artifacts["task_records"]}
        memory_item_ids = {item.id for item in artifacts["memory_items"]}

        errors: list[str] = []
        for bundle in bundles:
            self._find_invalid_refs(bundle.id, "source_ids", bundle.source_ids, source_ids, errors)
            self._find_invalid_refs(bundle.id, "evidence_ids", bundle.evidence_ids, evidence_ids, errors)
            self._find_invalid_refs(bundle.id, "product_card_ids", bundle.product_card_ids, product_card_ids, errors)
            self._find_invalid_refs(bundle.id, "claim_ids", bundle.claim_ids, claim_ids, errors)
            self._find_invalid_refs(bundle.id, "citation_check_ids", bundle.citation_check_ids, citation_check_ids, errors)
            self._find_invalid_refs(bundle.id, "task_record_ids", bundle.task_record_ids, task_record_ids, errors)
            self._find_invalid_refs(bundle.id, "memory_item_ids", bundle.memory_item_ids, memory_item_ids, errors)
            if not bundle.system_context:
                errors.append(f"{bundle.id}.system_context is empty")
            if not bundle.task_context:
                errors.append(f"{bundle.id}.task_context is empty")
            if not bundle.working_context:
                errors.append(f"{bundle.id}.working_context is empty")

        return self._make_check(
            task_id=task_id,
            guardrail_name="context_preserves_artifact_ids",
            passed=not errors and len(bundles) >= 6,
            severity=IssueSeverity.HIGH,
            target_type="context_bundle",
            message=(
                "All ContextBundle ids resolve to existing artifacts."
                if not errors and len(bundles) >= 6
                else "ContextBundle id preservation errors: " + "; ".join(errors)
            ),
        )

    def check_writer_context_claim_policy(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        bundles: list[ContextBundle] = artifacts["context_bundles"]
        reports = artifacts["reports"]
        writer_bundle = next(
            (bundle for bundle in bundles if bundle.agent_role == "writer"),
            None,
        )
        if writer_bundle is None:
            return self._make_check(
                task_id=task_id,
                guardrail_name="writer_context_requires_claim_ids",
                passed=False,
                severity=IssueSeverity.HIGH,
                target_type="context_bundle",
                message="No writer ContextBundle found.",
            )
        report_claim_ids = set(reports[-1].claim_ids) if reports else set()
        missing = sorted(report_claim_ids - set(writer_bundle.claim_ids))
        passed = bool(writer_bundle.claim_ids) and not missing
        return self._make_check(
            task_id=task_id,
            guardrail_name="writer_context_requires_claim_ids",
            passed=passed,
            severity=IssueSeverity.HIGH,
            target_type="context_bundle",
            target_id=writer_bundle.id,
            message=(
                "Writer context includes all report claim_ids."
                if passed
                else "Writer context is missing report claim_ids: " + ", ".join(missing)
            ),
            claim_ids=missing,
        )

    def check_memory_policy(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        memory_items: list[MemoryItem] = artifacts["memory_items"]
        source_ids = {source.id for source in artifacts["sources"]}
        evidence_ids = {item.id for item in artifacts["evidence"]}
        claim_ids = {claim.id for claim in artifacts["claims"]}
        citation_check_ids = {check.id for check in artifacts["citation_checks"]}
        errors: list[str] = []
        for item in memory_items:
            self._find_invalid_refs(item.id, "source_ids", item.source_ids, source_ids, errors)
            self._find_invalid_refs(item.id, "evidence_ids", item.evidence_ids, evidence_ids, errors)
            self._find_invalid_refs(item.id, "claim_ids", item.claim_ids, claim_ids, errors)
            self._find_invalid_refs(item.id, "citation_check_ids", item.citation_check_ids, citation_check_ids, errors)
            if item.metadata.get("volatile") and not item.last_verified_at:
                errors.append(f"{item.id} volatile memory is missing last_verified_at")
            if item.metadata.get("volatile") and not (item.evidence_ids or item.claim_ids):
                errors.append(f"{item.id} volatile memory is missing evidence_ids or claim_ids")

        return self._make_check(
            task_id=task_id,
            guardrail_name="memory_items_keep_traceable_refs",
            passed=not errors and bool(memory_items),
            severity=IssueSeverity.HIGH,
            target_type="memory_item",
            message=(
                "Memory items follow traceability and volatile fact policy."
                if not errors and memory_items
                else "Memory policy errors: " + "; ".join(errors)
            ),
        )

    def check_weak_citation_visibility(
        self,
        task_id: str,
        artifacts: dict[str, list],
    ) -> GuardrailCheck:
        weak_checks = [
            check for check in artifacts["citation_checks"] if check.status == "weak"
        ]
        if not weak_checks:
            return self._make_check(
                task_id=task_id,
                guardrail_name="weak_citations_are_visible",
                passed=True,
                severity=IssueSeverity.LOW,
                target_type="citation_check",
                message="No weak citations found.",
            )

        reviews: list[ReviewFeedback] = artifacts["review_feedback"]
        gates: list[QualityGateDecision] = artifacts["quality_gates"]
        feedback_tasks: list[TaskRecord] = artifacts["feedback_tasks"]
        issue_targets = {
            issue.target_id
            for review in reviews
            for issue in review.issues
        }
        feedback_claim_ids = {
            str(task.metadata.get("claim_id", ""))
            for task in feedback_tasks
        }
        visible = [
            check.claim_id
            for check in weak_checks
            if check.claim_id in issue_targets
            and check.claim_id in feedback_claim_ids
        ]
        passed = len(visible) == len(weak_checks) and bool(gates)
        return self._make_check(
            task_id=task_id,
            guardrail_name="weak_citations_are_visible",
            passed=passed,
            severity=IssueSeverity.MEDIUM,
            status=GuardrailStatus.WARNING if passed else GuardrailStatus.FAILED,
            target_type="citation_check",
            message=(
                "Weak citations are visible in ReviewFeedback and feedback tasks."
                if passed
                else "Weak citations are not fully represented in review/gate feedback."
            ),
            claim_ids=[check.claim_id for check in weak_checks],
            citation_check_ids=[check.id for check in weak_checks],
        )

    def _load_artifacts(self, task_id: str) -> dict[str, list]:
        from app.schemas import CompetitiveReport

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
            "reports": [
                CompetitiveReport(**item)
                for item in self.store.load_many(task_id, "reports")
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
            "working_memory": [
                WorkingMemory(**item)
                for item in self.store.load_many(task_id, WORKING_MEMORY_ARTIFACT)
            ],
            "memory_items": [
                MemoryItem(**item)
                for item in self.store.load_many(task_id, MEMORY_ITEMS_ARTIFACT)
            ],
            "context_bundles": [
                ContextBundle(**item)
                for item in self.store.load_many(task_id, CONTEXT_BUNDLES_ARTIFACT)
            ],
        }

    @staticmethod
    def _find_invalid_refs(
        owner_id: str,
        field_name: str,
        refs: list[str],
        valid_ids: set[str],
        errors: list[str],
    ) -> None:
        missing = [ref for ref in refs if ref not in valid_ids]
        if missing:
            errors.append(
                f"{owner_id}.{field_name} references missing ids: "
                + ", ".join(missing)
            )

    @staticmethod
    def _make_check(
        *,
        task_id: str,
        guardrail_name: str,
        passed: bool,
        severity: IssueSeverity,
        target_type: str,
        message: str,
        target_id: str = "",
        status: GuardrailStatus | None = None,
        source_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        claim_ids: list[str] | None = None,
        citation_check_ids: list[str] | None = None,
        task_record_ids: list[str] | None = None,
    ) -> GuardrailCheck:
        if status is None:
            status = GuardrailStatus.PASSED if passed else GuardrailStatus.FAILED
        effective_severity = IssueSeverity.LOW if status == GuardrailStatus.PASSED else severity
        return GuardrailCheck(
            id=f"guard_{guardrail_name}",
            task_id=task_id,
            guardrail_name=guardrail_name,
            status=status,
            passed=passed,
            severity=effective_severity,
            target_type=target_type,
            target_id=target_id,
            message=message,
            source_ids=source_ids or [],
            evidence_ids=evidence_ids or [],
            claim_ids=claim_ids or [],
            citation_check_ids=citation_check_ids or [],
            task_record_ids=task_record_ids or [],
        )


def run_guardrail_checks(
    *,
    task_id: str,
    store: ArtifactStore,
) -> list[GuardrailCheck]:
    return GuardrailChecker(store=store).run_all(task_id)
