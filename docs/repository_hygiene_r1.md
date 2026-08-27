# Repository Hygiene R1

This document records the repository/data boundary audited on 2026-08-27. It
does not change the evidence-first artifact contracts or remove legacy runtime
paths.

## Data policy

| Path | Classification | Git policy | Reason |
| --- | --- | --- | --- |
| `backend/app/`, `frontend/`, root/backend scripts | production source | tracked | Product and developer tooling source. |
| `backend/app/data/snapshots/` | stable fixture | tracked | Directly read by snapshot collector, planner, and evaluation code. |
| `backend/app/data/contract_tests/` | stable contract fixture | tracked | Fixed Step6C contract inputs and expected artifacts. |
| `backend/app/data/ab_tests/` | historical audit fixture | tracked | Read by the experiment API and evidence-alignment checks. |
| selected existing `backend/app/data/runs/snapshot_*` and referenced pilots | compatibility fixture | tracked for now | Multiple scripts/docs/API checks still reference these paths. Migrate later; do not silently delete. |
| `backend/app/data/evaluations/step6g_snapshot_url_eval/` | accepted evaluation snapshot | tracked for now | Documented historical evaluation output; deletion value is ambiguous. |
| `backend/app/data/runs/**` newly created content | runtime output | ignored | ArtifactStore's production default and user task output are mutable local state. Existing tracked fixtures remain tracked until deliberately migrated. |
| `backend/app/data/checks/` | test-generated output | ignored and removed from the index | `check_*.py` scripts construct/reset these ArtifactStore roots during execution. |
| `backend/app/data/taskboard_store_check/` | test-generated output | ignored and removed from the index | Rebuilt by TaskBoard store checks. |
| `backend/app/data/tmp/`, `backend/app/data/logs/`, root `app/data/` | local/temp/runtime output | ignored | Never a stable input contract. The verified-empty root `app/` tree was removed and will remain ignored if recreated. |
| generated search/RAG evaluation reports | test/evaluation output | ignored | Reproducible output; labeled input CSV is intentionally not covered by the output ignore rules. |

The index cleanup uses `git rm --cached`: files remain available in the local
working tree, but a future checkpoint commit will stop versioning them.

## Check script classification

No check script was deleted in R1. `OBSOLETE_CANDIDATE` is intentionally empty:
the audit found overlap, but not enough proof that any script has no independent
contract value.

### KEEP

- `check_artifact_atomic_write.py`
- `check_context_memory_artifacts.py`
- `check_dynamic_dag_artifacts.py`
- `check_e2e_task_workspace.py`
- `check_llm_artifacts.py`
- `check_llm_provider_adapter.py`
- `check_mcp_r1_fake_server.py`
- `check_mcp_r1_production_integration.py`
- `check_quality_gate_artifacts.py`
- `check_r1_bounded_fx1.py`
- `check_r1_bounded_research_r1.py`
- `check_r1_compat_bridge_r1.py`
- `check_r1_policy_runtime_r1.py`
- `check_r1_search_foundation_fx1.py`
- `check_r1_source_policy_r1.py`
- `check_ra_flow_fx1.py`
- `check_research_agent_r1.py`
- `check_run_artifacts.py`
- `check_search_provider_tavily.py` (mocked adapter contract)
- `check_search_source_quality_r1.py`
- `check_snapshot.py`
- `check_source_rag_hybrid_rerank.py`
- `check_source_rag_r1.py`
- `check_step6c_artifacts.py`
- `check_step6c_cross_industry.py`
- `check_step6c_evidence_alignment.py`
- `check_step6c_prompt_design.py`
- `check_step6c_writer.py`
- `check_step6d_intake.py`
- `check_step6d3_planning.py`
- `check_step6d4_execution.py`
- `check_step6e1_research_planner.py`
- `check_step6e5_research_loop.py` (legacy path is still API/frontend referenced)
- `check_step6f_analyst_output_stability.py`
- `check_step6g_intent_aware_extraction.py`
- `check_task_navigation.py`
- `check_taskboard_artifacts.py`
- `check_taskboard_schema.py`
- `check_taskboard_store.py`
- `check_v1_e2e_closeout.py`
- `check_workflow_trace.py`

### MERGE_LATER

- `check_official_first_source_acquisition_r1.py`
- `check_official_host_detection_fix.py`
- `check_source_acquisition_r2_first_party_first.py`
- `check_step6e2_browser_search.py`
- `check_step6e2_web_collector.py`
- `check_step6e3_web_evidence_extractor.py`
- `check_step6e4_gap_tasks.py`
- `check_step6e4_update.py`

These still cover useful V1/compatibility boundaries, but overlap newer Source
Policy, Search Foundation, bounded research, and compatibility projection suites.

### REAL_SMOKE

- `check_official_host_detection_tavily_smoke.py`
- `check_research_agent_r1_real_smoke.py`
- `check_search_provider_tavily_smoke.py`
- `check_source_rag_fastembed_smoke.py`
- `check_search_source_quality_r1_eval.py`
- `check_source_rag_hybrid_rerank_eval.py`
- `check_step6c_dual_real_pilot.py`
- `check_step6c_real_pilot.py`
- `check_step6c_writer_real_pilot.py`

These require a real provider/dependency or previously saved real-run artifacts.
They are not part of the default offline hygiene verification.

### OBSOLETE_CANDIDATE

- None with high confidence. Revisit only after a consolidated test runner maps
  every contract to its replacement.

## Legacy path reference audit

| Path | Status | Current references |
| --- | --- | --- |
| `ResearchLoopRunner` | production referenced, legacy product path | Exported by `app.execution`; FastAPI start/status/events/SSE endpoints and current frontend controls call it. |
| `CollectorQueueService` | production referenced | Shared by Research Agent R1's reliable Search/Fetch runtime and the legacy collector endpoint/ResearchLoop. It cannot be removed as a “legacy collector” wholesale. |
| snapshot `CollectorAgent` / `LocalSnapshotCollector` | compatibility only plus tests/demos | Used by snapshot workflow and snapshot regression paths, not the R1 evidence execution role. |
| `/api/runs` and run dashboard API | production referenced compatibility API | Current frontend recent-run navigation still calls both endpoints. |
| `snapshot_pipeline.py` | production referenced compatibility pipeline | `ExecutionRunner` and FastAPI execution endpoints still invoke it; Analyst/Reporting also reuse its tool registry. |

No legacy path in the requested scope was apparently unreferenced, so none was
deleted.
