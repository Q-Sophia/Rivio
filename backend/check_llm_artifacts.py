from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.harness.artifacts import ArtifactStore
from app.llm import LLM_CALLS_ARTIFACT, LLM_OUTPUTS_ARTIFACT
from app.llm.language import ZH_CN, validate_structured_output_language
from app.llm.structured import (
    parse_analysis_claims,
    parse_competitive_report,
    parse_product_cards,
    validate_non_empty_evidence_ids,
    validate_product_card_refs,
    validate_report_claim_ids,
)
from app.schemas import AgentRun, LLMCall, LLMOutput
from build_product_cards_demo import DEFAULT_TASK_ID


T = TypeVar("T")
EXPECTED_LLM_ROLES = {"extractor", "analyst", "writer"}
EXPECTED_OUTPUT_SCHEMAS = {"ProductCard[]", "AnalysisClaim[]", "CompetitiveReport"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate mock LLM calls, outputs, and parsed schema objects."
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


def validate_llm_artifacts(store: ArtifactStore, task_id: str) -> list[str]:
    errors: list[str] = []
    calls = load_typed_artifact(store, task_id, LLM_CALLS_ARTIFACT, LLMCall, errors)
    outputs = load_typed_artifact(store, task_id, LLM_OUTPUTS_ARTIFACT, LLMOutput, errors)
    agent_runs = load_typed_artifact(store, task_id, "agent_runs", AgentRun, errors)

    validate_calls(calls, agent_runs, errors)
    validate_outputs(outputs, calls, errors)
    return errors


def validate_calls(
    calls: list[LLMCall],
    agent_runs: list[AgentRun],
    errors: list[str],
) -> None:
    roles = {status_value(call.agent_role) for call in calls}
    if roles != EXPECTED_LLM_ROLES:
        errors.append(
            f"LLM roles mismatch: expected={sorted(EXPECTED_LLM_ROLES)}; actual={sorted(roles)}"
        )
    if len(calls) != 3:
        errors.append(f"Expected 3 LLMCall items, found {len(calls)}")
    run_ids = {run.id for run in agent_runs}
    for call in calls:
        if status_value(call.status) != "completed":
            errors.append(f"LLMCall {call.id} status={call.status}, expected completed")
        if status_value(call.provider) not in {"mock", "openai", "compatible"}:
            errors.append(f"LLMCall {call.id} has unsupported provider={call.provider}")
        if call.output_schema not in EXPECTED_OUTPUT_SCHEMAS:
            errors.append(f"LLMCall {call.id} has unexpected output_schema={call.output_schema}")
        if call.metadata.get("output_language") != ZH_CN:
            errors.append(
                f"LLMCall {call.id} output_language={call.metadata.get('output_language')}, expected {ZH_CN}"
            )
        if "简体中文" not in call.prompt_summary:
            errors.append(f"LLMCall {call.id} prompt_summary missing 简体中文 constraint")
        if call.agent_run_id not in run_ids:
            errors.append(
                f"LLMCall {call.id} references missing agent_run_id={call.agent_run_id}"
            )
        if not call.context_bundle_id:
            errors.append(f"LLMCall {call.id} missing context_bundle_id")
        if not call.input_artifact_refs:
            errors.append(f"LLMCall {call.id} missing input_artifact_refs")
        if call.used_fallback and not call.fallback_reason:
            errors.append(f"LLMCall {call.id} used fallback without fallback_reason")


def validate_outputs(
    outputs: list[LLMOutput],
    calls: list[LLMCall],
    errors: list[str],
) -> None:
    if len(outputs) != 3:
        errors.append(f"Expected 3 LLMOutput items, found {len(outputs)}")
    call_ids = {call.id for call in calls}
    output_call_ids = {output.llm_call_id for output in outputs}
    if output_call_ids != call_ids:
        errors.append(
            f"LLMOutput llm_call_id mismatch: calls={sorted(call_ids)} outputs={sorted(output_call_ids)}"
        )
    for output in outputs:
        if output.llm_call_id not in call_ids:
            errors.append(
                f"LLMOutput {output.id} references missing llm_call_id={output.llm_call_id}"
            )
        if output.validation_status != "passed":
            errors.append(
                f"LLMOutput {output.id} validation_status={output.validation_status}"
            )
        language_errors = validate_structured_output_language(
            output.output_schema,
            output.raw_output,
            output_language=ZH_CN,
        )
        errors.extend(f"LLMOutput {output.id}: {error}" for error in language_errors)
        try:
            if output.output_schema == "ProductCard[]":
                cards = parse_product_cards(output.raw_output)
                validate_product_card_refs(cards)
            elif output.output_schema == "AnalysisClaim[]":
                claims = parse_analysis_claims(output.raw_output)
                validate_non_empty_evidence_ids(claims)
            elif output.output_schema == "CompetitiveReport":
                report = parse_competitive_report(output.raw_output)
                validate_report_claim_ids(report)
            else:
                errors.append(
                    f"LLMOutput {output.id} unexpected output_schema={output.output_schema}"
                )
        except Exception as exc:
            errors.append(
                f"LLMOutput {output.id} failed structured parse: {type(exc).__name__}: {exc}"
            )


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
    errors = validate_llm_artifacts(store, args.task_id)
    print_result(errors)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
