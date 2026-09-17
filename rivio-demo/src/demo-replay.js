(function (global) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  class DemoReplay {
    constructor(data) { this.data = data; this.reset(); }
    reset() {
      this.stop(); this.cursor = 0; this.elapsed = 0;
      const final = this.data.finalSnapshot.workspace;
      this.workspace = { taskId: final.taskId, analysisTask: clone(final.analysisTask), stage: 'confirmed',
        sources: [], evidence: [], researchTasks: [], claims: [], claimsV2: [], citationChecks: [],
        reportStatements: [], researchGaps: [], qualityGates: [], evidenceCoverage: [], report: null, review: null };
      this.run = { task_id: final.taskId, status: 'confirmed', current_stage: 'confirmed', progress_percent: 0, completed_stages: [] };
      this.events = []; this.coverage = null; this.taskOutcomes = {}; this.finished = false;
    }
    stop() { if (this.timer) clearInterval(this.timer); this.timer = null; }
    snapshot() { return { workspace: this.workspace, pipelineRun: this.run, events: this.events,
      coverage: this.coverage, taskOutcomes: this.taskOutcomes, elapsed: this.elapsed, finished: this.finished }; }
    apply(event) {
      const final = this.data.finalSnapshot.workspace;
      const d = event.data || {};
      this.events.push(event);
      if (event.event_type.startsWith('pipeline_') || event.event_type.startsWith('stage_')) {
        this.run.current_stage = event.stage;
        if (event.event_type === 'pipeline_queued') this.run.status = 'queued';
        if (event.event_type === 'pipeline_started' || event.event_type === 'stage_started') this.run.status = 'running';
        this.run.progress_percent = event.progress_percent ?? this.run.progress_percent;
      }
      if (event.event_type === 'stage_completed') {
        this.run.completed_stages = [...new Set([...this.run.completed_stages, event.stage])];
        if (event.stage === 'planning') {
          this.workspace.researchTasks = clone(final.researchTasks.filter(t => t.collection_round === 1));
        }
        if (event.stage === 'researching') this.workspace.researchTasks = clone(final.researchTasks);
      }
      if (event.event_type === 'research_task_started') this.taskOutcomes[event.research_task_id] = 'running';
      if (event.event_type === 'research_task_completed') this.taskOutcomes[event.research_task_id] = d.outcome;
      if (event.event_type === 'agent_action') {
        if (d.source_id && d.action_status === 'completed') {
          const source = final.sources.find(s => s.id === d.source_id);
          if (source && !this.workspace.sources.some(s => s.id === source.id)) this.workspace.sources.push(source);
        }
        if (d.action === 'SUBMIT_EVIDENCE' && d.quote_verified && d.action_status === 'completed') {
          const evidence = final.evidence.find(e => e.id === d.evidence_id);
          if (evidence && !this.workspace.evidence.some(e => e.id === evidence.id)) {
            this.workspace.evidence.push(evidence);
            const source = final.sources.find(s => s.id === evidence.source_id);
            if (!this.workspace.sources.some(s => s.id === source.id)) this.workspace.sources.push(source);
          }
        }
      }
      if (event.event_type === 'coverage_refreshed') {
        this.coverage = clone(d.coverage_summary);
        // Only the final matrix is persisted; never invent the first matrix.
        if (this.coverage.verified_evidence_count === final.evidence.length) {
          this.workspace.evidenceCoverage = clone(final.evidenceCoverage);
          this.workspace.sources = clone(final.sources);
        }
      }
      if (event.event_type === 'handoff_created' && d.sender === 'professional_research_analyst_agent') {
        for (const key of ['claims', 'claimsV2', 'researchGaps', 'productCards']) this.workspace[key] = clone(final[key]);
      }
      if (event.event_type === 'handoff_created' && d.sender === 'citation_agent') this.workspace.citationChecks = clone(final.citationChecks);
      if (event.event_type === 'handoff_created' && d.sender === 'professional_writer_agent') {
        this.workspace.report = clone(final.report); this.workspace.reportStatements = clone(final.reportStatements);
      }
      if (event.event_type === 'handoff_created' && d.sender === 'reviewer_agent') this.workspace.review = clone(final.review);
      if (event.event_type === 'pipeline_completed') this.complete();
    }
    advance(ms) {
      this.elapsed = Math.min(ms, this.data.replay.durationMs);
      const events = this.data.replay.events;
      while (this.cursor < events.length && events[this.cursor].atMs <= this.elapsed) this.apply(events[this.cursor++]);
      return this.snapshot();
    }
    complete() {
      this.workspace = clone(this.data.finalSnapshot.workspace);
      this.run = clone(this.data.finalSnapshot.pipelineRun);
      this.finished = true; this.stop();
    }
    skip() { this.stop(); return this.advance(this.data.replay.durationMs); }
    play(onChange) {
      this.reset(); onChange(this.snapshot());
      const start = performance.now();
      this.timer = setInterval(() => onChange(this.advance(performance.now() - start)), 80);
    }
  }
  global.DemoReplay = DemoReplay;
})(window);
