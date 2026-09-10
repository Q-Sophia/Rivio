from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.harness.artifacts import ArtifactStore
from app.llm.client import LLMClient
from app.llm.config import LLMConfig
from app.llm.structured import (
    parse_competitive_analysis_portfolio_v2,
    validate_portfolio_v2_refs,
)
from app.prompts import PromptRegistry
from app.schemas import AgentRole, ContextBundle, LLMMode, LLMProvider
from harness.step6c_fixtures import build_step6c_fixtures, fixture_catalog_summary
from harness.step6c_metrics import evaluate_fixture_expectations, evaluate_portfolio_core


DEFAULT_ARTIFACT_ROOT = BACKEND_ROOT / "app" / "data" / "contract_tests"
SUMMARY_FILE = "step6c_suite_summary.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all Step6C mock fixtures and professional-analysis metrics."
    )
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run one or more selected fixture ids; repeat the option as needed.",
    )
    return parser.parse_args()


def _metric_passed(metrics: dict[str, dict]) -> bool:
    return all(bool(item["passed"]) for item in metrics.values())


def _fixture_payload(fixture) -> dict[str, list[dict]]:
    return {
        "analysis_task": [fixture.task.model_dump(mode="json")],
        "evidence": [item.model_dump(mode="json") for item in fixture.evidence],
    }


def run_case(*, fixture, store: ArtifactStore, prompt, config: LLMConfig) -> dict:
    task_id = fixture.task.task_id
    artifacts = _fixture_payload(fixture)
    store.save_many(task_id, "sources", fixture.sources)
    store.save_many(task_id, "evidence", fixture.evidence)
    store.save_many(task_id, "product_cards", fixture.product_cards)
    store.save_many(task_id, "llm_calls", [])
    store.save_many(task_id, "llm_outputs", [])

    client = LLMClient(config=config, store=store)
    raw, call, output = client.generate_structured(
        task_id=task_id,
        agent_role=AgentRole.ANALYST,
        agent_run_id=f"run_{fixture.case_id}",
        node_id="build_claims",
        context_bundle=ContextBundle(
            task_id=task_id,
            agent_role=AgentRole.ANALYST,
            node_id="build_claims",
        ),
        output_schema="CompetitiveAnalysisPortfolioV2",
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        prompt_hash=prompt.content_hash,
        prompt_summary=prompt.build_runtime_prompt(fixture.task),
        artifacts=artifacts,
    )
    portfolio = parse_competitive_analysis_portfolio_v2(raw)
    portfolio = portfolio.model_copy(
        update={
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.version,
            "metadata": {
                **portfolio.metadata,
                "prompt_hash": prompt.content_hash,
                "fixture_case_id": fixture.case_id,
            },
        }
    )
    validate_portfolio_v2_refs(
        portfolio,
        known_source_ids={item.id for item in fixture.sources},
        known_evidence_ids={item.id for item in fixture.evidence},
        known_competitors={item.name for item in fixture.product_cards},
        evidence_competitors={item.id: item.competitor for item in fixture.evidence},
    )
    store.save_many(task_id, "analysis_portfolios", [portfolio])
    store.save_many(task_id, "competitor_profiles", portfolio.competitor_profiles)
    store.save_many(task_id, "claims_v2", portfolio.items)
    store.save_many(task_id, "research_gaps", portfolio.research_gaps)

    core_metrics = evaluate_portfolio_core(
        portfolio=portfolio,
        sources=fixture.sources,
        evidence=fixture.evidence,
        allowed_dimensions={str(item.dimension) for item in fixture.evidence},
        forbidden_terms=fixture.forbidden_output_terms,
        injection_markers=fixture.injection_markers,
    )
    expectation_metrics = evaluate_fixture_expectations(
        fixture=fixture,
        portfolio=portfolio,
    )
    metrics = {**core_metrics, **expectation_metrics}
    passed = (
        _metric_passed(metrics)
        and not call.used_fallback
        and output.validation_status == "passed"
    )
    return {
        "case_id": fixture.case_id,
        "task_id": task_id,
        "passed": passed,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.content_hash,
        "llm_provider": str(config.provider),
        "llm_model": config.model,
        "real_calls_enabled": config.enable_real_calls,
        "used_fallback": call.used_fallback,
        "validation_status": output.validation_status,
        "counts": {
            "sources": len(fixture.sources),
            "evidence": len(fixture.evidence),
            "competitor_profiles": len(portfolio.competitor_profiles),
            "claims_v2": len(portfolio.items),
            "research_gaps": len(portfolio.research_gaps),
        },
        "metrics": metrics,
    }


def build_suite_summary(results: list[dict], prompt, expected_fixture_count: int) -> dict:
    failed_cases = [item["case_id"] for item in results if not item["passed"]]
    metric_values: dict[str, list[float]] = {}
    for result in results:
        for name, metric in result["metrics"].items():
            value = metric["value"]
            if isinstance(value, bool):
                numeric_value = 1.0 if value else 0.0
            elif isinstance(value, (int, float)):
                numeric_value = float(value)
            else:
                continue
            metric_values.setdefault(name, []).append(numeric_value)
    aggregates = {
        name: round(sum(values) / len(values), 4)
        for name, values in metric_values.items()
        if values
    }
    metric_passes: dict[str, list[bool]] = {}
    for result in results:
        for name, metric in result["metrics"].items():
            metric_passes.setdefault(name, []).append(bool(metric["passed"]))
    metric_pass_rates = {
        name: round(sum(values) / len(values), 4)
        for name, values in metric_passes.items()
        if values
    }
    result_by_case = {item["case_id"]: item for item in results}
    targeted_metrics = {}
    if "different_solution_paths" in result_by_case:
        targeted_metrics["different_solution_paths.path_tradeoff_coverage"] = (
            result_by_case["different_solution_paths"]["metrics"][
                "path_tradeoff_coverage"
            ]
        )
    if "normal_full" in result_by_case:
        targeted_metrics["normal_full.expected_claim_types"] = (
            result_by_case["normal_full"]["metrics"][
                "fixture_expected_claim_types"
            ]
        )
    return {
        "suite_id": "step6c_mock_fixture_suite_v1",
        "passed": not failed_cases and len(results) == expected_fixture_count,
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.content_hash,
        "fixture_count": len(results),
        "failed_cases": failed_cases,
        "real_llm_called": False,
        "aggregate_metrics": aggregates,
        "metric_pass_rates": metric_pass_rates,
        "targeted_metrics": targeted_metrics,
        "fixture_catalog": fixture_catalog_summary(),
        "cases": results,
    }


def main() -> None:
    args = parse_args()
    all_fixtures = build_step6c_fixtures()
    fixtures = all_fixtures
    if args.case_id:
        requested = set(args.case_id)
        fixtures = [item for item in fixtures if item.case_id in requested]
        missing = requested - {item.case_id for item in fixtures}
        if missing:
            raise SystemExit("Unknown case ids: " + ", ".join(sorted(missing)))

    prompt = PromptRegistry().load("competitive_analyst", allow_candidate=True)
    store = ArtifactStore(root_dir=args.artifact_root)
    config = LLMConfig(
        provider=LLMProvider.MOCK,
        model="mock-structured-v1",
        mode=LLMMode.LLM_WITH_FALLBACK,
        api_style="mock",
        output_language="zh-CN",
        enable_real_calls=False,
    )
    results = [
        run_case(fixture=fixture, store=store, prompt=prompt, config=config)
        for fixture in fixtures
    ]
    expected_fixture_count = len(fixtures) if args.case_id else len(all_fixtures)
    summary = build_suite_summary(results, prompt, expected_fixture_count)
    summary_path = Path(args.artifact_root) / SUMMARY_FILE
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"step6c_suite_passed={summary['passed']}")
    print(f"fixture_count={summary['fixture_count']}")
    print(f"failed_cases={','.join(summary['failed_cases'])}")
    print(f"prompt={prompt.prompt_id}@{prompt.version}")
    print(f"prompt_hash={prompt.content_hash}")
    print("real_llm_called=false")
    print(f"summary={summary_path}")
    for result in results:
        failed_metrics = [
            name
            for name, metric in result["metrics"].items()
            if not metric["passed"]
        ]
        print(
            f"case={result['case_id']} passed={result['passed']} "
            f"failed_metrics={','.join(failed_metrics)}"
        )
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
