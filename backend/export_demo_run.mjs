// Offline export only: no application imports, services, credentials or network.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const taskId = 'task_user_5117fc817c3a';
const dir = path.join(root, 'backend/app/data/runs', taskId);
const output = path.join(root, 'frontend/demo/demo_run.json');
const hashes = {};
const read = (name) => {
  const bytes = fs.readFileSync(path.join(dir, `${name}.json`));
  hashes[name] = createHash('sha256').update(bytes).digest('hex');
  const items = JSON.parse(bytes);
  assert(Array.isArray(items), name);
  for (const item of items) assert(!item.task_id || item.task_id === taskId, `${name}: foreign task`);
  return items;
};
const pick = (object, fields) => Object.fromEntries(fields.split(' ').filter(k => object[k] !== undefined).map(k => [k, object[k]]));
const list = (name, fields) => read(name).map(item => pick(item, `id task_id ${fields}`));
const task = read('analysis_tasks')[0];
const pipeline = read('pipeline_runs')[0];
assert.equal(pipeline.status, 'completed');
const workspace = {
  workspaceVersion: 'v1', taskId, stage: 'completed', stageDetail: '历史运行已完成，仍存在研究缺口。',
  analysisTask: { ...pick(task, 'id task_id query competitors industry focus_areas report_subject preferred_title'),
    metadata: { request_text: task.metadata.request_text } },
  sources: list('sources', 'title url source_type competitor accessed_at reliability_score'),
  evidence: list('evidence', 'source_id competitor dimension snippet normalized_fact confidence'),
  researchTasks: list('research_tasks', 'title objective competitor dimension status collection_round depends_on parent_research_task_id'),
  researchPlan: list('research_plans', 'decision_question status kiq_ids information_need_ids research_task_ids')[0],
  productCards: list('product_cards', 'name company positioning target_users pricing_summary core_features strengths weaknesses source_ids evidence_ids confidence'),
  claims: list('claims', 'dimension claim_text competitors evidence_ids confidence citation_status'),
  claimsV2: list('claims_v2', 'dimension claim_type claim_text competitors evidence_ids counter_evidence_ids uncertainty decision_impact confidence citation_status'),
  citationChecks: list('citation_checks', 'claim_id evidence_ids status message created_at'),
  evidenceCoverage: list('evidence_coverage', 'competitor dimension status source_ids evidence_ids limitations'),
  researchGaps: list('research_gaps', 'competitors dimension missing_information decision_blocked related_evidence_ids priority'),
  reportStatements: list('report_statements', 'report_id section line_index statement_kind text claim_ids evidence_ids research_gap_ids citation_status confidence'),
  report: list('reports', 'title markdown claim_ids created_at')[0],
  review: null, qualityGates: list('quality_gates', 'gate_name status passed blocking severity issue_ids citation_check_ids message created_at'),
};
const review = read('review_feedback')[0];
workspace.review = { ...pick(review, 'id task_id overall_score approved suggestions created_at'),
  issues: review.issues.map(i => pick(i, 'id severity target_type target_id message')) };
const pipelineRun = pick(pipeline, 'id task_id status current_stage progress_percent completed_stages started_at completed_at');
const rawEvents = read('pipeline_events');
assert.equal(new Set(rawEvents.map(e => e.pipeline_run_id)).size, 1);
assert(rawEvents.every(e => e.pipeline_run_id === pipeline.id));
assert.equal(new Set(rawEvents.map(e => e.sequence)).size, rawEvents.length);
const eventFields = 'action_id action action_status query source_id source_url source_title evidence_id quote_verified research_task_title competitor dimension finish_status action_created_at public_summary sender recipient outcome total_tasks completed_tasks failed_tasks';
const events = rawEvents.map(event => {
  const data = pick(event.data || {}, eventFields);
  if (event.event_type === 'coverage_refreshed') {
    const summary = event.data.coverage_summary;
    data.coverage_summary = {
      ...pick(summary, 'total_sources coverage_status_counts'),
      verified_evidence_count: summary.projection.verified_evidence_count,
    };
  }
  return { ...pick(event, 'id sequence event_type stage status research_task_id progress_percent created_at'), data };
});
// Presentation holds are separate from recorded timestamps. Preserve sequence exactly.
const anchors = [[1,3000],[6,4000],[334,22000],[337,26500],[338,27500],[340,28000],[341,30500],[342,32000],[344,33000]];
for (const event of events) {
  let index = anchors.findIndex(([seq]) => seq >= event.sequence);
  if (index <= 0) event.atMs = anchors[0][1];
  else {
    const [a, b] = [anchors[index - 1], anchors[index]];
    event.atMs = Math.round(a[1] + (event.sequence-a[0])/(b[0]-a[0])*(b[1]-a[1]));
  }
}
const ids = key => new Set(workspace[key].map(x => x.id));
const check = (values, target, label) => (values || []).forEach(id => assert(target.has(id), `${label}: ${id}`));
for (const e of workspace.evidence) check([e.source_id], ids('sources'), 'evidence/source');
for (const c of workspace.claims) check(c.evidence_ids, ids('evidence'), 'claim/evidence');
for (const c of workspace.citationChecks) { check([c.claim_id], ids('claims'), 'citation/claim'); check(c.evidence_ids, ids('evidence'), 'citation/evidence'); }
check(workspace.report.claim_ids, ids('claims'), 'report/claim');
for (const s of workspace.reportStatements) {
  assert.equal(s.report_id, workspace.report.id);
  check(s.claim_ids, ids('claims'), 'statement/claim'); check(s.evidence_ids, ids('evidence'), 'statement/evidence');
  check(s.research_gap_ids, ids('researchGaps'), 'statement/gap');
  assert(workspace.report.markdown.split('\n')[s.line_index]?.trim(), 'statement line');
}
const eventEvidence = new Set(events.filter(e => e.data.quote_verified && e.data.evidence_id).map(e => e.data.evidence_id));
assert.deepEqual([...eventEvidence].sort(), [...ids('evidence')].sort());
const data = {
  schemaVersion: 'rivio.demo.v1', mode: 'demo',
  provenance: { taskId, pipelineRunId: pipeline.id, singleRunOnly: true, artifactSha256: hashes },
  notice: 'Demo Replay · 基于真实历史运行记录的加速回放，不会重新调用 LLM 或外部搜索服务',
  brief: { origin: 'confirmed_analysis_task', request_text: task.metadata.request_text,
    decision_question: task.query, competitors: task.metadata.research_brief.comparison_targets,
    focus_areas: task.focus_areas, report_subject: task.report_subject },
  finalSnapshot: { workspace, pipelineRun },
  replay: { durationMs: 33000, timing: 'piecewise_compressed', events },
  limitations: ['Brief 来自已确认任务，不含原始 Draft 编辑过程。',
    '历史产物包含异常合并研究对象“小红书、抖音”，原样保留；未删除或重算。',
    '流程完成不代表证据充分：仍有 waiting / exhausted 任务和 Coverage 缺口。',
    'Reviewer 为历史规则审查，审查问题原样保留。'],
};
// Strict field selection above; fail closed on sensitive strings, including nested text.
const forbiddenKey = /^(api[_-]?key|token|authorization|cookie|prompt.*|.*reasoning.*|rationale|raw_.*|payload|context.*|tool_calls|llm.*)$/i;
const forbiddenText = /(?:\b(?:sk-|tvly-)[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{12,}|(?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*["']?[A-Za-z0-9_-]{12,}|\b[A-Za-z]:[\\/]|\/(?:Users|home|tmp)\/|Traceback \(most recent call last\))/i;
function audit(value, location = '$') {
  if (typeof value === 'string') {
    assert(!forbiddenText.test(value), `Sensitive text at ${location}`);
    if (/^https?:\/\//i.test(value)) {
      const url = new URL(value);
      assert(!url.username && !url.password, `URL credentials at ${location}`);
      assert(!/^(localhost|127\.|10\.|192\.168\.|\[?::1)/i.test(url.hostname), `Internal URL at ${location}`);
      assert(![...url.searchParams.keys()].some(k => /token|key|auth|secret|signature/i.test(k)), `Sensitive URL at ${location}`);
    }
  } else if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      assert(!forbiddenKey.test(key), `Forbidden field at ${location}.${key}`);
      audit(child, `${location}.${key}`);
    }
  }
}
audit(data);
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, JSON.stringify(data, null, 2) + '\n');
console.log(JSON.stringify({ taskId, events: events.length, sources: workspace.sources.length,
  evidence: workspace.evidence.length, claims: workspace.claims.length, statements: workspace.reportStatements.length,
  security: 'allowlist and sensitive-string checks passed', bytes: fs.statSync(output).size }));
