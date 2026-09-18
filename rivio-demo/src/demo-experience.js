/* Demo adapter: reuse the product renderers and WorkspaceProjection in app.js. */
async function setupDemoExperience() {
  const provider = window.researchDataProvider;
  document.body.classList.add('demo-mode');
  document.title = 'Rivio · Demo Replay';
  const banner = document.createElement('section');
  banner.className = 'demo-banner';
  banner.innerHTML = `<strong>Demo Replay · 基于真实历史运行记录的加速回放，不会重新调用 LLM 或外部搜索服务</strong>
    <div class="demo-controls"><button id="demo-play" disabled>开始 Demo 回放</button>
    <button id="demo-report" disabled>直接查看完整报告</button><button id="demo-skip" hidden>跳过回放</button>
    <button id="demo-replay" disabled>重新播放</button><span id="demo-clock" role="status">正在读取静态记录…</span></div>`;
  document.body.prepend(banner);
  const bannerSize = new ResizeObserver(() => document.body.style.setProperty('--demo-banner-height', `${banner.offsetHeight}px`));
  bannerSize.observe(banner);
  const stats = document.createElement('section');
  stats.className = 'demo-stats'; stats.id = 'demo-stats';
  qs('.workspace-product-header').after(stats);
  qs('#product-research-request').value = '小红书与抖音竞品分析';
  qs('#product-research-request').readOnly = true;
  qsa('.research-eyebrow').forEach(e => { e.textContent = e.textContent.replace(/LIVE/g, 'RECORDED'); });
  qsa('#product-brief-confirm-state input, #product-brief-confirm-state textarea').forEach(e => { e.readOnly = true; });
  let data;
  let screen = 'home';
  let lastFrame = '';
  let introPhase = '';
  function setUrl(view) {
    const url = new URL(location.href); url.searchParams.set('demo', '1');
    url.searchParams.delete('task');
    if (view === 'home') url.searchParams.delete('view'); else url.searchParams.set('view', view);
    history.replaceState(null, '', url);
  }
  function apply(snapshot) {
    qs('#product-parse-brief-btn').classList.toggle('demo-click-target', screen === 'playing' && snapshot.elapsed >= 1600 && snapshot.elapsed < 2000);
    qs('#product-confirm-brief-btn').classList.toggle('demo-click-target', screen === 'playing' && snapshot.elapsed >= 2500 && snapshot.elapsed < 3000);
    qs('#demo-clock').textContent = snapshot.finished ? '已加载历史最终结果' : `回放 ${(snapshot.elapsed / 1000).toFixed(1)} / 33 秒（非原始耗时）`;
    // Presentation of the saved request, not a recording of historical keystrokes.
    // Update before the event-frame guard: the first Pipeline event is at 3s.
    if (screen === 'playing' && snapshot.elapsed < 3000) {
      const phase = snapshot.elapsed < 2000 ? 'input' : 'confirm';
      if (introPhase !== phase) {
        introPhase = phase;
        setProductBriefPhase(phase);
      }
      if (phase === 'input') {
        const request = Array.from(data.brief.request_text);
        const count = Math.floor(request.length * Math.min(1, snapshot.elapsed / 1600));
        qs('#product-research-request').value = request.slice(0, count).join('');
        qs('#product-brief-input-status').textContent = '历史需求输入演示 · 使用已保存的原文，不提交请求';
      }
    }
    const frame = `${snapshot.events.length}:${snapshot.finished}`;
    if (frame === lastFrame) return;
    lastFrame = frame;
    state.data = normalizeWorkspaceData(snapshot.workspace, data.provenance.taskId);
    state.analysisTask = state.data.analysisTask;
    state.researchLoopRun = snapshot.pipelineRun;
    state.productEvents = snapshot.events;
    state.researchLoopEvents = snapshot.events;
    renderProductWorkspace();
    renderProductReport();
    const cov = snapshot.coverage;
    const counts = cov?.coverage_status_counts;
    const tasks = snapshot.workspace.researchTasks || [];
    const taskStatus = task => snapshot.finished ? task.status : (snapshot.taskOutcomes[task.id] || '尚未回放／尚未执行');
    const coverageText = counts ? `充分 ${counts.sufficient || 0} · 部分 ${counts.partial || 0} · 较弱 ${counts.weak || 0} · 缺失 ${counts.missing || 0}` : '等待历史 Coverage 检查点';
    const expanded = [...stats.querySelectorAll('details')].map(detail => detail.open);
    stats.innerHTML = `<div class="demo-metrics"><strong>Evidence ${state.data.evidence.length}</strong>
      <strong>已出现来源 ${state.data.sources.length}</strong><span>${cov ? `最近检查点：${cov.total_sources} 个采集来源` : '来源随历史记录出现'}</span></div>
      <p>Coverage：${escapeHtml(coverageText)}。流程进度不代表证据覆盖率。</p>
      <details><summary>历史研究任务（当前已展示 ${tasks.length} 项）</summary>${tasks.map(t => `<p>${escapeHtml(t.title)} — <strong>${escapeHtml(taskStatus(t))}</strong></p>`).join('')}</details>
      ${state.data.evidenceCoverage.length ? `<details><summary>最终 Coverage 明细（保留异常合并对象）</summary>${state.data.evidenceCoverage.map(c => `<p>${escapeHtml(c.competitor)} · ${escapeHtml(c.dimension)}：${escapeHtml(c.status)}</p>`).join('')}</details>` : ''}
      <details><summary>本地来源与证据摘录（无需请求原站）</summary>${state.data.sources.map(s => `<article><strong>${escapeHtml(s.title)}</strong><p>${escapeHtml(s.url)}</p>${state.data.evidence.filter(e => e.source_id === s.id).map(e => `<blockquote>${escapeHtml(e.snippet)}</blockquote>`).join('') || '<p>该来源暂无关联证据。</p>'}</article>`).join('')}</details>`;
    stats.querySelectorAll('details').forEach((detail, index) => { detail.open = expanded[index] || false; });
    // The panel owns overflow, not the inner stream. Wait for layout, and follow
    // every update even when WorkspaceProjection's rolling list stays at 160.
    requestAnimationFrame(() => {
      if (state.productView !== 'workspace') return;
      const panel = qs('#product-activity-stream').closest('.activity-stream-panel');
      panel.scrollTop = panel.scrollHeight;
    });
    qs('#product-workspace-status').textContent = snapshot.finished ? '历史流程已完成 · 仍有证据缺口' : '历史记录回放中';
    if (!snapshot.events.length) qs('#product-activity-stream').textContent = '等待回放历史事件。';
    if (!state.data.evidence.length) qs('#product-selected-evidence-chain').textContent = '等待历史验证证据。';
    qs('#demo-clock').textContent = snapshot.finished ? '已加载历史最终结果' : `回放 ${(snapshot.elapsed / 1000).toFixed(1)} / 33 秒（非原始耗时）`;
    qs('#demo-skip').hidden = snapshot.finished || screen !== 'playing';
    if (screen === 'playing' && snapshot.elapsed >= 3000 && state.productView === 'brief') navigateProduct('workspace');
    if (screen === 'playing' && snapshot.finished) { screen = 'report'; navigateProduct('report'); setUrl('report'); }
  }
  function play() {
    screen = 'playing'; setUrl('replay');
    lastFrame = '';
    introPhase = '';
    qs('#product-research-request').value = '';
    state.productLastActivityCount = 0; state.productSelectedReportStatementId = '';
    state.productReportEvidenceCollapsed = false;
    state.intentDraft = { ...data.brief, status: 'confirmed' };
    renderProductBriefSummary(state.intentDraft);
    qs('#product-brief-confirm-status').textContent = '历史已确认 Brief · 仅展示，不提交研究请求。';
    setProductBriefPhase('input'); navigateProduct('brief');
    provider.replay.play(apply);
  }
  function report() {
    screen = 'report'; lastFrame = ''; apply(provider.replay.skip()); navigateProduct('report'); setUrl('report');
  }
  function home() {
    screen = 'home'; lastFrame = ''; provider.replay.reset(); apply(provider.replay.snapshot());
    qs('#product-research-request').value = data.brief.request_text;
    qs('#product-brief-input-status').textContent = '点击“开始 Demo 回放”，演示输入需求 → 已确认 Brief → 团队研究。';
    setProductBriefPhase('input'); navigateProduct('brief'); setUrl('home');
    qs('#demo-clock').textContent = '单一历史 Run · 约 33 秒';
  }
  try {
    data = await provider.load();
    ['#demo-play', '#demo-report', '#demo-replay'].forEach(id => { qs(id).disabled = false; });
    qs('#demo-play').addEventListener('click', play);
    qs('#product-parse-brief-btn').addEventListener('click', () => {
      provider.replay.reset(); screen = 'confirm'; lastFrame = '';
      apply(provider.replay.snapshot());
      state.intentDraft = { ...data.brief, status: 'confirmed' };
      renderProductBriefSummary(state.intentDraft);
      setProductBriefPhase('confirm'); navigateProduct('brief');
      qs('#product-brief-confirm-status').textContent = '请检查 Research Brief 后确认。';
    });
    qs('#product-confirm-brief-btn').addEventListener('click', () => {
      screen = 'playing'; lastFrame = ''; setUrl('replay');
      provider.replay.play(apply, 3000);
    });
    qs('#product-edit-brief-btn').addEventListener('click', home);
    qs('#demo-replay').addEventListener('click', play);
    qs('#demo-report').addEventListener('click', report);
    qs('#demo-skip').addEventListener('click', report);
    qs('#product-home-btn').addEventListener('click', home);
    qsa('[data-product-view]').forEach(button => button.addEventListener('click', () => {
      if (button.dataset.productView === 'report') report();
      else if (button.dataset.productView === 'brief') home();
      else navigateProduct('workspace');
    }));
    qs('#product-report-evidence-toggle').addEventListener('click', toggleProductReportEvidence);
    window.addEventListener('pagehide', () => { provider.dispose(); bannerSize.disconnect(); });
    window.addEventListener('popstate', () => {
      if (new URLSearchParams(location.search).get('view') === 'report') report(); else home();
    });
    const view = new URLSearchParams(location.search).get('view');
    if (view === 'report') report(); else if (view === 'replay') play(); else home();
  } catch (error) {
    provider.dispose(); qs('#demo-clock').textContent = `${error.message}；不会连接 Live 服务。`;
  }
}
