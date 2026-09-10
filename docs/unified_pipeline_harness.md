# Unified Multi-Agent Pipeline Harness

## Runtime shape

```text
AnalysisTask
  -> ResearchPipelineHarness (control plane, not an Agent)
      -> ResearchPlannerAgent
      -> ResearchAgentCoordinator
          -> ResearchEvidenceAgent
          -> Mission Supervisor (bounded research controller)
      -> LLMProfessionalAnalystAgent
      -> CitationAgent
      -> LLMProfessionalWriterAgent
      -> ReviewerAgent
      -> QualityGate (deterministic controller)
```

The Harness owns lifecycle, stage transitions, event projection, cooperative
stop, checkpoint/resume and versioned handoff envelopes. It does not search,
collect, extract evidence, analyze claims or write reports itself.

The evidence-first chain is unchanged:

```text
SourceDocument -> SourceEvidence -> ProductCard -> AnalysisClaim
-> CitationCheck -> CompetitiveReport -> ReviewFeedback
```

## Persisted control artifacts

- `pipeline_runs`: one durable top-level lifecycle record per analysis task.
- `pipeline_events`: one ordered event stream, including forwarded Research
  Coordinator events and `evidence_added` events.
- `pipeline_checkpoints`: stage-boundary snapshots containing ArtifactStore
  collection hashes and stable artifact IDs.
- `agent_handoffs`: protocol `1.0` JSON envelopes containing sender, recipient,
  correlation/idempotency IDs and ArtifactStore references.

Handoffs carry references instead of copying source/evidence bodies. This keeps
ArtifactStore as the cross-agent source of truth and preserves `source_id`,
`evidence_id` and `claim_id` provenance.

## Automatic path

```text
planning -> researching -> analyzing (+ citation) -> reporting
(+ reviewer + quality gate) -> completed
```

The existing manual Research, Analyst and Reporting endpoints remain available
as compatibility and recovery boundaries. The new-analysis page starts the
automatic pipeline through `/api/analysis-tasks/{task_id}/pipeline/run`.

## Stop and recovery

- During Research, a stop request is forwarded to the existing Research Agent
  Coordinator and takes effect at its safe ResearchTask boundary.
- During a synchronous Analyst or Writer LLM call, the in-flight call is not
  force-killed. The Harness stops before the next stage and preserves outputs
  that were saved successfully.
- A process-lost active run becomes `interrupted`, not silently `failed`.
- Resubmission uses the same `pipeline_run_id`, increments `resume_count`, skips
  completed stages and continues from the last persisted checkpoint.
- Handoff `idempotency_key` prevents duplicate logical stage handoffs on resume.

## Current bounded scope

This milestone automates the existing static evidence-first workflow. It does
not yet rerun the professional Analyst after `analysis_research_gaps`. The
future Analyst -> Research feedback loop must add an explicit iteration state,
retry/source budget and stop condition before it is enabled.

## Reference patterns

The implementation borrows patterns rather than adding a framework dependency:

- LangGraph: durable checkpoints and replay from stage boundaries.
- OpenAI Agents SDK: manager/handoff separation, typed handoff input and trace
  correlation.
- Microsoft Agent Framework: Workflow/Executor separation and cancellation of
  pending work.
- A2A: Messages for control and Artifacts for durable task outputs.

Primary references:

- <https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/checkpointers.mdx>
- <https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md>
- <https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md>
- <https://github.com/microsoft/agent-framework/blob/main/python/samples/03-workflows/README.md>
- <https://github.com/a2aproject/A2A/blob/main/docs/specification.md>
