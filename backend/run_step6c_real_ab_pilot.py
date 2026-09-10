from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient
from app.llm.config import load_llm_config
from app.llm.structured import (
    parse_competitive_analysis_portfolio_v2,
    reject_unaligned_portfolio_v2_claims,
    validate_portfolio_v2_refs,
)
from app.prompts import PromptRegistry
from app.schemas import (
    AgentRole,
    AnalysisTask,
    CompetitiveAnalysisPortfolioV2,
    LLMMode,
    LLMProvider,
    ProductCard,
    SourceDocument,
    SourceEvidence,
)
from check_snapshot import DEFAULT_SNAPSHOT_ID, build_snapshot_task
from harness.step6c_metrics import evaluate_portfolio_core


DEFAULT_SOURCE_TASK_ID = "snapshot_step6c_professional_mock"
DEFAULT_EXPERIMENT_ID = "step6c_deepseek_v4_pilot"
VARIANTS = (
    ("v1_baseline", "competitive_analyst_baseline"),
    ("v2_candidate", "competitive_analyst"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded real-provider Step6C prompt A/B pilot or one-variant "
            "validation. Variants use the same model, inputs, and V2 output schema."
        )
    )
    parser.add_argument("--source-task-id", default=DEFAULT_SOURCE_TASK_ID)
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    parser.add_argument("--source-artifact-root", type=Path, default=None)
    parser.add_argument("--artifact-root", type=Path, default=None)
    parser.add_argument(
        "--variant",
        choices=["all", *(item[0] for item in VARIANTS)],
        default="all",
        help="Run both variants or one bounded validation variant.",
    )
    parser.add_argument(
        "--recompute-only",
        action="store_true",
        help="Recompute the summary from saved artifacts without an API call.",
    )
    return parser.parse_args()


def _clone_inputs(
    *,
    source_store: ArtifactStore,
    source_task_id: str,
    target_task_id: str,
    snapshot_id: str,
) -> tuple[AnalysisTask, list[SourceDocument], list[SourceEvidence], list[ProductCard]]:
    task = build_snapshot_task(snapshot_id=snapshot_id, task_id=target_task_id)
    sources = [
        SourceDocument(**item).model_copy(update={"task_id": target_task_id})
        for item in source_store.load_many(source_task_id, "sources")
    ]
    evidence = [
        SourceEvidence(**item).model_copy(update={"task_id": target_task_id})
        for item in source_store.load_many(source_task_id, "evidence")
    ]
    product_cards = [
        ProductCard(**item).model_copy(update={"task_id": target_task_id})
        for item in source_store.load_many(source_task_id, "product_cards")
    ]
    if not sources or not evidence or not product_cards:
        raise ValueError(
            "A/B 输入不完整：source task 必须包含 sources、evidence 和 product_cards"
        )
    return task, sources, evidence, product_cards


def _save_portfolio_artifacts(
    *,
    store: ArtifactStore,
    task_id: str,
    portfolio: CompetitiveAnalysisPortfolioV2,
) -> None:
    artifact_groups = {
        "analysis_portfolios": [portfolio],
        "brief_assessments": [portfolio.brief_assessment],
        "competitor_profiles": portfolio.competitor_profiles,
        "intelligence_questions": portfolio.key_intelligence_questions,
        "information_needs": portfolio.information_needs,
        "evidence_coverage": portfolio.evidence_coverage,
        "comparability_notes": portfolio.comparability_notes,
        "claims_v2": portfolio.items,
        "research_gaps": portfolio.research_gaps,
    }
    for artifact_type, items in artifact_groups.items():
        store.save_many(task_id, artifact_type, items)


def _metric_pass_rate(metrics: dict[str, dict[str, Any]]) -> float:
    scored = [
        item
        for name, item in metrics.items()
        if name != "claim_type_distribution"
    ]
    if not scored:
        return 0.0
    return round(sum(bool(item["passed"]) for item in scored) / len(scored), 4)


def _run_variant(
    *,
    variant: str,
    prompt_id: str,
    experiment_id: str,
    source_store: ArtifactStore,
    source_task_id: str,
    snapshot_id: str,
    output_store: ArtifactStore,
    config,
    registry: PromptRegistry,
) -> dict[str, Any]:
    task_id = f"{experiment_id}_{variant}"
    task, sources, evidence, product_cards = _clone_inputs(
        source_store=source_store,
        source_task_id=source_task_id,
        target_task_id=task_id,
        snapshot_id=snapshot_id,
    )
    output_store.save_many(task_id, "sources", sources)
    output_store.save_many(task_id, "evidence", evidence)
    output_store.save_many(task_id, "product_cards", product_cards)
    output_store.save_many(task_id, "llm_calls", [])
    output_store.save_many(task_id, "llm_outputs", [])

    prompt = registry.load(prompt_id, allow_candidate=True)
    runtime_prompt = prompt.build_runtime_prompt(task)
    artifacts = {
        "analysis_task": [task.model_dump(mode="json")],
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    client = LLMClient(config=config, store=output_store)
    raw_output, call, _output = client.generate_structured(
        task_id=task_id,
        agent_role=AgentRole.ANALYST,
        agent_run_id=f"run_{variant}",
        node_id=f"node_{variant}",
        context_bundle=None,
        output_schema="CompetitiveAnalysisPortfolioV2",
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        prompt_summary=runtime_prompt,
        artifacts=artifacts,
    )
    parsed_portfolio = parse_competitive_analysis_portfolio_v2(raw_output)
    portfolio = parsed_portfolio.model_copy(
        update={
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.version,
            "items": [
                item.model_copy(update={"produced_by_agent_run_id": f"run_{variant}"})
                for item in parsed_portfolio.items
            ],
            "metadata": {
                **parsed_portfolio.metadata,
                "prompt_hash": prompt.content_hash,
                "experiment_id": experiment_id,
                "variant": variant,
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
    _save_portfolio_artifacts(
        store=output_store,
        task_id=task_id,
        portfolio=portfolio,
    )
    metrics = evaluate_portfolio_core(
        portfolio=portfolio,
        sources=sources,
        evidence=evidence,
        allowed_dimensions=set(task.focus_areas),
    )
    failed_metrics = [
        name
        for name, result in metrics.items()
        if name != "claim_type_distribution" and not result["passed"]
    ]
    rejected_claims = call.metadata.get("rejected_portfolio_claims", [])
    result_status = "quality_gate_failed" if failed_metrics else (
        "completed_with_rejections" if rejected_claims else "completed"
    )
    return {
        "variant": variant,
        "task_id": task_id,
        "status": result_status,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.content_hash,
        "request_id": call.metadata.get("request_id", ""),
        "duration_ms": call.duration_ms,
        "input_tokens": call.metadata.get("input_tokens", 0),
        "output_tokens": call.metadata.get("output_tokens", 0),
        "used_fallback": call.used_fallback,
        "competitor_profiles_count": len(portfolio.competitor_profiles),
        "claims_v2_count": len(portfolio.items),
        "research_gaps_count": len(portfolio.research_gaps),
        "rejected_claims_count": len(rejected_claims),
        "rejected_claims": rejected_claims,
        "metric_pass_rate": _metric_pass_rate(metrics),
        "quality_gate_failures": failed_metrics,
        "metrics": metrics,
    }


def _load_saved_variant_result(
    *,
    output_store: ArtifactStore,
    experiment_id: str,
    variant: str,
) -> dict[str, Any]:
    task_id = f"{experiment_id}_{variant}"
    calls = output_store.load_many(task_id, "llm_calls")
    call = calls[-1] if calls else {}
    outputs = output_store.load_many(task_id, "llm_outputs")
    portfolios = output_store.load_many(task_id, "analysis_portfolios")
    base = {
        "variant": variant,
        "task_id": task_id,
        "prompt_id": call.get("prompt_id", ""),
        "prompt_version": call.get("prompt_version", ""),
        "prompt_hash": call.get("prompt_hash", ""),
        "request_id": (call.get("metadata") or {}).get("request_id", ""),
        "duration_ms": call.get("duration_ms", 0),
        "input_tokens": (call.get("metadata") or {}).get("input_tokens", 0),
        "output_tokens": (call.get("metadata") or {}).get("output_tokens", 0),
        "used_fallback": bool(call.get("used_fallback", False)),
    }
    sources = [
        SourceDocument(**item)
        for item in output_store.load_many(task_id, "sources")
    ]
    evidence = [
        SourceEvidence(**item)
        for item in output_store.load_many(task_id, "evidence")
    ]
    product_cards = [
        ProductCard(**item)
        for item in output_store.load_many(task_id, "product_cards")
    ]
    recovered_from_saved_raw_output = False
    rejected_claims = list(
        (call.get("metadata") or {}).get("rejected_portfolio_claims", [])
    )
    if portfolios:
        portfolio = CompetitiveAnalysisPortfolioV2(**portfolios[-1])
    elif outputs and isinstance(outputs[-1].get("raw_output"), dict):
        parsed = parse_competitive_analysis_portfolio_v2(outputs[-1]["raw_output"])
        portfolio, recovered_rejections = reject_unaligned_portfolio_v2_claims(
            parsed,
            evidence_competitors={item.id: item.competitor for item in evidence},
            known_competitors={item.name for item in product_cards},
        )
        rejected_claims = recovered_rejections
        portfolio = portfolio.model_copy(
            update={
                "prompt_id": str(call.get("prompt_id") or portfolio.prompt_id),
                "prompt_version": str(
                    call.get("prompt_version") or portfolio.prompt_version
                ),
                "metadata": {
                    **portfolio.metadata,
                    "prompt_hash": str(call.get("prompt_hash") or ""),
                    "experiment_id": experiment_id,
                    "variant": variant,
                    "recovered_from_saved_raw_output": True,
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
        _save_portfolio_artifacts(
            store=output_store,
            task_id=task_id,
            portfolio=portfolio,
        )
        recovered_from_saved_raw_output = True
    else:
        return {
            **base,
            "status": "failed",
            "error": call.get("error", "未保存有效 analysis_portfolios"),
        }

    metrics = evaluate_portfolio_core(
        portfolio=portfolio,
        sources=sources,
        evidence=evidence,
        allowed_dimensions=set(portfolio.brief_assessment.selected_dimensions),
    )
    failed_metrics = [
        name
        for name, result in metrics.items()
        if name != "claim_type_distribution" and not result["passed"]
    ]
    result_status = "quality_gate_failed" if failed_metrics else (
        "completed_with_rejections" if rejected_claims else "completed"
    )
    return {
        **base,
        "status": result_status,
        "competitor_profiles_count": len(portfolio.competitor_profiles),
        "claims_v2_count": len(portfolio.items),
        "research_gaps_count": len(portfolio.research_gaps),
        "rejected_claims_count": len(rejected_claims),
        "rejected_claims": rejected_claims,
        "recovered_from_saved_raw_output": recovered_from_saved_raw_output,
        "metric_pass_rate": _metric_pass_rate(metrics),
        "quality_gate_failures": failed_metrics,
        "metrics": metrics,
    }


def _finalize_summary(
    *,
    base_summary: dict[str, Any],
    results: list[dict[str, Any]],
    expected_variant_count: int,
) -> dict[str, Any]:
    accepted_statuses = {"completed", "completed_with_rejections"}
    completed = [item for item in results if item["status"] in accepted_statuses]
    if len(completed) == expected_variant_count:
        status = (
            "completed_with_rejections"
            if any(item["status"] == "completed_with_rejections" for item in completed)
            else "completed"
        )
    elif completed:
        status = "partial_completed"
    else:
        status = "failed"
    preferred_variant = ""
    ranked_results = completed or [
        item for item in results if item["status"] == "quality_gate_failed"
    ]
    if ranked_results:
        preferred_variant = max(
            ranked_results,
            key=lambda item: (
                item["metric_pass_rate"],
                item["metrics"]["decision_impact_coverage"]["value"],
                item["metrics"]["uncertainty_disclosure_rate"]["value"],
            ),
        )["variant"]
    return {
        **base_summary,
        "status": status,
        "preferred_variant_for_next_pilot": preferred_variant,
        "results": results,
    }


def main() -> None:
    args = parse_args()
    selected_variants = (
        VARIANTS
        if args.variant == "all"
        else tuple(item for item in VARIANTS if item[0] == args.variant)
    )
    default_ab_root = Path(__file__).resolve().parent / "app" / "data" / "ab_tests"
    output_store = ArtifactStore(root_dir=args.artifact_root or default_ab_root)
    summary_path = output_store.root_dir / f"{args.experiment_id}_summary.json"
    if args.recompute_only:
        if not summary_path.exists():
            raise SystemExit(f"找不到已有 A/B summary: {summary_path}")
        existing = json.loads(summary_path.read_text(encoding="utf-8"))
        results = [
            _load_saved_variant_result(
                output_store=output_store,
                experiment_id=args.experiment_id,
                variant=variant,
            )
            for variant, _prompt_id in selected_variants
        ]
        summary = _finalize_summary(
            base_summary=existing,
            results=results,
            expected_variant_count=len(selected_variants),
        )
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"summary={summary_path.resolve()}")
        print(f"status={summary['status']}")
        print(
            "preferred_variant_for_next_pilot="
            f"{summary['preferred_variant_for_next_pilot']}"
        )
        return

    config = load_llm_config(mode=LLMMode.LLM.value)
    if config.provider != LLMProvider.COMPATIBLE:
        raise SystemExit("A/B Pilot 要求 LLM_PROVIDER=compatible")
    readiness_errors = config.real_call_readiness_errors()
    if readiness_errors:
        raise SystemExit("；".join(readiness_errors))

    source_store = ArtifactStore(root_dir=args.source_artifact_root)
    registry = PromptRegistry()
    results: list[dict[str, Any]] = []
    for variant, prompt_id in selected_variants:
        try:
            result = _run_variant(
                variant=variant,
                prompt_id=prompt_id,
                experiment_id=args.experiment_id,
                source_store=source_store,
                source_task_id=args.source_task_id,
                snapshot_id=args.snapshot_id,
                output_store=output_store,
                config=config,
                registry=registry,
            )
        except Exception as exc:
            result = {
                "variant": variant,
                "task_id": f"{args.experiment_id}_{variant}",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        print(
            f"variant={variant} status={result['status']} "
            f"metric_pass_rate={result.get('metric_pass_rate', 0)}"
        )

    base_summary = {
        "experiment_id": args.experiment_id,
        "real_llm_called": True,
        "provider": config.provider.value,
        "model": config.model,
        "api_style": config.api_style,
        "structured_output_mode": config.structured_output_mode,
        "thinking_mode": config.thinking_mode,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "source_task_id": args.source_task_id,
        "same_input_and_schema": True,
        "call_budget": len(selected_variants),
        "selected_variant": args.variant,
    }
    summary = _finalize_summary(
        base_summary=base_summary,
        results=results,
        expected_variant_count=len(selected_variants),
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"summary={summary_path.resolve()}")
    print(
        "preferred_variant_for_next_pilot="
        f"{summary['preferred_variant_for_next_pilot']}"
    )
    if summary["status"] == "failed":
        for result in results:
            if result["status"] == "failed":
                print(
                    f"failed_variant={result['variant']} error={result['error']}",
                    file=sys.stderr,
                )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
