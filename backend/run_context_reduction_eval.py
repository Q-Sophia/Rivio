from __future__ import annotations

import argparse
import importlib
import json
import statistics
import sys
import types
from pathlib import Path
from typing import Any


TASK_IDS = (
    "task_user_46c24a3ccd54",
    "task_user_4143cb765d5e",
    "task_user_8666280bf195",
)

BASELINE_ARTIFACT_TYPES = (
    "analysis_tasks",
    "research_plans",
    "research_kiqs",
    "research_information_needs",
    "research_tasks",
    "sources",
    "source_chunks",
    "evidence",
    "product_cards",
    "research_agent_observations",
    "research_worker_results",
    "research_batch_results",
)

ANALYST_STAGE_VISIBLE_TYPES = frozenset(
    {
        *BASELINE_ARTIFACT_TYPES,
        "research_agent_runs",
        "task_records",
        "dag_nodes",
    }
)

TOKEN_METRIC = "estimated_tokens"
TOKEN_ESTIMATOR = "canonical_json_character_count_divided_by_4"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _estimate_tokens(value: Any) -> int:
    return max(len(_canonical_json(value)) // 4, 1)


def _reduction_ratio(baseline_tokens: int, max_stage_tokens: int) -> float:
    if baseline_tokens <= 0:
        raise ValueError("baseline_tokens must be positive")
    return 1.0 - (max_stage_tokens / baseline_tokens)


class StageReadOnlyStore:
    """Expose a historical task as it existed immediately before Analyst."""

    def __init__(self, store: Any):
        self._store = store

    def load_many(self, task_id: str, artifact_type: str) -> list[dict[str, Any]]:
        if artifact_type not in ANALYST_STAGE_VISIBLE_TYPES:
            return []
        return self._store.load_many(task_id, artifact_type)

    def save_many(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("context reduction evaluation store is read-only")


def _verified_evidence_ids(store: Any, task_id: str) -> set[str]:
    ids: set[str] = set()
    for artifact_type in ("research_agent_runs", "research_worker_results"):
        for item in store.load_many(task_id, artifact_type):
            ids.update(
                str(value)
                for value in item.get("verified_evidence_ids", [])
                if value
            )
    return ids


def _baseline_payload(
    store: Any,
    task_id: str,
    verified_evidence_ids: set[str],
) -> tuple[dict[str, Any], dict[str, int]]:
    artifacts: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for artifact_type in BASELINE_ARTIFACT_TYPES:
        items = store.load_many(task_id, artifact_type)
        if artifact_type == "evidence":
            items = [
                item
                for item in items
                if str(item.get("id") or "") in verified_evidence_ids
            ]
        artifacts[artifact_type] = items
        counts[artifact_type] = len(items)
    return (
        {
            "task_id": task_id,
            "agent_role": "analyst",
            "artifacts": artifacts,
        },
        counts,
    )


def _build_analyst_context_bundle(store: Any, task_id: str) -> Any:
    from app.context.builder import ContextBuilder
    from app.context.memory import (
        build_memory_items_from_artifacts,
        build_working_memory_from_artifacts,
    )
    from app.schemas import AgentRole

    stage_store = StageReadOnlyStore(store)
    working_memory = build_working_memory_from_artifacts(
        task_id=task_id,
        store=stage_store,
    )
    memory_items = build_memory_items_from_artifacts(
        task_id=task_id,
        store=stage_store,
        working_memory=working_memory,
    )
    return ContextBuilder(store=stage_store).build_for_role(
        task_id=task_id,
        agent_role=AgentRole.ANALYST,
        working_memory=working_memory,
        memory_items=memory_items,
    )


def _latest_stage_call(
    store: Any,
    task_id: str,
    output_schema: str,
) -> dict[str, Any]:
    calls = [
        item
        for item in store.load_many(task_id, "llm_calls")
        if item.get("output_schema") == output_schema
    ]
    if not calls:
        raise ValueError(
            f"{task_id} has no historical {output_schema} request metadata"
        )
    return calls[-1]


def _provider_from_historical_call(call: dict[str, Any]) -> Any:
    """Use the production provider factory without invoking generate/network."""

    from app.llm.config import LLMConfig
    from app.llm.provider import build_provider
    from app.schemas import LLMMode, LLMProvider

    metadata = call.get("metadata") or {}
    config = LLMConfig(
        provider=LLMProvider(str(call.get("provider") or "compatible")),
        model=str(call.get("model") or "offline-payload-eval"),
        mode=LLMMode.LLM,
        base_url="https://offline.invalid/v1",
        api_key_env="CONTEXT_REDUCTION_EVAL_UNUSED_API_KEY",
        max_tokens=int(metadata.get("max_tokens") or 4000),
        temperature=float(metadata.get("temperature") or 0.2),
        output_language=str(metadata.get("output_language") or "zh-CN"),
        enable_real_calls=False,
        api_style=str(metadata.get("api_surface") or "chat_completions"),
        structured_output_mode=str(
            metadata.get("structured_output_mode") or "json_object"
        ),
        thinking_mode=str(metadata.get("thinking_mode") or "provider_default"),
        trust_env_proxy=False,
    )
    config.validate()
    return build_provider(config=config)


def _load_professional_analyst_class() -> Any:
    """Load the production submodule without executing its cyclic package init."""

    module_name = "app.agents.llm_snapshot"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded.LLMProfessionalAnalystAgent

    package_name = "app.agents"
    if package_name not in sys.modules:
        agents_dir = Path(__file__).resolve().parent / "app" / "agents"
        package = types.ModuleType(package_name)
        package.__path__ = [str(agents_dir)]
        package.__package__ = package_name
        sys.modules[package_name] = package
    module = importlib.import_module(module_name)
    return module.LLMProfessionalAnalystAgent


def _bind_current_framework(task: Any, research_tasks: list[Any]) -> tuple[Any, list[Any], str]:
    """Bind with production logic, normalizing only a stale pinned hash in memory."""

    from app.analysis_assessment import resolve_framework_assessment_binding
    from app.frameworks import get_framework_registry

    try:
        return (
            resolve_framework_assessment_binding(
                task=task,
                research_tasks=research_tasks,
            ),
            research_tasks,
            "historical_research_task_binding",
        )
    except ValueError as exc:
        if "Framework hash" not in str(exc):
            raise

    versioned = [item for item in research_tasks if item.framework_id]
    references = {
        (item.framework_id, item.framework_version) for item in versioned
    }
    if len(references) != 1:
        raise ValueError("stale Framework replay requires one id/version pair")
    framework_id, framework_version = next(iter(references))
    current_framework = get_framework_registry().load_framework(
        framework_id,
        framework_version,
    )
    normalized_tasks = [
        item.model_copy(
            update={"framework_content_hash": current_framework.content_hash}
        )
        if item.framework_id
        else item
        for item in research_tasks
    ]
    return (
        resolve_framework_assessment_binding(
            task=task,
            research_tasks=normalized_tasks,
        ),
        normalized_tasks,
        "current_registry_hash_normalized_in_memory",
    )


def _build_final_stage_payloads(
    store: Any,
    task_id: str,
    verified_evidence_ids: set[str],
) -> list[dict[str, Any]]:
    """Reconstruct the three pre-provider payloads through production builders."""

    from app.prompts import PromptRegistry
    from app.schemas import (
        AgentRole,
        AnalysisTask,
        CompetitorProfile,
        ResearchTask,
        SourceEvidence,
    )

    task_items = store.load_many(task_id, "analysis_tasks")
    if len(task_items) != 1:
        raise ValueError(f"{task_id} requires exactly one analysis_tasks item")
    task = AnalysisTask(**task_items[0])
    evidence = [
        SourceEvidence(**item)
        for item in store.load_many(task_id, "evidence")
        if str(item.get("id") or "") in verified_evidence_ids
    ]
    research_tasks = [
        ResearchTask(**item)
        for item in store.load_many(task_id, "research_tasks")
    ]
    competitor_profiles = [
        CompetitorProfile(**item)
        for item in store.load_many(task_id, "competitor_profiles")
    ]
    if not evidence:
        raise ValueError(f"{task_id} has no verified evidence")
    if not competitor_profiles:
        raise ValueError(f"{task_id} has no saved Brief/Profile stage output")

    analyst = _load_professional_analyst_class()
    deduped_evidence = analyst._dedupe_evidence(evidence)
    binding, assessment_research_tasks, framework_replay_mode = (
        _bind_current_framework(task, research_tasks)
    )
    prompt_registry = PromptRegistry()
    analyst_prompt = prompt_registry.load(
        "competitive_analyst",
        allow_candidate=True,
    )
    assessment_prompt = prompt_registry.load(
        "competitive_analysis_assessment",
        allow_candidate=True,
    )
    context_bundle = _build_analyst_context_bundle(store, task_id)

    brief_call = _latest_stage_call(
        store,
        task_id,
        "AnalystBriefProfilesStage",
    )
    claims_call = _latest_stage_call(
        store,
        task_id,
        "AnalystClaimsStage",
    )
    for call in (brief_call, claims_call):
        if call.get("prompt_id") != analyst_prompt.prompt_id:
            raise ValueError(f"{task_id} Analyst prompt id does not match registry")
        if call.get("prompt_version") != analyst_prompt.version:
            raise ValueError(
                f"{task_id} Analyst prompt version does not match registry"
            )
        if call.get("prompt_hash") != analyst_prompt.content_hash:
            raise ValueError(f"{task_id} Analyst prompt hash does not match registry")

    brief_evidence = analyst._select_stage_evidence(
        deduped_evidence,
        max_per_competitor=8,
        max_total=32,
    )
    assessment_evidence = analyst._select_stage_evidence(
        deduped_evidence,
        max_per_competitor=20,
        max_total=80,
    )
    claims_evidence = analyst._select_stage_evidence(
        deduped_evidence,
        max_per_competitor=16,
        max_total=60,
    )
    stage_inputs = (
        {
            "stage_name": "brief",
            "output_schema": "AnalystBriefProfilesStage",
            "prompt_summary": str(brief_call.get("prompt_summary") or ""),
            "artifacts": analyst._stage_artifacts(
                task=task,
                evidence=brief_evidence,
            ),
            "call": brief_call,
            "evidence_ids": {item.id for item in brief_evidence},
        },
        {
            "stage_name": "assessment",
            "output_schema": "AnalystAssessmentStage",
            "prompt_summary": assessment_prompt.build_runtime_prompt(task),
            "artifacts": analyst._assessment_stage_artifacts(
                task=task,
                evidence=assessment_evidence,
                research_tasks=assessment_research_tasks,
                framework=binding.framework,
                scope=binding.scope,
            ),
            "call": brief_call,
            "evidence_ids": {item.id for item in assessment_evidence},
        },
        {
            "stage_name": "claims",
            "output_schema": "AnalystClaimsStage",
            "prompt_summary": str(claims_call.get("prompt_summary") or ""),
            "artifacts": analyst._stage_artifacts(
                task=task,
                evidence=claims_evidence,
                competitor_profiles=competitor_profiles,
            ),
            "call": claims_call,
            "evidence_ids": {item.id for item in claims_evidence},
        },
    )

    results: list[dict[str, Any]] = []
    for stage in stage_inputs:
        if not stage["prompt_summary"]:
            raise ValueError(
                f"{task_id} {stage['stage_name']} has no production prompt text"
            )
        provider = _provider_from_historical_call(stage["call"])
        payload = provider.build_request_payload(
            task_id=task_id,
            agent_role=AgentRole.ANALYST,
            output_schema=stage["output_schema"],
            prompt_summary=stage["prompt_summary"],
            system_context=context_bundle.system_context,
            artifacts=stage["artifacts"],
        )
        results.append(
            {
                "stage_name": stage["stage_name"],
                "output_schema": stage["output_schema"],
                "final_request_tokens": _estimate_tokens(payload),
                "retained_evidence_refs": sorted(stage["evidence_ids"]),
                "retained_evidence_ref_count": len(stage["evidence_ids"]),
                "request_builder": (
                    f"{type(provider).__module__}."
                    f"{type(provider).__name__}.build_request_payload"
                ),
                "framework_replay_mode": (
                    framework_replay_mode
                    if stage["stage_name"] == "assessment"
                    else None
                ),
            }
        )
    return results


def _evaluate_task(store: Any, task_id: str) -> dict[str, Any]:
    task_dir = store.root_dir / task_id
    if not task_dir.is_dir():
        raise FileNotFoundError(f"historical task not found: {task_dir}")

    verified_ids = _verified_evidence_ids(store, task_id)
    baseline, baseline_counts = _baseline_payload(store, task_id, verified_ids)
    stages = _build_final_stage_payloads(store, task_id, verified_ids)
    stage_tokens = {
        item["stage_name"]: item["final_request_tokens"] for item in stages
    }
    union_refs = set().union(
        *(set(item["retained_evidence_refs"]) for item in stages)
    )
    baseline_evidence_ids = {
        str(item.get("id") or "")
        for item in baseline["artifacts"]["evidence"]
        if item.get("id")
    }
    union_refs &= baseline_evidence_ids

    baseline_tokens = _estimate_tokens(baseline)
    max_stage_tokens = max(stage_tokens.values())
    total_stage_tokens = sum(stage_tokens.values())
    baseline_evidence_count = len(baseline_evidence_ids)
    union_retention = (
        len(union_refs) / baseline_evidence_count
        if baseline_evidence_count
        else None
    )
    return {
        "task_id": task_id,
        "baseline_tokens": baseline_tokens,
        "brief_stage_tokens": stage_tokens["brief"],
        "assessment_stage_tokens": stage_tokens["assessment"],
        "claims_stage_tokens": stage_tokens["claims"],
        "max_stage_tokens": max_stage_tokens,
        "avg_stage_tokens": statistics.mean(stage_tokens.values()),
        "total_stage_tokens": total_stage_tokens,
        "context_reduction_ratio": _reduction_ratio(
            baseline_tokens,
            max_stage_tokens,
        ),
        "baseline_verified_evidence_count": baseline_evidence_count,
        "current_retained_evidence_ref_count": len(union_refs),
        "union_retained_evidence_refs": sorted(union_refs),
        "union_retained_evidence_refs_across_stages": sorted(union_refs),
        "evidence_ref_union_retention_rate": union_retention,
        "stages": stages,
        "baseline_artifact_counts": baseline_counts,
    }


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def _write_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "context_reduction_eval.json"
    report_path = output_dir / "CONTEXT_REDUCTION_EVAL.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = payload["summary"]
    lines = [
        "# Analyst Context Token Reduction Eval",
        "",
        "## Method",
        "",
        f"- Token metric: `{payload['token_metric']}`",
        f"- Estimator: `{payload['token_estimator']}`",
        "- Reduction compares the full upstream Baseline with the largest single Analyst stage request payload.",
        "- Current payloads are built by the production provider request builder; no Provider call is made.",
        "- Evidence completeness uses the union of evidence IDs carried by all three stage payloads.",
        "",
        "## Baseline Artifact Scope",
        "",
        *[f"- `{name}`" for name in payload["baseline_artifact_types"]],
        "",
        "## Results",
        "",
        "| Task | Baseline | Brief | Assessment | Claims | Max stage | Total staged | Reduction | Evidence union |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["tasks"]:
        lines.append(
            "| {task_id} | {baseline} | {brief} | {assessment} | {claims} | "
            "{max_stage} | {total} | {reduction} | {retention} |".format(
                task_id=item["task_id"],
                baseline=item["baseline_tokens"],
                brief=item["brief_stage_tokens"],
                assessment=item["assessment_stage_tokens"],
                claims=item["claims_stage_tokens"],
                max_stage=item["max_stage_tokens"],
                total=item["total_stage_tokens"],
                reduction=_format_percent(item["context_reduction_ratio"]),
                retention=_format_percent(
                    item["evidence_ref_union_retention_rate"]
                ),
            )
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Tasks: {summary['task_count']}",
            f"- Avg baseline tokens: {summary['avg_baseline_tokens']:.2f}",
            f"- Avg max stage tokens: {summary['avg_max_stage_tokens']:.2f}",
            f"- Avg context reduction: {_format_percent(summary['avg_context_reduction_ratio'])}",
            f"- Median context reduction: {_format_percent(summary['median_context_reduction_ratio'])}",
            f"- Avg total staged tokens: {summary['avg_total_staged_tokens']:.2f}",
            f"- Evidence ref union retention: {_format_percent(summary['evidence_ref_union_retention_rate'])}",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_evaluation() -> dict[str, Any]:
    from app.harness.artifacts import ArtifactStore

    backend_dir = Path(__file__).resolve().parent
    store = ArtifactStore(backend_dir / "app" / "data" / "runs")
    results = [_evaluate_task(store, task_id) for task_id in TASK_IDS]
    total_baseline_evidence = sum(
        item["baseline_verified_evidence_count"] for item in results
    )
    total_retained_evidence = sum(
        item["current_retained_evidence_ref_count"] for item in results
    )
    reductions = [item["context_reduction_ratio"] for item in results]
    summary = {
        "task_count": len(results),
        "avg_baseline_tokens": statistics.mean(
            item["baseline_tokens"] for item in results
        ),
        "avg_max_stage_tokens": statistics.mean(
            item["max_stage_tokens"] for item in results
        ),
        "avg_context_reduction_ratio": statistics.mean(reductions),
        "median_context_reduction_ratio": statistics.median(reductions),
        "avg_total_staged_tokens": statistics.mean(
            item["total_stage_tokens"] for item in results
        ),
        "baseline_verified_evidence_count": total_baseline_evidence,
        "union_retained_evidence_ref_count": total_retained_evidence,
        "evidence_ref_union_retention_rate": (
            total_retained_evidence / total_baseline_evidence
            if total_baseline_evidence
            else None
        ),
    }
    payload = {
        "eval_name": "analyst_context_token_reduction_final_payload",
        "token_metric": TOKEN_METRIC,
        "token_estimator": TOKEN_ESTIMATOR,
        "reduction_basis": "baseline_tokens_vs_max_stage_tokens",
        "baseline_artifact_types": list(BASELINE_ARTIFACT_TYPES),
        "analyst_stages": ["brief", "assessment", "claims"],
        "current_request_builder": (
            "app.llm.provider.build_provider(config).build_request_payload"
        ),
        "tasks": results,
        "summary": summary,
    }
    _write_outputs(
        backend_dir / "eval_outputs" / "context_reduction",
        payload,
    )
    return payload


def self_test() -> None:
    sample = {"z": ["中文", 1], "a": {"kept": True}}
    assert _canonical_json(sample) == _canonical_json(
        {"a": {"kept": True}, "z": ["中文", 1]}
    )
    baseline_tokens = _estimate_tokens({"context": "x" * 400})
    stage_tokens = [
        _estimate_tokens({"request": "x" * size})
        for size in (80, 100, 60)
    ]
    ratio = _reduction_ratio(baseline_tokens, max(stage_tokens))
    assert baseline_tokens > max(stage_tokens) > 0
    assert sum(stage_tokens) > max(stage_tokens)
    assert 0.0 < ratio < 1.0
    print("SELF_TEST_PASS")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline Analyst final-request context reduction evaluation."
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run a deterministic test without reading historical tasks.",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    payload = run_evaluation()
    summary = payload["summary"]
    print(f"Tasks: {summary['task_count']}")
    print(f"Avg baseline tokens: {summary['avg_baseline_tokens']:.2f}")
    print(f"Avg max stage tokens: {summary['avg_max_stage_tokens']:.2f}")
    print(
        "Avg context reduction: "
        f"{_format_percent(summary['avg_context_reduction_ratio'])}"
    )
    print(
        "Median context reduction: "
        f"{_format_percent(summary['median_context_reduction_ratio'])}"
    )
    print(
        "Avg total staged tokens: "
        f"{summary['avg_total_staged_tokens']:.2f}"
    )
    print(
        "Evidence ref union retention: "
        f"{_format_percent(summary['evidence_ref_union_retention_rate'])}"
    )


if __name__ == "__main__":
    main()
