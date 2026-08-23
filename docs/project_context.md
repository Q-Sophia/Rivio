# Project Context

## Step6G Intent-aware Extraction Update（2026-08-21）

Step6G 已完成第一轮固定 URL 验证。Intent 现在允许单对象和未知行业，单对象会标记后续竞品发现；当前可先从本地来源目录发现候选竞品。Live 研究任务会重新采集 `sources.json` 的已知 URL，同一网页可跨不同研究维度复用而不重复联网。Extractor 现在使用 objective / query_hints / competitor / dimension，并优先保留明确命中意图的逐字原文。9 个固定 URL 当前读取成功 7 个，两个 FlowIn 页超时；32 条人工证据中当前网页仍可逐字恢复 5 条，Extractor 找回 5/5。两种意图的证据重合率为 1.25%，相关率分别为 100% 和 85.37%，全程未调用 DeepSeek 或智谱搜索。资料不完整不再阻止 Analyst 形成阶段性结论，缺失内容继续保留为 ResearchGap。

## Frontend Runtime API Integration Update（2026-08-21）

网页的真实后端调用闭环已修复并验收。此前 Intent Agent（意图智能体）从网页调用 DeepSeek 时继承了不可用的 `127.0.0.1:9` 环境代理，导致 `[WinError 10061]`；LLM Provider（模型供应商）和智谱 SearchProvider（搜索供应商）现默认使用明确直连，且 LLM 配置可安全读取 Windows 用户级环境变量。新增 `GET /api/integrations/status`，只返回 DeepSeek／智谱是否配置、模型、用途和连接策略，不调用外部 API、不暴露密钥。前端“新建分析”会显示这两个后端集成状态，并在真实调用后展示 Call ID、Provider、Model、耗时和结构校验结果。已从网页真实调用 `deepseek-v4-flash` Intent Agent 1 次，单次尝试成功、Pydantic 校验通过；本次没有调用 Analyst、Writer 或智谱搜索。

## Step6F Research Analysis Update（2026-08-21）

Step6F 已完成。已有 DeepSeek Domain Analyst（领域分析智能体）现在可直接读取 Step6E 形成的 `ResearchPlan + SourceDocument + SourceEvidence + ProductCard`，输出通过 Pydantic 校验且逐条绑定 `evidence_ids` 的 `CompetitiveAnalysisPortfolioV2 / AnalysisClaim`，随后由确定性 CitationAgent（引用检查智能体）生成 CitationCheck。真实腾讯会议试验仅调用 Analyst 1 次，得到 13 条结论，13 条引用检查全部为 `supported`；未调用 Writer，也未执行新的网页搜索。完成产物会阻断重复执行，避免二次计费；Step6E 的确定性覆盖与缺口产物不会被 LLM 覆盖。

## Step6E.5 Research Loop Runner Update（2026-08-21）

Step6E.5 已完成。ResearchLoopRunner（研究循环执行器）以单工作线程自动调度现有 Collector -> Extractor -> Analyst coverage（采集 -> 抽取 -> 覆盖检查）边界；`research_loop_runs / research_loop_events` 持久化状态与 SSE 事件。覆盖充分时正常完成；来源数、采集轮次或动作安全预算耗尽时转 `requires_human`；仍有需求但无 ready 任务时明确记录 `no_runnable_task`。自动采集只使用剩余来源额度，避免最后一项任务越过 `max_total_sources`。原有三个单步按钮继续保留，本步骤不调用 LLM。

## Step6E.4 Evidence Gap Loop Update（2026-08-21）

Step6E.4 已完成。Extractor（抽取智能体）完成 SourceEvidence 后会动态发布 `evaluate_evidence_coverage`，由 Analyst（分析智能体）更新 ProductCard（产品卡片）和 EvidenceCoverage（证据覆盖）。覆盖矩阵以 ResearchPlan / AnalysisTask 的必需竞品与维度为准，因此没有任何证据的维度也会明确标记 `missing`。`partial / weak / conflicting / missing` 会生成 ResearchGap（研究缺口）和定向补采任务；每个 ResearchTask 保存 `collection_round / parent_research_task_id / research_gap_id`，并严格服从 `max_collection_rounds` 与 `max_total_sources`，不会无限补采。该步骤为确定性规则版，不调用 LLM（大模型）。

## Step6E.3 Source Evidence Update（2026-08-14）

SearchProvider（搜索供应商）现以智谱为主、博查为备用；Windows 运行时可安全读取用户级 `ZHIPU_API_KEY`，不输出密钥。Collector 完成后会动态发布 `extract_source_evidence`，由 WebEvidenceExtractorAgent（网页证据抽取智能体）把 WebPageContent 转换成 SourceEvidence。每条证据都保存字符起止位置、web_page_id、content_hash、source URL 和 `quote_verified=true`。真实腾讯会议试验通过智谱找到 3 个官方域名结果，2 页采集成功、1 页 404；最终得到 13 条逐字可回放证据，未调用 LLM。新增证据现已通过 Step6E.4 接入 EvidenceCoverage / ProductCard 更新和有限轮次补采判断。

## Step6E.2 Browser + Search Update（2026-08-14）

已加入 Browser Fallback（浏览器渲染回退）和可插拔 SearchProvider（搜索供应商）。动态页普通 HTTP 正文过短时，才使用本机 Headless Edge / Chrome（无头浏览器）；无 Seed URL 的 ResearchTask 优先使用智谱搜索，博查作为备用。搜索结果先保存为 `search_attempts / web_search_results`，通过 URL 安全筛选后才能进入 Collector。真实历史 URL 成功率从 5/9 提升至 7/9；两个 FlowIn 页面仍因浏览器超时失败，必须保留失败状态。新采集的 WebPageContent 必须交给 Extractor 形成可引用的 SourceEvidence，不能直接进入 Analyst / Writer。

Use this file as persistent project memory for future development work.

## Current Status Update（2026-08-21）

项目已推进到 Step6F：Research Planner 生成动态任务后，ResearchLoopRunner 可自动驱动 Collector / Extractor / Coverage Analyst（采集 / 抽取 / 覆盖分析）完成研究闭环；覆盖充分后，用户可显式确认一次真实 DeepSeek Analyst 调用，把结构化证据整理为带引用校验的分析结论。Writer 仍未在此路径调用。

当前默认边界：

```text
页面刷新 / 加载草稿 -> 不调用 LLM（大模型）
点击解析需求       -> 一次 Intent Agent 调用
点击确认任务       -> 只保存，execution_started=false
```

Step6D.3 已完成：Dataset Compatibility Gate（资料兼容闸门）会确定性检查行业、具体竞品和分析维度；完全兼容时可由用户显式授权并创建 6 个 TaskRecord（任务记录），部分兼容或跨行业任务会被阻断。授权只进入 TaskBoard（任务板），仍保持 `execution_started=false`。

Step6D.4 已完成：单工作线程 Execution Runner（执行器）领取已授权任务，复用用户 TaskBoard 与 AnalysisTask 驱动现有 evidence-first（证据优先）工作流；`execution_runs / execution_events` 落盘，前端通过 SSE（服务器发送事件）实时展示六步状态。Mock 自动验收通过，真实 DeepSeek 模式保留但本步骤验收未产生真实调用。后端重启会把孤立运行显式标记失败，尚未实现 checkpoint resume（检查点续跑）。

Step6E.1 当前使用 Mock（模拟）规划，不调用真实模型。每个研究任务有来源偏好、查询提示、停止条件和最多 3 轮的预算；缺失资料保持 `waiting_for_collector`，不会伪造采集结果。

历史 URL 已完成 HTTP + Browser Fallback（浏览器渲染回退）验证，SearchProvider（搜索供应商）已接入智谱并保留博查备用。SourceEvidence 仍必须由 Extractor 结构化生成，新增资料不能绕过证据链。

## Current Direction

The project is building an evidence-backed competitive intelligence agent system.
The current scenario is online education real-time interaction and virtual classroom solutions, focused on:

- ClassIn
- Tencent Cloud real-time interaction / TRTC education solution
- BigBlueButton

The first local data chain is Snapshot Mode:

```text
sources.json / evidence.json
  -> LocalSnapshotCollector
  -> SourceDocument / SourceEvidence schema validation
  -> ArtifactStore saves sources/evidence
  -> run_collect_demo.py reads back saved artifacts
```

LLM、前端与网页采集能力已经按阶段接入；后续新增能力仍必须经过 schema、traceability（可追溯性）、Quality Gate（质量闸门）和 Evaluation Harness（评估框架）验证。

## Reference Projects

The workspace contains other projects that should be used as implementation references.

### competitive-analysis-agent-main

Use `competitive-analysis-agent-main` as the domain prototype reference.

Borrow ideas for:

- Competitive analysis workflow
- Agent role split and collaboration flow
- Report structure
- Demo data shape
- Frontend report and trace presentation patterns

Do not copy it blindly. Adapt only the parts that fit this project's evidence-first product positioning and schema chain.

### learn-claude-code-main

Use `learn-claude-code-main` as the harness engineering reference.

Borrow ideas for:

- Agent loop mechanics
- Task/DAG state transitions
- ToolCall trace structure
- Artifact workflow
- Evaluation harness
- MCP-style tool boundaries

Prefer its harness concepts when designing runtime orchestration, traceability, tool boundaries, and evaluation flows.

## Implementation Bias

- Keep every important analysis claim connected to evidence.
- Keep every evidence item traceable to a source document.
- Prefer local deterministic Snapshot Mode before live collection.
- Add LLM calls only after schemas, artifacts, DAG/task state, and citation checks are inspectable.
- Treat frontend as a later visualization layer over stable artifacts, not as the first integration surface.
