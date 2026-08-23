# Snapshot Pipeline Runbook

This runbook describes the current deterministic local snapshot pipeline.

The pipeline does not use LLMs, FastAPI, frontend code, automatic crawlers, DAGExecutor, AgentRun, or ToolCall tracing.

## Inputs

Snapshot input files:

- `backend/app/data/snapshots/online_education/sources.json`
- `backend/app/data/snapshots/online_education/evidence.json`

The input records must validate through the current schemas:

- `SourceDocument`
- `SourceEvidence`

Every `SourceEvidence.source_id` must resolve to a `SourceDocument.id`.

## Command

Run from `competitive-intel-agents/backend`.

If `python` is available in the shell:

```powershell
python .\run_snapshot_pipeline_demo.py
python .\check_run_artifacts.py
```

In the current workspace, a known working Python environment is available at:

```powershell
..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe
```

Use it like this:

```powershell
& "..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe" .\run_snapshot_pipeline_demo.py
& "..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe" .\check_run_artifacts.py
```

Optional arguments:

```powershell
python .\run_snapshot_pipeline_demo.py --task-id snapshot_online_education_demo --snapshot-id online_education
python .\check_run_artifacts.py --task-id snapshot_online_education_demo
```

## Pipeline Steps

`run_snapshot_pipeline_demo.py` executes these deterministic steps in order:

1. Collect snapshot through `LocalSnapshotCollector`.
2. Save `sources` and `evidence` through `ArtifactStore`.
3. Build `product_cards`.
4. Build rule-based `claims`.
5. Run citation checks.
6. Build one `CompetitiveReport`.
7. Build one `ReviewFeedback`.

If any step fails, the pipeline stops immediately and prints the failed step plus the exception type and message.

## Output Artifacts

Default output directory:

```text
backend/app/data/runs/snapshot_online_education_demo/
```

Expected files:

- `sources.json`
- `evidence.json`
- `product_cards.json`
- `claims.json`
- `citation_checks.json`
- `reports.json`
- `review_feedback.json`

## Pipeline Summary

The pipeline prints:

- `sources_count`
- `evidence_count`
- `product_cards_count`
- `claims_count`
- `citation_checks_count`
- `reports_count`
- `review_feedback_count`
- `supported_count`
- `weak_count`
- `approved`
- `review_score`

## Validation Harness

`check_run_artifacts.py` does not generate business artifacts. It only validates an existing run.

It checks:

- Required artifact files exist and are non-empty JSON lists.
- All records validate through the current schema classes.
- `evidence.source_id` resolves to `source.id`.
- `product_cards.source_ids` and `product_cards.evidence_ids` resolve to real sources/evidence.
- `claims.evidence_ids` resolve to real evidence.
- `citation_checks.claim_id` resolves to real claims.
- `report.claim_ids` resolve to real claims.
- `report.markdown` contains every claim id as `[cl_xxx]`.
- `review_feedback` explains every weak, missing-evidence, or invalid-evidence citation with a claim-level issue.

## Passing Standard

A passing local run should satisfy:

- `check_run_artifacts.py` prints `PASS`.
- Required artifact count is present for the full chain.
- There are no broken references across:

```text
SourceDocument
  -> SourceEvidence
  -> ProductCard
  -> AnalysisClaim
  -> CitationCheck
  -> CompetitiveReport
  -> ReviewFeedback
```

Weak citations are allowed only if `ReviewFeedback` contains an explanatory issue for the affected claim.
