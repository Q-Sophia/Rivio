# Snapshot Agent Workflow Runbook

This runbook describes Milestone 5 and Milestone 6: the local Snapshot Pipeline with an Agent Workflow Trace Layer, plus the lightweight Agent Runtime, ToolRegistry, and Evaluation Harness.

The current workflow is still deterministic and local. It does not use LLMs, FastAPI, frontend code, automatic web search, crawlers, RAG, embeddings, Chroma, long-term memory, or an MCP server.

## Inputs

Snapshot source artifacts:

- `backend/app/data/snapshots/online_education/sources.json`
- `backend/app/data/snapshots/online_education/evidence.json`

The workflow reads these through `LocalSnapshotCollector` and then writes run artifacts through `ArtifactStore`.

## Command

Run from `competitive-intel-agents/backend`.

```powershell
python .\run_snapshot_agent_workflow_demo.py
python .\check_workflow_trace.py
python .\check_run_artifacts.py
python .\harness\run_eval.py
```

In the current workspace, a known working Python environment is:

```powershell
..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe
```

Use it like this:

```powershell
& "..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe" .\run_snapshot_agent_workflow_demo.py
& "..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe" .\check_workflow_trace.py
& "..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe" .\check_run_artifacts.py
& "..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe" .\harness\run_eval.py
```

## Workflow DAG

The first `DAGExecutor` implementation is sequential only. It does not run nodes in parallel.

Fixed node order:

```text
collect_sources
  -> build_product_cards
  -> build_claims
  -> check_citations
  -> build_report
  -> review_report
```

Agent role mapping:

```text
collect_sources       -> collector
build_product_cards   -> extractor
build_claims          -> analyst
check_citations       -> citation
build_report          -> writer
review_report         -> reviewer
```

## Output Artifacts

Default output directory:

```text
backend/app/data/runs/snapshot_online_education_demo/
```

Business artifacts:

- `sources.json`
- `evidence.json`
- `product_cards.json`
- `claims.json`
- `citation_checks.json`
- `reports.json`
- `review_feedback.json`

Workflow trace artifacts:

- `dag_nodes.json`
- `agent_runs.json`
- `tool_calls.json`
- `pipeline_summary.json`
- `eval_summary.json`

## Trace Semantics

Milestone 6 execution layers:

- `BaseAgent`: deterministic business logic wrapper for one role.
- `AgentRuntime`: owns `AgentRun` lifecycle, timestamps, duration, errors, and DAG output refs.
- `ToolRegistry`: owns internal tool registration and automatic `ToolCall` trace recording.
- `DAGExecutor`: keeps the fixed sequential DAG and stops on the first failed node.

`DAGNode` records workflow topology and node state:

- node id and label
- agent role
- input artifact refs
- output artifact refs
- status
- started/completed timestamps

`AgentRun` records the execution of one node:

- task id
- node id
- agent role
- status
- short input/output summaries
- started/completed timestamps
- duration
- error if failed

`ToolCall` records lightweight internal operations:

- `snapshot_collector.collect`
- `artifact_store.load_many`
- `artifact_store.save_many`
- `artifact_validator.check_refs`
- `citation_checker.check_claims`
- `review_checker.check_report`

Tool calls do not represent external APIs in this milestone. They are internal trace records for artifact IO, reference validation, citation checking, and review checking.

`pipeline_summary.json` records the final run summary:

- artifact counts
- DAG node count
- AgentRun count
- ToolCall count
- citation status counts
- review approval and score
- pipeline status

## Acceptance Criteria

A successful Milestone 6 run should satisfy:

- `check_workflow_trace.py` prints `PASS`.
- `check_run_artifacts.py` prints `PASS`.
- `harness/run_eval.py` prints `eval_passed=True`.
- `dag_nodes=6`.
- `agent_runs=6`.
- `reports=1`.
- `review_feedback=1`.
- `approved=True`.
- Final `pipeline_status=completed`.

Weak citations are allowed only when `review_feedback.json` contains a claim-level issue explaining the weak citation.

The current workflow still does not use LLMs, FastAPI, frontend code, automatic web search, crawlers, RAG, embeddings, Chroma, long-term memory, or an MCP server.
