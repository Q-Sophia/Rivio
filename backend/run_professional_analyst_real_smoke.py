"""Explicitly authorized live Analyst smoke; never invokes research or reporting."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.workflow.snapshot_pipeline import build_snapshot_tool_registry
from app.agents import LLMProfessionalAnalystAgent
from app.agents.runtime import AgentRuntime
from app.analysis_assessment import (
    materialize_analysis_assessment,
    resolve_framework_assessment_binding,
)
from app.execution.research_analysis import ResearchAnalysisService
from app.frameworks import load_framework
from app.harness.artifacts import ArtifactStore
from app.llm import LLMClient
from app.llm.provider import OpenAIChatCompletionsProvider
from app.schemas import (
    AgentContext, AgentRole, AnalysisTask, DAGNode, ResearchTask,
    SourceDocument, SourceEvidence, RunStatus, AnalystAssessmentStage,
)
from app.workflow.trace import TraceRecorder


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")


class AuditedProvider(OpenAIChatCompletionsProvider):
    """Record actual HTTP JSON bodies while retaining production request logic."""

    def __init__(self, *, store, task_id, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.task_id = task_id
        self.request_count = 0

    def _post_with_retry(self, payload):
        if self.request_count >= 6:
            raise RuntimeError("Smoke HTTP call budget exhausted (6)")
        if self.config.base_url.rstrip("/") != "https://api.deepseek.com/v1":
            raise RuntimeError("Smoke only permits the configured official DeepSeek endpoint")
        self.request_count += 1
        number = self.request_count
        user = json.loads(payload["messages"][1]["content"])
        self.store.save_many(self.task_id, f"request_{number:02d}", [Record(**payload)])
        print(f"request={number} schema={user['output_schema']} evidence={len(user['artifacts'].get('evidence', []))}", flush=True)
        started = time.perf_counter()
        data, attempts, headers = super()._post_with_retry(payload)
        self.store.save_many(self.task_id, f"response_{number:02d}", [Record(**data)])
        print(f"response={number} seconds={time.perf_counter()-started:.2f} usage={data.get('usage')} finish={(data.get('choices') or [{}])[0].get('finish_reason')}", flush=True)
        return data, attempts, headers


def directory_hashes(directory, *, production=False):
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*")) if path.is_file()
        and "__pycache__" not in path.parts
        and (not production or "data" not in path.relative_to(directory).parts)
    }


def export_recorded_assessment(root, task_id):
    """Replay only deterministic validation/materialization; no provider instance."""
    store = ArtifactStore(root)
    responses = []
    assessment_response = None
    actual_input = None
    for path in sorted((root / task_id).glob("request_*.json")):
        request = json.loads(path.read_text(encoding="utf-8"))[0]
        response = store.load_many(task_id, path.stem.replace("request_", "response_"))[0]
        responses.append(response)
        user = json.loads(request["messages"][1]["content"])
        if user["output_schema"] == "AnalystAssessmentStage":
            actual_input = user["artifacts"]
            assessment_response = response
    if assessment_response is None:
        raise RuntimeError("No recorded real assessment response")
    raw = json.loads(assessment_response["choices"][0]["message"]["content"])
    stage = AnalystAssessmentStage(**raw["item"])
    store.save_many(task_id, "assessment_model_output", [Record(**raw)])
    task = AnalysisTask(**actual_input["analysis_task"][0])
    tasks = [ResearchTask(**r) for r in actual_input["research_tasks"]]
    evidence = [SourceEvidence(**r) for r in actual_input["evidence"]]
    binding = resolve_framework_assessment_binding(task=task, research_tasks=tasks)
    existing = store.load_many(task_id, "analysis_assessments")[0]
    assessment = materialize_analysis_assessment(
        stage=stage, binding=binding, evidence=evidence, task=task,
        pipeline_id=existing["pipeline_id"], assessment_round=existing["assessment_round"],
        analyst_agent_run_id=existing["analyst_agent_run_id"],
    )
    assert assessment.id == existing["id"]
    assert [g.model_dump(mode="json") for g in assessment.research_gaps] == existing["research_gaps"]
    store.save_many(task_id, "research_gaps", assessment.research_gaps)
    initial = store.load_many(task_id, "smoke_summary")[0]
    result = {
        **initial,
        "status": "assessment_foundation_passed",
        "full_analyst_agent_status": initial["status"],
        "full_analyst_agent_error": initial["error"],
        "gap_export_method": "deterministic materialization of untouched real response; not a successful full portfolio run",
        "input_tokens": sum(r["usage"]["prompt_tokens"] for r in responses),
        "output_tokens": sum(r["usage"]["completion_tokens"] for r in responses),
        "assessment_request_id": assessment_response["id"],
        "assessment_usage": assessment_response["usage"],
        "overall_status": assessment.overall_status,
        "coverage_score": assessment.coverage_score,
        "gap_count": len(assessment.research_gaps),
        "additional_llm_calls_for_export": 0,
    }
    store.save_many(task_id, "assessment_smoke_result", [Record(**result)])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Authorize real DeepSeek calls")
    parser.add_argument("--label", default="professional_analyst_deepseek_smoke_20260908")
    parser.add_argument("--export-recorded-only", action="store_true")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parent
    if args.export_recorded_only:
        export_recorded_assessment(backend.parent / "artifacts" / args.label, args.label)
        return
    origin = backend / "app/data/runs/task_user_58adfe02725c"
    production_before = directory_hashes(backend / "app", production=True)
    source_before = directory_hashes(origin)
    root = backend.parent / "artifacts" / args.label
    task_id = args.label
    if root.exists():
        raise RuntimeError(f"Refusing to overwrite a previous smoke: {root}")
    store = ArtifactStore(root)
    def read(kind):
        return json.loads((origin / f"{kind}.json").read_text(encoding="utf-8"))
    original_task = AnalysisTask(**read("analysis_tasks")[-1])
    framework = load_framework("competitive_intelligence", "1.0.0")
    dimensions = [d for d in framework.dimensions if d.dimension_id in {
        "product_capability", "commercial_strategy", "customer_experience"
    }]
    task = original_task.model_copy(update={
        "id": task_id, "task_id": task_id,
        "focus_areas": [d.evidence_dimension for d in dimensions],
        "metadata": {**original_task.metadata, "smoke_origin_task_id": original_task.id},
    })
    authorized = {eid for run in read("research_agent_runs") for eid in run.get("verified_evidence_ids", [])}
    evidence = [SourceEvidence(**e).model_copy(update={"task_id": task_id})
                for e in read("evidence") if e["id"] in authorized and e["dimension"] in {"feature", "pricing", "customer"}]
    source_ids = {e.source_id for e in evidence}
    sources = [SourceDocument(**s).model_copy(update={"task_id": task_id})
               for s in read("sources") if s["id"] in source_ids]
    assert source_ids == {s.id for s in sources}
    old_tasks = read("research_tasks")
    tasks = []
    mapping = {"产品能力": "feature", "定价与成本": "pricing"}
    for competitor in task.competitors:
        for dimension in dimensions:
            previous = next((r for r in old_tasks if r["competitor"] == competitor
                             and mapping.get(r["dimension"], r["dimension"]) == dimension.evidence_dimension), None)
            fields = dict(previous or {})
            fields.update(
                schema_version="v2", task_id=task_id,
                id=(previous["id"] if previous else f"smoke_customer_{competitor.lower()}"),
                information_need_id=(previous["information_need_id"] if previous else f"smoke_need_customer_{competitor.lower()}"),
                title=f"{competitor} {dimension.label}",
                objective=dimension.objective_template.format(competitor=competitor),
                competitor=competitor, dimension=dimension.evidence_dimension,
                research_intent=dimension.research_intent,
                stop_condition="；".join(dimension.completion_criteria),
                framework_id=framework.framework_id, framework_version=framework.version,
                framework_dimension_id=dimension.dimension_id,
                framework_content_hash=framework.content_hash,
                metadata={"smoke_input_construction": "framework pinning of historical task" if previous else "customer scope fixture; never scheduled"},
            )
            tasks.append(ResearchTask(**fields))
    binding = resolve_framework_assessment_binding(task=task, research_tasks=tasks)
    for kind, models in {
        "analysis_tasks": [task], "sources": sources, "evidence": evidence,
        "research_tasks": tasks, "framework_definition": [framework],
    }.items():
        store.save_many(task_id, kind, models)
    store.save_many(task_id, "assessment_scope", [Record(**item) for item in binding.scope])
    config = ResearchAnalysisService._deepseek_config()
    errors = config.real_call_readiness_errors()
    if errors:
        raise RuntimeError("; ".join(errors))
    summary = {
        "source_task_id": original_task.id, "query": task.query,
        "model": config.model, "base_url": config.base_url,
        "framework_id": framework.framework_id, "framework_version": framework.version,
        "framework_content_hash": framework.content_hash,
        "research_task_count": len(tasks), "source_count": len(sources),
        "evidence_count": len(evidence), "evidence_dimensions": dict(Counter(e.dimension for e in evidence)),
        "fixture_adjustments": ["task_id isolated", "scope limited to three Framework dimensions",
                                "four legacy tasks pinned to Framework v2; two customer evaluation fixtures added",
                                "historical quotes/facts/IDs/source metadata retained", "no previous claims or gaps supplied"],
        "search_calls": 0, "automatic_loop": False,
    }
    store.save_many(task_id, "input_summary", [Record(**summary)])
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        return
    provider = AuditedProvider(config=config, store=store, task_id=task_id)
    recorder = TraceRecorder(store=store, task_id=task_id)
    tools = build_snapshot_tool_registry(store=store, recorder=recorder)
    agent = LLMProfessionalAnalystAgent(
        name="professional_analyst_real_smoke", role=AgentRole.ANALYST,
        tools=tools, llm_client=LLMClient(config=config, store=store, provider=provider),
    )
    node = DAGNode(id=f"analyst_{task_id}", task_id=task_id, label="Analyst semantic smoke", agent_role=AgentRole.ANALYST)
    recorder.dag_nodes.append(node)
    context = AgentContext(task_id=task_id, task=task, node_id=node.id, metadata={
        "preserve_research_artifacts": True, "require_r1_evidence_authority": True,
        "authorized_evidence_ids": sorted(e.id for e in evidence),
        "pipeline_id": f"smoke_{task_id}", "explicit_real_llm_authorization": True,
    })
    started = time.perf_counter()
    result = AgentRuntime(store=store, recorder=recorder).run(agent=agent, context=context, node=node)
    recorder.save_trace()
    store.save_many(task_id, "smoke_agent_result", [result])
    calls = store.load_many(task_id, "llm_calls")
    summary.update(
        status=result.status, error=result.error,
        elapsed_seconds=round(time.perf_counter()-started, 2),
        http_calls=provider.request_count,
        logical_llm_calls=len(calls),
        input_tokens=sum(c.get("metadata", {}).get("input_tokens", 0) for c in calls),
        output_tokens=sum(c.get("metadata", {}).get("output_tokens", 0) for c in calls),
        original_run_unchanged=(source_before == directory_hashes(origin)),
        production_code_unchanged=all(
            hashlib.sha256((backend / "app" / path).read_bytes()).hexdigest() == digest
            for path, digest in production_before.items() if not path.startswith("data") and "__pycache__" not in path
        ),
        tool_names=sorted({c.get("tool_name", "") for c in store.load_many(task_id, "tool_calls")}),
    )
    store.save_many(task_id, "smoke_summary", [Record(**summary)])
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if result.status != RunStatus.COMPLETED.value:
        raise RuntimeError(result.error)
    assert summary["original_run_unchanged"] and summary["production_code_unchanged"]
    assert store.load_many(task_id, "analysis_assessments")
    print(f"SMOKE_PASS artifacts={root / task_id}", flush=True)


if __name__ == "__main__":
    main()
