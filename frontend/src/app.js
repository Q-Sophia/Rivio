const API_BASE =
  window.location.protocol === "http:" || window.location.protocol === "https:"
    ? window.location.origin
    : "http://127.0.0.1:8000";

const state = {
  activeView: "overview",
  activeTaskId: "",
  activeLegacyRun: null,
  data: {},
  runs: [],
  recentTasks: [],
  workspaceLoadSequence: 0,
  step6cExperiment: null,
  reportEvidenceCollapsed: false,
  intentDraft: null,
  intentCall: null,
  recentDrafts: [],
  analysisTask: null,
  researchPlan: null,
  researchTasks: [],
  researchLoopRun: null,
  researchLoopEvents: [],
  researchLoopEventSource: null,
  researchAnalysis: null,
  researchReporting: null,
  researchCanAnalyze: false,
  integrationStatus: null,
};

const views = {
  intake: "新建分析",
  overview: "项目总览",
  workflow: "任务协作",
  llm: "LLM 链路（大模型链路）",
  step6c: "Step 6C 专业分析实验",
  claims: "结论与证据",
  report: "分析报告",
  governance: "质量治理",
};

const artifactLabels = {
  sources_count: "Sources（来源）",
  evidence_count: "Evidence（证据）",
  product_cards_count: "ProductCards（产品卡片）",
  claims_count: "Claims（分析结论）",
  citation_checks_count: "CitationChecks（引用检查）",
  reports_count: "Reports（报告）",
  dag_nodes_count: "DAGNodes（流程节点）",
  agent_runs_count: "AgentRuns（执行记录）",
  tool_calls_count: "ToolCalls（工具调用）",
  supported_count: "Supported（证据支持）",
  weak_count: "Weak（弱证据）",
  review_score: "Review Score（审查分）",
};

const roleLabels = {
  intent: "Intent（意图识别）",
  collector: "Collector（采集）",
  extractor: "Extractor（抽取）",
  analyst: "Analyst（分析）",
  citation: "Citation（引用检查）",
  writer: "Writer（写作）",
  reviewer: "Reviewer（审查）",
  orchestrator: "Orchestrator（编排）",
};

const taskLabels = {
  collect_sources: "采集来源与证据",
  build_product_cards: "抽取产品卡片",
  build_claims: "形成分析结论",
  check_citations: "检查结论引用",
  build_report: "生成竞品报告",
  review_report: "审查最终报告",
  quality_gate_report: "执行报告质量闸门",
  supplement_analysis: "补充薄弱分析",
  evaluate_evidence_coverage: "更新证据覆盖并判断补采",
  supplement_collection: "按证据缺口补充采集",
};

const endpoints = {
  runs: "/api/runs",
  analysisTasks: "/api/analysis-tasks?limit=20",
  taskDrafts: "/api/task-drafts",
  integrations: "/api/integrations/status",
  parseTaskDraft: "/api/task-drafts/parse",
  taskDraft: (draftId) => `/api/task-drafts/${encodeURIComponent(draftId)}`,
  confirmTaskDraft: (draftId) => `/api/task-drafts/${encodeURIComponent(draftId)}/confirm`,
  researchPlan: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/research-plan`,
  runCollectorOnce: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/collector/run-once`,
  runExtractorOnce: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/extractor/run-once`,
  runCoverageOnce: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/coverage/run-once`,
  startResearchLoop: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/research-loop`,
  researchLoopStatus: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/research-loop`,
  researchLoopEvents: (taskId, after = 0) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/research-loop/events/stream?after=${after}`,
  researchAnalysis: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/research-analysis`,
  researchReporting: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/research-reporting`,
  workspace: (taskId) => `/api/analysis-tasks/${encodeURIComponent(taskId)}/workspace`,
  latestStep6CExperiment: "/api/experiments/step6c/latest",
  dashboard: (runId, taskId) =>
    `/api/runs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/dashboard`,
  summary: (taskId) => `/api/tasks/${taskId}/summary`,
  sources: (taskId) => `/api/tasks/${taskId}/sources`,
  evidence: (taskId) => `/api/tasks/${taskId}/evidence`,
  productCards: (taskId) => `/api/tasks/${taskId}/product-cards`,
  claims: (taskId) => `/api/tasks/${taskId}/claims`,
  citationChecks: (taskId) => `/api/tasks/${taskId}/citation-checks`,
  report: (taskId) => `/api/tasks/${taskId}/report`,
  review: (taskId) => `/api/tasks/${taskId}/review`,
  trace: (taskId) => `/api/tasks/${taskId}/trace`,
  evalSummary: (taskId) => `/api/tasks/${taskId}/eval`,
  taskBoard: (taskId) => `/api/tasks/${taskId}/task-board`,
  taskRecords: (taskId) => `/api/tasks/${taskId}/task-records`,
  qualityGates: (taskId) => `/api/tasks/${taskId}/quality-gates`,
  feedbackTasks: (taskId) => `/api/tasks/${taskId}/feedback-tasks`,
  contextBundles: (taskId) => `/api/tasks/${taskId}/context-bundles`,
  workingMemory: (taskId) => `/api/tasks/${taskId}/working-memory`,
  memoryItems: (taskId) => `/api/tasks/${taskId}/memory-items`,
  guardrailChecks: (taskId) => `/api/tasks/${taskId}/guardrail-checks`,
  llmCalls: (taskId) => `/api/tasks/${taskId}/llm-calls`,
  llmOutputs: (taskId) => `/api/tasks/${taskId}/llm-outputs`,
  reportStatements: (taskId) => `/api/tasks/${taskId}/report-statements`,
};

const ACTIVE_TASK_STORAGE_KEY = "lastActiveTaskId";
const LEGACY_ACTIVE_TASK_STORAGE_KEY = "competitive-intel-agents.activeTaskId";
const EMPTY_STAGE_COPY = "当前任务尚未生成该阶段产物。";
const TASK_WORKSPACE_VIEWS = new Set(["overview", "workflow", "llm", "claims", "report", "governance"]);

function normalizeWorkspaceData(payload = {}, taskId = "") {
  const emptyLists = [
    "sources", "evidence", "productCards", "claims", "citationChecks", "taskRecords",
    "qualityGates", "feedbackTasks", "contextBundles", "memoryItems", "guardrailChecks",
    "llmCalls", "llmOutputs", "analysisPortfolios", "briefAssessments", "competitorProfiles",
    "intelligenceQuestions", "informationNeeds", "evidenceCoverage", "comparabilityNotes",
    "claimsV2", "researchGaps", "reportStatements", "researchTasks", "webPages",
    "collectionAttempts", "searchAttempts", "webSearchResults", "evidenceExtractionAttempts",
    "analysisEvidenceCoverage", "analysisResearchGaps",
  ];
  const normalized = { ...payload, taskId: payload.taskId || taskId };
  emptyLists.forEach((key) => {
    normalized[key] = Array.isArray(payload[key]) ? payload[key] : [];
  });
  normalized.trace = {
    task_id: payload.trace?.task_id || normalized.taskId,
    dag_nodes: Array.isArray(payload.trace?.dag_nodes) ? payload.trace.dag_nodes : [],
    agent_runs: Array.isArray(payload.trace?.agent_runs) ? payload.trace.agent_runs : [],
    tool_calls: Array.isArray(payload.trace?.tool_calls) ? payload.trace.tool_calls : [],
  };
  for (const key of [
    "analysisTask", "run", "summary", "report", "review", "researchPlan", "taskBoard",
    "evalSummary", "workingMemory",
  ]) {
    normalized[key] = payload[key] ?? null;
  }
  normalized.stage = payload.stage || "confirmed";
  normalized.stageDetail = payload.stageDetail || "AnalysisTask 已确认，等待后续研究产物";
  return normalized;
}

function getTaskIdFromUrl() {
  return new URLSearchParams(window.location.search).get("task")?.trim() || "";
}

function getStoredActiveTaskId() {
  return (
    window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY)
    || window.localStorage.getItem(LEGACY_ACTIVE_TASK_STORAGE_KEY)
    || ""
  ).trim();
}

function taskRestoreCandidates(urlTaskId, storedTaskId) {
  return [urlTaskId, storedTaskId].filter(
    (taskId, index, items) => taskId && items.indexOf(taskId) === index,
  );
}

function syncTaskUrl(taskId, historyMode = "replace") {
  if (historyMode === "none") return;
  const url = new URL(window.location.href);
  if (taskId) url.searchParams.set("task", taskId);
  else url.searchParams.delete("task");
  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  if (historyMode === "push") window.history.pushState({ taskId }, "", nextUrl);
  else window.history.replaceState({ taskId }, "", nextUrl);
}

function taskWorkspaceUrl(taskId) {
  const url = new URL(window.location.href);
  url.searchParams.set("task", taskId);
  return `${url.pathname}${url.search}${url.hash}`;
}

function setActiveTaskContext(taskId, { historyMode = "replace" } = {}) {
  state.activeTaskId = taskId;
  state.activeLegacyRun = null;
  if (taskId) {
    window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, taskId);
    window.localStorage.removeItem(LEGACY_ACTIVE_TASK_STORAGE_KEY);
    qs("#task-id").value = taskId;
    const planning = qs("#research-planning");
    if (planning) planning.dataset.taskId = taskId;
  }
  syncTaskUrl(taskId, historyMode);
  const selector = qs("#run-id");
  if (selector) selector.value = "";
  renderRecentTasks();
}

function qs(selector) {
  return document.querySelector(selector);
}

function qsa(selector) {
  return [...document.querySelectorAll(selector)];
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(text, variant = "") {
  const pill = qs("#status-pill");
  pill.textContent = text;
  pill.className = `status-pill ${variant}`.trim();
}

function showError(message) {
  const panel = qs("#error-panel");
  panel.textContent = message;
  panel.classList.remove("hidden");
  setStatus("读取失败", "error");
}

function clearError() {
  const panel = qs("#error-panel");
  panel.textContent = "";
  panel.classList.add("hidden");
}

async function fetchJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      detail = JSON.parse(text).detail || text;
    } catch (_) {
      // Keep the original response text when it is not JSON.
    }
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return response.json();
}

function splitDraftList(value) {
  return String(value || "")
    .split(/[\n，,；;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function setIntentStatus(text, variant = "") {
  const element = qs("#intent-status");
  element.textContent = text;
  element.className = `intake-status ${variant}`.trim();
}

function renderIntegrationStatus(payload) {
  state.integrationStatus = payload;
  const deepseek = payload.deepseek || {};
  const search = payload.search || {};
  const deepseekStatus = qs("#deepseek-integration-status");
  const searchStatus = qs("#search-integration-status");
  deepseekStatus.textContent = deepseek.configured ? "后端已配置" : "后端未配置";
  deepseekStatus.className = deepseek.configured ? "ok" : "error";
  qs("#deepseek-integration-detail").textContent = deepseek.configured
    ? `${deepseek.model} · ${deepseek.transport === "direct" ? "直连" : "环境代理"} · 点击业务按钮时才调用`
    : (deepseek.errors || ["缺少可用配置"]).join("；");
  searchStatus.textContent = search.configured ? "后端已配置" : "后端未配置";
  searchStatus.className = search.configured ? "ok" : "error";
  qs("#search-integration-detail").textContent = search.configured
    ? `${search.provider} · 网页搜索 · 采集任务缺少网址时才调用`
    : "当前没有可用的网页搜索 Provider（供应商）配置。";
  qs("#integration-status-note").textContent = payload.message || "状态检查不会调用外部 API。";
  qs("#integration-status-note").className = "intake-status ok";
}

async function loadIntegrationStatus() {
  const button = qs("#refresh-integrations-btn");
  button.disabled = true;
  try {
    renderIntegrationStatus(await fetchJson(endpoints.integrations));
  } catch (error) {
    qs("#integration-status-note").textContent = `后端配置状态读取失败：${error.message}`;
    qs("#integration-status-note").className = "intake-status error";
  } finally {
    button.disabled = false;
  }
}

function getDraftEdits() {
  return {
    decision_question: qs("#draft-decision").value.trim(),
    industry: qs("#draft-industry").value.trim(),
    competitors: splitDraftList(qs("#draft-competitors").value),
    target_customers: splitDraftList(qs("#draft-customers").value),
    core_scenarios: splitDraftList(qs("#draft-scenarios").value),
    focus_areas: splitDraftList(qs("#draft-focus").value),
    constraints: splitDraftList(qs("#draft-constraints").value),
    report_subject: qs("#draft-subject").value.trim(),
    preferred_title: qs("#draft-title").value.trim(),
  };
}

function updateDraftReadiness() {
  if (!state.intentDraft) return;
  const edited = getDraftEdits();
  const missing = [];
  if (!edited.decision_question) missing.push("决策问题");
  if (edited.competitors.length < 1) missing.push("至少一个研究对象");
  const confirmed = state.intentDraft.status === "confirmed";
  const ready = !missing.length && !confirmed;
  const readiness = qs("#draft-readiness");
  readiness.textContent = confirmed ? "已确认" : ready ? "可以确认" : `缺少 ${missing.length} 项`;
  readiness.className = `count-label tag ${ready || confirmed ? "true" : "false"}`;
  qs("#confirm-draft-btn").disabled = !ready;
  const subject = edited.preferred_title || (edited.report_subject ? `${edited.report_subject}报告` : "等待生成报告主题");
  qs("#draft-title-preview").textContent = subject;
  const questions = [
    ...missing.map((item) => `需要补充：${item}`),
    ...(edited.industry ? [] : ["可选补充：所属行业；不填写时由后续研究识别。"]),
    ...(edited.competitors.length === 1 ? ["已允许单对象任务；后续将从已有资料或网页搜索中发现主要竞品。"] : []),
    ...(edited.target_customers.length ? [] : ["可选补充：主要面向哪类客户或购买角色？"]),
    ...(edited.core_scenarios.length ? [] : ["可选补充：最需要比较的使用或购买场景是什么？"]),
  ];
  qs("#draft-questions").innerHTML = questions.length
    ? questions.map((item) => `<div class="question-item">${escapeHtml(item)}</div>`).join("")
    : '<div class="small-text">必要信息已经完整。</div>';
}

function renderIntentDraft(draft, call = null) {
  state.intentDraft = draft;
  state.intentCall = call;
  qs("#draft-editor").classList.remove("hidden");
  qs("#analysis-request").value = draft.request_text || qs("#analysis-request").value;
  qs("#draft-decision").value = draft.decision_question || "";
  qs("#draft-industry").value = draft.industry || "";
  qs("#draft-competitors").value = (draft.competitors || []).join("\n");
  qs("#draft-customers").value = (draft.target_customers || []).join("\n");
  qs("#draft-scenarios").value = (draft.core_scenarios || []).join("\n");
  qs("#draft-focus").value = (draft.focus_areas || []).join("\n");
  qs("#draft-constraints").value = (draft.constraints || []).join("\n");
  qs("#draft-subject").value = draft.report_subject || "";
  qs("#draft-title").value = draft.preferred_title || "";
  const confirmed = draft.status === "confirmed";
  qsa("#draft-editor input, #draft-editor textarea").forEach((input) => {
    input.disabled = confirmed;
  });
  qs("#intent-provider").textContent = call
    ? `${call.provider} · ${call.model}`
    : `${draft.metadata?.llm_provider || "已保存"} · ${draft.metadata?.llm_model || "草稿"}`;
  const callRecord = qs("#intent-call-record");
  if (call) {
    callRecord.innerHTML = `<strong>后端真实调用记录：1 次</strong><br />Call ID：${escapeHtml(call.call_id)} · Provider：${escapeHtml(call.provider)} · Model：${escapeHtml(call.model)} · 用时：${escapeHtml(call.duration_ms)} ms · 结构校验：${escapeHtml(call.validation_status)}`;
    callRecord.classList.remove("hidden");
  } else {
    callRecord.classList.add("hidden");
  }
  qs("#confirmed-task").classList.add("hidden");
  updateDraftReadiness();
  const confirmedTaskId = draft.metadata?.confirmed_task_id || "";
  if (confirmed && confirmedTaskId) {
    state.analysisTask = { id: confirmedTaskId };
    setActiveTaskContext(confirmedTaskId);
    showPlanningShell(confirmedTaskId);
    loadResearchPlan(confirmedTaskId);
  } else {
    resetExecutionPlanning();
  }
}

async function loadRecentDrafts() {
  try {
    const payload = await fetchJson(endpoints.taskDrafts);
    state.recentDrafts = payload.drafts || [];
    const select = qs("#recent-draft");
    select.innerHTML = state.recentDrafts.length
      ? state.recentDrafts.map((draft) => `<option value="${escapeHtml(draft.id)}">${escapeHtml(draft.report_subject || draft.request_text.slice(0, 34))} · ${escapeHtml(draft.status)}</option>`).join("")
      : '<option value="">暂无草稿</option>';
  } catch (error) {
    setIntentStatus(`草稿列表读取失败：${error.message}`, "error");
  }
}

async function loadSelectedDraft() {
  const draftId = qs("#recent-draft").value;
  if (!draftId) {
    setIntentStatus("当前没有可加载的草稿。", "error");
    return;
  }
  try {
    const payload = await fetchJson(endpoints.taskDraft(draftId));
    renderIntentDraft(payload.draft);
    const confirmedTaskId = payload.draft.metadata?.confirmed_task_id || "";
    if (payload.draft.status === "confirmed" && confirmedTaskId) {
      await loadTaskWorkspace(confirmedTaskId);
    }
    setIntentStatus("已加载保存的草稿；本次操作没有调用大模型。", "ok");
  } catch (error) {
    setIntentStatus(`加载草稿失败：${error.message}`, "error");
  }
}

async function parseIntent() {
  const requestText = qs("#analysis-request").value.trim();
  if (requestText.length < 10) {
    setIntentStatus("请至少输入 10 个字符，并说明比较对象或决策目标。", "error");
    return;
  }
  const button = qs("#parse-intent-btn");
  button.disabled = true;
  button.textContent = "正在解析…";
  setIntentStatus("正在调用 Intent Agent（意图智能体）。请不要重复点击；这不会启动正式分析。", "busy");
  const callRecord = qs("#intent-call-record");
  callRecord.innerHTML = "<strong>网页已向 FastAPI 后端发送请求。</strong><br />正在等待 DeepSeek 返回结构化任务草稿……";
  callRecord.classList.remove("hidden");
  try {
    const payload = await fetchJson(endpoints.parseTaskDraft, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_text: requestText }),
    });
    renderIntentDraft(payload.draft, payload.llm_call);
    setIntentStatus(
      payload.draft.ready_for_confirmation
        ? "需求已解析并通过必要字段检查，请检查或修改后再确认。"
        : "需求已解析，但还有必要信息需要补充。",
      payload.draft.ready_for_confirmation ? "ok" : "busy",
    );
    await loadRecentDrafts();
  } catch (error) {
    setIntentStatus(`解析失败：${error.message}`, "error");
    callRecord.innerHTML = `<strong>后端调用失败，未生成任务草稿。</strong><br />${escapeHtml(error.message)}`;
  } finally {
    button.disabled = false;
    button.textContent = "解析需求";
  }
}

async function confirmDraft() {
  if (!state.intentDraft) return;
  const button = qs("#confirm-draft-btn");
  button.disabled = true;
  button.textContent = "正在保存…";
  setIntentStatus("正在保存 AnalysisTask（分析任务），不会启动分析。", "busy");
  try {
    const payload = await fetchJson(endpoints.confirmTaskDraft(state.intentDraft.id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(getDraftEdits()),
    });
    state.analysisTask = payload.analysis_task;
    setActiveTaskContext(payload.analysis_task.id, { historyMode: "push" });
    renderIntentDraft(payload.draft);
    await loadTaskWorkspace(payload.analysis_task.id);
    const result = qs("#confirmed-task");
    result.innerHTML = `<strong>任务已确认，尚未开始分析。</strong><br />任务编号：${escapeHtml(payload.analysis_task.id)} · 状态：${escapeHtml(payload.analysis_task.status)} · execution_started=false（尚未启动执行）`;
    result.classList.remove("hidden");
    setIntentStatus("任务已经保存。下一步由 Research Planner 生成当前任务的研究计划。", "ok");
    await loadRecentDrafts();
    await loadRecentTasks();
  } catch (error) {
    setIntentStatus(`确认失败：${error.message}`, "error");
    updateDraftReadiness();
  } finally {
    button.textContent = "确认任务（不启动分析）";
  }
}

function resetExecutionPlanning() {
  closeResearchLoopEventStream();
  state.analysisTask = null;
  state.researchPlan = null;
  state.researchTasks = [];
  state.researchLoopRun = null;
  state.researchLoopEvents = [];
  state.researchAnalysis = null;
  state.researchReporting = null;
  state.researchCanAnalyze = false;
  qs("#research-planning").classList.add("hidden");
}

function showPlanningShell(taskId) {
  qs("#research-planning").classList.remove("hidden");
  qs("#research-planning").dataset.taskId = taskId;
  qs("#research-plan-result").classList.add("hidden");
  qs("#research-plan-status").textContent = "尚未规划";
  qs("#build-research-plan-btn").disabled = false;
  qs("#build-research-plan-btn").textContent = "生成研究计划";
  qs("#research-plan-copy").textContent = "第一版使用 Mock（模拟）规划，不调用 DeepSeek，也不访问网络。";
}

function renderResearchPlan(payload) {
  const plan = payload.research_plan;
  const tasks = payload.research_tasks || [];
  const kiqs = payload.kiqs || [];
  const waiting = tasks.filter((item) => item.status === "waiting_for_collector");
  const extractorReady = (payload.task_board?.tasks || []).some(
    (item) => item.target_agent_role === "extractor" && ["ready", "pending"].includes(item.status),
  );
  const coverageReady = (payload.task_board?.tasks || []).some(
    (item) => item.task_type === "evaluate_evidence_coverage" && ["ready", "pending"].includes(item.status),
  );
  const coverage = payload.evidence_coverage || [];
  const gaps = payload.research_gaps || [];
  const productCards = payload.product_cards || [];
  const canAnalyzeCurrentEvidence = coverage.length > 0 && productCards.length > 0;
  state.researchPlan = plan;
  state.researchTasks = tasks;
  state.researchCanAnalyze = canAnalyzeCurrentEvidence;
  qs("#research-plan-result").classList.remove("hidden");
  qs("#research-plan-status").textContent = plan.status === "ready_for_analysis"
    ? "覆盖充分"
    : canAnalyzeCurrentEvidence
      ? "研究进行中，可先分析"
      : "需要先取得证据";
  qs("#research-plan-status").className = `count-label tag ${waiting.length ? "warning" : "true"}`;
  qs("#research-plan-copy").textContent = waiting.length
    ? `Planner（规划智能体）发现 ${waiting.length} 个待采集任务；Collector（采集智能体）可读取 Seed URL，并通过智谱搜索缺失网址。`
    : "当前人工快照覆盖全部研究任务，可以继续进入分析计划。";
  qs("#build-research-plan-btn").disabled = true;
  qs("#build-research-plan-btn").textContent = "研究计划已生成";
  qs("#research-plan-facts").innerHTML = [
    ["关键问题", `${kiqs.length} 个`],
    ["信息需求", `${(payload.information_needs || []).length} 个`],
    ["研究任务", `${tasks.length} 个`],
    ["等待采集", `${waiting.length} 个`],
    ["证据覆盖", `${coverage.length} 格`],
    ["待补缺口", `${gaps.length} 个`],
  ].map(([label, value]) => `<div class="compatibility-fact"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  qs("#research-kiq-list").innerHTML = kiqs.map((item) => `<div><strong>${escapeHtml(item.question)}</strong><br /><span>${escapeHtml(item.decision_link)}</span></div>`).join("");
  const researchTaskStatus = {
    waiting_for_collector: "待采集",
    covered_by_snapshot: "已有人工快照",
    collected: "网页已采集",
    evidence_extracted: "证据已抽取",
    collection_failed: "采集失败",
    requires_human: "需要人工处理",
  };
  qs("#research-task-list").innerHTML = tasks.map((item) => `
    <div class="${["waiting_for_collector", "collection_failed", "requires_human"].includes(item.status) ? "warning-line" : "ok-line"}">
      ${escapeHtml(researchTaskStatus[item.status] || item.status)} · ${escapeHtml(item.title)} · 第 ${escapeHtml(item.collection_round || 1)} 轮
      <br /><small>停止条件：${escapeHtml(item.stop_condition)}</small>
    </div>
  `).join("");
  const loopStatus = state.researchLoopRun?.status || "";
  const loopActive = ["queued", "running"].includes(loopStatus);
  const loopTerminal = ["completed", "requires_human", "failed"].includes(loopStatus);
  const hasRunnableTask = waiting.length > 0 || extractorReady || coverageReady;
  qs("#run-collector-once-btn").disabled = loopActive || waiting.length === 0;
  qs("#run-extractor-once-btn").disabled = loopActive || !extractorReady;
  qs("#run-coverage-once-btn").disabled = loopActive || !coverageReady;
  qs("#start-research-loop-btn").disabled = loopActive || loopTerminal || !hasRunnableTask;
  qs("#run-research-analysis-btn").disabled = (
    loopActive
    || !canAnalyzeCurrentEvidence
    || Boolean(state.researchAnalysis?.completed)
  );
}

async function runCollectorOnce() {
  const taskId = qs("#research-planning").dataset.taskId;
  if (!taskId) return;
  const button = qs("#run-collector-once-btn");
  button.disabled = true;
  button.textContent = "正在领取并采集…";
  qs("#collector-run-copy").textContent = "正在执行网页搜索（如需要）、URL 安全检查、HTTP 读取；正文过短时会启动 Browser Fallback（浏览器渲染回退）。";
  try {
    const result = await fetchJson(endpoints.runCollectorOnce(taskId), { method: "POST" });
    qs("#collector-run-copy").textContent = result.status === "completed"
      ? `采集完成：新增 ${result.collected_sources} 个 SourceDocument（来源文档）；搜索发现=${result.search_used ? "是" : "否"}，浏览器回退=${result.browser_fallback_count || 0} 次。`
      : `${result.message || result.status}；系统没有伪造采集结果。`;
    await loadResearchPlan(taskId);
  } catch (error) {
    qs("#collector-run-copy").textContent = `采集未执行：${error.message}`;
  } finally {
    button.textContent = "采集下一项任务";
  }
}

async function runExtractorOnce() {
  const taskId = qs("#research-planning").dataset.taskId;
  if (!taskId) return;
  const button = qs("#run-extractor-once-btn");
  button.disabled = true;
  button.textContent = "正在抽取并校验证据…";
  qs("#collector-run-copy").textContent = "Extractor（抽取智能体）正在从网页正文中定位可逐字核验的事实句。";
  try {
    const result = await fetchJson(endpoints.runExtractorOnce(taskId), { method: "POST" });
    qs("#collector-run-copy").textContent = result.status === "completed"
      ? `证据抽取完成：新增 ${result.evidence_count} 条 SourceEvidence（来源证据），均保留原文位置和来源 URL。`
      : `${result.message || result.status}；系统没有生成不可追溯证据。`;
    await loadResearchPlan(taskId);
  } catch (error) {
    qs("#collector-run-copy").textContent = `证据抽取未执行：${error.message}`;
  } finally {
    button.textContent = "抽取下一项证据";
  }
}

async function runCoverageOnce() {
  const taskId = qs("#research-planning").dataset.taskId;
  if (!taskId) return;
  const button = qs("#run-coverage-once-btn");
  button.disabled = true;
  button.textContent = "正在更新并判断…";
  qs("#collector-run-copy").textContent = "Analyst（分析智能体）正在更新产品卡片和证据覆盖，并按预算判断是否需要下一轮补采。";
  try {
    const result = await fetchJson(endpoints.runCoverageOnce(taskId), { method: "POST" });
    const counts = result.coverage_status_counts || {};
    qs("#collector-run-copy").textContent = `覆盖更新完成：充分 ${counts.sufficient || 0} 格、部分 ${counts.partial || 0} 格、缺失 ${counts.missing || 0} 格；新增 ${result.new_research_task_count || 0} 个有限轮次补采任务，当前轮次 ${result.current_collection_round || 0}/${result.max_collection_rounds || 3}。`;
    await loadResearchPlan(taskId);
  } catch (error) {
    qs("#collector-run-copy").textContent = `证据覆盖更新未执行：${error.message}`;
  } finally {
    button.textContent = "更新覆盖并判断补采";
  }
}

function researchLoopStatusLabel(status) {
  return {
    queued: "排队中",
    running: "自动研究中",
    completed: "覆盖充分",
    requires_human: "需要人工处理",
    failed: "执行失败",
  }[status] || status || "等待启动";
}

function researchLoopStageLabel(stage) {
  return {
    collector: "Collector（采集）",
    extractor: "Extractor（抽取）",
    coverage: "Analyst（覆盖检查）",
  }[stage] || stage || "Research Loop（研究循环）";
}

function researchLoopStopLabel(reason) {
  return {
    coverage_sufficient: "证据覆盖充分",
    budget_exhausted: "研究预算耗尽",
    no_runnable_task: "没有可执行任务",
    failed: "执行失败",
  }[reason] || reason || "尚未停止";
}

function renderResearchLoopRuntime(run, events = state.researchLoopEvents) {
  state.researchLoopRun = run;
  state.researchLoopEvents = events || [];
  const status = run?.status || "pending";
  const percent = Number(run?.progress_percent || 0);
  const active = ["queued", "running"].includes(status);
  const terminal = ["completed", "requires_human", "failed"].includes(status);
  const statusElement = qs("#research-loop-status");
  statusElement.textContent = researchLoopStatusLabel(status);
  statusElement.className = `count-label tag ${status === "completed" ? "true" : ["requires_human", "failed"].includes(status) ? "false" : "warning"}`;
  qs("#research-loop-progress-bar").style.width = `${Math.min(Math.max(percent, 0), 100)}%`;
  qs("#research-loop-progress-percent").textContent = `${percent}%`;
  qs("#research-loop-progress-message").textContent = run?.message || "等待用户启动";

  const startButton = qs("#start-research-loop-btn");
  if (run) {
    startButton.disabled = active || terminal || !state.researchPlan;
    startButton.textContent = status === "queued"
      ? "等待后台领取"
      : status === "running"
        ? "正在自动研究"
        : status === "completed"
          ? "研究覆盖已经充分"
          : status === "requires_human"
            ? "已停止，等待人工处理"
            : status === "failed"
              ? "研究循环失败"
              : "自动运行研究循环";
  }
  qs("#run-collector-once-btn").disabled = active || qs("#run-collector-once-btn").disabled;
  qs("#run-extractor-once-btn").disabled = active || qs("#run-extractor-once-btn").disabled;
  qs("#run-coverage-once-btn").disabled = active || qs("#run-coverage-once-btn").disabled;

  qs("#research-loop-facts").innerHTML = run ? [
    ["循环动作", `${run.actions_completed || 0}/${run.max_actions || 0}`],
    ["来源预算", `${run.source_count || 0}/${run.max_total_sources || 0}`],
    ["采集轮次", `${run.current_collection_round || 0}/${run.max_collection_rounds || 0}`],
    ["研究缺口", `${run.research_gap_count || 0} 个`],
    ["采集/抽取/覆盖", `${run.collector_runs || 0}/${run.extractor_runs || 0}/${run.coverage_runs || 0}`],
    ["停止原因", researchLoopStopLabel(run.stop_reason)],
  ].map(([label, value]) => `<div class="compatibility-fact"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("") : "";

  qs("#research-loop-event-list").innerHTML = state.researchLoopEvents.length
    ? state.researchLoopEvents.map((event) => `
      <article class="execution-event ${escapeHtml(event.status)}">
        <span class="execution-event-sequence">${String(event.sequence).padStart(2, "0")}</span>
        <div>
          <strong>${escapeHtml(researchLoopStageLabel(event.stage))}</strong>
          <small>${escapeHtml(event.message)}</small>
        </div>
        <span>${escapeHtml(event.progress_percent)}%</span>
      </article>
    `).join("")
    : '<div class="small-text">启动后将实时显示 Collector → Extractor → Analyst（采集 → 抽取 → 覆盖检查）的自动交接。</div>';

  const result = qs("#research-loop-result");
  if (terminal) {
    const title = status === "completed" ? "自动研究循环已经完成。" : "自动研究循环已停止，未伪装成覆盖充分。";
    result.innerHTML = `<strong>${escapeHtml(title)}</strong><br />停止原因：${escapeHtml(researchLoopStopLabel(run.stop_reason))}。${run.error ? `<br />${escapeHtml(run.error)}` : ""}`;
    result.classList.remove("hidden");
  } else {
    result.classList.add("hidden");
  }
}

function closeResearchLoopEventStream() {
  if (state.researchLoopEventSource) {
    state.researchLoopEventSource.close();
    state.researchLoopEventSource = null;
  }
}

function connectResearchLoopEventStream(taskId) {
  closeResearchLoopEventStream();
  const after = state.researchLoopEvents.at(-1)?.sequence || 0;
  const source = new EventSource(`${API_BASE}${endpoints.researchLoopEvents(taskId, after)}`);
  state.researchLoopEventSource = source;
  source.addEventListener("research-loop", (message) => {
    const event = JSON.parse(message.data);
    if (!state.researchLoopEvents.some((item) => item.sequence === event.sequence)) {
      state.researchLoopEvents.push(event);
    }
    const current = state.researchLoopRun || {};
    const run = {
      ...current,
      status: event.status,
      current_stage: event.stage || current.current_stage,
      progress_percent: event.progress_percent,
      message: event.message,
      actions_completed: Math.max(current.actions_completed || 0, event.action_index || 0),
      stop_reason: event.data?.stop_reason || current.stop_reason,
      error: event.event_type === "failed" ? event.message : current.error,
    };
    if (["completed", "requires_human", "failed"].includes(event.status)) {
      closeResearchLoopEventStream();
      loadResearchPlan(taskId);
    } else {
      renderResearchLoopRuntime(run);
    }
  });
  source.onerror = () => {
    if (!["completed", "requires_human", "failed"].includes(state.researchLoopRun?.status)) {
      qs("#research-loop-progress-message").textContent = "实时连接暂时断开，正在读取最新持久化状态…";
      closeResearchLoopEventStream();
      window.setTimeout(() => loadResearchLoopStatus(taskId), 800);
    }
  };
}

async function loadResearchLoopStatus(taskId) {
  try {
    const payload = await fetchJson(endpoints.researchLoopStatus(taskId));
    renderResearchLoopRuntime(payload.research_loop_run, payload.events || []);
    if (!payload.terminal) connectResearchLoopEventStream(taskId);
  } catch (error) {
    if (String(error.message).startsWith("404")) {
      renderResearchLoopRuntime(null, []);
      return;
    }
    qs("#research-loop-progress-message").textContent = `研究循环状态读取失败：${error.message}`;
  }
}

async function startResearchLoop() {
  const taskId = qs("#research-planning").dataset.taskId;
  if (!taskId || !state.researchPlan) return;
  const button = qs("#start-research-loop-btn");
  button.disabled = true;
  button.textContent = "正在提交…";
  qs("#research-loop-progress-message").textContent = "正在提交有限研究循环；不会调用真实大模型。";
  try {
    const payload = await fetchJson(endpoints.startResearchLoop(taskId), { method: "POST" });
    renderResearchLoopRuntime(payload.research_loop_run, []);
    connectResearchLoopEventStream(taskId);
  } catch (error) {
    qs("#research-loop-progress-message").textContent = `研究循环未启动：${error.message}`;
    button.disabled = false;
    button.textContent = "自动运行研究循环";
  }
}

function renderResearchAnalysis(payload) {
  state.researchAnalysis = payload;
  const completed = Boolean(payload?.completed);
  const button = qs("#run-research-analysis-btn");
  button.disabled = completed || !state.researchCanAnalyze;
  button.textContent = completed
    ? "DeepSeek 分析已经完成"
    : "调用 DeepSeek 生成分析结论（1 次）";
  const result = qs("#research-analysis-result");
  if (completed) {
    const supported = (payload.citation_checks || []).filter((item) => item.status === "supported").length;
    const calls = payload.analyst_llm_calls || [];
    const latestCall = calls.at(-1) || {};
    result.innerHTML = `<strong>当前网页研究产物已经生成分析结论。</strong><br />V2 结论：${escapeHtml((payload.claims_v2 || []).length)} 条 · 引用检查：${escapeHtml((payload.citation_checks || []).length)} 条 · supported（充分支持）：${escapeHtml(supported)} 条<br />后端 Analyst 真实调用记录：${escapeHtml(calls.length)} 次 · ${escapeHtml(latestCall.provider || "-")} · ${escapeHtml(latestCall.model || "-")} · ${escapeHtml(latestCall.duration_ms || 0)} ms`;
    result.classList.remove("hidden");
    qs("#research-analysis-copy").textContent = "Analyst 已读取结构化 SourceEvidence / ProductCard / EvidenceCoverage，原确定性覆盖与缺口产物保持不变。";
  } else {
    result.classList.add("hidden");
  }
}

function renderResearchReporting(payload) {
  state.researchReporting = payload;
  const completed = Boolean(payload?.completed);
  const writerRequired = Boolean(payload?.writer_required);
  const analysisCompleted = Boolean(state.researchAnalysis?.completed);
  const button = qs("#run-research-reporting-btn");
  button.disabled = completed || !analysisCompleted;
  button.textContent = completed
    ? "正式报告已生成"
    : writerRequired
      ? "生成竞品分析报告（额外 1 次 DeepSeek）"
      : "继续审查与质量闸门（不调用 DeepSeek）";

  const result = qs("#research-reporting-result");
  const report = payload?.report;
  const review = payload?.review;
  const gate = payload?.quality_gate;
  if (report) {
    result.innerHTML = `<strong>${escapeHtml(report.title || "正式竞品分析报告")}</strong><br />报告论点映射：${escapeHtml((payload.report_statements || []).length)} 条 · Reviewer：${review ? escapeHtml(review.approved ? "通过" : "需修订") : "尚未生成"} · QualityGate：${gate ? escapeHtml(gate.status || "已生成") : "尚未生成"}<br />Writer 真实调用记录：${escapeHtml((payload.writer_llm_calls || []).length)} 次`;
    result.classList.remove("hidden");
  } else {
    result.classList.add("hidden");
  }

  if (completed) {
    qs("#research-reporting-copy").textContent = "正式报告、Reviewer 审查和 QualityGate 已生成；可在“分析报告”和“质量治理”页面查看当前任务产物。";
  } else if (report) {
    qs("#research-reporting-copy").textContent = "Writer 报告已保留，当前只需从 Reviewer 或 QualityGate 阶段恢复，不会重复调用 DeepSeek。";
  } else if (analysisCompleted) {
    qs("#research-reporting-copy").textContent = "将复用当前任务已有 Analyst/Citation 产物；Writer 额外调用 1 次真实 DeepSeek，不会重新搜索、采集、抽取或分析。";
  }
}

async function loadResearchReporting(taskId) {
  try {
    const payload = await fetchJson(endpoints.researchReporting(taskId));
    renderResearchReporting(payload);
  } catch (error) {
    qs("#research-reporting-copy").textContent = `报告链路状态读取失败：${error.message}`;
  }
}

async function loadResearchAnalysis(taskId) {
  try {
    const payload = await fetchJson(endpoints.researchAnalysis(taskId));
    renderResearchAnalysis(payload);
    await loadResearchReporting(taskId);
  } catch (error) {
    qs("#research-analysis-copy").textContent = `分析结论状态读取失败：${error.message}`;
  }
}

async function runResearchReporting() {
  const taskId = qs("#research-planning").dataset.taskId;
  const current = state.researchReporting || {};
  if (!taskId || !state.researchAnalysis?.completed || current.completed) return;
  const writerRequired = Boolean(current.writer_required);
  const message = writerRequired
    ? "本次会额外调用 1 次真实 DeepSeek Writer。只读取当前任务已有的 Analyst/Citation 产物，不会重新搜索、采集、抽取、分析或重跑 Citation。是否继续？"
    : "已有 Writer 报告。本次只恢复 Reviewer 与 QualityGate，不会调用 DeepSeek。是否继续？";
  if (!window.confirm(message)) return;

  const button = qs("#run-research-reporting-btn");
  button.disabled = true;
  button.textContent = writerRequired ? "DeepSeek 正在写报告…" : "正在恢复质量阶段…";
  qs("#research-reporting-copy").textContent = writerRequired
    ? "正在进行 1 次真实 DeepSeek Writer 调用；Writer 成功后会离线执行 Reviewer 与 QualityGate。"
    : "正在从已有报告恢复 Reviewer 与 QualityGate；不会产生新的模型费用。";
  try {
    const payload = await fetchJson(endpoints.researchReporting(taskId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "deepseek",
        acknowledge_real_llm_call: writerRequired,
      }),
    });
    renderResearchReporting(payload);
    await loadTaskWorkspace(taskId);
  } catch (error) {
    qs("#research-reporting-copy").textContent = `正式报告链路未完成：${error.message}。已成功保存的上游阶段产物会保留，可再次点击恢复。`;
    await loadResearchReporting(taskId);
  }
}

async function runResearchAnalysis() {
  const taskId = qs("#research-planning").dataset.taskId;
  if (!taskId || !state.researchCanAnalyze) return;
  const confirmed = window.confirm("本次通常调用 2 次真实 DeepSeek Analyst；仅当某个结构化阶段因长度截断时允许重试 1 次，总计最多 4 次。不会重新搜索或采集网页。是否继续？");
  if (!confirmed) return;
  const button = qs("#run-research-analysis-btn");
  button.disabled = true;
  button.textContent = "DeepSeek 正在分析…";
  qs("#research-analysis-copy").textContent = "正在进行两阶段真实 Analyst 调用（通常 2 次，截断重试时最多 4 次）；不会重新搜索、采集或调用 Writer。";
  try {
    const payload = await fetchJson(endpoints.researchAnalysis(taskId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "deepseek",
        acknowledge_real_llm_call: true,
      }),
    });
    renderResearchAnalysis(payload);
    await loadResearchReporting(taskId);
  } catch (error) {
    qs("#research-analysis-copy").textContent = `DeepSeek 分析未完成：${error.message}`;
    button.disabled = false;
    button.textContent = "调用 DeepSeek 生成分析结论（1 次）";
  }
}

async function loadResearchPlan(taskId) {
  try {
    const payload = await fetchJson(endpoints.researchPlan(taskId));
    if (qs("#research-planning").dataset.taskId === taskId) {
      renderResearchPlan(payload);
      await loadResearchLoopStatus(taskId);
      await loadResearchAnalysis(taskId);
    }
  } catch (error) {
    if (!String(error.message).startsWith("404")) {
      qs("#research-plan-copy").textContent = `研究计划读取失败：${error.message}`;
    }
  }
}

async function buildResearchPlan() {
  const taskId = qs("#research-planning").dataset.taskId;
  if (!taskId) return;
  const button = qs("#build-research-plan-btn");
  button.disabled = true;
  button.textContent = "正在规划…";
  qs("#research-plan-copy").textContent = "正在拆解关键问题与信息需求；不会调用真实模型。";
  try {
    const payload = await fetchJson(endpoints.researchPlan(taskId), { method: "POST" });
    renderResearchPlan(payload);
    setIntentStatus("研究计划已生成，缺失资料已经发布为动态 ResearchTask（研究任务）。", "ok");
  } catch (error) {
    button.disabled = false;
    button.textContent = "重新生成研究计划";
    qs("#research-plan-copy").textContent = `研究规划失败：${error.message}`;
  }
}

function taskStatusLabel(status) {
  return {
    pending: "等待开始",
    queued: "排队中",
    running: "运行中",
    in_progress: "进行中",
    analyzed: "已生成分析",
    reported: "已生成报告",
    requires_human: "需要人工处理",
    failed: "失败",
    completed: "已完成",
  }[status] || status || "未知状态";
}

function renderRecentTasks() {
  const container = qs("#recent-task-list");
  if (!container) return;
  if (!state.recentTasks.length) {
    container.innerHTML = '<div class="recent-task-empty">暂无 task-centric 任务</div>';
    return;
  }
  container.innerHTML = state.recentTasks.map((task) => {
    const active = task.task_id === state.activeTaskId;
    const progress = Math.min(Math.max(Number(task.progress_percent || 0), 0), 100);
    return `
      <a class="recent-task-item ${active ? "active" : ""}" href="${escapeHtml(taskWorkspaceUrl(task.task_id))}" data-active-task-id="${escapeHtml(task.task_id)}" aria-current="${active ? "page" : "false"}">
        <span class="recent-task-heading">
          <strong>${escapeHtml(task.title || task.request_text || task.task_id)}</strong>
          <em class="tag ${escapeHtml(task.status)}">${escapeHtml(taskStatusLabel(task.status))}</em>
        </span>
        <small>${escapeHtml(task.task_id)}</small>
        <span class="recent-task-stage">${escapeHtml(task.current_stage || task.stage)} · ${progress}%</span>
        <span class="recent-task-progress" aria-hidden="true"><i style="width: ${progress}%"></i></span>
      </a>
    `;
  }).join("");
}

async function loadRecentTasks() {
  try {
    const payload = await fetchJson(endpoints.analysisTasks);
    state.recentTasks = payload.tasks || [];
    renderRecentTasks();
  } catch (error) {
    const container = qs("#recent-task-list");
    if (container) {
      container.innerHTML = `<div class="recent-task-empty">最近任务读取失败：${escapeHtml(error.message)}</div>`;
    }
  }
}

function showNoActiveTask(message = "请选择最近任务，或在“新建分析”中确认一个任务。") {
  state.activeTaskId = "";
  state.activeLegacyRun = null;
  qs("#task-id").value = "";
  qs("#stage-name").textContent = "尚未选择任务";
  qs("#stage-detail").textContent = message;
  qs("#runtime-pill").textContent = "Task Workspace（任务工作区）";
  setStatus("请选择任务");
  renderRecentTasks();
}

async function restoreTaskContext() {
  const urlTaskId = getTaskIdFromUrl();
  const storedTaskId = getStoredActiveTaskId();
  const candidates = taskRestoreCandidates(urlTaskId, storedTaskId);
  for (const taskId of candidates) {
    const restored = await loadTaskWorkspace(taskId, {
      historyMode: taskId === urlTaskId ? "none" : "replace",
      suppressError: true,
    });
    if (restored) return true;
  }
  if (urlTaskId) syncTaskUrl("", "replace");
  if (storedTaskId) {
    window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
    window.localStorage.removeItem(LEGACY_ACTIVE_TASK_STORAGE_KEY);
  }
  showNoActiveTask(
    candidates.length
      ? "URL 与本地保存的任务均不存在，请从最近任务重新选择。"
      : undefined,
  );
  return false;
}

function runOptionLabel(run) {
  const source = run.is_real_llm ? "真实 LLM" : "mock 模拟";
  const status = run.pipeline_status === "completed" ? "完成" : run.pipeline_status;
  return `${run.stage || "未知阶段"} · ${source} · ${run.model} · ${run.claims_count} 条结论 · ${status}`;
}

function runSelectionValue(run) {
  return `${run.run_id}::${run.task_id}`;
}

async function loadRuns({ selectDefaultLegacy = false } = {}) {
  try {
    const payload = await fetchJson(endpoints.runs);
    state.runs = payload.runs || [];
    const selector = qs("#run-id");
    selector.innerHTML = '<option value="">历史运行 / 调试（主动选择）</option>' + state.runs
      .map(
        (run) => `
          <option value="${escapeHtml(runSelectionValue(run))}" data-run-id="${escapeHtml(run.run_id)}" data-task-id="${escapeHtml(run.task_id)}">
            ${escapeHtml(runOptionLabel(run))}
          </option>
        `,
      )
      .join("");
    if (!state.runs.length || state.activeTaskId || !selectDefaultLegacy) {
      selector.value = "";
      return;
    }
    const preferred =
      state.runs.find((run) => run.stage === "Step 6D.4" && run.pipeline_status === "completed") ||
      state.runs.find((run) => run.stage?.startsWith("Step 6C") && run.pipeline_status === "completed") ||
      state.runs.find((run) => run.is_real_llm && run.pipeline_status === "completed") ||
      state.runs[0];
    selector.value = runSelectionValue(preferred);
    qs("#task-id").value = preferred.task_id;
    state.activeLegacyRun = preferred;
    await loadLegacyDashboard(preferred);
  } catch (error) {
    showError(`读取历史运行列表失败：${error.message}`);
  }
}

async function loadTaskWorkspace(
  taskId = state.activeTaskId,
  { historyMode = "replace", suppressError = false } = {},
) {
  if (!taskId) return false;
  const loadSequence = ++state.workspaceLoadSequence;
  clearError();
  setStatus("刷新任务工作区");
  try {
    const workspace = await fetchJson(endpoints.workspace(taskId));
    if (loadSequence !== state.workspaceLoadSequence) return false;
    setActiveTaskContext(taskId, { historyMode });
    state.analysisTask = workspace.analysisTask || state.analysisTask;
    state.data = normalizeWorkspaceData(workspace, taskId);
    render();
    setStatus(`当前任务 · ${taskId}`, "ok");
    return true;
  } catch (error) {
    if (loadSequence === state.workspaceLoadSequence && !suppressError) {
      showError(`读取当前任务工作区失败：${error.message}`);
    }
    return false;
  }
}

async function loadLegacyDashboard(run = state.activeLegacyRun, { historyMode = "push" } = {}) {
  if (!run) {
    showError("请主动选择一个历史 Run（运行批次）。");
    return;
  }
  clearError();
  setStatus("读取历史调试运行");
  try {
    const [dashboard, experiment] = await Promise.all([
      fetchJson(endpoints.dashboard(run.run_id, run.task_id)),
      fetchJson(endpoints.latestStep6CExperiment).catch(() => null),
    ]);
    state.activeTaskId = "";
    state.activeLegacyRun = run;
    syncTaskUrl("", historyMode);
    qs("#task-id").value = run.task_id;
    renderRecentTasks();
    state.data = normalizeWorkspaceData(dashboard, run.task_id);
    state.step6cExperiment = experiment;
    render();
    setStatus(`历史调试 · ${run.task_id}`, "ok");
  } catch (error) {
    showError(`读取历史 Run Dashboard（调试面板）失败：${error.message}`);
  }
}

async function loadTask() {
  if (state.activeTaskId) {
    await loadTaskWorkspace(state.activeTaskId);
  } else {
    await restoreTaskContext();
  }
  await loadRecentTasks();
}

function render() {
  renderOverview();
  renderWorkflow();
  renderLLM();
  renderStep6C();
  renderReport();
  renderClaims();
  renderGovernance();
}

function renderOverview() {
  const {
    run,
    summary: savedSummary,
    productCards = [],
    citationChecks = [],
    qualityGates = [],
    guardrailChecks = [],
    sources = [],
    evidence = [],
    claims = [],
    report,
    review,
    trace = {},
    stage: workspaceStage,
    stageDetail,
  } = state.data;
  const summary = savedSummary || {
    sources_count: sources.length,
    evidence_count: evidence.length,
    product_cards_count: productCards.length,
    claims_count: claims.length,
    citation_checks_count: citationChecks.length,
    reports_count: report ? 1 : 0,
    review_feedback_count: review ? 1 : 0,
    dag_nodes_count: trace.dag_nodes?.length || 0,
    agent_runs_count: trace.agent_runs?.length || 0,
    tool_calls_count: trace.tool_calls?.length || 0,
    supported_count: citationChecks.filter((item) => item.status === "supported").length,
    weak_count: citationChecks.filter((item) => item.status === "weak").length,
    review_score: review?.overall_score ?? "—",
    pipeline_status: workspaceStage || "confirmed",
    approved: Boolean(review?.approved),
    metadata: {},
  };

  const metadata = summary.metadata || {};
  const gate = qualityGates?.[0];
  const guardrailFailures = (guardrailChecks || []).filter((item) => item.status === "failed").length;
  const taskWorkspace = Boolean(state.activeTaskId);
  qs("#runtime-pill").textContent = taskWorkspace
    ? "Task Workspace（任务工作区）"
    : `${metadata.llm_provider || "mock"} · ${metadata.llm_model || "structured"}`;
  const isRealLLM = Boolean(run?.is_real_llm);
  const stage = taskWorkspace ? (workspaceStage || "confirmed") : (run?.stage || "未知阶段");
  const isDualReal = Boolean(metadata.analyst_writer_real);
  qs("#stage-name").textContent = stage;
  qs("#stage-detail").textContent = taskWorkspace
    ? (stageDetail || "根据当前任务 Artifact 自动识别")
    : (run?.stage_detail || "根据所选运行自动识别");
  const professionalCopy = stage.startsWith("Step 6C")
    ? "本运行包含 BriefAssessment（简报评估）、竞品角色、KIQ（关键情报问题）、证据覆盖、V2 结论与检索缺口。"
    : "本运行仍使用第一版分析产物。";
  qs("#boundary-copy").textContent = taskWorkspace
    ? `当前六个主页面只读取任务 ${state.activeTaskId} 已保存的 artifacts（产物）；缺失阶段显示空态，不会回退到历史 Demo。`
    : `${professionalCopy} 当前正在查看用户主动选择的历史 Run（调试运行）。`;
  qs("#run-story").textContent = taskWorkspace
    ? `当前任务 ${state.activeTaskId} 已有 ${sources.length} 个来源、${evidence.length} 条证据、${claims.length} 条结论。${savedSummary ? "已生成运行摘要。" : EMPTY_STAGE_COPY}`
    : isDualReal
    ? `本次 Extractor（抽取智能体）使用 Mock（模拟），Analyst 与 Writer（分析与写作智能体）真实调用 DeepSeek；` +
      `形成 ${summary.claims_count} 条通过证据校验的结论，另有 ${metadata.analyst_rejected_claims_count || 0} 条不合格结论被整条拒绝，最终报告审查${summary.approved ? "通过" : "未通过"}。`
    : `本次系统读取了 ${summary.sources_count} 个来源、抽取 ${summary.evidence_count} 条证据，` +
      `由 ${summary.agent_runs_count} 次 Agent（智能体）执行形成 ${summary.claims_count} 条分析结论，` +
      `最终报告审查${summary.approved ? "通过" : "未通过"}。`;
  qs("#hero-review-score").textContent = review?.overall_score ?? summary.review_score ?? "—";
  qs("#hero-pipeline-status").textContent = summary.pipeline_status === "completed" ? "已完成" : summary.pipeline_status;
  qs("#hero-gate-status").textContent = gate?.status === "passed_with_warnings" ? "通过，有提醒" : (gate?.status || "未运行");
  qs("#hero-guardrail-status").textContent = `${guardrailFailures} 项`;
  qs("#chain-status").textContent = summary.approved ? "主链路完整" : (taskWorkspace ? "渐进生成中" : "需要处理");

  const keys = [
    "sources_count",
    "evidence_count",
    "product_cards_count",
    "claims_count",
    "citation_checks_count",
    "reports_count",
    "dag_nodes_count",
    "agent_runs_count",
    "tool_calls_count",
    "supported_count",
    "weak_count",
    "review_score",
  ];

  qs("#summary-grid").innerHTML = keys
    .map(
      (key) => `
        <div class="summary-item">
          <span>${artifactLabels[key]}</span>
          <strong>${escapeHtml(summary[key])}</strong>
        </div>
      `,
    )
    .join("");

  const chain = [
    ["SourceDocument", "来源文档", summary.sources_count],
    ["SourceEvidence", "结构化证据", summary.evidence_count],
    ["ProductCard", "产品卡片", summary.product_cards_count],
    ["AnalysisClaim", "分析结论", summary.claims_count],
    ["CitationCheck", "引用检查", summary.citation_checks_count],
    ["CompetitiveReport", "竞品报告", summary.reports_count],
    ["ReviewFeedback", "审查反馈", summary.review_feedback_count],
  ];
  qs("#evidence-chain").innerHTML = chain
    .map(
      ([name, label, count], index) => `
        <div class="chain-step">
          <span class="chain-index">${String(index + 1).padStart(2, "0")}</span>
          <strong>${escapeHtml(label)}</strong>
          <small>${escapeHtml(name)}</small>
          <b>${escapeHtml(count)}</b>
        </div>
      `,
    )
    .join("");

  qs("#product-count").textContent = productCards.length;
  qs("#product-list").innerHTML = productCards.length ? productCards
    .map(
      (product) => `
        <article class="list-item">
          <div class="item-head">
            <h4 class="item-title">${escapeHtml(product.name)}</h4>
            <span class="tag">${escapeHtml(product.confidence)}</span>
          </div>
          <div class="small-text">${escapeHtml(product.positioning || "暂无定位摘要")}</div>
          <div class="chip-row">
            ${(product.core_features || []).slice(0, 5).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}
          </div>
          <div class="item-meta">sources=${product.source_ids?.length ?? 0} · evidence=${product.evidence_ids?.length ?? 0}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#citation-count").textContent = citationChecks.length;
  qs("#citation-list").innerHTML = citationChecks.length ? citationChecks
    .map(
      (check) => `
        <article class="list-item">
          <div class="item-head">
            <h4 class="item-title">${escapeHtml(check.claim_id)}</h4>
            <span class="tag ${escapeHtml(check.status)}">${escapeHtml(check.status)}</span>
          </div>
          <div class="small-text">${escapeHtml(check.message)}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;
}

function renderWorkflow() {
  const { taskBoard, taskRecords = [], trace = { dag_nodes: [], agent_runs: [], tool_calls: [] } } = state.data;
  const completed = taskRecords.filter((task) => task.status === "completed").length;
  const skipped = taskRecords.filter((task) => task.status === "skipped").length;
  qs("#task-count").textContent = `${taskRecords.length} tasks`;
  qs("#board-summary").innerHTML = taskBoard ? `
    <span><b>${completed}</b> 已完成</span>
    <span><b>${skipped}</b> 待后续能力</span>
    <span><b>${escapeHtml(taskBoard.status)}</b> 任务板</span>
  ` : `<span><b>0</b> ${EMPTY_STAGE_COPY}</span>`;
  qs("#task-board-list").innerHTML = taskRecords.length ? taskRecords
    .map(
      (task, index) => `
        <article class="task-card">
          <div class="task-number">${String(index + 1).padStart(2, "0")}</div>
          <div class="task-main">
            <div class="item-head">
              <h4 class="item-title">${escapeHtml(taskLabels[task.task_key] || taskLabels[task.task_type] || task.task_key)}</h4>
              <span class="tag ${escapeHtml(task.status)}">${escapeHtml(task.status)}</span>
            </div>
            <div class="task-role">${escapeHtml(roleLabels[task.target_agent_role] || task.target_agent_role)}</div>
            <div class="item-meta">依赖：${escapeHtml(task.depends_on?.join("、") || "无")} · 尝试 ${escapeHtml(task.attempts)}/${escapeHtml(task.max_attempts)}</div>
          </div>
          <div class="task-io"><span>输入 ${task.input_refs?.length || 0}</span><span>输出 ${task.output_refs?.length || 0}</span></div>
        </article>
      `,
    )
    .join("") : `<article class="task-card"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;
  renderTrace();
}

function renderLLM() {
  const { run, summary, llmCalls = [], llmOutputs = [] } = state.data;
  const metadata = summary?.metadata || {};
  const validOutputs = llmOutputs.filter((output) => output.validation_status === "passed").length;
  const fallbackCount = llmCalls.filter((call) => call.used_fallback).length;
  const outputByCall = new Map(llmOutputs.map((output) => [output.llm_call_id, output]));
  const isRealLLM = Boolean(run?.is_real_llm);
  const isStep6C = run?.stage?.startsWith("Step 6C");
  const isDualReal = Boolean(metadata.analyst_writer_real);
  const rejectedClaims = Number(metadata.analyst_rejected_claims_count || 0);

  qs("#llm-stage-kicker").textContent = state.activeTaskId
    ? `当前任务 · ${state.activeTaskId}`
    : isDualReal
    ? "Step 6C.3 · Real Analyst + Writer（真实分析与写作）"
    : isStep6C
    ? `Step 6C · Professional Analysis（专业分析）· ${isRealLLM ? "真实模型" : "mock 模拟"}`
    : isRealLLM
      ? "Step 6B.2 · Real Structured Output（真实结构化输出）"
      : "Step 6A · Mock Structured Output（模拟结构化输出）";
  qs("#llm-stage-copy").textContent = state.activeTaskId && !llmCalls.length
    ? EMPTY_STAGE_COPY
    : isDualReal
    ? `Extractor（抽取智能体）固定使用 Mock；Analyst 与 Writer（分析与写作智能体）分别真实调用 ${metadata.analyst_llm_model || run.model}。Analyst 原始输出中有 ${rejectedClaims} 条证据归属不合格结论被整条拒绝，只有校验后的结论进入报告。`
    : isStep6C
    ? `本次运行在证据优先主链路之外新增专业分析产物；${run.model} 负责结构化生成，所有关键结论仍必须绑定 evidence_ids（证据编号）并通过质量校验。`
    : isRealLLM
      ? `本次运行由 ${run.model} 真实参与 Extractor、Analyst、Writer（提取、分析、写作）三个阶段，所有输出继续经过中文、结构和证据引用校验。`
      : "本次运行使用 mock LLM（模拟大模型）复现工程链路，适合无网络回归验证。";

  qs("#llm-summary").innerHTML = `
    <span><b>${llmCalls.length}</b> 次调用</span>
    <span><b>${validOutputs}/${llmOutputs.length}</b> 校验通过</span>
    <span><b>${fallbackCount}</b> 次回退</span>
    ${isDualReal ? `<span><b>${rejectedClaims}</b> 条分析结论被拒绝</span>` : ""}
  `;
  qs("#llm-output-count").textContent = `${llmOutputs.length} outputs`;
  qs("#llm-call-list").innerHTML = llmCalls.length ? llmCalls
    .map((call, index) => {
      const output = outputByCall.get(call.id);
      const inputCount = Object.values(call.input_artifact_refs || {}).reduce((total, refs) => total + refs.length, 0);
      return `
        <article class="llm-card">
          <div class="llm-card-top">
            <span class="llm-step">0${index + 1}</span>
            <div class="chip-row">
              <span class="tag ${call.provider === "mock" ? "warning" : "completed"}">${call.provider === "mock" ? "Mock 模拟" : "真实 LLM"}</span>
              <span class="tag ${escapeHtml(call.status)}">${escapeHtml(call.status)}</span>
            </div>
          </div>
          <p class="panel-kicker">${escapeHtml(roleLabels[call.agent_role] || call.agent_role)}</p>
          <h3>${escapeHtml(call.output_schema)}</h3>
          <p>${escapeHtml(call.prompt_summary || "生成结构化业务产物")}</p>
          <div class="llm-facts">
            <span>模型 <b>${escapeHtml(call.model)}</b></span>
            <span>输入引用 <b>${inputCount}</b></span>
            <span>解析对象 <b>${output?.parsed_object_ids?.length || 0}</b></span>
            <span>耗时 <b>${escapeHtml(call.duration_ms)}ms</b></span>
          </div>
          <div class="llm-card-foot">
            <span class="tag ${output?.validation_status === "passed" ? "completed" : "failed"}">schema ${escapeHtml(output?.validation_status || "unknown")}</span>
            <span class="tag ${call.used_fallback ? "warning" : "completed"}">fallback=${escapeHtml(call.used_fallback)}</span>
          </div>
        </article>
      `;
    })
    .join("") : `<article class="llm-card"><p>${EMPTY_STAGE_COPY}</p></article>`;

  qs("#llm-output-list").innerHTML = llmOutputs.length ? llmOutputs
    .map(
      (output) => `
        <article class="output-row">
          <div>
            <strong>${escapeHtml(output.output_schema)}</strong>
            <div class="item-meta">${escapeHtml(output.id)} · ${escapeHtml(roleLabels[output.agent_role] || output.agent_role)}</div>
          </div>
          <span>${output.parsed_object_ids?.length || 0} 个对象</span>
          <span class="tag ${output.validation_status === "passed" ? "completed" : "failed"}">${escapeHtml(output.validation_status)}</span>
        </article>
      `,
    )
    .join("") : `<article class="output-row"><span>${EMPTY_STAGE_COPY}</span></article>`;

  qs("#runtime-pill").title = `provider=${metadata.llm_provider}; mode=${metadata.llm_mode}`;
}

function metricValue(value) {
  if (typeof value === "number") {
    return value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : String(value);
  }
  if (value && typeof value === "object") {
    return Object.entries(value).map(([key, count]) => `${key}:${count}`).join(" · ");
  }
  return String(value ?? "—");
}

function renderStep6C() {
  const selected = state.data || {};
  const pilot = state.step6cExperiment;
  const pilotResult = pilot?.result || {};
  const experiment = pilot?.experiment || {};
  const pilotHasArtifacts = Boolean(pilot?.analysisPortfolios?.length);
  const selectedHasArtifacts = Boolean(selected.analysisPortfolios?.length);
  const artifacts = pilotHasArtifacts ? pilot : selected;
  const sourceLabel = pilotHasArtifacts ? "DeepSeek 真实实验" : selectedHasArtifacts ? "所选工作流" : "暂无 Step 6C 产物";
  const profiles = artifacts.competitorProfiles || [];
  const kiqs = artifacts.intelligenceQuestions || [];
  const coverage = artifacts.evidenceCoverage || [];
  const comparability = artifacts.comparabilityNotes || [];
  const claims = artifacts.claimsV2 || [];
  const gaps = artifacts.researchGaps || [];
  const brief = artifacts.briefAssessments?.[0];
  const evidence = artifacts.evidence || selected.evidence || [];
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));

  qs("#step6c-summary").innerHTML = `
    <span><b>${escapeHtml(sourceLabel)}</b> 当前展示</span>
    <span><b>${profiles.length}</b> 竞品画像</span>
    <span><b>${claims.length}</b> V2 结论</span>
    <span><b>${gaps.length}</b> 检索缺口</span>
  `;

  const experimentStatus = pilotResult.status || experiment.status || "not_run";
  const experimentPassed = ["completed", "completed_with_rejections", "passed"].includes(experimentStatus);
  const rejectedClaimsCount = pilotResult.rejected_claims_count || 0;
  const banner = qs("#experiment-banner");
  banner.className = `experiment-banner ${experimentPassed ? "passed" : "failed"}`;
  qs("#experiment-title").textContent = pilot
    ? `${experiment.model || "DeepSeek"} · Prompt ${pilotResult.prompt_version || "unknown"} · ${experimentPassed ? rejectedClaimsCount ? `通过，剔除 ${rejectedClaimsCount} 条不合格结论` : "已通过" : "被质量闸门拦截"}`
    : "尚未发现真实模型实验记录";
  const failedMetrics = pilotResult.quality_gate_failures || [];
  qs("#experiment-copy").textContent = pilot
    ? experimentPassed
      ? rejectedClaimsCount
        ? `Harness（运行框架）没有改写或补造证据，而是整条剔除 ${rejectedClaimsCount} 条不满足逐竞品证据规则的结论；其余产物已通过全部指标，可进入下一步人工审查。`
        : "真实模型输出已通过结构、证据和专业分析指标，可进入下一步人工审查。"
      : `模型已经生成了结构化专业分析，但 ${failedMetrics.join("、") || "质量指标"} 未达标，因此没有进入正式报告。`
    : "当前只能展示所选工作流产物，尚无可读取的 DeepSeek 真实实验。";
  qs("#experiment-facts").innerHTML = pilot
    ? `
        <span>实验状态 <b>${escapeHtml(experimentStatus)}</b></span>
        <span>指标通过率 <b>${escapeHtml(metricValue(pilotResult.metric_pass_rate))}</b></span>
        <span>输入 tokens（词元） <b>${escapeHtml(pilotResult.input_tokens ?? "—")}</b></span>
        <span>输出 tokens（词元） <b>${escapeHtml(pilotResult.output_tokens ?? "—")}</b></span>
        <span>剔除结论 <b>${escapeHtml(rejectedClaimsCount)}</b></span>
        <span>阻断指标 <b>${escapeHtml(failedMetrics.join("、") || "无")}</b></span>
      `
    : '<span>状态 <b>not_run</b></span>';

  qs("#brief-status").textContent = brief
    ? brief.sufficient_for_analysis ? "信息充分" : "信息不足，带缺口分析"
    : "无数据";
  qs("#brief-status").className = `count-label tag ${brief?.sufficient_for_analysis ? "true" : "warning"}`;
  qs("#brief-panel").innerHTML = brief
    ? `
        <article class="list-item">
          <p class="panel-kicker">Decision question（决策问题）</p>
          <h4 class="item-title">${escapeHtml(brief.decision_question)}</h4>
          <div class="small-text">行业：${escapeHtml(brief.industry || "未提供")} · 分析维度：${escapeHtml((brief.selected_dimensions || []).join("、"))}</div>
          <div class="chip-row">${(brief.missing_fields || []).map((item) => `<span class="chip warning-chip">缺少 ${escapeHtml(item)}</span>`).join("")}</div>
        </article>
      `
    : '<article class="list-item"><div class="small-text">此运行没有 BriefAssessment（简报评估）产物。</div></article>';

  qs("#kiq-count").textContent = kiqs.length;
  qs("#kiq-list").innerHTML = kiqs.length
    ? kiqs.map((item) => `
        <article class="list-item">
          <div class="item-head"><h4 class="item-title">${escapeHtml(item.question)}</h4><span class="tag ${escapeHtml(item.priority)}">${escapeHtml(item.priority)}</span></div>
          <div class="small-text">决策关联：${escapeHtml(item.decision_link)}</div>
          <div class="chip-row">${(item.dimensions || []).map((dimension) => `<span class="chip">${escapeHtml(dimension)}</span>`).join("")}</div>
        </article>
      `).join("")
    : '<article class="list-item"><div class="small-text">暂无 KIQ（关键情报问题）。</div></article>';

  qs("#profile-count").textContent = profiles.length;
  qs("#profile-list").innerHTML = profiles.length
    ? profiles.map((profile) => `
        <article class="profile-card">
          <div class="item-head"><h4 class="item-title">${escapeHtml(profile.name)}</h4><span class="tag">${escapeHtml(profile.role)}</span></div>
          <p>${escapeHtml(profile.selection_reason)}</p>
          <dl>
            <div><dt>代表路径</dt><dd>${escapeHtml(profile.represented_path)}</dd></div>
            <div><dt>价值主张</dt><dd>${escapeHtml(profile.value_proposition)}</dd></div>
            <div><dt>交付模式</dt><dd>${escapeHtml(profile.delivery_model)}</dd></div>
          </dl>
          <div class="chip-row">${(profile.comparable_dimensions || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>
        </article>
      `).join("")
    : '<article class="list-item"><div class="small-text">暂无 CompetitorProfile（竞品画像）。</div></article>';

  const coverageCounts = coverage.reduce((counts, item) => {
    counts[item.status] = (counts[item.status] || 0) + 1;
    return counts;
  }, {});
  qs("#coverage-status").textContent = `${coverage.length} 格 · sufficient ${coverageCounts.sufficient || 0}`;
  qs("#coverage-list").innerHTML = coverage.length
    ? coverage.map((item) => `
        <article class="coverage-cell ${escapeHtml(item.status)}">
          <div><strong>${escapeHtml(item.competitor)}</strong><span>${escapeHtml(item.dimension)}</span></div>
          <span class="tag ${item.status === "sufficient" ? "completed" : "warning"}">${escapeHtml(item.status)}</span>
          <small>${escapeHtml(item.limitations || "无已记录限制")}</small>
        </article>
      `).join("")
    : '<article class="list-item"><div class="small-text">暂无 EvidenceCoverage（证据覆盖）矩阵。</div></article>';

  qs("#comparability-count").textContent = comparability.length;
  qs("#comparability-list").innerHTML = comparability.length
    ? comparability.map((note) => `
        <article class="list-item">
          <div class="item-head"><h4 class="item-title">${escapeHtml(note.dimension)}</h4><span class="tag ${note.comparable ? "true" : "false"}">${note.comparable ? "可比较" : "不可直接比较"}</span></div>
          <div>${escapeHtml((note.competitors || []).join(" vs "))}</div>
          <div class="small-text">口径：${escapeHtml(note.basis)}</div>
          <div class="item-meta">限制：${escapeHtml(note.limitations)}</div>
        </article>
      `).join("")
    : '<article class="list-item"><div class="small-text">暂无 ComparabilityNote（可比性说明）。</div></article>';

  qs("#claim-v2-count").textContent = claims.length;
  qs("#claim-v2-list").innerHTML = claims.length
    ? claims.map((claim) => {
        const coveredCompetitors = new Set(
          (claim.evidence_ids || [])
            .map((id) => evidenceById.get(id)?.competitor)
            .filter(Boolean),
        );
        const missingCompetitors = (claim.competitors || []).filter((name) => !coveredCompetitors.has(name));
        return `
          <article class="claim-v2-card ${missingCompetitors.length ? "quality-warning" : ""}">
            <div class="item-head">
              <div><span class="claim-type">${escapeHtml(claim.claim_type)}</span><h4 class="item-title">${escapeHtml(claim.claim_text)}</h4></div>
              <span class="tag ${missingCompetitors.length ? "failed" : "completed"}">${missingCompetitors.length ? "证据不平衡" : "证据覆盖"}</span>
            </div>
            <div class="claim-v2-meta"><span>维度 ${escapeHtml(claim.dimension)}</span><span>置信度 ${escapeHtml(claim.confidence)}</span><span>证据 ${claim.evidence_ids?.length || 0}</span></div>
            <div class="small-text"><strong>推理摘要：</strong>${escapeHtml(claim.reasoning_summary)}</div>
            <div class="small-text"><strong>不确定性：</strong>${escapeHtml(claim.uncertainty || "未披露")}</div>
            <div class="decision-impact"><strong>决策影响</strong>${escapeHtml(claim.decision_impact)}</div>
            ${missingCompetitors.length ? `<div class="quality-warning-copy">阻断原因：提到了 ${escapeHtml(missingCompetitors.join("、"))}，但 evidence_ids（证据编号）中没有对应竞品证据。</div>` : ""}
          </article>
        `;
      }).join("")
    : '<article class="list-item"><div class="small-text">暂无 AnalysisClaimV2（专业分析结论）。</div></article>';

  qs("#gap-count").textContent = gaps.length;
  qs("#gap-list").innerHTML = gaps.length
    ? gaps.map((gap) => `
        <article class="gap-card">
          <div class="item-head"><h4 class="item-title">${escapeHtml(gap.missing_information)}</h4><span class="tag ${escapeHtml(gap.priority)}">${escapeHtml(gap.priority)}</span></div>
          <p><strong>阻塞的决策：</strong>${escapeHtml(gap.decision_blocked)}</p>
          <p><strong>检索停止条件：</strong>${escapeHtml(gap.stop_condition)}</p>
          <div class="chip-row">${(gap.suggested_queries || []).map((query) => `<span class="chip">${escapeHtml(query)}</span>`).join("")}</div>
        </article>
      `).join("")
    : '<article class="list-item"><div class="small-text">暂无 ResearchGap（检索缺口）。</div></article>';
}

function renderReport() {
  const { report, reportStatements = [] } = state.data;
  if (!report) {
    qs("#report-title").textContent = "CompetitiveReport（竞品分析报告）";
    qs("#report-claim-count").textContent = "尚未生成";
    qs("#report-markdown").innerHTML = `<article class="trace-empty">${EMPTY_STAGE_COPY}</article>`;
    qs("#report-trace-status").textContent = "暂无报告";
    qs("#report-evidence-detail").innerHTML = `<article class="trace-empty">${EMPTY_STAGE_COPY}</article>`;
    applyReportEvidenceVisibility();
    return;
  }
  qs("#report-title").textContent = report.title || "CompetitiveReport（竞品分析报告）";
  const writerVersion = report.sections?.report_version;
  const statements = reportStatements.filter((item) => item.report_id === report.id);
  qs("#report-claim-count").textContent = writerVersion
    ? `${statements.length} 条可追溯论点 · Writer ${writerVersion}`
    : `${statements.length} 条可追溯论点`;
  qs("#report-markdown").innerHTML = reportMarkdownToHtml(
    report.markdown || "",
    statements,
  );
  applyReportEvidenceVisibility();

  qsa("[data-report-statement-id]").forEach((item) => {
    const activate = () => activateReportStatement(item.dataset.reportStatementId);
    const activateAndReveal = () => {
      activate();
      revealReportEvidence();
    };
    item.addEventListener("mouseenter", activate);
    item.addEventListener("focus", activateAndReveal);
    item.addEventListener("click", activateAndReveal);
  });

  if (statements.length) {
    activateReportStatement(statements[0].id);
  } else {
    qs("#report-trace-status").textContent = "当前运行暂无映射";
    qs("#report-evidence-detail").innerHTML = `
      <article class="trace-empty">
        这是一份旧运行产物，尚未生成 ReportStatement（报告论点）映射。请切换到最新 Step 6C 运行。
      </article>
    `;
  }
}

function applyReportEvidenceVisibility() {
  const layout = qs("#report-view .report-layout");
  const panel = qs("#report-evidence-panel");
  const button = qs("#report-evidence-toggle");
  if (!layout || !panel || !button) return;
  const collapsed = state.reportEvidenceCollapsed;
  layout.classList.toggle("evidence-collapsed", collapsed);
  panel.setAttribute("aria-hidden", String(collapsed));
  button.setAttribute("aria-expanded", String(!collapsed));
  button.textContent = "收起";
  button.setAttribute("aria-label", "收起证据追溯栏");
}

function toggleReportEvidence() {
  state.reportEvidenceCollapsed = !state.reportEvidenceCollapsed;
  applyReportEvidenceVisibility();
}

function revealReportEvidence() {
  if (!state.reportEvidenceCollapsed) return;
  state.reportEvidenceCollapsed = false;
  applyReportEvidenceVisibility();
  qs("#report-evidence-panel")?.scrollTo({ top: 0, behavior: "smooth" });
}

function reportMarkdownToHtml(markdown, statements) {
  const statementByLine = new Map(statements.map((item) => [item.line_index, item]));
  const visibleLines = [];
  for (const [lineIndex, line] of markdown.split("\n").entries()) {
    if (line.trim() === "## 结论引用索引") break;
    visibleLines.push({ lineIndex, line });
  }
  return markdownLinesToHtml(visibleLines, statementByLine);
}

function markdownToHtml(markdown) {
  const lines = markdown.split("\n").map((line, lineIndex) => ({ line, lineIndex }));
  return markdownLinesToHtml(lines, new Map());
}

function markdownLinesToHtml(lines, statementByLine) {
  const html = [];
  let inTable = false;
  let tableRows = [];

  function flushTable() {
    if (!inTable) return;
    const rows = tableRows.filter((line) => !/^\s*\|?\s*:?-{3,}:?\s*\|/.test(line.replaceAll("|", "")));
    const tableHtml = rows
      .map((line, index) => {
        const cells = line
          .split("|")
          .map((cell) => cell.trim())
          .filter(Boolean);
        const tag = index === 0 ? "th" : "td";
        return `<tr>${cells.map((cell) => `<${tag}>${inlineMarkdown(cell)}</${tag}>`).join("")}</tr>`;
      })
      .join("");
    html.push(`<table>${tableHtml}</table>`);
    inTable = false;
    tableRows = [];
  }

  for (const { line, lineIndex } of lines) {
    const trimmed = line.trim();
    if (trimmed.includes("|") && trimmed.startsWith("|")) {
      inTable = true;
      tableRows.push(trimmed);
      continue;
    }
    flushTable();

    if (!trimmed) {
      continue;
    }
    const statement = statementByLine.get(lineIndex);
    const readable = statement
      ? trimmed.replace(/\[[^\]]+\]/g, "").replace(/\s+/g, " ").trim()
      : trimmed;
    const isListItem = readable.startsWith("- ");
    const isNestedListItem = /^\s{2,}-\s/.test(line);
    const classNames = [
      statement ? "report-statement" : "",
      isListItem ? "report-list-item" : "",
      isNestedListItem ? "nested" : "",
    ].filter(Boolean);
    const attributes = [
      classNames.length ? ` class="${classNames.join(" ")}"` : "",
      statement ? ` data-report-statement-id="${escapeHtml(statement.id)}" tabindex="0"` : "",
    ].join("");
    const traceHint = statement
      ? '<span class="trace-indicator" aria-hidden="true">查看证据</span>'
      : "";

    if (readable.startsWith("# ")) {
      html.push(`<h1>${inlineMarkdown(readable.slice(2))}</h1>`);
    } else if (readable.startsWith("## ")) {
      html.push(`<h2>${inlineMarkdown(readable.slice(3))}</h2>`);
    } else if (readable.startsWith("### ")) {
      html.push(`<h3>${inlineMarkdown(readable.slice(4))}</h3>`);
    } else if (readable.startsWith("- ")) {
      html.push(`<p${attributes}><span class="report-bullet">•</span>${inlineMarkdown(readable.slice(2))}${traceHint}</p>`);
    } else {
      html.push(`<p${attributes}>${inlineMarkdown(readable)}${traceHint}</p>`);
    }
  }
  flushTable();
  return html.join("");
}

function activateReportStatement(statementId) {
  const {
    reportStatements = [],
    claims = [],
    citationChecks = [],
    evidence = [],
    sources = [],
    researchGaps = [],
  } = state.data;
  const statement = reportStatements.find((item) => item.id === statementId);
  if (!statement) return;

  qsa("[data-report-statement-id]").forEach((item) => {
    item.classList.toggle("active", item.dataset.reportStatementId === statementId);
  });
  const claimById = new Map(claims.map((item) => [item.id, item]));
  const citationByClaimId = new Map(citationChecks.map((item) => [item.claim_id, item]));
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));
  const sourceById = new Map(sources.map((item) => [item.id, item]));
  const gapById = new Map(researchGaps.map((item) => [item.id, item]));
  const boundClaims = (statement.claim_ids || []).map((id) => claimById.get(id)).filter(Boolean);
  const boundEvidence = (statement.evidence_ids || []).map((id) => evidenceById.get(id)).filter(Boolean);
  const boundGaps = (statement.research_gap_ids || []).map((id) => gapById.get(id)).filter(Boolean);
  const status = statement.citation_status || "pending";
  const statusText = status === "pending" && statement.statement_kind === "profile"
    ? "资料已关联"
    : status === "pending" && statement.statement_kind === "research_gap"
      ? "待补充资料"
      : status === "pending" && statement.statement_kind === "comparability"
        ? "方法判断"
        : ({
    supported: "证据支持",
    weak: "弱支持",
    unsupported: "不支持",
    missing_evidence: "缺少证据",
    invalid_evidence: "证据无效",
    pending: "待检查",
  }[status] || status);
  qs("#report-trace-status").textContent = `${statusText} · ${boundEvidence.length} 条证据`;

  const claimHtml = boundClaims.map((claim) => {
    const citation = citationByClaimId.get(claim.id);
    const citationMessage = citation?.status === "supported"
      ? `引用检查：${citation.evidence_ids?.length || 0} 条证据均可追溯到来源文档。`
      : citation?.status === "weak"
        ? `引用检查：${citation.evidence_ids?.length || 0} 条证据可以追溯，但其中包含弱来源，结论需保留限制。`
        : citation?.message || "";
    return `
      <article class="trace-claim-card">
        <div class="trace-section-label">AnalysisClaim（分析结论）</div>
        <p>${escapeHtml(readerFriendlyClaimText(claim.claim_text))}</p>
        ${claim.metadata?.reasoning_summary ? `<div class="small-text"><strong>推理依据：</strong>${escapeHtml(claim.metadata.reasoning_summary)}</div>` : ""}
        ${claim.metadata?.uncertainty ? `<div class="small-text"><strong>不确定性：</strong>${escapeHtml(claim.metadata.uncertainty)}</div>` : ""}
        ${citationMessage ? `<div class="citation-message">${escapeHtml(citationMessage)}</div>` : ""}
      </article>
    `;
  }).join("");

  const evidenceHtml = boundEvidence.map((item) => {
    const source = sourceById.get(item.source_id);
    const sourceUrl = safeExternalUrl(source?.url);
    const sourceLabel = source?.title || "来源文档待补充";
    return `
      <article class="trace-evidence-card">
        <div class="item-head">
          <span class="trace-section-label">SourceEvidence（来源证据）</span>
          <span class="tag">${escapeHtml(item.competitor || item.dimension)}</span>
        </div>
        <blockquote>${escapeHtml(item.snippet || item.normalized_fact)}</blockquote>
        <div class="small-text">维度：${escapeHtml(item.dimension)} · 证据置信度：${Math.round((item.confidence || 0) * 100)}%</div>
        <div class="trace-source-row">
          <span>${escapeHtml(source?.source_type || "manual")}</span>
          ${sourceUrl
            ? `<a href="${sourceUrl}" target="_blank" rel="noreferrer">${escapeHtml(sourceLabel)} ↗</a>`
            : `<span>${escapeHtml(sourceLabel)}</span>`}
        </div>
      </article>
    `;
  }).join("");

  const gapHtml = boundGaps.map((gap) => `
    <article class="trace-gap-card">
      <div class="trace-section-label">ResearchGap（研究缺口）</div>
      <p>${escapeHtml(gap.missing_information)}</p>
      <div class="small-text"><strong>阻塞决策：</strong>${escapeHtml(gap.decision_blocked)}</div>
      <div class="small-text"><strong>停止条件：</strong>${escapeHtml(gap.stop_condition)}</div>
    </article>
  `).join("");

  qs("#report-evidence-detail").innerHTML = `
    <article class="trace-statement-card">
      <div class="item-head">
        <span class="trace-section-label">当前报告论点 · ${escapeHtml(statement.section)}</span>
        <span class="tag ${escapeHtml(status)}">${escapeHtml(statusText)}</span>
      </div>
      <p>${escapeHtml(statement.text)}</p>
      <div class="item-meta">置信度 ${Math.round((statement.confidence || 0) * 100)}%</div>
    </article>
    ${claimHtml}
    ${evidenceHtml || gapHtml || '<article class="trace-empty">这是方法性或上下文判断，当前没有直接绑定来源证据。</article>'}
    ${evidenceHtml ? gapHtml : ""}
  `;
}

function safeExternalUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? escapeHtml(url.href) : "";
  } catch {
    return "";
  }
}

function readerFriendlyClaimText(value) {
  return String(value || "").replace(
    /^(?:现有证据表明|现有资料表明|根据现有证据)[，,]\s*/,
    "",
  );
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderClaims() {
  const { claims = [], citationChecks = [], evidence = [], sources = [] } = state.data;

  const citationByClaimId = new Map(citationChecks.map((item) => [item.claim_id, item]));
  qs("#claim-count").textContent = claims.length;
  qs("#evidence-count").textContent = evidence.length;
  qs("#claim-list").innerHTML = claims.length ? claims
    .map((claim, index) => {
      const citation = citationByClaimId.get(claim.id);
      return `
        <article class="list-item clickable" data-claim-index="${index}">
          <div class="item-head">
            <h4 class="item-title">${escapeHtml(claim.claim_text)}</h4>
            <span class="tag ${escapeHtml(citation?.status || claim.citation_status)}">${escapeHtml(citation?.status || claim.citation_status)}</span>
          </div>
          <div class="item-meta">${escapeHtml(claim.dimension)} · ${escapeHtml(claim.id)} · evidence=${claim.evidence_ids?.length ?? 0}</div>
        </article>
      `;
    })
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qsa("[data-claim-index]").forEach((item) => {
    item.addEventListener("click", () => {
      qsa("[data-claim-index]").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      const claim = claims[Number(item.dataset.claimIndex)];
      renderEvidenceDetail(claim, evidence, sources, citationByClaimId.get(claim.id));
    });
  });

  if (claims.length) {
    const first = qs("[data-claim-index]");
    first?.classList.add("active");
    renderEvidenceDetail(claims[0], evidence, sources, citationByClaimId.get(claims[0].id));
  } else {
    qs("#evidence-detail").innerHTML = `
      <article class="trace-empty">
        ${EMPTY_STAGE_COPY}<br />当前任务已抽取 ${evidence.length} 条 SourceEvidence（来源证据），但尚无可展示的 AnalysisClaim（分析结论）。
      </article>
    `;
  }
}

function renderEvidenceDetail(claim, evidence, sources, citation) {
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));
  const sourceById = new Map(sources.map((item) => [item.id, item]));
  const boundEvidence = (claim.evidence_ids || []).map((id) => evidenceById.get(id)).filter(Boolean);

  qs("#evidence-detail").innerHTML = `
    <article class="list-item">
      <div class="item-head">
        <h4 class="item-title">${escapeHtml(claim.id)}</h4>
        <span class="tag ${escapeHtml(citation?.status || claim.citation_status)}">${escapeHtml(citation?.status || claim.citation_status)}</span>
      </div>
      <div>${escapeHtml(claim.claim_text)}</div>
      <div class="small-text">${escapeHtml(citation?.message || "暂无引用检查说明")}</div>
    </article>
    ${boundEvidence
      .map((item) => {
        const source = sourceById.get(item.source_id);
        return `
          <article class="list-item">
            <div class="item-head">
              <h4 class="item-title">${escapeHtml(item.id)}</h4>
              <span class="tag">${escapeHtml(item.dimension)}</span>
            </div>
            <div>${escapeHtml(item.normalized_fact || item.snippet)}</div>
            <div class="small-text">${escapeHtml(item.snippet)}</div>
            <div class="item-meta">
              SourceDocument（来源文档）:
              ${source ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title)}</a>` : escapeHtml(item.source_id)}
            </div>
          </article>
        `;
      })
      .join("")}
  `;
}

function renderTrace() {
  const { trace = { dag_nodes: [], agent_runs: [], tool_calls: [] } } = state.data;
  const dagNodes = trace.dag_nodes || [];
  const agentRuns = trace.agent_runs || [];
  const toolCalls = trace.tool_calls || [];

  qs("#dag-count").textContent = dagNodes.length;
  qs("#agent-run-count").textContent = agentRuns.length;
  qs("#tool-call-count").textContent = toolCalls.length;

  qs("#dag-list").innerHTML = dagNodes.length ? dagNodes
    .map(
      (node) => `
        <article class="timeline-item">
          <div class="item-head">
            <h4 class="item-title">${escapeHtml(node.label)}</h4>
            <span class="tag ${escapeHtml(node.status)}">${escapeHtml(node.status)}</span>
          </div>
          <div class="item-meta">${escapeHtml(node.agent_role)} · outputs=${node.output_refs?.length ?? 0}</div>
        </article>
      `,
    )
    .join("") : `<article class="timeline-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#agent-run-list").innerHTML = agentRuns.length ? agentRuns
    .map(
      (run) => `
        <article class="list-item">
          <div class="item-head">
            <h4 class="item-title">${escapeHtml(run.agent_role)}</h4>
            <span class="tag ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span>
          </div>
          <div>${escapeHtml(run.output_summary || run.input_summary)}</div>
          <div class="item-meta">${escapeHtml(run.id)} · ${escapeHtml(run.duration_ms)}ms</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#tool-call-list").innerHTML = toolCalls.length ? toolCalls
    .map(
      (call) => `
        <article class="list-item">
          <div class="item-head">
            <h4 class="item-title">${escapeHtml(call.tool_name)}</h4>
            <span class="tag ${escapeHtml(call.status)}">${escapeHtml(call.status)}</span>
          </div>
          <div class="small-text">${escapeHtml(call.output_summary)}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;
}

function renderGovernance() {
  const {
    review,
    evalSummary,
    qualityGates = [],
    feedbackTasks = [],
    guardrailChecks = [],
    contextBundles = [],
    workingMemory,
    memoryItems = [],
  } = state.data;

  const gate = qualityGates?.[0];
  qs("#gate-status").textContent = gate?.status || "尚未生成";
  qs("#gate-status").className = `count-label tag ${gate?.passed ? "completed" : "failed"}`;
  qs("#quality-gate-list").innerHTML = qualityGates.length ? qualityGates
    .map(
      (item) => `
        <article class="list-item">
          <div class="item-head"><h4 class="item-title">${escapeHtml(item.gate_name)}</h4><span class="tag ${item.passed ? "completed" : "failed"}">${escapeHtml(item.status)}</span></div>
          <div>${escapeHtml(item.message)}</div>
          <div class="item-meta">blocking=${escapeHtml(item.blocking)} · issues=${item.issue_ids?.length || 0} · new tasks=${item.created_task_ids?.length || 0}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#feedback-count").textContent = feedbackTasks?.length || 0;
  qs("#feedback-task-list").innerHTML = feedbackTasks.length ? feedbackTasks
    .map(
      (task) => `
        <article class="list-item">
          <div class="item-head"><h4 class="item-title">${escapeHtml(taskLabels[task.task_type] || task.task_key)}</h4><span class="tag ${escapeHtml(task.status)}">${escapeHtml(task.status)}</span></div>
          <div>${escapeHtml(task.reason)}</div>
          <div class="item-meta">${escapeHtml(roleLabels[task.target_agent_role] || task.target_agent_role)} · ${escapeHtml(task.metadata?.execution_policy || "")}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#guardrail-count").textContent = guardrailChecks?.length || 0;
  qs("#guardrail-list").innerHTML = guardrailChecks.length ? guardrailChecks
    .map(
      (check) => `
        <article class="list-item">
          <div class="item-head"><h4 class="item-title">${escapeHtml(check.guardrail_name)}</h4><span class="tag ${escapeHtml(check.status)}">${escapeHtml(check.status)}</span></div>
          <div class="small-text">${escapeHtml(check.message)}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#context-count").textContent = contextBundles?.length || 0;
  qs("#context-list").innerHTML = contextBundles.length ? contextBundles
    .map(
      (bundle) => `
        <article class="list-item">
          <div class="item-head"><h4 class="item-title">${escapeHtml(roleLabels[bundle.agent_role] || bundle.agent_role)}</h4><span class="tag">L${escapeHtml(bundle.compression_level)}</span></div>
          <div class="small-text">tokens ${escapeHtml(bundle.estimated_tokens)} / ${escapeHtml(bundle.token_budget)}</div>
          <div class="item-meta">sources=${bundle.source_ids?.length || 0} · evidence=${bundle.evidence_ids?.length || 0} · claims=${bundle.claim_ids?.length || 0} · memory=${bundle.memory_item_ids?.length || 0}</div>
        </article>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#memory-count").textContent = memoryItems?.length || 0;
  qs("#memory-list").innerHTML = workingMemory || memoryItems.length ? `
    ${workingMemory ? `
    <article class="list-item memory-focus">
      <p class="panel-kicker">Working Memory（工作记忆）</p>
      <h4 class="item-title">${escapeHtml(workingMemory.current_goal)}</h4>
      <div class="small-text">${escapeHtml(workingMemory.task_summary || "")}</div>
      <div class="item-meta">weak claims=${workingMemory.weak_claim_ids?.length || 0} · feedback tasks=${workingMemory.feedback_task_ids?.length || 0}</div>
    </article>
    ` : ""}
    ${memoryItems.map((item) => `
      <article class="list-item">
        <div class="item-head"><h4 class="item-title">${escapeHtml(item.summary || item.key)}</h4><span class="tag">${escapeHtml(item.scope)}</span></div>
        <div class="small-text">${escapeHtml(item.kind)} · confidence=${escapeHtml(item.confidence)}</div>
      </article>
    `).join("")}
  ` : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#review-status").textContent = review ? (review.approved ? "approved=true" : "approved=false") : "尚未生成";
  qs("#review-status").className = `count-label tag ${review?.approved ? "true" : "false"}`;
  qs("#review-panel").innerHTML = review ? `
    <article class="list-item">
      <div class="item-head">
        <h4 class="item-title">Review score（审查分）</h4>
        <span class="tag ${review.approved ? "true" : "false"}">${escapeHtml(review.overall_score)}</span>
      </div>
      <div class="small-text">reviewer_run_id=${escapeHtml(review.reviewer_run_id)}</div>
    </article>
    ${(review.issues || [])
      .map(
        (issue) => `
          <article class="list-item">
            <div class="item-head">
              <h4 class="item-title">${escapeHtml(issue.target_type)} · ${escapeHtml(issue.target_id)}</h4>
              <span class="tag ${escapeHtml(issue.severity)}">${escapeHtml(issue.severity)}</span>
            </div>
            <div>${escapeHtml(issue.message)}</div>
          </article>
        `,
      )
      .join("")}
    ${(review.suggestions || [])
      .map((suggestion) => `<article class="list-item">${escapeHtml(suggestion)}</article>`)
      .join("")}
  ` : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;

  qs("#eval-status").textContent = evalSummary ? (evalSummary.passed ? "passed=true" : "passed=false") : "尚未生成";
  qs("#eval-status").className = `count-label tag ${evalSummary?.passed ? "true" : "false"}`;
  qs("#eval-list").innerHTML = evalSummary ? Object.values(evalSummary.metrics || {})
    .map(
      (metric) => `
        <div class="metric-row">
          <div>
            <strong>${escapeHtml(metric.name)}</strong>
            <div class="small-text">${escapeHtml(metric.details)}</div>
          </div>
          <span>${escapeHtml(metric.value)}</span>
          <span class="tag ${metric.passed ? "true" : "false"}">${metric.passed}</span>
        </div>
      `,
    )
    .join("") : `<article class="list-item"><div class="small-text">${EMPTY_STAGE_COPY}</div></article>`;
}

function setupNavigation() {
  qsa(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.view;
      state.activeView = view;
      qsa(".nav-item").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      qsa(".view").forEach((item) => item.classList.remove("active"));
      qs(`#${view}-view`).classList.add("active");
      qs("#page-title").textContent = views[view];
      if (state.activeTaskId && TASK_WORKSPACE_VIEWS.has(view)) {
        loadTaskWorkspace(state.activeTaskId);
      }
    });
  });
}

function navigateTo(view) {
  const button = qs(`[data-view="${view}"]`);
  if (!button || !views[view]) return;
  state.activeView = view;
  qsa(".nav-item").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  qsa(".view").forEach((item) => item.classList.remove("active"));
  qs(`#${view}-view`)?.classList.add("active");
  qs("#page-title").textContent = views[view];
  if (state.activeTaskId && TASK_WORKSPACE_VIEWS.has(view)) {
    loadTaskWorkspace(state.activeTaskId);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setup() {
  setupNavigation();
  qsa("[data-jump]").forEach((button) => {
    button.addEventListener("click", () => navigateTo(button.dataset.jump));
  });
  qs("#refresh-btn").addEventListener("click", loadTask);
  qs("#report-evidence-toggle").addEventListener("click", toggleReportEvidence);
  qs("#parse-intent-btn").addEventListener("click", parseIntent);
  qs("#load-draft-btn").addEventListener("click", loadSelectedDraft);
  qs("#confirm-draft-btn").addEventListener("click", confirmDraft);
  qs("#build-research-plan-btn").addEventListener("click", buildResearchPlan);
  qs("#run-collector-once-btn").addEventListener("click", runCollectorOnce);
  qs("#run-extractor-once-btn").addEventListener("click", runExtractorOnce);
  qs("#run-coverage-once-btn").addEventListener("click", runCoverageOnce);
  qs("#start-research-loop-btn").addEventListener("click", startResearchLoop);
  qs("#run-research-analysis-btn").addEventListener("click", runResearchAnalysis);
  qs("#run-research-reporting-btn").addEventListener("click", runResearchReporting);
  qs("#refresh-integrations-btn").addEventListener("click", loadIntegrationStatus);
  qsa("#draft-editor input, #draft-editor textarea").forEach((input) => {
    input.addEventListener("input", updateDraftReadiness);
  });
  qs("#run-id").addEventListener("change", () => {
    const selectedValue = qs("#run-id").value;
    const run = state.runs.find((item) => runSelectionValue(item) === selectedValue);
    if (run) {
      loadLegacyDashboard(run, { historyMode: "push" });
    }
  });
  window.addEventListener("popstate", () => {
    restoreTaskContext();
  });
  loadRecentDrafts();
  loadIntegrationStatus();
  loadRecentTasks();
  loadRuns({ selectDefaultLegacy: false });
  restoreTaskContext();
}

setup();
