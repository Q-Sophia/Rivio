# FastAPI Artifact API（产物接口）运行说明

当前 FastAPI（Python 接口框架）提供 read-only Artifact API（只读产物接口）。它读取本地 `ArtifactStore` 已生成的 JSON（结构化数据）文件，供浏览器或后续前端查询。

Step6A（步骤 6A）新增了 `llm-calls` 和 `llm-outputs` 两类查询接口。Step6A.6（步骤 6A.6）进一步将研究报告和业务分析产物的默认语言统一为简体中文（zh-CN）。Step6B.1（步骤 6B.1）新增真实 Provider（供应商）适配层，但真实调用保险默认关闭。只读 API（应用程序接口）本身不会执行 LLM（大模型）、不会发起模型请求，也不会修改 workflow（工作流）产物。

## 数据来源

API（应用程序接口）读取已有 run（运行记录）目录：

```text
backend/app/data/runs/{task_id}/
```

默认示例任务：

```text
snapshot_online_education_demo
```

查询 Step6A（步骤 6A）接口前，先在 `competitive-intel-agents/backend` 目录运行：

```powershell
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\run_llm_agent_workflow_demo.py
```

## 启动服务

```powershell
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload
```

前端控制台地址：

```text
http://127.0.0.1:8000/
```

OpenAPI docs（接口文档）地址：

```text
http://127.0.0.1:8000/docs
```

## 接口列表

基础与发现接口：

```text
GET /api/health
GET /api/tasks
GET /api/tasks/{task_id}/artifacts
```

证据链接口：

```text
GET /api/tasks/{task_id}/summary
GET /api/tasks/{task_id}/sources
GET /api/tasks/{task_id}/evidence
GET /api/tasks/{task_id}/product-cards
GET /api/tasks/{task_id}/claims
GET /api/tasks/{task_id}/citation-checks
GET /api/tasks/{task_id}/report
GET /api/tasks/{task_id}/review
```

调度、治理和上下文接口：

```text
GET /api/tasks/{task_id}/task-board
GET /api/tasks/{task_id}/task-records
GET /api/tasks/{task_id}/quality-gates
GET /api/tasks/{task_id}/feedback-tasks
GET /api/tasks/{task_id}/context-bundles
GET /api/tasks/{task_id}/working-memory
GET /api/tasks/{task_id}/memory-items
GET /api/tasks/{task_id}/guardrail-checks
GET /api/tasks/{task_id}/trace
GET /api/tasks/{task_id}/eval
```

Step6A（步骤 6A）LLM（大模型）可观测接口：

```text
GET /api/tasks/{task_id}/llm-calls
GET /api/tasks/{task_id}/llm-outputs
```

## Step6A（步骤 6A）返回内容

`llm-calls` 读取 `llm_calls.json`，返回 Extractor / Analyst / Writer Agent（抽取/分析/写作智能体）的调用记录，主要字段包括：

```text
id
task_id
agent_run_id
node_id
agent_role
provider
model
mode
prompt_id
prompt_summary
context_bundle_id
input_artifact_refs
output_schema
output_summary
status
used_fallback
fallback_reason
duration_ms
error
metadata.output_language
metadata.real_calls_enabled
metadata.api_surface
metadata.structured_output_mode
metadata.request_id
metadata.attempts
metadata.input_tokens
metadata.output_tokens
```

`llm-outputs` 读取 `llm_outputs.json`，返回每次调用的 structured output（结构化输出）记录，主要字段包括：

```text
id
task_id
llm_call_id
agent_role
output_schema
raw_output
parsed_object_ids
validation_status
validation_errors
```

默认 mock LLM workflow（模拟大模型工作流）运行后，两个接口应各返回 3 条记录：

```text
/llm-calls   -> 3
/llm-outputs -> 3
```

其中 `metadata.output_language=zh-CN`；`ProductCard（产品卡片）`、`AnalysisClaim（分析结论）` 和 `CompetitiveReport（竞品报告）` 的用户可见业务文本应为简体中文。产品英文名称、API（应用程序编程接口）等必要术语及各类稳定 ID 保持原值。

Step6B.1 的调用记录还会说明当前是 `mock`、`responses（响应接口）` 还是 `chat_completions（聊天补全接口）` 路径、是否启用真实调用、结构化输出模式、尝试次数与 Token usage（令牌用量）。API Key（接口密钥）不会写入 artifact（产物）或通过接口返回。

每次 workflow（工作流）开始前都会清空这两个 LLM artifact（大模型产物），因此同一 `task_id` 重跑后仍应为 3 条，而不是累计为 6 条。

## 示例查询

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/snapshot_online_education_demo/summary
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/snapshot_online_education_demo/llm-calls
Invoke-RestMethod http://127.0.0.1:8000/api/tasks/snapshot_online_education_demo/llm-outputs
```

## 验收标准

对于 `snapshot_online_education_demo`：

```text
/summary -> pipeline_status=completed
/sources -> 9 条
/evidence -> 32 条
/product-cards -> 3 条
/claims -> 5 条
/citation-checks -> 5 条
/report -> 1 份报告
/review -> approved=true
/trace -> 包含 dag_nodes / agent_runs / tool_calls
/eval -> passed=true
/llm-calls -> 3 条，provider=mock，全部 completed
/llm-outputs -> 3 条，全部 validation_status=passed，中文语言一致性通过
/eval -> llm_output_language_consistency_rate=1.0
```

对应的本地 artifact check（产物校验）命令：

```powershell
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_llm_artifacts.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_workflow_trace.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_run_artifacts.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\harness\run_eval.py
```

## 当前边界

当前 API（应用程序接口）的数据流是：

```text
ArtifactStore / JSON artifacts
    -> FastAPI read-only endpoints
    -> 内置只读前端控制台或其他浏览器客户端
```

它不是：

```text
真实 LLM Agent API
WebCollector API
爬虫服务
写入式任务控制接口
SSE 实时事件流
```

Step6A（步骤 6A）的默认回归数据仍使用 mock LLM（模拟大模型）。Step6B.2（步骤 6B.2）已经使用 DeepSeek V4 Flash（深度求索第四代快速模型）完成真实结构化工作流验证；爬虫仍不在当前范围内，前端查看能力由 Step6A.5（步骤 6A.5）提供。

Step6B.1（步骤 6B.1）已经提供真实模型适配代码，但默认 `LLM_ENABLE_REAL_CALLS=false`。实际配置密钥并执行小规模真实调用属于 Step6B.2（步骤 6B.2）。

DeepSeek V4 真实联调使用 `compatible` Provider（兼容供应商）、`chat_completions`（聊天补全接口）、`json_object`（JSON 对象）和 `LLM_THINKING_MODE=disabled`（关闭思考模式）。API Key（接口密钥）只从 `LLM_API_KEY_ENV` 指向的环境变量读取，API 返回内容仍必须通过中文、Pydantic（数据模型）和 evidence-first（证据优先）引用校验。

Step6C 的真实 Prompt A/B（提示词对照试运行）产物保存在 `backend/app/data/ab_tests/`，与可发布的完整工作流运行目录隔离。最新 `2.2.2-candidate` 原始输出有 1 条证据归属不合格结论，Harness（运行框架）整条拒绝后剩余 9 条通过全部自动指标，状态为 `completed_with_rejections（剔除后完成）`。这些试验产物不会进入 `/api/runs` 的正式报告选择列表，也不会覆盖任何 approved（已批准）报告。

前端已经增加 Step6C 专业分析实验栏目，展示 `competitor_profiles`、`intelligence_questions`、`evidence_coverage`、`comparability_notes`、`claims_v2` 和 `research_gaps`。页面会把最新结果显示为“通过，剔除 1 条不合格结论”，而不是伪装成原始输出全部通过；它仍不显示为 approved（已批准）正式报告。

Step6C Mock（模拟）正式运行的 `/report` 与统一 Dashboard（仪表盘）现在返回 Writer v2（第二版撰写智能体）报告。报告标题根据 AnalysisTask（分析任务）动态解析，当前演示标题为《在线教育实时互动与虚拟教室解决方案竞品分析报告》；`sections` 中记录 `report_version`、`prompt_id`、`prompt_hash`、`title_source` 和 `research_gap_ids`。该更新没有新增写入式 API，也不会因页面刷新触发 LLM（大模型）调用。

报告产物中的确定性引用补全只覆盖输入中已存在的 `claim_id（结论编号）`；补全记录保存在对应 `LLMCall.metadata.report_claim_refs_added`，可通过 `/llm-calls` API（接口）审计。

本次真实验证产物位于：

```text
backend/app/data/runs/snapshot_step6b2_deepseek_v4_final/snapshot_online_education_demo/
```

结果为 `pipeline_status=completed`、`approved=true`、3 条真实 `LLMCall（大模型调用）` 全部 completed（完成）、3 条 `LLMOutput（大模型输出）` 全部 passed（通过），且 `llm_fallback_count=0`。`check_llm_artifacts.py`、`check_workflow_trace.py`、`check_run_artifacts.py` 均 PASS（通过），`harness/run_eval.py` 返回 `eval_passed=True`。

前端运行批次接口：

```text
GET /api/runs
GET /api/runs/{run_id}/tasks/{task_id}/dashboard
GET /api/experiments/step6c/latest
```

`/api/runs` 会发现默认目录和独立验收目录中的本地运行，并根据工作流元数据返回 `stage`、`stage_detail` 和 `stage_rank`。当前按阶段、完成状态、是否真实调用和修改时间排序，因此已完成的 Step6C 运行默认排在 Step6B.2 之前。`dashboard` 接口一次返回前端所需的整套只读 artifacts（产物），避免多个接口中任意一次失败导致页面保留旧数据。旧的 `/api/tasks/{task_id}/...` 接口继续保留兼容。

`/api/experiments/step6c/latest` 只读取 `backend/app/data/ab_tests/` 中最近的可解析实验，返回实验 summary（摘要）、选中 variant（变体）、质量指标和过滤后的 V2 产物。对于 `completed_with_rejections（剔除后完成）`，响应还包含 `rejected_portfolio_claims_count` 和拒绝原因审计信息。它不把实验目录注册成正式 Run（运行批次），也不会触发新的 LLM（大模型）调用。

## Step6A.5（步骤 6A.5）前端控制台

现有静态前端已经挂载到同一个 FastAPI（接口服务）首页，不需要额外启动前端开发服务器。控制台现在展示：

```text
项目总览与 evidence-first（证据优先）主链路
TaskBoard（任务板）与 AgentRun（智能体执行记录）
LLMCall / LLMOutput（大模型调用与结构化输出）
AnalysisClaim -> Evidence（结论到证据追溯）
CompetitiveReport（竞品分析报告）
Quality Gate / Feedback Task（质量闸门与反馈任务）
Context / Memory / Guardrails（上下文、记忆与安全护栏）
Evaluation Harness（评估框架）
```

当前控制台仍是 read-only viewer（只读查看器），用于解释系统已经完成什么以及报告为什么可信；它还不能从页面发起新任务。

## Step6C 专业分析只读接口

Step6C（步骤 6C）新增以下只读 Artifact API（产物接口）：

```text
GET /api/tasks/{task_id}/analysis-portfolios
GET /api/tasks/{task_id}/brief-assessments
GET /api/tasks/{task_id}/competitor-profiles
GET /api/tasks/{task_id}/intelligence-questions
GET /api/tasks/{task_id}/information-needs
GET /api/tasks/{task_id}/evidence-coverage
GET /api/tasks/{task_id}/comparability-notes
GET /api/tasks/{task_id}/claims-v2
GET /api/tasks/{task_id}/research-gaps
GET /api/tasks/{task_id}/report-statements
```

示例任务：

```text
snapshot_step6c_professional_mock
```

这些接口只读取已落盘的 JSON Artifact（制品），不会触发 LLM（大模型）、网络检索或爬虫。旧运行没有 Step6C 文件时，原有接口不受影响；统一 Dashboard（仪表盘）中的对应字段返回空数组。

## Step6C.2C 报告论点追溯接口

专业 WriterAgent（写作智能体）在保存 `reports.json` 时，同时保存 `report_statements.json`。每个 ReportStatement（报告论点）包含：

```text
report_id              所属报告
section                所属章节
line_index             原始 Markdown 行号
text                   去除内部编号后的可读论点
claim_ids              对应 AnalysisClaim（分析结论）
evidence_ids           对应 SourceEvidence（来源证据）
research_gap_ids       对应 ResearchGap（研究缺口）
citation_status        引用检查状态
confidence             置信度
```

统一 Dashboard（仪表盘）新增 `reportStatements` 字段。前端根据 `line_index` 把报告段落变成可交互论点，再用 `evidence_ids -> SourceEvidence.source_id -> SourceDocument.url` 解析右侧证据面板。这样，页面不需要解析或显示 `[claim_id]`，同时仍保留完整可审计链路。

当前接口是 read-only（只读）的，不会刷新报告、调用真实 LLM（大模型）或访问来源网页。

## Step6C.2D 角色路由与真实 Writer 运行

合格运行 `snapshot_step6c2d_writer_deepseek_v4_pilot_v2` 使用 mixed role routing（混合角色路由）：Extractor / Analyst（抽取/分析智能体）为 Mock（模拟），Writer（写作智能体）为 `deepseek-v4-flash`。`/api/runs` 将其识别为 `Step 6C.2D`，并在 `stage_detail` 中显示真实 Writer 模型；Dashboard（仪表盘）仍只读取已经落盘的产物，刷新页面不会再次调用 DeepSeek（深度求索）。

`pipeline_summary.metadata` 记录：

```text
writer_only_real=true
writer_llm_provider=compatible
writer_llm_model=deepseek-v4-flash
upstream_llm_provider=mock
real_llm_calls_count=1
real_writer_calls_count=1
mock_llm_calls_count=2
llm_fallback_count=0
```

Writer 的 `LLMCall.metadata.input_internal_reference_fields` 必须为空，报告 metadata（元数据）中的 `internal_reference_fields_hidden=true`。这表示大模型没有收到带语义暗示的来源/证据内部编号；证据编号仍保留在模型外，并通过 ReportStatement（报告论点）返回前端进行追溯。

## Step6C.3 双真实 Agent 运行

运行 `snapshot_step6c3_analyst_writer_deepseek_v4_pilot` 使用 mixed role routing（混合角色路由）：Extractor（抽取智能体）为 Mock（模拟），Analyst / Writer（分析/写作智能体）为 `deepseek-v4-flash`。`/api/runs` 将其识别为 `Step 6C.3`，阶段等级高于 Step6C.2D，因此成为默认展示运行。

`pipeline_summary.metadata` 新增或更新：

```text
analyst_writer_real=true
analyst_llm_model=deepseek-v4-flash
writer_llm_model=deepseek-v4-flash
real_analyst_calls_count=1
real_writer_calls_count=1
real_extractor_calls_count=0
mock_llm_calls_count=1
analyst_rejected_claims_count=1
llm_fallback_count=0
```

`/api/runs` 的 Run descriptor（运行描述）同时返回 `analyst_writer_real`、`analyst_llm_model`、真实 Analyst / Writer 调用数和被拒绝结论数。Dashboard（仪表盘）仍只读取落盘数据，不会因页面刷新再次调用模型。

## Step6D.1 + Step6D.2 任务入口 API（接口）

FastAPI（后端接口）现在增加一组有边界的写入接口：

```text
GET  /api/task-drafts
POST /api/task-drafts/parse
GET  /api/task-drafts/{draft_id}
POST /api/task-drafts/{draft_id}/confirm
```

`POST /api/task-drafts/parse` 是唯一允许从页面触发一次 LLM（大模型）的接口，输出必须符合 `AnalysisTaskDraft` 并通过后端必要字段校验。`GET`、页面刷新和加载历史草稿都不会调用模型。

`confirm` 只把草稿转换为 `status=pending（待执行）` 的 `AnalysisTask（分析任务）`，不会创建 `pipeline_summary.json`，不会进入 `/api/runs`，不会调用 Analyst / Writer（分析 / 写作智能体），也不会访问网络。响应固定包含：

```text
execution_started=false
message=任务已确认并保存，尚未开始分析。
```

CORS（跨域资源共享）允许 `GET / POST / PUT`，但当前没有提供启动工作流、爬虫或修改正式报告的接口。

## Step6D.3 资料兼容与执行授权 API（接口）

```text
GET  /api/analysis-tasks/{task_id}/plan
POST /api/analysis-tasks/{task_id}/plan
POST /api/analysis-tasks/{task_id}/authorize
```

`plan` 使用本地确定性规则对照当前 `online_education` 资料集，不调用 LLM（大模型）、爬虫或网络。结果保存为：

```text
dataset_compatibility_assessments.json
execution_plans.json
```

只有 `compatibility=compatible`、`plan.status=ready` 且请求明确包含 `acknowledge_dataset_scope=true` 时，`authorize` 才会创建 `execution_authorizations.json`、`task_board.json` 和 6 个 `task_records.json`。授权响应仍固定返回 `execution_started=false`，不会创建 `pipeline_summary.json`，因此不会冒充已经执行的 Run（运行批次）。

## Step6D.4 后台执行与 SSE API（接口）

```text
POST /api/analysis-tasks/{task_id}/execute
GET  /api/analysis-tasks/{task_id}/execution
GET  /api/analysis-tasks/{task_id}/events?after={sequence}
GET  /api/analysis-tasks/{task_id}/events/stream?after={sequence}
```

`execute` 只接受已通过资料兼容闸门并获得显式授权的任务。请求体为 `{"mode":"mock"}` 或 `{"mode":"deepseek"}`；默认是无网络、无费用的 Mock（模拟）模式。接口返回 `202` 后，由单工作线程 Execution Runner（执行器）复用已授权 TaskBoard（任务板）运行证据链。

`events/stream` 的媒体类型是 `text/event-stream`，事件包含连续 `sequence（序号）`、步骤键、状态、进度百分比和消息。`execution_runs.json` 与 `execution_events.json` 均先落盘，页面刷新可按序号续接显示。后台进程中断会转成明确的 `failed`，当前不自动续跑。

## Step6E.1 研究规划 API（接口）

```text
GET  /api/analysis-tasks/{task_id}/research-plan
POST /api/analysis-tasks/{task_id}/research-plan
```

`POST` 使用 Mock Research Planner（模拟研究规划智能体）生成 `ResearchPlan / KIQ / InformationNeed / ResearchTask` 并发布动态 TaskBoard；不调用 DeepSeek、不访问网络。`GET` 只读取落盘结果，不重复规划。

## Step6E.2 Collector API（采集接口）

```text
POST /api/analysis-tasks/{task_id}/collector/run-once
```

接口只领取一个 ready + waiting_for_collector 任务。成功写入 `sources.json`、`web_pages.json`、`collection_attempts.json`；无 Seed URL 返回 `requires_human`，网页失败返回 `failed`。所有 URL 在请求及每次重定向前均经过 SSRF（服务端请求伪造）安全检查。

### Browser Fallback（浏览器渲染回退）与 SearchProvider（搜索供应商）

`run-once` 现在会在普通 HTTP 正文过短时使用本机 Headless Edge / Chrome（无头浏览器）。无 Seed URL 时，系统根据 `SEARCH_PROVIDER` 调用搜索接口；当前主要配置为 `zhipu + ZHIPU_API_KEY`，并保留 `bocha + BOCHA_API_KEY` 备用，再采集通过安全检查的候选 URL。

```text
GET /api/tasks/{task_id}/search-attempts
GET /api/tasks/{task_id}/web-search-results
GET /api/tasks/{task_id}/web-pages
GET /api/tasks/{task_id}/collection-attempts
```

未配置搜索密钥时只影响“自动寻找 URL”，不会影响已有 Seed URL 的采集，也不会复用 DeepSeek 密钥。

### Step6E.3 Extractor API（抽取接口）

```text
POST /api/analysis-tasks/{task_id}/extractor/run-once
GET  /api/tasks/{task_id}/evidence-extraction-attempts
```

Collector 成功后会发布一个 `extract_source_evidence` TaskRecord（任务记录）。Extractor（抽取智能体）只领取该 ready 任务，并把目标 WebPageContent 转换成 SourceEvidence。成功证据必须关联有效 source_id，并能通过 `source_text_start / source_text_end` 在原正文逐字回放；失败时保留 `evidence_extraction_attempts`，不会生成空证据或让原始网页绕过证据链进入报告。

### Step6E.4 Evidence Coverage API（证据覆盖接口）

```text
POST /api/analysis-tasks/{task_id}/coverage/run-once
GET  /api/tasks/{task_id}/product-cards
GET  /api/tasks/{task_id}/evidence-coverage
GET  /api/tasks/{task_id}/research-gaps
```

Extractor 成功后会发布 `evaluate_evidence_coverage` TaskRecord。`coverage/run-once` 只领取该 ready 任务，由 Analyst（分析智能体）按 ResearchPlan 的必需竞品和维度更新 ProductCard 与 EvidenceCoverage。无证据维度必须写为 `missing`；未关闭缺口可以发布 `supplement_collection`，但不得超过 `max_collection_rounds` 或 `max_total_sources`。该接口使用确定性规则，不调用 LLM（大模型）。

### Step6E.5 Research Loop API（研究循环接口）

```text
POST /api/analysis-tasks/{task_id}/research-loop
GET  /api/analysis-tasks/{task_id}/research-loop
GET  /api/analysis-tasks/{task_id}/research-loop/events
GET  /api/analysis-tasks/{task_id}/research-loop/events/stream
```

`POST` 在单工作线程中自动调度 Collector -> Extractor -> Coverage Analyst（采集 -> 抽取 -> 覆盖分析）。`GET` 返回落盘的 `ResearchLoopRun / ResearchLoopEvent`，页面刷新不会重复启动。SSE 事件名为 `research-loop`。

终态包括：覆盖充分时 `completed + coverage_sufficient`；来源／轮次／动作预算耗尽时 `requires_human + budget_exhausted`；无 ready 任务但仍有研究需求时 `requires_human + no_runnable_task`；未处理异常或进程中断时 `failed`。自动采集会把单次来源上限收紧到剩余总预算，不能越过 `max_total_sources`。

### Step6F Research Analysis API（研究分析接口）

```text
GET  /api/analysis-tasks/{task_id}/research-analysis
POST /api/analysis-tasks/{task_id}/research-analysis
```

`GET` 只读取已有分析组合、结论、引用检查、分析覆盖和本次 Analyst 调用记录，不产生模型费用。

`POST` 只接受：

```json
{"mode":"deepseek","acknowledge_real_llm_call":true}
```

它不再要求研究计划已经达到 `ready_for_analysis`；只要当前任务已有来源、逐字证据、产品卡片和覆盖结果，就可以基于现有资料形成阶段性分析，未覆盖部分继续保留为 ResearchGap（研究缺口）。首次成功执行会产生且只产生 1 次真实 DeepSeek Analyst 调用，再由 CitationAgent（引用检查智能体）确定性校验所有结论。它不会调用 Writer，也不会启动 Collector / SearchProvider。已有完成产物时会在调用模型前拒绝重复执行。

### 后端外部服务状态 API（接口）

```text
GET /api/integrations/status
```

该接口只检查后端是否能读取 DeepSeek 与智谱的非秘密配置，并返回模型、Provider（供应商）、用途和直连／代理策略。它不会发起模型或搜索请求，也不会返回 API Key。真实是否成功以业务接口返回的 `llm_call / search_attempt` 落盘记录为准。

### Task Workspace API（任务工作区接口）

```text
GET /api/analysis-tasks/{task_id}/workspace
```

该接口以 `task_id` 为边界直接读取 `ArtifactStore` 当前已有产物，不要求任务属于 `/api/runs`，也不要求存在 `pipeline_summary`。只有 `analysis_tasks.json` 的新任务会返回 `stage=confirmed`；后续计划、来源、证据、覆盖、结论、报告和审查出现后，重复读取会自然反映新增 Artifact。缺失集合统一返回 `[]`，缺失单对象统一返回 `null`，trace（执行轨迹）始终返回三个安全数组。接口不会跨 task 查找替代产物，也不会调用模型、搜索或网络。

旧 `/api/runs/{run_id}/tasks/{task_id}/dashboard` 继续用于完整历史运行和调试，不是新任务主产品工作区的数据源。
