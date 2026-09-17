// Run: node frontend/check-demo.mjs (Playwright must be resolvable via NODE_PATH).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const frontendRoot = path.dirname(fileURLToPath(import.meta.url));
const root = process.env.DEMO_STATIC_ROOT ? path.resolve(process.env.DEMO_STATIC_ROOT) : frontendRoot;
const data = JSON.parse(fs.readFileSync(path.join(root, 'demo/demo_run.json')));
const sandbox = { window: {}, clearInterval, setInterval, performance };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'src/demo-replay.js'), 'utf8'), sandbox);
const replay = new sandbox.window.DemoReplay(data);
for (const event of data.replay.events) {
  const snapshot = replay.advance(event.atMs);
  if (event.sequence === 285) {
    assert.equal(snapshot.workspace.evidence.length, 11);
    assert.equal(snapshot.coverage.total_sources, 16);
    assert.equal(snapshot.workspace.evidenceCoverage.length, 0);
  }
  if (event.sequence < 337) assert.equal(snapshot.workspace.claims.length, 0);
  if (event.sequence < 341) assert.equal(snapshot.workspace.report, null);
}
assert.deepEqual(JSON.parse(JSON.stringify(replay.workspace)), data.finalSnapshot.workspace);
replay.reset(); assert.equal(replay.workspace.evidence.length, 0); assert.equal(replay.workspace.report, null);
assert.equal(replay.skip().workspace.evidence.length, 13);

// Live transport contract remains unchanged, without contacting a real service.
const calls = [];
const liveSandbox = { window: {}, fetch: async (...args) => { calls.push(args); return { ok: true, json: async () => ({ ok: true }) }; },
  EventSource: class { constructor(url) { this.url = url; } } };
vm.createContext(liveSandbox);
vm.runInContext(fs.readFileSync(path.join(frontendRoot, 'src/live-data-provider.js'), 'utf8'), liveSandbox);
const live = new liveSandbox.window.LiveDataProvider('http://example.test');
const options = { method: 'POST', body: '{"unchanged":true}' };
await live.request('/api/test', options);
assert.equal(calls[0][0], 'http://example.test/api/test'); assert.equal(calls[0][1], options);
assert.equal(live.subscribePipeline('/api/events?after=7').url, 'http://example.test/api/events?after=7');
liveSandbox.fetch = async () => ({ ok: false, status: 409, statusText: 'Conflict', text: async () => '{"detail":"unchanged"}' });
await assert.rejects(() => live.request('/api/test'), /409 Conflict: unchanged/);

// A static server only: deliberately no backend/API route exists.
const apiHits = [];
const server = http.createServer((req, res) => {
  const route = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  if (route.startsWith('/api/')) { apiHits.push(route); res.writeHead(503); res.end(); return; }
  const file = path.resolve(root, '.' + (route === '/' ? '/index.html' : route));
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
  res.setHeader('Content-Type', ({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.css':'text/css; charset=utf-8'})[path.extname(file)] || 'application/octet-stream');
  res.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  browser = await chromium.launch({ headless: true, ...(process.env.DEMO_BROWSER_CHANNEL ? { channel: process.env.DEMO_BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    window.__sseAttempts = 0;
    window.EventSource = class { constructor() { window.__sseAttempts++; throw new Error('SSE forbidden in test'); } };
    localStorage.setItem('rivio-test-sentinel', 'unchanged');
  });
  const requests = []; const errors = [];
  context.on('request', request => requests.push(request.url()));
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + (process.env.DEMO_STATIC_ROOT ? '/' : '/?demo=1'));
  await page.locator('#demo-play:enabled').waitFor();
  const stored = await page.evaluate(() => JSON.stringify(localStorage));
  assert.equal(await page.locator('#product-brief-title').textContent(), '小红书与抖音竞品分析');
  await page.locator('#demo-report').click();
  await page.locator('#product-report-view.active').waitFor();
  assert.equal(await page.locator('#product-report-title').textContent(), data.finalSnapshot.workspace.report.title);
  const statements = page.locator('#product-report-body [data-report-statement-id]');
  assert.equal(await statements.count(), 32);
  for (let i = 0; i < await statements.count(); i++) {
    await statements.nth(i).click();
    assert((await page.locator('#product-report-evidence-detail').textContent()).trim());
  }
  assert.equal(await page.evaluate(() => state.data.evidence.length), 13);
  assert.equal(await page.evaluate(() => state.data.sources.length), 17);
  assert.equal(await page.evaluate(() => JSON.stringify(localStorage)), stored);
  const screenshotDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rivio-demo-'));
  await page.screenshot({ path: path.join(screenshotDir, 'report.png'), fullPage: false });
  await page.locator('#demo-replay').click();
  assert.equal(await page.evaluate(() => state.data.report), null);
  await page.locator('#product-brief-input-state.active').waitFor();
  await page.waitForTimeout(800);
  const typed = await page.locator('#product-research-request').inputValue();
  assert(typed.length > 0 && typed.length < data.brief.request_text.length);
  assert(data.brief.request_text.startsWith(typed));
  await page.locator('#product-brief-confirm-state.active').waitFor();
  assert.equal(await page.locator('#product-research-request').inputValue(), data.brief.request_text);
  await page.waitForTimeout(9000);
  assert.equal(await page.locator('#product-workspace-view').getAttribute('class'), 'research-page active');
  const evidenceMidway = await page.evaluate(() => state.data.evidence.length);
  assert(evidenceMidway > 0 && evidenceMidway < 13);
  await page.screenshot({ path: path.join(screenshotDir, 'workspace.png'), fullPage: false });
  await page.locator('#demo-skip').click();
  assert.equal(await page.evaluate(() => state.data.evidence.length), 13);
  await page.locator('#demo-replay').click();
  await page.locator('#product-report-view.active').waitFor({ timeout: 37000 });
  assert.equal(await page.evaluate(() => state.data.researchTasks.filter(t => t.status === 'waiting_for_collector').length), 14);
  assert.equal(await page.evaluate(() => state.data.researchTasks.filter(t => t.status === 'evidence_exhausted').length), 7);
  assert.equal(await page.evaluate(() => state.data.evidenceCoverage.filter(c => c.status === 'sufficient').length), 1);
  await page.reload();
  await page.locator('#product-report-view.active').waitFor();
  assert.equal(await page.evaluate(() => window.__sseAttempts), 0);
  // Even accidental legacy calls fail locally, without opening a connection.
  assert(await page.evaluate(async () => { try { await fetchJson('/api/runs'); return false; } catch { return true; } }));
  assert(await page.evaluate(() => { try { window.researchDataProvider.subscribePipeline('/api/events'); return false; } catch { return true; } }));
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const external = requests.filter(url => !url.startsWith(base + '/'));
  assert.deepEqual(external, []); assert.deepEqual(apiHits, []); assert.deepEqual(errors, []);
  // Missing static data is terminal; no Live fallback.
  const broken = await context.newPage();
  await broken.route('**/demo/demo_run.json', route => route.fulfill({ status: 404, body: '' }));
  await broken.goto(base + '/?demo=1');
  await broken.getByText('静态 Demo 数据加载失败 (404)；不会连接 Live 服务。').waitFor();
  assert(await broken.locator('#demo-play').isDisabled());
  assert.deepEqual(apiHits, []);
  if (process.env.DEMO_STATIC_ROOT) {
    await page.goto(base + '/?demo=0');
    await page.locator('#demo-play:enabled').waitFor();
    assert.equal(await page.evaluate(() => window.researchDataProvider.mode), 'demo');
    assert.equal(await page.evaluate(() => typeof window.LiveDataProvider), 'undefined');
    assert.deepEqual(apiHits, []);
  }
  console.log(JSON.stringify({ result: 'PASS', backend: 'absent; static server only',
    checks: ['saved request typing → Brief → Workspace', 'event checkpoints', 'no early report/claims', 'direct report', '32 citation interactions',
      'replay reset', 'skip', 'full 33s replay', 'refresh report deep link', 'historical gaps',
      'localStorage isolation', 'mobile width', 'missing JSON fails closed', 'API/SSE guards', 'Live transport contract'],
    evidenceMidway, apiRequests: apiHits.length, externalRequests: external.length,
    eventSourceAttempts: await page.evaluate(() => window.__sseAttempts), pageErrors: errors, screenshotDir }, null, 2));
} finally {
  await browser?.close(); await new Promise(resolve => server.close(resolve));
}
