# Competitive Intel Agents 新对话交接摘要

> 更新时间：2026-08-23  
> 用途：新对话开始时，请让 Codex 先完整阅读本文件和项目根目录 `AGENTS.md`，再继续开发。本文不包含任何 API Key（接口密钥）。

## 0. E2E Task 1：Task-centric Workspace 已完成

`E2E-TASK-WORKSPACE` 已建立 `activeTaskId + workspace` 主产品上下文。`GET /api/analysis-tasks/{task_id}/workspace` 直接读取该任务目录中的现有 Artifact，不要求 `pipeline_summary`、报告或审查已经生成；缺失列表返回 `[]`，缺失单对象返回 `null`。前端确认或加载已确认任务后会把该 task_id 设为唯一活动任务，项目总览、任务协作、LLM 链路、结论与证据、分析报告和质量治理均读取该 workspace。刷新按钮和进入这些页面都不会自动回退历史 Demo。`/api/runs` 与历史 dashboard 保留为用户主动选择的 Legacy / Debug（历史／调试）入口。

稳定回归为 `backend/check_e2e_task_workspace.py`。本任务没有调用 DeepSeek、智谱或外部网络。Task 2（Analyst 输出过长）尚未开始。

## 1. 用户偏好与不可变约束

- 默认使用中文解释；出现必要英文术语时，在后面增加中文注释，例如 `EvidenceCoverage（证据覆盖）`。
- 研究报告、前端业务文本和主要文档以简体中文为主。
- 项目是通用竞品分析助手；当前在线教育云平台资料只是第一套示例数据，不能把行业模板写死进通用架构。
- 用户关注“代码对产品有什么作用”和“前端能看到什么”，完成后要用业务语言解释并说明前端入口。
- 用户额度有限：优先针对性检查，避免重复遍历、重复联网、无必要的真实 LLM（大模型）调用和大段低价值输出。
- 不得暴露、打印或提交 API Key。DeepSeek 和智谱密钥保存在 Windows 用户级环境变量中。
- 不能破坏证据优先主链路：

```text
SourceDocument -> SourceEvidence -> ProductCard -> AnalysisClaim
-> CitationCheck -> CompetitiveReport -> ReviewFeedback
```

- 网页、SearchProvider（搜索供应商）、MCP（模型上下文协议）或 RAG（检索增强生成）的输出必须先变成结构化来源与证据，不能让 Writer（写作智能体）直接根据网页自由写报告。
- 反馈和补采循环必须有最大轮数、来源预算和明确停止条件，不能无限运行。

## 2. 项目定位和角色设计

目标架构：`Dynamic TaskBoard-driven Multi-Agent Competitive Intelligence System（由任务板驱动的动态多智能体竞品情报系统）`。

目前确认的角色分工：

- Intent / Requirement Analyst（意图／需求分析智能体）：理解用户需求、决策用途、竞品范围和分析维度。
- Orchestrator / Research Planner（编排／研究规划智能体）：拆分 KIQ（关键情报问题）、InformationNeed（信息需求）和 ResearchTask（研究任务），发布 TaskBoard（任务板）。
- Collector（采集智能体）：领取采集任务，通过 Seed URL（种子网址）或 SearchProvider 寻找资料。
- Extractor（抽取智能体）：把网页正文转成可逐字追溯的 SourceEvidence，再形成 ProductCard。
- Domain Analyst（领域分析智能体）：结合专业 Prompt / Skill（提示词／技能）分析产品卡片和证据，发现缺口并发布补采任务。
- Citation / Writer / Reviewer（引用检查／写作／审阅智能体）：验证结论、写报告并执行质量闸门。

用户之前提出“分析用户需求”和“分析采集资料”应是两个职责，这是正确方向；当前已通过 Intent/Planner 与 Analyst 分开实现。

## 3. 已完成阶段

- M1-M6：Schema（数据结构）、ArtifactStore（产物存储）、静态证据链、Agent Runtime（智能体运行时）、Trace（追踪）、Evaluation Harness（评估框架）。
- TaskBoard（任务板）、Dynamic DAG（动态任务图）、Quality Gate（质量闸门）、Context/Memory（上下文／记忆）、Guardrails（安全护栏）。
- Step6A：LLM 结构化输出链路，最初为 Mock（模拟）模型。
- Step6B：接入 DeepSeek 真实模型；Extractor 可保持 Mock，Analyst / Writer 可使用 DeepSeek。当前真实试验模型记录为 `deepseek-v4-flash`。
- Step6C：专业竞品分析 Prompt（提示词）、证据对齐约束、专业报告写作、报告语句与证据追溯、右侧证据栏及收起／点击恢复交互。
- Step6D.1-D.2：用户输入、意图解析、草稿确认。
- Step6D.3：Dataset Compatibility Gate（资料兼容闸门）与执行授权。
- Step6D.4：后台 Execution Runner（执行器）、持久化事件、SSE（服务器发送事件）实时进度。
- Step6E.1：Research Planner（研究规划智能体），确定性 Mock 规划，带研究预算。
- Step6E.2：WebCollector（网页采集器）、URL 安全校验、Browser Fallback（浏览器渲染回退）。
- Step6E.2 Search：SearchProvider 抽象；智谱搜索为主、博查备用。智谱正式端点为 `https://open.bigmodel.cn/api/paas/v4/web_search`。
- Step6E.3：网页正文转 SourceEvidence；保存字符起止位置、URL、content_hash、web_page_id 和 `quote_verified=true`，不调用 LLM。
- Step6E.4：证据覆盖更新与有限轮次补采闭环，详情见下一节。
- Step6E.5：Research Loop Runner（研究循环执行器），自动调度采集、抽取与覆盖检查，持久化状态、停止原因和 SSE 事件。
- Step6F：Research Analysis（研究分析），用户显式确认后让现有 DeepSeek Analyst 读取 Step6E 结构化证据，输出证据绑定结论并执行 CitationCheck。
- Frontend Runtime API Integration：网页可直接调用 FastAPI 后端的 DeepSeek Intent Agent；DeepSeek／智谱配置状态和真实 LLM Call ID、模型、耗时、校验结果均由后端动态返回并展示。
- Step6G 第一轮：允许单对象／未知行业任务；本地来源目录发现候选竞品；Live 任务重新采集已知 URL；Extractor 按 objective / query_hints 进行意图筛选；资料不完整时允许阶段性 Analyst 分析。

## 3.2 Step6G 固定 URL 验收状态

- 输入为人工 `sources.json` 的 9 个 URL；人工 `evidence.json` 只作评估答案，没有作为抽取输入。
- 当前抓取成功 7/9；两个 FlowIn 动态页浏览器回退超时。
- 32 条人工证据中，当前网页版本仍能逐字找到 5 条；Extractor 找回 5/5。
- 教学体验意图相关率 100%；集成／部署意图相关率 85.37%。
- 两种意图证据重合率 1.25%，证明当前证据集合会随 ResearchTask 改变。
- 广义抽取仍有 98 条额外候选，尚未全部人工判定有用，不能夸大为 98 条新证据。
- 本次固定 URL 抽取和差异化评估真实 LLM 调用为 0、搜索 API 调用为 0。
- 详细记录：`docs/step6g_intent_aware_extraction.md`。

## 3.1 Step6F 最终状态

Step6F 已完成并做过一次真实、限额验收：

```text
ResearchPlan + SourceDocument + SourceEvidence + ProductCard
-> DeepSeek Domain Analyst（1 次）
-> CompetitiveAnalysisPortfolioV2 + AnalysisClaim
-> CitationAgent -> CitationCheck
```

- 真实任务：`task_step6e3_zhipu_tencent_meeting_pilot`。
- 模型记录：`deepseek-v4-flash`；真实 Analyst 调用总数为 1。
- 生成 `claims_v2=13`；`citation_checks=13`，全部 `supported`。
- 所有结论的 evidence_id 都属于当前任务已有 SourceEvidence。
- Step6E 的确定性 `evidence_coverage / research_gaps` 保持不变；LLM 结果另存为 `analysis_evidence_coverage / analysis_research_gaps`。
- 已有完成产物时，第二次请求会在模型调用前阻断，避免重复计费。
- Step6F 没有调用 Writer，也没有触发新的网页搜索或采集。
- 前端入口位于 Research Planning 区域：“调用 DeepSeek 生成分析结论（1 次）”。
- 详细说明见 `docs/step6f_research_analysis.md`。
- 下一步建议：独立接入 Writer，只读取已通过 CitationCheck 的结论，并继续要求用户明确确认额外 1 次真实调用；当前不要把 Writer 自动串在 Analyst 后面。

## 4. Step6E.4-E.5 最终状态

Step6E.4 已完成并通过本地验收：

```text
Collector -> SourceDocument / WebPageContent
-> Extractor -> SourceEvidence
-> Analyst -> ProductCard + EvidenceCoverage + ResearchGap
-> Collector（只有存在缺口且预算允许时）
```

核心行为：

- Extractor 完成后自动向 TaskBoard 发布 `evaluate_evidence_coverage`。
- Analyst 领取该任务，更新 ProductCard 和 EvidenceCoverage。
- 覆盖矩阵依据 ResearchPlan / AnalysisTask 的完整“竞品 × 必需维度”生成；零证据维度也必须标记 `missing（缺失）`。
- `partial / weak / conflicting / missing（部分覆盖／较弱／冲突／缺失）` 会生成 ResearchGap 和定向 `supplement_collection（补充采集）`。
- ResearchTask 新增：`collection_round`、`parent_research_task_id`、`research_gap_id`。
- 最新任务仍处于 `waiting_for_collector / collected` 时，重复刷新不会提前发布下一轮。
- 达到 `max_collection_rounds` 或 `max_total_sources` 后停止补采并保留缺口审计。
- Collector 当前已经能领取 `supplement_collection`，不要求任务类型必须为初次采集。
- Step6E.4 使用确定性规则；本次没有联网、没有真实 LLM 调用。

前端入口：

```text
新建分析 -> 确认任务 -> 生成研究计划
-> 采集下一项任务
-> 抽取下一项证据
-> 更新覆盖并判断补采
```

“更新覆盖并判断补采”只在 Research Planning（研究规划）区域出现，项目总览页面不显示。前端会展示证据覆盖格数、研究缺口数和采集轮次。

主要 API（接口）：

```text
POST /api/analysis-tasks/{task_id}/collector/run-once
POST /api/analysis-tasks/{task_id}/extractor/run-once
POST /api/analysis-tasks/{task_id}/coverage/run-once
GET  /api/analysis-tasks/{task_id}/research-plan
GET  /api/tasks/{task_id}/evidence-coverage
GET  /api/tasks/{task_id}/research-gaps
```

Step6E.5 已完成并通过本地验收：

```text
ResearchLoopRunner
-> CollectorQueueService
-> ExtractorQueueService
-> Step6E4QueueService
-> coverage sufficient / budget exhausted / no runnable task
```

核心行为：

- `ResearchLoopRun / ResearchLoopEvent` 分别落盘到 `research_loop_runs.json / research_loop_events.json`，刷新后可读。
- 单工作线程优先完成已产生的 Extractor 和 Coverage 任务，再领取下一项 Collector 任务。
- 覆盖充分时以 `completed + coverage_sufficient` 停止。
- 最大来源数、最大采集轮次或动作安全预算耗尽时，以 `requires_human + budget_exhausted` 停止。
- 仍有研究需求但 TaskBoard 没有 ready 任务时，以 `requires_human + no_runnable_task` 停止。
- 自动 Collector 只使用剩余来源额度，不能在最后一次采集中越过 `max_total_sources`。
- 同一任务只允许一个活动循环；已有终态时拒绝重复启动，防止重复来源、证据与 TaskRecord。
- 原有三个单步按钮保留；自动循环运行期间禁用，避免并发领取。
- Step6E.5 使用现有确定性 Extractor / Coverage；本次没有真实网络访问和真实 LLM 调用。

新增 API：

```text
POST /api/analysis-tasks/{task_id}/research-loop
GET  /api/analysis-tasks/{task_id}/research-loop
GET  /api/analysis-tasks/{task_id}/research-loop/events
GET  /api/analysis-tasks/{task_id}/research-loop/events/stream
```

前端入口：

```text
新建分析 -> 确认任务 -> 生成研究计划 -> 自动运行研究循环
```

## 5. 最近验证结果

```text
py_compile                              PASS
check_step6e1_research_planner.py       PASS
check_step6e2_web_collector.py          PASS
check_step6e3_web_evidence_extractor.py PASS
check_step6e4_gap_tasks.py              PASS
check_step6e4_update.py                 PASS
check_step6e5_research_loop.py          PASS
Step6E.5 API + SSE 路由                 PASS
前端 JavaScript 语法                    PASS
本地浏览器页面入口                       PASS
浏览器 Console 错误                     0
真实网络访问                            false
真实 LLM 调用                           0
```

Step6E.4 专项断言：缺失维度可识别、ProductCard 可更新、Analyst 可领取任务、Collector 补采任务可发布、重复刷新不重复入队、最大轮次有效。

Step6E.5 专项断言：自动三段闭环、覆盖充分停止、预算耗尽转人工、持久化状态恢复、SSE、重复启动阻断、URL／证据／TaskRecord 唯一性均有效。

## 6. 运行环境

项目根目录：

```text
C:\Users\qyn\Desktop\北工大-课程材料结集\多Agent竞品分析助手\competitive-intel-agents
```

后端目录：

```text
C:\Users\qyn\Desktop\北工大-课程材料结集\多Agent竞品分析助手\competitive-intel-agents\backend
```

优先 Python：

```text
..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe
```

启动方式：

```powershell
.\start_app.ps1
```

或双击：

```text
start_app.cmd
```

本地地址：`http://127.0.0.1:8001/`。交接时后端健康检查已通过；启动脚本已修复 PowerShell 路径兼容问题。新对话开始时应重新检查 `/api/health`，不要假设旧 PID 永远存在。

环境变量名称（只检查是否存在，绝不输出值）：

```text
DEEPSEEK_API_KEY
ZHIPU_API_KEY
SEARCH_PROVIDER=zhipu
```

Windows 环境变量未被当前进程继承时，智谱搜索配置会通过 `winreg` 安全读取用户级环境变量。

## 7. Step6E.5-Step6F 主要文件

```text
backend/app/schemas.py
backend/app/execution/research_loop.py
backend/app/execution/research_analysis.py
backend/app/execution/__init__.py
backend/app/intake/step6e4.py
backend/app/intake/research_planning.py
backend/app/extraction/service.py
backend/app/collection/service.py
backend/app/agents/web_evidence.py
backend/app/agents/llm_snapshot.py
backend/app/api/main.py
backend/check_step6e4_gap_tasks.py
backend/check_step6e4_update.py
backend/check_step6e5_research_loop.py
frontend/index.html
frontend/src/app.js
docs/step6e4_evidence_gap_loop.md
docs/step6e5_research_loop_runner.md
docs/step6f_research_analysis.md
docs/project_context.md
docs/final_architecture_and_roadmap.md
docs/runbook_fastapi_artifact_api.md
docs/runbook_taskboard_workflow.md
start_app.ps1
```

注意：工作区可能存在用户自己的未提交修改。不要使用 `git reset --hard` 或覆盖无关文件；编辑前先读根目录 `AGENTS.md`。

## 8. 当前模型与搜索边界

- DeepSeek：已经支持真实 Analyst / Writer 调用，但除非用户明确要求真实运行，否则优先 Mock 或本地确定性测试，避免费用。
- 智谱：目前主要用于 Web Search（网页搜索）；用户以后可能再接智谱大模型，但尚未把智谱模型作为正式 Analyst / Writer Provider（供应商）。
- 博查：代码保留备用适配器，不是必需依赖。
- 当前没有真正 MCP Client / Server（MCP 客户端／服务端）。已有 ToolRegistry 是 MCP-style（类似 MCP 的工具边界），不能宣称已完成 MCP。
- 当前没有正式 RAG、Embedding（向量嵌入）或向量数据库；在资料规模和跨任务复用需求明显增加前不必急着接入。

## 9. 建议下一步

`Step6G Intent-aware Evidence Extraction（意图驱动证据抽取）` 第一轮已经完成：固定使用本地已保存的 URL，已验证 Extractor（抽取智能体）可以根据 Intent Agent 识别出的决策问题、竞品、研究维度和所需事实，从同一批网页中抽出不同且相关的 SourceEvidence。

目标流程：

```text
用户输入需求 -> Intent Agent -> ResearchTask
-> 固定本地 URL / WebPageContent
-> Intent-aware Extractor
-> SourceEvidence（逐字回放）
-> DeepSeek Analyst -> AnalysisClaim -> CitationCheck
```

本轮验收已证明：对同一批网页输入两个不同研究需求，Extractor 会输出明显不同的证据集合，重合率仅 1.25%，且证据保持原文位置和 `quote_verified=true`。当前更需要继续降低 98 条额外候选中的低价值噪声，并改善两个 FlowIn 页面的采集稳定性；之后再让 Analyst 基于新证据形成阶段性结论。当前仍不需要 MCP、RAG、Redis、MySQL 或 LangGraph。

之后的合理顺序：

1. Step6G.2：降低抽取候选噪声，改善 FlowIn 网页采集，并用新抽取证据验证阶段性 Analyst 分析。
2. 强化 Collector 根据 ResearchTask 通过智谱检索 URL 的准确性。
3. 将已通过 CitationCheck 的 AnalysisClaim 接入动态 Writer，生成可随证据增量完善的报告草案，并明确显示尚缺资料。
4. 加入可组合专家 Skill（技能），例如定价、定位、能力、生态和风险分析规范。
5. 资料规模增大后再考虑 Chunk、Embedding、RAG、向量库、MCP 和并发基础设施。

## 10. 新对话推荐开场指令

可直接对新对话发送：

```text
请继续 competitive-intel-agents 项目。先完整阅读项目根目录 AGENTS.md 和 docs/next_chat_handoff.md，再检查当前代码与服务状态。默认用中文解释，英文术语后加中文注释；不要输出任何 API Key，不要破坏 evidence-first 主链路。网页已能直接调用 DeepSeek Intent Agent，并显示后端 DeepSeek／智谱状态和真实调用记录。Step6G 第一轮固定 URL 意图抽取已经完成；下一步做 Step6G.2：降低额外候选噪声、改善 FlowIn 网页采集，并验证新证据上的阶段性 Analyst 分析。暂不接 MCP、RAG、Redis、MySQL 或 LangGraph。我的额度有限，请减少重复检查、无必要联网和真实 LLM 调用。
```
