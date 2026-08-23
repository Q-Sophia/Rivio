# Step6E.2：网页采集、浏览器回退与搜索发现

## 已完成能力

Collector（采集智能体）只领取 `ready + collector + waiting_for_collector` 的动态研究任务，并按以下有界流程执行：

```text
ResearchTask
  -> 有 Seed URL：直接采集
  -> 无 Seed URL：SearchProvider（搜索供应商）发现候选 URL
  -> URL / DNS / 重定向 / robots.txt 安全检查
  -> 普通 HTTP 正文提取
  -> 正文过短时 Browser Fallback（浏览器渲染回退）
  -> SourceDocument + WebPageContent + CollectionAttempt
```

Browser Fallback 使用本机 Edge / Chrome 的 Headless Mode（无头模式）和一次性浏览器配置目录；普通 HTTP 能读取时不会启动浏览器。单页有固定超时和渲染预算，不会无限等待或重试。

SearchProvider 当前以智谱 Web Search API（网页搜索接口）为主要实现，同时保留博查适配器备用。搜索密钥与 DeepSeek 密钥严格分离：

```powershell
[Environment]::SetEnvironmentVariable("SEARCH_PROVIDER", "zhipu", "User")
[Environment]::SetEnvironmentVariable("ZHIPU_API_KEY", "你的智谱密钥", "User")
```

未配置对应搜索密钥时，有 Seed URL 的任务仍可采集；无 Seed URL 的任务会明确进入 `requires_human`，不会复用 `DEEPSEEK_API_KEY`，也不会抓取搜索结果页面冒充搜索 API。

## 审计产物

```text
search_attempts.json       搜索请求、供应商、状态和错误
web_search_results.json    候选结果、排名、安全筛选和是否被选中
sources.json               SourceDocument（来源文档）
web_pages.json             WebPageContent（网页正文）
collection_attempts.json   HTTP / Browser 采集状态和内容哈希
```

搜索和浏览器输出不会直接进入报告。后续仍必须由 Extractor（抽取智能体）把正文转换为 `SourceEvidence`，从而保留：

```text
SourceDocument -> SourceEvidence -> ProductCard -> AnalysisClaim
-> CitationCheck -> CompetitiveReport -> ReviewFeedback
```

## API（接口）

```text
POST /api/analysis-tasks/{task_id}/collector/run-once
GET  /api/tasks/{task_id}/search-attempts
GET  /api/tasks/{task_id}/web-search-results
GET  /api/tasks/{task_id}/web-pages
GET  /api/tasks/{task_id}/collection-attempts
```

## 验证结果（2026-08-14）

```powershell
python check_step6e2_web_collector.py
python check_step6e2_browser_search.py
```

Mock（模拟）回归均 PASS，覆盖浏览器回退触发、渲染 DOM 结构化、智谱与博查响应解析、搜索结果审计、不安全 URL 拒绝和搜索后采集。

真实历史 URL：原先普通 HTTP 为 5/9；加入 Browser Fallback 后为 7/9。腾讯云实时互动产品页和开发者文章已通过浏览器回退取得正文；两个 FlowIn 页面在 Edge 与 Chrome 中均超过严格超时，因此保留失败审计，不伪装成功。成功页面对应的人工证据关键术语覆盖为 17/20（85%）。

腾讯会议等没有历史 Seed URL 的对象已具备自动发现链路；真实智谱搜索已经在 Step6E.3 试验中通过。
