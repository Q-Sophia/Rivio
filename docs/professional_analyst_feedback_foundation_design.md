# Professional Analyst Feedback Foundation — Design Proposal

## 0. Scope and confirmation gate

This document records the pre-implementation design gate that was completed
before code changes. Implementation status and verification results are kept
in `PROFESSIONAL_ANALYST_FEEDBACK_IMPLEMENTATION_REPORT.md`.

This task should implement only:

1. Framework-aware Professional Analyst assessment;
2. a new `AnalysisAssessment` artifact;
3. a backward-compatible extension of the existing `ResearchGap`;
4. persistence, validation, trace and handoff visibility for those artifacts.

It should not yet implement:

- Harness conditional routing from Analyst back to Research;
- automatic conversion of Analyst gaps into supplement `ResearchTask` items;
- another Pipeline or Evidence model;
- an unbounded research/analysis loop.

## 1. Current implementation found in code

### 1.1 Existing Analyst

The project already has a real Professional Analyst path; a second Analyst
must not be introduced.

```text
ResearchAnalysisService.run_once
  -> LLMProfessionalAnalystAgent
       -> bounded stage A: AnalystBriefProfilesStage
       -> bounded stage B: AnalystClaimsStage
       -> deterministic CompetitiveAnalysisPortfolioV2 assembly
  -> CitationAgent
```

Relevant code:

- `backend/app/execution/research_analysis.py::ResearchAnalysisService`
- `backend/app/agents/llm_snapshot.py::LLMProfessionalAnalystAgent`
- `backend/app/prompts/competitive_analyst/v2.yaml`

The current agent is evidence-grounded and already enforces an allowlist of
Evidence IDs verified by Research Agent runs. It also validates Source ->
Evidence and Claim -> Evidence references.

However, in the current research-analysis path it does **not** evaluate the
Framework:

- it does not load `FrameworkDefinition`;
- its bounded LLM stages are explicitly told not to generate
  `EvidenceCoverage` or `ResearchGap`;
- `analysis_evidence_coverage` and `analysis_research_gaps` are copied from the
  deterministic upstream artifacts rather than produced by the Analyst;
- the analysis completion flag means that an analysis portfolio exists, not
  that Framework requirements are sufficient.

Therefore the existing class is a professional claim/portfolio generator, but
not yet a Framework-aware feedback evaluator.

### 1.2 Existing ResearchTask and Framework provenance

There is one `ResearchTask` schema. Framework-planned v2 tasks already carry:

- `framework_id`;
- `framework_version`;
- `framework_dimension_id`;
- `framework_content_hash`.

`ResearchTask.dimension` remains the canonical Evidence dimension. This is the
correct input boundary for an Analyst assessment: group Evidence by competitor
and `framework_dimension_id`, but keep canonical `dimension` for existing
Evidence and source-quality behavior.

### 1.3 Existing EvidenceCoverage

`backend/app/schemas.py::EvidenceCoverage` and
`backend/app/intake/step6e4.py::build_evidence_coverage` already provide a
deterministic technical coverage projection:

- per competitor and canonical dimension;
- missing/partial/weak/conflicting/sufficient status;
- Source and Evidence IDs;
- simple quantity/source-strength rules.

This is useful pre-analysis input, but it cannot answer the new professional
questions:

- which exact Framework required facts are covered;
- whether a missing fact changes the user's decision;
- whether Framework completion criteria are met;
- whether cross-competitor comparison is possible;
- whether the evidence mix is appropriate for the dimension.

It should remain as a deterministic signal and should not be stretched into
the new semantic assessment model.

### 1.4 Existing ResearchGap

`backend/app/schemas.py::ResearchGap` already exists and is used by:

- deterministic Step6E4 coverage refresh;
- Research Agent PARTIAL/EXHAUSTED bridge;
- Mission and bounded supplement-task creation;
- Analyst portfolio/report disclosure.

The current model already has competitors, dimension, missing-information
summary, decision impact text, insufficiency reason, suggested queries,
preferred source types, priority, stop condition and related Evidence IDs.

It does not yet have:

- a typed gap category;
- a structured missing-facts list;
- impact severity;
- Framework/assessment provenance;
- an explicit loop ownership/origin marker.

The correct change is to extend this model with optional/defaulted fields. A
second `AnalystResearchGap` class must not be created.

### 1.5 Existing collection loops

Three behaviors must be distinguished before adding the future outer loop:

```text
Level 1 — ResearchTask inner loop (keep)
  ResearchEvidenceAgent: decide -> SEARCH/FETCH/READ/SUBMIT -> observe
  Stops on task outcome or per-task action/search/source/failure budget.

Level 2 — current Coordinator coverage loop (already exists)
  ResearchAgentCoordinator -> Step6E4 deterministic coverage
  -> Mission Supervisor / supplement ResearchTask -> next collection_round.

Level 3 — desired Analyst feedback loop (future task)
  completed research batch -> Framework-aware assessment
  -> Harness decision -> supplement research batch -> reassessment.
```

Directly enabling Level 3 without an ownership rule would create overlapping
cross-task supplement schedulers. This foundation should add provenance fields
needed to distinguish the loops, but should not activate Level 3.

Recommended ownership for the future:

- Research Agent owns only actions inside one ResearchTask.
- ResearchAgentCoordinator owns execution/failure isolation of one research
  batch. Existing Step6E4/Mission supplements are treated as bounded technical
  completion inside that batch and may not start a new Analyst cycle.
- Pipeline Harness alone owns the outer Analyst -> Research transition.
- Only a Harness materializer may convert a Professional Analyst gap into a
  new supplement ResearchTask.

For Framework-aware Pipeline runs, a later implementation should either cap
the current Coordinator cross-task supplement phase to one bounded batch or
explicitly account for it inside the outer-cycle budget. Otherwise the current
`max_collection_rounds` will multiply with the future
`max_analysis_iterations`, producing an unexpectedly large cost surface.

### 1.6 Existing ArtifactStore, handoff and checkpoint

`ArtifactStore` is the existing cross-agent source of truth. The Harness uses
`ArtifactReference` values containing IDs, count and collection content hash;
handoff messages carry references rather than copying payloads.

The current Harness flow is linear:

```text
planning -> researching -> analyzing -> reporting -> completed
```

It is linear. It does not inspect an Analyst decision before reporting.
`PipelineCheckpoint` records completed stage names and artifact hashes, but
does not yet record assessment iteration, evidence-batch identity or the next
conditional action.

The protocol already defines `HandoffMessageType.FEEDBACK`, but the current
`_handoff()` helper always emits the default artifact-handoff type. This is a
ready extension point for the later control-loop task.

## 2. Gap between current and target design

| Target capability | Current implementation | Required foundation change |
| --- | --- | --- |
| Framework is the analysis standard | Planner uses Framework; Analyst does not | Resolve the exact Framework pinned by ResearchTask v2 and pass a validated definition to the existing Analyst. |
| Required-fact coverage | Only dimension-level count/strength projection | Add per-Framework-dimension, per-competitor fact assessment. |
| Professional decision impact | Free-form gap text and claim uncertainty | Add typed impact plus concise evidence-grounded reasoning. |
| AnalysisAssessment | Does not exist | Add one canonical artifact and Pydantic model. |
| Typed ResearchGap | Existing untyped model | Extend existing ResearchGap with backward-compatible fields. |
| Analyst-generated gaps | Analyst currently copies upstream gaps | Generate validated Analyst gaps and merge into canonical `research_gaps` without overwriting upstream entries. |
| Batch idempotency | Analysis is one-shot by portfolio existence | Bind assessment to Framework hash and Evidence batch hash. |
| Harness decision | Linear transition to report | Not implemented now; expose a machine-readable decision signal for the next phase. |

## 3. Proposed schemas

All schemas should remain in `backend/app/schemas.py` to follow the current
artifact convention.

### 3.1 Enums

```python
class AnalysisAssessmentStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"

class DimensionAssessmentStatus(str, Enum):
    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"

class ResearchGapType(str, Enum):
    MISSING_FACT = "missing_fact"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MISSING_SOURCE_TYPE = "missing_source_type"
    MISSING_COMPARISON = "missing_comparison"
    DECISION_BLOCKING = "decision_blocking"

class ResearchGapImpact(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ResearchGapOrigin(str, Enum):
    RESEARCH_AGENT = "research_agent"
    DETERMINISTIC_COVERAGE = "deterministic_coverage"
    PROFESSIONAL_ANALYST = "professional_analyst"
```

### 3.2 DimensionAssessment

```python
class DimensionAssessment(SchemaModel):
    id: str
    task_id: str
    dimension_id: str                 # Framework dimension ID
    evidence_dimension: str           # existing canonical dimension
    competitor: str
    status: DimensionAssessmentStatus
    coverage_score: float             # 0..1
    covered_facts: list[str]           # exact Framework required_facts values
    missing_facts: list[str]           # exact Framework required_facts values
    evidence_ids: list[str]
    completion_criteria_met: list[str]
    completion_criteria_unmet: list[str]
    reasoning: str                     # concise auditable summary, not hidden CoT
```

Assessments are per competitor and Framework dimension. A dimension-only
aggregate would incorrectly mark a comparison covered when only one competitor
has Evidence.

Validation invariants:

- `covered_facts` and `missing_facts` are disjoint;
- together they cover every required fact for the selected Framework dimension;
- fact and criterion strings must exactly match the loaded Framework;
- all `evidence_ids` belong to the current verified Evidence allowlist;
- a `COVERED` result cannot have missing facts or unmet completion criteria;
- `MISSING` cannot cite Evidence as support;
- no competitor/dimension pair appears twice.

### 3.3 AssessmentInsight

```python
class AssessmentInsight(SchemaModel):
    summary: str
    dimension_id: str
    competitors: list[str]
    evidence_ids: list[str]
    confidence: float
    decision_impact: str
```

These are assessment-level observations, not report claims. They must remain
Evidence-bound and must not bypass `AnalysisClaimV2` or CitationAgent.

### 3.4 AnalysisAssessment

```python
class AnalysisAssessment(SchemaModel):
    id: str
    pipeline_id: str
    task_id: str
    assessment_round: int
    framework_id: str
    framework_version: str
    framework_content_hash: str
    evidence_batch_hash: str
    evidence_ids: list[str]
    overall_status: AnalysisAssessmentStatus
    coverage_score: float
    dimension_assessments: list[DimensionAssessment]
    insights: list[AssessmentInsight]
    research_gaps: list[ResearchGap]
    analyst_agent_run_id: str
    created_at: datetime
```

The additional batch fields are necessary for future corrective retrieval:

- `evidence_batch_hash` makes the assessment idempotent for unchanged input;
- `assessment_round` separates outer Analyst cycles from Research Agent
  `collection_round`;
- `evidence_ids` makes the exact assessed batch auditable;
- `pipeline_id` links a Harness run when invoked by the Pipeline. A manual
  analysis entry may use an explicit standalone analysis-run ID.

`coverage_score` should be computed by application code from the validated
per-dimension fact assignments, rather than trusted as an unconstrained LLM
number. Professional judgment remains in fact coverage, completion-criteria
decisions and gap impact.

Suggested deterministic overall rules:

- `SUFFICIENT`: every in-scope pair is COVERED/NOT_APPLICABLE, every Framework
  required fact is assigned, completion criteria are met, and no high/critical
  decision-blocking gap remains;
- `PARTIAL`: at least one relevant fact is supported, but an actionable gap
  remains;
- `INSUFFICIENT`: no in-scope pair has usable support, or a critical gap makes
  the requested comparison impossible.

### 3.5 Extend the existing ResearchGap

Keep every current field and add defaulted fields:

```python
gap_type: ResearchGapType = ResearchGapType.MISSING_FACT
impact: ResearchGapImpact = ResearchGapImpact.MEDIUM
origin: ResearchGapOrigin = ResearchGapOrigin.DETERMINISTIC_COVERAGE
missing_facts: list[str] = []
assessment_id: str = ""
framework_id: str = ""
framework_version: str = ""
framework_dimension_id: str = ""
framework_content_hash: str = ""
research_task_ids: list[str] = []
blocks_decision: bool = False
```

Mapping existing fields instead of duplicating them:

- current `preferred_source_types` is the canonical equivalent of
  `suggested_sources`;
- current `suggested_queries` remains the retrieval hint list;
- current `missing_information` remains the readable summary;
- new `missing_facts` supplies the structured list requested by the feedback
  loop;
- current `decision_blocked` remains the readable impact explanation;
- new `blocks_decision` is the machine-readable decision flag.

Legacy ResearchGap JSON remains valid because all additions have defaults.

## 4. Framework-aware Analyst flow

### 4.1 Framework resolution

`ResearchAnalysisService` should receive `FrameworkRegistry` by dependency
injection, like `ResearchPlanningService`.

Resolution rules:

1. Read all in-scope ResearchTasks.
2. For v2 tasks, require one consistent framework ID/version/hash per
   assessment batch.
3. Call `load_framework(framework_id, framework_version)`; never read YAML.
4. Verify the loaded content hash equals the pinned task hash.
5. Resolve scope from each task's `framework_dimension_id` and competitor.
6. For legacy v1 tasks, use the configured default Framework in an explicit
   `legacy_default` binding mode; do not mutate old ResearchTasks.

### 4.2 Evidence bundle

Reuse only the existing verified Evidence authority:

```text
ResearchAgentRun.verified_evidence_ids
  -> SourceEvidence allowlist
  -> SourceDocument provenance/source type/reliability
  -> group by competitor + evidence_dimension
  -> compact AssessmentEvidenceBundle
```

The bundle should contain IDs and compact fields needed for judgment, not full
web pages or conversation history. This keeps the Analyst context bounded and
preserves Source/Evidence references.

### 4.3 Existing Professional Analyst extension

Extend `LLMProfessionalAnalystAgent`; do not add a parallel Analyst system.
Add one bounded structured assessment stage before portfolio assembly:

```text
Stage A: BriefAssessment + CompetitorProfile
Stage B: Framework-based AnalysisAssessment draft
Stage C: ComparabilityNote + evidence-grounded AnalysisClaimV2
Python: validate references, compute scores/status, merge gaps, assemble portfolio
```

The assessment stage receives:

- validated Framework Definition;
- current ResearchTasks;
- compact verified Evidence bundle;
- deterministic EvidenceCoverage as a signal, not as the final decision;
- the user's decision question and constraints.

The LLM may decide whether a required fact/criterion is supported and explain
decision impact. Application code must validate every ID and Framework value,
compute the final scores/status, and reject malformed output.

This changes the expected bounded Analyst call budget from 2–4 calls to 3–6
when the final claims path is also executed. The exact authorization message,
test expectations and trace metadata must be updated. The future outer-loop
implementation can optimize cost by running Stage C only after a final
SUFFICIENT/terminal assessment.

## 5. Artifact authority and data flow

### 5.1 Canonical artifacts

```text
sources / evidence                 existing, unchanged
research_tasks                     existing, unchanged
evidence_coverage                  existing deterministic projection
analysis_assessments               NEW canonical append-only assessment history
research_gaps                      existing canonical merged gap collection
analysis_portfolios / claims_v2    existing final-analysis artifacts
```

Add `analysis_assessments` to `ArtifactStore.DEFAULT_ARTIFACT_TYPES`.

`analysis_research_gaps` currently exists. It should remain a compatibility
projection during migration, not become a second source of truth. New Analyst
gaps should be merged into canonical `research_gaps` with deterministic IDs and
also referenced by `AnalysisAssessment.research_gaps`. Existing gap IDs and
payloads must not be overwritten.

### 5.2 Foundation data flow

```text
FrameworkRegistry.load_framework(exact pinned version)
             |
ResearchTask v2 + verified Evidence + Source metadata
             |
             v
LLMProfessionalAnalystAgent assessment stage
             |
             v
schema/reference/framework validation
             |
             +--> analysis_assessments (append-only)
             +--> research_gaps (merge new professional gaps)
             +--> analysis_research_gaps (compatibility projection, if retained)
             |
             v
existing claims -> CitationAgent -> current report path
```

The current task does not branch back to Research. A PARTIAL/INSUFFICIENT
assessment is persisted and exposed, while the current linear Pipeline remains
compatible until the next Harness-loop task.

### 5.3 Idempotency

Use a stable assessment identity derived from:

```text
pipeline_id + assessment_round + framework_content_hash + evidence_batch_hash
```

If the same Evidence batch and Framework are assessed again for the same
round, return the existing artifact and do not call the LLM again. Analyst gap
IDs should derive from assessment ID, competitor, dimension and gap type.

## 6. Handoff and checkpoint integration

Foundation-only Harness changes should be metadata/output visibility, not
control flow:

- add `analysis_assessments` to `ANALYSIS_OUTPUTS`;
- include `research_tasks` in the Research -> Analyst artifact references so
  Framework provenance is explicit;
- pass the current `PipelineRun.id` into `ResearchAnalysisService.run_once` as
  `pipeline_id`;
- include assessment ID/status/round and Evidence batch hash in the analyzing
  checkpoint state or checkpoint metadata;
- optionally emit a `FEEDBACK` handoff from Professional Analyst to the Harness
  containing only artifact references and a machine-readable status.

Do not add a conditional edge in this task. The next phase should make the
Harness the only owner of:

```text
SUFFICIENT -> Citation/Writer
PARTIAL or INSUFFICIENT + actionable gaps + budget -> materialize supplement tasks
terminal gap/no budget/no progress -> Citation/Writer with disclosed limitations
```

## 7. Planned code changes after confirmation

### New files

| File | Purpose |
| --- | --- |
| `backend/app/prompts/competitive_analysis_assessment/v1.yaml` | Versioned bounded assessment-stage prompt loaded through PromptRegistry. |
| `backend/check_professional_analysis_assessment.py` | Required SUFFICIENT/PARTIAL/INSUFFICIENT cases and validation tests with a deterministic fake LLM provider. |

### Existing files to extend

| File | Change |
| --- | --- |
| `backend/app/schemas.py` | Add assessment enums/models and backward-compatible ResearchGap fields. |
| `backend/app/prompts/registry.json` | Register the assessment-stage prompt/schema contract. |
| `backend/app/agents/llm_snapshot.py` | Add Framework-aware assessment stage to existing Professional Analyst, validate and persist assessment/gaps. |
| `backend/app/execution/research_analysis.py` | Resolve pinned Framework, build Evidence batch identity, pass pipeline context, expose assessment payload, protect upstream gap entries. |
| `backend/app/harness/artifacts.py` | Register `analysis_assessments`. |
| `backend/app/harness/pipeline.py` | Include assessment in handoff/checkpoint outputs and pass pipeline ID; no conditional loop. |
| `backend/check_step6f_analyst_output_stability.py` | Update bounded stage/call assertions and verify upstream artifacts are not overwritten. |
| `backend/check_unified_pipeline_harness.py` | Verify assessment references survive handoff/checkpoint while the stage order remains linear. |

No changes are planned to Research Agent actions, Evidence schemas, Citation
validation, Writer/Reviewer logic, Framework YAML loading, or supplement task
materialization.

## 8. Test and acceptance plan

### Required cases

1. **Complete Evidence**
   - every selected Framework required fact is covered for every competitor;
   - completion criteria are met;
   - output is `SUFFICIENT`, score is deterministic, no actionable gap.

2. **Missing customer feedback**
   - official/news Evidence exists but required real user experience facts are
     absent;
   - output is `PARTIAL`;
   - creates a `missing_fact` or `missing_source_type` ResearchGap with high
     impact when it affects the decision;
   - suggested source types use the existing `preferred_source_types` field.

3. **Evidence does not satisfy Framework**
   - Evidence is verified but belongs to the wrong dimension/competitor or
     cannot support any required fact;
   - output is `INSUFFICIENT` or `PARTIAL` according to whether any usable fact
     remains;
   - invalid Evidence IDs or invented Framework facts are rejected.

### Compatibility and regression

- legacy ResearchGap JSON parses unchanged;
- current `evidence_coverage` entries remain byte-for-byte unchanged;
- current Research Agent and Evidence verification tests remain green;
- CitationAgent still validates Claims only and cannot cite assessment insights
  as report facts;
- report generation remains operational on the current linear Harness;
- Harness checkpoint/resume and stop behavior remain green;
- no real network or LLM calls in foundation tests.

## 9. Reference-project ideas adopted

- **LangGraph:** model assessment as an explicit durable state update and make
  conditional routing a Harness responsibility at a checkpoint boundary.
- **Self-RAG:** separate retrieval from critique; express critique as validated
  typed signals that can later trigger corrective retrieval.
- **STORM:** use explicit Framework dimensions as controlled perspectives for
  coverage and gap discovery rather than letting the model invent an unlimited
  set of research directions.

These are design ideas only. No external framework dependency or copied code is
required.

## 10. Implementation baseline used

Recommended implementation baseline:

1. extend the existing `LLMProfessionalAnalystAgent` with one dedicated bounded
   assessment stage;
2. add one canonical append-only `analysis_assessments` artifact;
3. extend the existing `ResearchGap` and keep `research_gaps` canonical;
4. expose assessment references through current handoff/checkpoint plumbing;
5. leave Analyst -> Research conditional routing and supplement-task
   materialization for the next task;
6. before enabling that future loop, make the Harness the sole outer-loop owner
   and explicitly budget the current Coordinator coverage rounds.

This bounded baseline was used for the implementation; automatic gap-driven
supplement scheduling remains explicitly out of scope.
