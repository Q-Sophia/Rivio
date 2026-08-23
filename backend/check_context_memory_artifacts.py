from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.context.builder import CONTEXT_BUNDLES_ARTIFACT
from app.context.guardrails import GUARDRAIL_CHECKS_ARTIFACT
from app.context.memory import MEMORY_ITEMS_ARTIFACT, WORKING_MEMORY_ARTIFACT
from app.harness.artifacts import ArtifactStore
from app.schemas import (
    AnalysisClaim,
    CitationCheck,
    ContextBundle,
    GuardrailCheck,
    MemoryItem,
    ProductCard,
    QualityGateDecision,
    SourceDocument,
    SourceEvidence,
    TaskRecord,
    WorkingMemory,
)
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")
EXPECTED_CONTEXT_ROLES = {
    "collector",
    "extractor",
    "analyst",
    "citation",
    "writer",
    "reviewer",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate context, memory, and guardrail artifacts."
    )
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-root", type=Path, default=None)
    return parser.parse_args()


def artifact_path(store: ArtifactStore, task_id: str, artifact_type: str) -> Path:
    return store.root_dir / task_id / f"{artifact_type}.json"


def load_typed_artifact(
    store: ArtifactStore,
    task_id: str,
    artifact_type: str,
    model: type[T],
    errors: list[str],
) -> list[T]:
    path = artifact_path(store, task_id, artifact_type)
    if not path.exists():
        errors.append(f"Missing artifact file: {path}")
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{artifact_type}.json is invalid JSON: {exc}")
        return []
    if not isinstance(raw, list):
        errors.append(f"{artifact_type}.json must contain a JSON list")
        return []
    if not raw:
        errors.append(f"{artifact_type}.json must not be empty")
        return []

    items: list[T] = []
    for index, item in enumerate(raw):
        try:
            items.append(model(**item))
        except ValidationError as exc:
            errors.append(
                f"{artifact_type}.json[{index}] failed schema validation: {exc}"
            )
    return items


def status_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def validate_context_memory_artifacts(store: ArtifactStore, task_id: str) -> list[str]:
    errors: list[str] = []
    sources = load_typed_artifact(store, task_id, "sources", SourceDocument, errors)
    evidence = load_typed_artifact(store, task_id, "evidence", SourceEvidence, errors)
    product_cards = load_typed_artifact(
        store,
        task_id,
        "product_cards",
        ProductCard,
        errors,
    )
    claims = load_typed_artifact(store, task_id, "claims", AnalysisClaim, errors)
    citation_checks = load_typed_artifact(
        store,
        task_id,
        "citation_checks",
        CitationCheck,
        errors,
    )
    task_records = load_typed_artifact(
        store,
        task_id,
        "task_records",
        TaskRecord,
        errors,
    )
    quality_gates = load_typed_artifact(
        store,
        task_id,
        "quality_gates",
        QualityGateDecision,
        errors,
    )
    working_memory = load_typed_artifact(
        store,
        task_id,
        WORKING_MEMORY_ARTIFACT,
        WorkingMemory,
        errors,
    )
    memory_items = load_typed_artifact(
        store,
        task_id,
        MEMORY_ITEMS_ARTIFACT,
        MemoryItem,
        errors,
    )
    context_bundles = load_typed_artifact(
        store,
        task_id,
        CONTEXT_BUNDLES_ARTIFACT,
        ContextBundle,
        errors,
    )
    guardrail_checks = load_typed_artifact(
        store,
        task_id,
        GUARDRAIL_CHECKS_ARTIFACT,
        GuardrailCheck,
        errors,
    )

    validate_working_memory(
        working_memory,
        sources,
        evidence,
        product_cards,
        claims,
        citation_checks,
        task_records,
        quality_gates,
        errors,
    )
    validate_memory_items(memory_items, sources, evidence, claims, citation_checks, errors)
    validate_context_bundles(
        context_bundles,
        sources,
        evidence,
        product_cards,
        claims,
        citation_checks,
        task_records,
        memory_items,
        errors,
    )
    validate_guardrail_checks(guardrail_checks, errors)
    return errors


def validate_working_memory(
    memories: list[WorkingMemory],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    product_cards: list[ProductCard],
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    task_records: list[TaskRecord],
    quality_gates: list[QualityGateDecision],
    errors: list[str],
) -> None:
    if not memories:
        return
    memory = memories[-1]
    refs = {
        "selected_source_ids": ({source.id for source in sources}, memory.selected_source_ids),
        "selected_evidence_ids": ({item.id for item in evidence}, memory.selected_evidence_ids),
        "selected_product_card_ids": ({card.id for card in product_cards}, memory.selected_product_card_ids),
        "selected_claim_ids": ({claim.id for claim in claims}, memory.selected_claim_ids),
        "quality_gate_ids": ({gate.id for gate in quality_gates}, memory.quality_gate_ids),
        "feedback_task_ids": ({task.id for task in task_records}, memory.feedback_task_ids),
    }
    for field_name, (valid_ids, item_ids) in refs.items():
        find_missing_refs(f"working_memory.{field_name}", item_ids, valid_ids, errors)

    weak_claim_ids = {
        check.claim_id for check in citation_checks if status_value(check.status) == "weak"
    }
    if set(memory.weak_claim_ids) != weak_claim_ids:
        errors.append(
            "WorkingMemory.weak_claim_ids does not match weak CitationCheck claim ids"
        )


def validate_memory_items(
    memory_items: list[MemoryItem],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    errors: list[str],
) -> None:
    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    claim_ids = {claim.id for claim in claims}
    citation_check_ids = {check.id for check in citation_checks}
    long_term_count = sum(1 for item in memory_items if item.scope == "long_term")
    run_count = sum(1 for item in memory_items if item.scope == "run")
    if long_term_count < 3:
        errors.append("Expected at least 3 long_term MemoryItem entries")
    if run_count < 1:
        errors.append("Expected at least 1 run MemoryItem entry")

    for item in memory_items:
        find_missing_refs(f"{item.id}.source_ids", item.source_ids, source_ids, errors)
        find_missing_refs(f"{item.id}.evidence_ids", item.evidence_ids, evidence_ids, errors)
        find_missing_refs(f"{item.id}.claim_ids", item.claim_ids, claim_ids, errors)
        find_missing_refs(
            f"{item.id}.citation_check_ids",
            item.citation_check_ids,
            citation_check_ids,
            errors,
        )
        if item.metadata.get("volatile") and not item.last_verified_at:
            errors.append(f"Volatile MemoryItem {item.id} is missing last_verified_at")
        if item.metadata.get("volatile") and not (item.evidence_ids or item.claim_ids):
            errors.append(
                f"Volatile MemoryItem {item.id} must keep evidence_ids or claim_ids"
            )


def validate_context_bundles(
    bundles: list[ContextBundle],
    sources: list[SourceDocument],
    evidence: list[SourceEvidence],
    product_cards: list[ProductCard],
    claims: list[AnalysisClaim],
    citation_checks: list[CitationCheck],
    task_records: list[TaskRecord],
    memory_items: list[MemoryItem],
    errors: list[str],
) -> None:
    roles = {status_value(bundle.agent_role) for bundle in bundles}
    if roles != EXPECTED_CONTEXT_ROLES:
        errors.append(
            f"ContextBundle roles mismatch: expected={sorted(EXPECTED_CONTEXT_ROLES)}; actual={sorted(roles)}"
        )

    source_ids = {source.id for source in sources}
    evidence_ids = {item.id for item in evidence}
    product_card_ids = {card.id for card in product_cards}
    claim_ids = {claim.id for claim in claims}
    citation_check_ids = {check.id for check in citation_checks}
    task_record_ids = {task.id for task in task_records}
    memory_item_ids = {item.id for item in memory_items}

    for bundle in bundles:
        find_missing_refs(f"{bundle.id}.source_ids", bundle.source_ids, source_ids, errors)
        find_missing_refs(f"{bundle.id}.evidence_ids", bundle.evidence_ids, evidence_ids, errors)
        find_missing_refs(f"{bundle.id}.product_card_ids", bundle.product_card_ids, product_card_ids, errors)
        find_missing_refs(f"{bundle.id}.claim_ids", bundle.claim_ids, claim_ids, errors)
        find_missing_refs(f"{bundle.id}.citation_check_ids", bundle.citation_check_ids, citation_check_ids, errors)
        find_missing_refs(f"{bundle.id}.task_record_ids", bundle.task_record_ids, task_record_ids, errors)
        find_missing_refs(f"{bundle.id}.memory_item_ids", bundle.memory_item_ids, memory_item_ids, errors)
        if not bundle.system_context:
            errors.append(f"ContextBundle {bundle.id} has empty system_context")
        if not bundle.task_context:
            errors.append(f"ContextBundle {bundle.id} has empty task_context")
        if not bundle.working_context:
            errors.append(f"ContextBundle {bundle.id} has empty working_context")
        if not bundle.artifact_refs:
            errors.append(f"ContextBundle {bundle.id} has empty artifact_refs")
        if bundle.estimated_tokens > bundle.token_budget:
            errors.append(
                f"ContextBundle {bundle.id} exceeds token budget: "
                f"{bundle.estimated_tokens}>{bundle.token_budget}"
            )

    writer = next((bundle for bundle in bundles if bundle.agent_role == "writer"), None)
    if writer is not None and not writer.claim_ids:
        errors.append("Writer ContextBundle must include claim_ids")
    analyst = next((bundle for bundle in bundles if bundle.agent_role == "analyst"), None)
    if analyst is not None and not analyst.evidence_ids:
        errors.append("Analyst ContextBundle must include evidence_ids")


def validate_guardrail_checks(
    guardrail_checks: list[GuardrailCheck],
    errors: list[str],
) -> None:
    if len(guardrail_checks) < 6:
        errors.append("Expected at least 6 GuardrailCheck entries")
    failed = [check.guardrail_name for check in guardrail_checks if check.status == "failed"]
    if failed:
        errors.append("GuardrailCheck failures found: " + ", ".join(failed))


def find_missing_refs(
    owner: str,
    refs: list[str],
    valid_ids: set[str],
    errors: list[str],
) -> None:
    missing = [ref for ref in refs if ref not in valid_ids]
    if missing:
        errors.append(f"{owner} references missing ids: {', '.join(missing)}")


def print_result(errors: list[str]) -> None:
    if errors:
        print("FAIL")
        print(f"errors={len(errors)}")
        for index, error in enumerate(errors, start=1):
            print(f"{index}. {error}")
        return

    print("PASS")
    print("errors=0")


def main() -> None:
    args = parse_args()
    store = ArtifactStore(root_dir=args.artifact_root)
    errors = validate_context_memory_artifacts(store, args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
