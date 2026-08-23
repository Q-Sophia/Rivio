# Frontend Artifact Viewer 运行说明

## 推荐：一键启动

在项目根目录双击：

```text
start_app.cmd
```

脚本会自动完成：

```text
检查 8001 端口和 /api/health 健康状态
避免重复启动 FastAPI（后端接口）
在隐藏窗口中启动 Uvicorn（Python Web 服务器）
等待后端就绪后打开 http://127.0.0.1:8001/
记录日志和后端进程号
```

运行日志和 PID（进程号）分别保存在：

```text
backend/app/data/logs/api-8001.startup.log
backend/app/data/logs/api-8001.pid
```

如果 8001 端口被其他程序占用但健康检查失败，脚本会停止并给出提示，不会擅自结束其他程序。

当前页面是 M7 之后的轻量前端查看器，用来展示已有 artifacts（产物）。页面支持在 Run（运行批次）下拉框中切换 Step6A.5（步骤 6A.5）、Step6B.2（步骤 6B.2）和 Step6C（步骤 6C）运行。

页面左侧“当前阶段”不是项目进度计时器，而是所选运行的阶段标签。后端根据 `pipeline_summary.metadata.runtime`、`professional_analysis`、`real_calls_enabled` 和 LLM Provider（大模型供应商）返回阶段；切换 Run（运行批次）或点击刷新后，标签会随数据更新，不再在 HTML / JavaScript（网页 / 脚本）中写死为 `Step 6B.2`。

Step6C 增加独立“专业分析实验”栏目。它可以读取最新 `ab_tests` 真实试验产物，并把 `quality_gate_failed`（质量闸门失败）或 `completed_with_rejections（剔除后完成）` 如实显示，与 `/api/runs` 中的正式报告运行隔离。

它不是完整产品前端，也不接 LLM（大模型）、不做爬虫、不触发新的 workflow（工作流）。它只读取 FastAPI（后端接口）返回的数据。

## 页面展示什么

当前前端会展示：

```text
Pipeline Summary（流水线总结）
ProductCard（产品卡片）
AnalysisClaim（分析结论）
SourceEvidence（证据）
CitationCheck（引用检查）
CompetitiveReport（竞品分析报告）
ReviewFeedback（审查反馈）
DAGNode（流程节点）
AgentRun（Agent 执行记录）
ToolCall（工具调用记录）
Evaluation Harness（评估框架）指标
Run（运行批次）及其 Provider / Model（供应商 / 模型）
BriefAssessment（研究简报评估）
CompetitorProfile（竞品画像与竞争角色）
KeyIntelligenceQuestion（关键情报问题）
EvidenceCoverage（证据覆盖）
ComparabilityNote（可比性说明）
AnalysisClaimV2（第二版分析结论）
ResearchGap（研究缺口）
ReportStatement（报告论点与证据映射）
真实 Prompt Pilot 的质量指标与结论拒绝记录
```

页面当前默认选择阶段最高且状态 completed（完成）的运行，即 `snapshot_step6c_professional_mock`。该正式回归运行使用 mock LLM（模拟大模型）；最新 DeepSeek 真实 Prompt Pilot（提示词试运行）只在实验栏目中展示。其原始 10 条结论有 1 条证据归属不合格，Harness（运行框架）整条剔除后，剩余 9 条通过全部自动指标。

## 启动后端 API

在 `backend` 目录运行：

```powershell
..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

检查：

```text
http://127.0.0.1:8000/api/health
```

## 启动前端页面

在 `frontend` 目录运行：

```powershell
..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe -m http.server 3000 --bind 127.0.0.1
```

然后打开：

```text
http://127.0.0.1:3000/
```

## 默认运行

页面会先读取 `/api/runs`（运行批次接口），默认优先选择阶段最高且状态 completed（完成）的运行。当前默认运行是：

```text
run_id=default
task_id=snapshot_step6c3_analyst_writer_deepseek_v4_pilot
```

对应本地 artifacts（产物）在：

```text
backend/app/data/runs/snapshot_step6c3_analyst_writer_deepseek_v4_pilot/
```

Step6C Mock（模拟）和 Step6B.2（步骤 6B.2）的历史运行仍可在下拉框选择；阶段名称来自后端运行元数据，不是前端固定文案。

## 当前验收点

页面应该能看到：

```text
stage = Step 6C.3
provider = mixed（混合角色路由）
analyst model = deepseek-v4-flash
writer model = deepseek-v4-flash
sources = 9
evidence = 32
product_cards = 3
claims = 21
claims_v2 = 21
competitor_profiles = 3
research_gaps = 5
report_statements = 35
dag_nodes = 6
agent_runs = 6
tool_calls = 32
approved = true
review_score = 7.0
eval passed = true
llm_calls = 3
llm_fallback_count = 0
```

最新真实 Prompt Pilot 的实验区应显示：

```text
model = deepseek-v4-flash
prompt_version = 2.2.2-candidate
status = completed_with_rejections
metric_pass_rate = 100.0%
competitor_profiles = 3
claims_v2 = 9
research_gaps = 5
rejected_claims = 1
rejected_claim_id = claim_006
blocking_metrics = none
```

`completed_with_rejections（剔除后完成）`表示原始模型输出并非全部正确。页面展示的是经过确定性证据校验后的 9 条结论，同时提示有 1 条结论被整条拒绝；系统没有改写该结论或补造证据。该实验仍不是 approved（已批准）正式报告。

Step6C.3（步骤 6C.3）运行的“分析报告”栏目现在应显示：

```text
title = 在线教育实时互动与虚拟教室解决方案竞品分析报告
writer = competitive_writer@2.1.1-candidate
title_source = report_subject
schema_version = v2
```

报告正文包含执行摘要、研究背景、竞品路径、核心维度、成本与采用条件、风险限制、研究缺口和决策建议。这里的“平台通用”表示系统可以处理不同行业，不表示每份报告使用“通用竞品分析报告”作为标题。

Step6C.2C（步骤 6C.2C）把引用索引从读者界面移到审计数据层：

```text
报告自然语言段落
  -> ReportStatement（报告论点）
  -> AnalysisClaim（分析结论）
  -> SourceEvidence（来源证据）
  -> SourceDocument（来源文档与 URL）
```

报告正文不再显示 `[clv2_...]`、`[gap_...]` 等内部编号，也不展示“结论引用索引”章节。带“查看证据”标记的段落可以用鼠标悬停、点击或键盘聚焦；右侧 Evidence Trace（证据追溯）面板会显示对应结论、引用状态、人工整理的证据原文片段和来源链接。当前 SourceEvidence（来源证据）来自手工整理的 snapshot（快照）；未来接入 WebCollector（网页采集器）后，仍复用同一结构，只需把来源 URL 和网页原文写入 SourceDocument / SourceEvidence。

“结论引用索引”和内部编号仍保留在 `reports.json` 与 `report_statements.json` 中，用于 CitationAgent（引用检查智能体）、ReviewerAgent（审查智能体）和 Evaluation Harness（评估框架）审计，不会为了界面美观而删除证据链。

右侧 Evidence Trace（证据追溯）面板标题内新增“收起”按钮。点击后右栏临时隐藏，报告正文扩展为单栏；隐藏状态下点击任意带“查看证据”的报告论点，右栏会自动重新展开并显示该论点对应的证据。键盘聚焦论点也会恢复证据栏。按钮同步维护 `aria-expanded（无障碍展开状态）`，不会删除或重新请求任何证据数据。

Step6C.3 的“LLM 链路”栏目会把三次调用分别标记为“Mock 模拟”或“真实 LLM（大模型）”：Extractor 为 Mock，Analyst 和 Writer 为真实模型。页面同时显示 `1 条分析结论被拒绝`，并在项目总览说明只有通过证据校验的 21 条结论进入报告。

## 当前边界

当前前端只是 Artifact Viewer（产物查看器）：

```text
ArtifactStore（产物存储器）
  -> FastAPI（后端接口）
  -> Frontend Artifact Viewer（前端产物查看器）
```

当前页面仍然不会：

```text
Seeded WebCollector（种子链接采集器）
自动网络检索或爬虫
在没有用户确认与授权时启动完整工作流
直接编辑正式 evidence / claim / report artifacts（证据 / 结论 / 报告产物）
```

Step6D.4 已增加 SSE（服务器发送事件）来显示用户主动启动任务后的实时进度。刷新页面只会恢复已落盘状态和事件，不会重复启动 Agent（智能体）或再次调用 DeepSeek。

Step6E.5 在 Research Planning（研究规划）区域增加“自动运行研究循环”。点击后自动执行 Collector -> Extractor -> Analyst coverage（采集 -> 抽取 -> 覆盖检查），并实时展示动作数、来源预算、采集轮次、缺口数和停止原因。`coverage_sufficient` 表示可以进入后续分析；`budget_exhausted / no_runnable_task` 会明确显示“需要人工处理”。三个单步按钮继续保留，自动循环运行时会禁用，避免并发重复领取同一任务。

Step6F 在同一区域增加“调用 DeepSeek 生成分析结论（1 次）”。只要已有来源、逐字证据、产品卡片和覆盖结果，即使研究计划仍有缺口，也可以先生成阶段性分析；缺口继续保留并驱动后续采集。点击时会显示真实调用确认框。成功后展示结论数、引用检查结果和模型调用记录；刷新页面只读取已落盘产物，不会再次计费。该按钮只调用 Analyst 1 次，不调用 Writer，也不重新搜索网页。已有 Step6F 完成产物时按钮禁用，后端也会拒绝重复调用。

“新建分析”顶部现在显示 Backend integrations（后端外部服务）状态：DeepSeek 用于 Intent / Analyst / Writer，智谱用于网页搜索。状态读取来自 `GET /api/integrations/status`，只检查配置，不调用外部 API。点击“解析需求”时，页面明确显示请求已经发送到 FastAPI；成功后展示真实 Call ID、Provider、Model、耗时和 Pydantic 结构校验结果。Analyst 完成区也直接读取并展示后端 `analyst_llm_calls`，不再依赖人工说明调用次数。

## Step6D 新建分析页面

侧边栏新增“新建分析”。它是当前唯一不是纯只读的页面，但写入范围只限于任务草稿和待执行任务：

```text
输入自然语言需求
  -> 点击“解析需求”
  -> 一次 Intent Agent（意图智能体）调用
  -> 可编辑 AnalysisTaskDraft（分析任务草稿）
  -> 点击“确认任务（不启动分析）”
  -> 保存 pending（待执行）的 AnalysisTask（分析任务）
```

页面会显示 Provider / Model（供应商 / 模型）、缺失字段、补充问题和动态报告标题预览。最近草稿可以重新加载，加载与刷新不会再次调用模型。

任务现在只要求至少一个研究对象，行业可以暂时未知。比如“请调研 ClassIn 的竞品分析”可以直接确认；单对象任务会标记需要发现主要竞品。当前阶段先从本地 `sources.json` 来源目录补出候选竞品，后续再由 Collector / 智谱搜索扩展。

“确认任务”成功后应显示 `execution_started=false（尚未启动执行）`。Step6D.3 的 Dataset Compatibility Gate（资料兼容闸门）只约束旧版人工快照六步执行能否安全复用，不再代表动态研究流程必须停下。部分兼容或不兼容时，页面会提示应走研究规划、继续采集，并允许在已有证据上形成阶段性分析。

当前真实测试草稿包含“腾讯会议”，而资料集覆盖的是“腾讯云实时互动 / TRTC 教育方案”，因此页面应显示：

```text
兼容状态 = partial（部分兼容）
竞品覆盖 = 67%
缺少竞品资料 = 腾讯会议
执行计划 = blocked（已阻断）
授权按钮 = disabled（不可用）
```

完全兼容任务授权后先加入 TaskBoard（任务板），不会在授权请求中同步调用 Analyst / Writer（分析 / 写作智能体）。Step6D.4 随后显示独立的 Execution Runner（后台执行器）区域：

```text
选择 Mock（模拟）或 DeepSeek（真实）模式
点击“开始后台执行”
查看六步实时事件与进度条
失败时查看明确错误，不展示伪成功
完成后点击“查看本次分析报告”
```

默认选择 Mock，不产生模型费用。只有用户切换到 DeepSeek 并再次点击启动时，才会读取本机密钥并让 Analyst + Writer（分析 + 写作智能体）各发起一次真实调用。页面刷新不会重复启动任务，而是通过 execution status（执行状态）和 SSE（服务器发送事件）恢复显示。

## Task-centric Workspace（任务中心工作区）

前端主产品上下文使用 `state.activeTaskId + state.data(workspace)`。确认新任务或加载已确认草稿后，task_id 会写入当前上下文并加载 `/api/analysis-tasks/{task_id}/workspace`。项目总览、任务协作、LLM 链路、结论与证据、分析报告和质量治理在进入时都会刷新同一任务的 workspace；页面刷新按钮也优先刷新 `activeTaskId`。

尚未生成的报告、结论、审查、评测、质量闸门和 trace 显示“当前任务尚未生成该阶段产物。”，不会保留或回填上一历史运行内容。侧边栏 Run 选择器现在明确标注为“历史 Run（调试入口，主动切换）”；只有用户主动切换时才进入 Legacy Dashboard（历史调试面板）。
