# Framework Registry + Framework-aware ResearchTask v2 Gap Analysis

## 0. Scope and decision gate

This document covers Phase 0 only. It does not implement or enable any runtime
behavior.

In scope for the next implementation phase:

1. A versioned Framework Definition schema.
2. A Framework Registry as the only YAML access boundary.
3. A backward-compatible, framework-aware extension of the existing
   `ResearchTask` model.

Explicitly out of scope:

- Analyst Feedback Loop.
- automatic scheduling from `ResearchGap`.
- Harness iteration control.
- Intent Agent changes.
- dynamic questionnaires.
- a second task model, artifact chain, or pipeline.

## 1. Current related module structure

### 1.1 Intake and Research Brief

| Concern | Current implementation | Current shape |
| --- | --- | --- |
| Intent result | `backend/app/schemas.py::AnalysisTaskDraft` | Typed top-level intent fields, including `primary_target`, `comparison_targets`, `focus_areas`, and `competitor_discovery`. |
| Confirmed task | `backend/app/schemas.py::AnalysisTask` | Stable task identity and the legacy `competitors`/`focus_areas` inputs. |
| Research Brief | `backend/app/intake/service.py` | Not a standalone Pydantic schema or artifact. It is stored as `AnalysisTask.metadata["research_brief"]`. |
| Planning boundary | `backend/app/intake/research_planning.py::ResearchPlanningService` | Runs the existing Planner once, persists existing planning artifacts, then publishes `TaskRecord` entries. |

Because Intent Agent changes are out of scope, Phase 1 must not add framework
selection fields to the intake API or infer frameworks from user text. A
registry-declared default framework/version should be used by the Planner. An
explicit framework reference already supplied by trusted internal code may be
accepted later without changing intent behavior.

### 1.2 Planner

`backend/app/agents/research_planner.py::ResearchPlannerAgent` currently:

- selects `task.focus_areas` or the hard-coded `DEFAULT_DIMENSIONS`;
- creates one `KeyIntelligenceQuestion` and one `InformationNeed` per dimension;
- creates one existing `ResearchTask` per competitor and dimension;
- derives `research_intent` through
  `app.tools.router.research_intent_for_dimension`;
- hard-codes `required_facts`, preferred source types, query hints,
  comparability basis, and stop condition;
- persists `research_plans`, `research_kiqs`,
  `research_information_needs`, and `research_tasks` through `ArtifactStore`.

The Planner is therefore the primary Framework Registry integration point. The
framework should replace its hard-coded planning templates, not replace the
Planner, `InformationNeed`, or `ResearchTask`.

### 1.3 ResearchTask and its consumers

There is one canonical `ResearchTask` in `backend/app/schemas.py`. Its current
fields already cover most executable research instructions:

- task and information-need identity;
- title and objective;
- competitor and dimension;
- research intent;
- query hints and seed URLs;
- preferred domains and source types;
- priority and stop condition;
- dependency, collection-round, parent-task, and gap provenance.

It is consumed directly by:

- `ResearchAgentCoordinator` and Research Mission construction;
- `ResearchEvidenceAgent` and its action decider;
- Web collection and source-quality ranking;
- Source RAG query construction;
- evidence extraction and exact-quote verification;
- evidence-coverage calculation and current bounded supplement logic.

Several consumers normalize `ResearchTask.dimension` through the existing
`canonical_dimension`/`normalize_dimension` functions. Source ranking and
official-first/community behavior also contain hard-coded policies for the
current canonical dimensions. Phase 1 must therefore keep `dimension`
compatible with the existing evidence dimensions. Arbitrary framework-only
dimension names cannot safely replace it yet.

### 1.4 Research Agent input protocol

`backend/app/execution/research_agent.py` sends the model a structured artifact
bundle containing:

- the full serialized `ResearchTask`;
- the referenced `InformationNeed`;
- compact research state;
- at most four recent observations;
- filtered Mission context.

Adding typed framework provenance to the existing `ResearchTask` automatically
makes that provenance visible to the Research Agent without creating a new
input artifact. The executable instructions should still be materialized into
the existing `objective`, `research_intent`, `query_hints`,
`preferred_source_types`, and `stop_condition` fields so the Research Agent
does not need to read YAML or depend directly on the registry.

### 1.5 Evidence Pipeline

The current evidence-first chain is already framework-neutral:

```text
ResearchTask
  -> ResearchSourceCandidate
  -> SourceDocument / WebPageContent
  -> SourceChunk / SourceRetrievalRun
  -> SourceEvidence
  -> ProductCard / EvidenceCoverage
  -> AnalysisClaim / CitationCheck / CompetitiveReport
```

Evidence identity and verification depend on the existing task, competitor,
canonical dimension, source/page/chunk IDs, exact quote, offsets, and content
hash. Framework support does not require a new evidence artifact or a change to
the verification chain. Framework provenance can flow through the owning
`research_task_id`.

### 1.6 Harness Artifact and Agent Handoff

`ArtifactStore` already persists `research_tasks` as a registered artifact
type. `ResearchPipelineHarness` already includes `research_tasks` in Planning
outputs, checkpoints their IDs/count/content hash, and hands them to the
Research stage using the existing `AgentHandoff.artifact_refs` envelope.

Consequences for Phase 1:

- no `framework_research_tasks` artifact;
- no second handoff message type;
- no new Pipeline stage;
- no Harness loop changes;
- no change to Evidence Feed or final report flow;
- the existing `research_tasks` collection hash will automatically include the
  new framework provenance fields.

### 1.7 Existing framework-like definitions

Framework knowledge currently exists in three ungoverned forms:

1. hard-coded Planner defaults and templates;
2. `framework_selection` text embedded in the Analyst prompt YAML;
3. a hard-coded `DOMAIN_FRAMEWORK` MemoryItem in `context/memory.py`.

None of these is a versioned planning registry. Phase 1 should not try to
migrate Analyst prompt selection or Memory into the new registry; that would
expand scope beyond Research Planning. The new registry becomes the authority
only for framework-driven construction of planning artifacts in this phase.

## 2. Existing capabilities to reuse

| Existing capability | Reuse decision |
| --- | --- |
| Pydantic `SchemaModel` with `schema_version` and metadata | Use for Framework and Dimension definitions; do not introduce dataclass/dict-only domain models. |
| Single canonical `ResearchTask` | Extend it with optional typed framework provenance. |
| `KeyIntelligenceQuestion` | Populate its question and dimension from the selected Framework Dimension. |
| `InformationNeed` | Populate required facts, source preferences, comparability basis, and research intent from the Framework Dimension. |
| `ArtifactStore` | Keep all task/run outputs in existing artifact types. Framework definitions are application configuration, not per-task artifacts. |
| Prompt Registry pattern | Reuse version/status/path-safety/hash/validation ideas, while implementing a separate domain registry interface because frameworks are not prompts. |
| Research Agent structured input | Continue serializing the same ResearchTask and InformationNeed. |
| Source quality and canonical dimension routing | Preserve by requiring every Framework Dimension to map to one existing canonical evidence dimension. |
| Agent Handoff and checkpoints | No schema or lifecycle change required. |

## 3. Framework Registry integration points

### 3.1 Proposed package boundary

```text
backend/app/frameworks/
  __init__.py
  registry.py
  registry.json
  definitions/
    competitive_intelligence/
      1.0.0.yaml
```

Only `app.frameworks.registry` may use `yaml.safe_load` or access framework
definition files. Agent, intake, execution, retrieval, and collection code must
receive validated Pydantic objects through the Registry interface.

Required public interface:

```python
load_framework(framework_id, version)
get_dimension(framework_id, dimension_id)
```

Proposed deterministic interpretation:

- `load_framework(framework_id, version)` requires an exact registered
  version and returns a validated `FrameworkDefinition`;
- `get_dimension(framework_id, dimension_id)` reads from the registry-declared
  enabled/default version and returns a validated
  `FrameworkDimensionDefinition`;
- persisted `ResearchTask` instances must always pin the exact
  `framework_version`; they must never store `latest` or depend on an implicit
  default during execution.

The Registry should also enforce:

- framework ID/version in YAML match the registry entry;
- status is enabled unless candidate use is explicitly allowed by a test or
  experiment caller;
- resolved paths remain under the framework root;
- duplicate dimension IDs and aliases are rejected;
- all referenced default dimensions exist;
- source types and canonical evidence dimensions are valid;
- file content hash is returned with the validated definition;
- loaded definitions are immutable to callers or returned as defensive model
  copies.

### 3.2 Framework Definition schema

Minimal proposed models in the existing `backend/app/schemas.py`:

```text
FrameworkDefinition
  schema_version
  framework_id
  version
  name
  description
  default_dimension_ids[]
  dimensions[]
  metadata

FrameworkDimensionDefinition
  dimension_id
  label
  aliases[]
  evidence_dimension
  research_intent
  kiq_template
  objective_template
  required_facts[]
  query_templates[]
  preferred_source_types[]
  comparability_basis
  stop_condition
  priority
  metadata
```

`evidence_dimension` is deliberately separate from `dimension_id`. It maps a
framework-specific dimension onto the current downstream dimension contract,
for example:

```text
dimension_id: value_proposition
evidence_dimension: positioning
```

This lets planning become framework-aware without changing Evidence,
Coverage, source ranking, Research Agent policies, or Analyst schemas in this
phase.

Templates should support only a small deterministic token allowlist, initially
`{competitor}`, `{dimension_label}`, and `{decision_question}`. The Planner,
not the YAML loader, renders them. Unknown template tokens should fail planning
instead of silently leaking into a ResearchTask.

### 3.3 Planner integration

The Planner should receive a registry dependency or validated framework from
`ResearchPlanningService`; it must not construct file paths or parse YAML.

Minimal flow:

```text
ResearchPlanningService
  -> load exact default framework version through Framework Registry
  -> ResearchPlannerAgent receives FrameworkDefinition
  -> select default dimensions or filter them by existing task.focus_areas
  -> render KIQ + InformationNeed + ResearchTask using dimension definition
  -> persist the existing artifact collections
```

Because Intent changes are excluded, Phase 1 should use one configured default
framework, proposed as `competitive_intelligence@1.0.0`. Existing
`AnalysisTask.focus_areas` may filter dimensions by `dimension_id`, label,
alias, or `evidence_dimension`. Unknown requested focus areas should fail with
a clear planning error or remain on the legacy path; they must not be silently
invented by an LLM.

### 3.4 Research Agent and downstream integration

The Research Agent should not call the Registry. It receives a fully
materialized framework-aware ResearchTask and the existing InformationNeed.
This preserves deterministic replay if framework defaults later change.

Downstream code continues to use:

- `ResearchTask.dimension` as the canonical evidence dimension;
- `ResearchTask.research_intent` for research strategy;
- existing query hints/source preferences/stop condition;
- `research_task_id` for evidence and provenance linkage.

Framework IDs are provenance and audit fields in Phase 1, not a new source of
runtime branching throughout the system.

## 4. Minimal ResearchTask modification

### 4.1 Proposed fields

Extend the existing `ResearchTask`; do not create `ResearchTaskV2` as a second
class or artifact.

```text
framework_id: str = ""
framework_version: str = ""
framework_dimension_id: str = ""
framework_content_hash: str = ""
```

Rules:

1. Legacy stored tasks with `schema_version="v1"` and empty framework fields
   remain valid.
2. A framework-planned task is written with `schema_version="v2"`.
3. For a v2 framework-planned task, all four framework fields are required as
   a group.
4. `dimension` remains required and stores the mapped canonical
   `evidence_dimension` for downstream compatibility.
5. Existing executable fields remain authoritative snapshots of the planning
   decision. Runtime code must not reload YAML to reinterpret an already
   persisted task.
6. `framework_content_hash` binds the task to the exact validated file content
   used during planning.

The base `SchemaModel.schema_version` should not be globally changed. The
Planner should explicitly create framework-derived tasks with `v2`; this
avoids incorrectly labeling tasks created by legacy paths as framework-aware.

### 4.2 Child and supplement task inheritance

The current Research Mission code can create focused child ResearchTasks, and
the existing bounded coverage helper can create supplement tasks. Although
automatic ResearchGap scheduling is out of scope, these existing paths must
not drop framework provenance when their parent is already v2.

Minimal compatibility rule:

- child/supplement tasks copy `schema_version`, framework ID/version,
  framework dimension ID, and content hash from the latest owning parent task;
- no new framework selection occurs during Research execution;
- legacy parents continue producing legacy tasks.

This is propagation only, not implementation of a new feedback loop.

### 4.3 What does not change

- `research_tasks.json` remains the only ResearchTask artifact.
- ResearchTask IDs and existing parent/gap fields remain unchanged.
- `InformationNeed` remains a separate existing artifact.
- `ResearchAgentAction` and action types do not change.
- Evidence verification schemas do not change.
- Source ranking thresholds and official-first/freshness behavior do not
  change.
- Pipeline stages, Harness control flow, Handoff schema, and SSE do not change.

## 5. Expected implementation files

### New files

| File | Purpose |
| --- | --- |
| `backend/app/frameworks/__init__.py` | Export the Registry interface and validated definition types. |
| `backend/app/frameworks/registry.py` | The only YAML/file-loading boundary; version resolution, validation, path safety, and content hashing. |
| `backend/app/frameworks/registry.json` | Registered framework versions, status, path, and enabled/default version. |
| `backend/app/frameworks/definitions/competitive_intelligence/1.0.0.yaml` | Initial framework definition replacing the Planner's current hard-coded default planning template. |
| `backend/check_framework_registry.py` | Registry contract tests: exact load, default dimension lookup, invalid IDs/versions, path escape, YAML/schema mismatch, duplicate dimensions, immutability. |
| `backend/check_framework_research_task_v2.py` | Backward compatibility and Planner materialization tests. |

### Existing files expected to change

| File | Minimal change |
| --- | --- |
| `backend/app/schemas.py` | Add `FrameworkDefinition`, `FrameworkDimensionDefinition`, and optional framework provenance fields/validation to the existing `ResearchTask`. |
| `backend/app/intake/research_planning.py` | Resolve the configured framework through the Registry and inject the validated definition into the Planner. |
| `backend/app/agents/research_planner.py` | Replace hard-coded default dimension planning templates with validated Framework Dimension data; continue producing existing artifacts. |
| `backend/app/execution/research_mission.py` | Propagate v2 framework provenance to an existing Mission-created child ResearchTask. |
| `backend/app/intake/step6e4.py` | Propagate v2 framework provenance in the existing bounded supplement-task path only; do not add new scheduling behavior. |

### Files explicitly not expected to change

- `backend/app/harness/pipeline.py`
- `backend/app/harness/protocol.py`
- `backend/app/harness/artifacts.py`
- Evidence schemas and verifier code
- Research Agent action schema and action loop
- Analyst, Writer, Reviewer, and Quality Gate code
- API intake/request schemas
- frontend code

## 6. Phase 1 acceptance criteria

1. Business/Agent code contains no direct YAML read for framework definitions.
2. `load_framework(id, version)` returns the exact validated version and rejects
   unknown, disabled, mismatched, or path-escaping definitions.
3. `get_dimension(id, dimension_id)` resolves through the registered default
   version and rejects unknown dimensions.
4. Legacy v1 ResearchTask JSON continues to parse unchanged.
5. Framework-derived ResearchTasks use the same model/artifact and include the
   four pinned provenance fields.
6. Planner outputs remain `ResearchPlan`, `KeyIntelligenceQuestion`,
   `InformationNeed`, and `ResearchTask`; no duplicate artifacts are created.
7. Research Agent can consume the v2 task without changes to its action
   protocol.
8. Existing Evidence Pipeline, official-first/freshness ranking, Handoff,
   checkpoint, and reporting tests remain green.
9. Existing child/supplement creation preserves framework provenance but no new
   Analyst/Gap/Harness iteration is activated.

## 7. Confirmation decisions before coding

Phase 1 can proceed with the following defaults unless changed during review:

1. Initial framework ID/version:
   `competitive_intelligence@1.0.0`.
2. `get_dimension(framework_id, dimension_id)` resolves the registry-declared
   enabled/default version; persisted tasks always pin the exact version.
3. Framework-specific dimensions map to existing canonical evidence dimensions
   through `evidence_dimension`.
4. Only framework-planned ResearchTasks are marked `schema_version="v2"`;
   legacy paths remain readable and child tasks inherit their parent version.
5. Framework definitions are configuration loaded through the Registry, not a
   new task ArtifactStore collection.

