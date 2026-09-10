# Professional Analyst Feedback Foundation — Implementation Report

## Outcome

The existing Professional Analyst now performs one Framework-aware assessment
stage between brief/profile generation and claim generation:

```text
FrameworkRegistry + ResearchTask v1/v2 + Verified Evidence
                         |
                         v
Existing LLMProfessionalAnalystAgent
  1. AnalystBriefProfilesStage
  2. AnalystAssessmentStage
  3. AnalystClaimsStage
                         |
                         v
AnalysisAssessment + canonical ResearchGap merge
                         |
                         v
Existing Citation -> Writer -> Reviewer pipeline
```

The implementation does not create another Analyst, Evidence model,
ResearchTask model, Pipeline, or automatic supplement loop.

## Architecture changes

### Professional assessment boundary

- The existing `LLMProfessionalAnalystAgent` receives the pinned
  `FrameworkDefinition`, current `ResearchTask` records and only the Evidence
  already authorized by the Research Agent.
- No Analyst model stage receives `SourceDocument`, `WebPage`, `SourceChunk`,
  search results, `ProductCard`, or raw page content. A fail-closed LLM
  boundary applies a per-stage artifact allowlist and rejects nested
  raw-content fields before the provider can be called. `SourceDocument` is
  retained only on the Python side for provenance validation.
- The LLM emits semantic draft fields only. Python owns IDs, Framework
  provenance, evidence-batch identity, per-dimension scores and overall status.
- Validation requires exact competitor/dimension scope coverage, an exact
  partition of Framework `required_facts` and `completion_criteria`, and valid
  Evidence IDs with matching competitor and evidence dimension.
- Assessment insight and gap references are checked against the same scope and
  Evidence allowlist.

### Artifacts

- Added appendable `analysis_assessments` containing the durable
  `AnalysisAssessment` history.
- Extended the existing `ResearchGap` with defaulted `gap_type`, `impact`,
  `origin`, Framework provenance, assessment provenance and related
  ResearchTask IDs. Existing JSON remains valid.
- Upstream deterministic/Research Agent gaps remain canonical and immutable;
  Professional Analyst gaps are merged by stable ID into `research_gaps` and
  mirrored to the existing `analysis_research_gaps` compatibility projection.
- `EvidenceCoverage` remains the deterministic upstream projection and is not
  overwritten by the Analyst.

### Harness and handoff

- Research-to-Analyst handoff now explicitly includes `research_tasks`.
- The Pipeline run ID is propagated into `AnalysisAssessment.pipeline_id`.
- Analysis handoffs and checkpoints include `analysis_assessments` and the
  canonical `research_gaps` reference/hash.
- The Harness remains linear. It records the Analyst decision state but does
  not branch back to Research or materialize supplement tasks in this phase.

### Reliability and cost bounds

- Analyst execution is three bounded schema stages, normally three LLM calls
  and at most six when stage-level truncation retry is required.
- A matching `(pipeline_id, framework_content_hash, evidence_batch_hash)`
  assessment is reused, preventing duplicate assessment charging after a
  resumable partial failure.
- Claim validation and CitationAgent behavior are unchanged.

## Files changed for this foundation

- `backend/app/schemas.py`
- `backend/app/analysis_assessment.py`
- `backend/app/agents/llm_snapshot.py`
- `backend/app/execution/research_analysis.py`
- `backend/app/llm/client.py`
- `backend/app/llm/provider.py`
- `backend/app/llm/language.py`
- `backend/app/prompts/competitive_analysis_assessment/v1.yaml`
- `backend/app/prompts/registry.json`
- `backend/app/harness/artifacts.py`
- `backend/app/harness/pipeline.py`
- `backend/app/api/main.py`
- `backend/check_professional_analysis_assessment.py`
- `backend/check_step6f_analyst_output_stability.py`
- `backend/check_unified_pipeline_harness.py`
- `docs/professional_analyst_feedback_foundation_design.md`

## Verification

Passed without network or real LLM calls:

- `check_professional_analysis_assessment.py`
  - complete Evidence -> `SUFFICIENT`, coverage `1.0`, no gap;
  - missing user-feedback facts -> `PARTIAL` plus a high-impact gap;
  - verified Evidence from the wrong Framework dimension -> `INSUFFICIENT`;
  - invalid cross-dimension Evidence reference is rejected;
  - legacy `ResearchGap` remains parseable.
- `check_step6f_analyst_output_stability.py`
  - three bounded Analyst stages;
  - evidence allowlist, central structured retry and truncation retry remain
    enforced;
  - upstream EvidenceCoverage and ResearchGap entries remain preserved.
- `check_unified_pipeline_harness.py`
  - linear stage order, stop/resume, handoff and checkpoint behavior pass;
  - assessment and ResearchTask references survive the relevant boundaries.
- `check_framework_registry.py`
- `check_framework_planning_v2.py`
- `check_research_task_v2_backward_compat.py`
- `check_step6c_evidence_alignment.py`
- `check_step6c_writer.py`
- targeted Ruff and Python compile checks.

The historical default fixture used by `check_step6c_artifacts.py` still reports
a stored `prompt_hash` mismatch because that persisted run was generated with
an older prompt snapshot. The current evidence-alignment and Writer regressions
pass; no historical run artifact was rewritten to conceal the mismatch.

## Deferred Analyst loop controller

The next phase may add a Harness Feedback Controller that reads the latest
`AnalysisAssessment` at the analyzing checkpoint and makes a bounded decision:

```text
SUFFICIENT -> Citation/Writer
PARTIAL or INSUFFICIENT + budget available -> materialize supplement ResearchTask
PARTIAL or INSUFFICIENT + no budget -> continue with explicit limitations
```

That controller must own iteration limits, idempotent gap-to-task conversion,
checkpoint/resume and stop conditions. It should not move search or tool calls
into the Analyst.
