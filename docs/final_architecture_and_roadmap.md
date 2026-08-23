# 最终架构与路线图

这份文档用来固定 `competitive-intel-agents` 的最终形态、架构目标和后续路线，避免后续开发跑偏。

当前 M1-M6 已完成的是一个稳定的本地证据链底座。最终系统不是普通固定流水线，也不是一次 LLM 直接写报告，而是要演进成：

```text
Dynamic TaskBoard-driven Multi-Agent Competitive Intelligence System
```

中文可以理解为：

```text
由 Orchestrator + TaskBoard + 专职 Agent + 反馈循环 驱动的 evidence-first 竞品情报系统
```

## 1. 最终系统定位

最终系统的目标流程是：

1. 用户输入竞品分析需求。
2. `OrchestratorAgent` 解析需求，识别行业、竞品、分析维度和输出格式。
3. 系统生成 `TaskBoard` / Dynamic DAG。
4. `CollectorAgent` 认领或执行采集任务。
5. `CollectorAgent` 通过 WebCollector / Search / Scraper / MCP Tools 检索公开资料。
6. 系统把采集结果结构化为 `SourceDocument` 和 `SourceEvidence`。
7. `ExtractorAgent` 把 evidence 聚合成 `ProductCard`。
8. `AnalystAgent` 做 SWOT、市场定位、功能对比、定价分析、生态分析、风险分析。
9. 如果 `AnalystAgent` 发现证据不足，就创建 `supplement_collection` 或 `supplement_analysis` 任务。
10. `CollectorAgent` 继续补采。
11. `AnalystAgent` 重新分析受影响部分。
12. `WriterAgent` 基于 verified claims 写报告。
13. 如果 `WriterAgent` 发现章节缺材料，就创建补采或补分析任务。
14. `CitationAgent` 做 claim-level citation verification。
15. `ReviewerAgent` 做报告结构、证据充分性和质量审查。
16. 如果审查不通过，就创建 `revise_report`、`supplement_collection` 或 `supplement_analysis` 任务。
17. 系统最多进行 N 轮反馈循环。
18. 最终输出可追溯、可验证、可复现的竞品分析报告。

必须强调：

```text
最终系统可以上网搜索和自动采集，
但不是把网页原文直接交给 WriterAgent 写报告。
```

Web Search / Web Scrape / MCP 工具返回的结果必须先变成：

```text
SourceDocument / SourceEvidence
```

然后才能进入产品卡、分析结论、引用检查、报告和审查链路。

## 2. 最终工作流示意

最终工作流：

```text
User Request
    ↓
OrchestratorAgent
    ↓
TaskBoard / Dynamic DAG
    ↓
CollectorAgent
    ↓
SourceDocument / SourceEvidence
    ↓
ExtractorAgent
    ↓
ProductCard
    ↓
AnalystAgent
    ↓
AnalysisClaim
    ↓
CitationAgent
    ↓
CitationCheck
    ↓
WriterAgent
    ↓
CompetitiveReport
    ↓
ReviewerAgent
    ↓
ReviewFeedback
    ↓
If approved → Final Report
If not approved → create supplement / revise tasks → back to TaskBoard
```

主证据链必须始终保留：

```text
SourceDocument
-> SourceEvidence
-> ProductCard
-> AnalysisClaim
-> CitationCheck
-> CompetitiveReport
-> ReviewFeedback
```

后续无论接 LLM、WebCollector、MCP、RAG 还是前端，都不能破坏这条 evidence-first 主链路。

## 3. Five-layer Harness Architecture

本项目最终不是：

```text
Prompt + LLM
```

而是：

```text
Agent = LLM + Harness
```

这里的 Harness 可以理解为“把模型变成可用 Agent 的工程外壳”，包括工具、任务编排、上下文、权限、质量闸门、存储、trace、metrics 和 API。

最终采用五层 Harness 架构：

```text
Tool Layer
Orchestration Layer
Context Layer
Governance Layer
Infrastructure Layer
```

### 3.1 Tool Layer：工具层

职责：

- 管理所有 Agent 可调用工具。
- 通过 `ToolRegistry` 统一注册、调用和记录工具。
- 支持工具权限控制。
- 支持 `ToolCall` trace。
- 后续支持 MCP 外部工具接入。

当前 M1-M6 已有：

```text
ToolRegistry
ToolCall
artifact_store.load_many
artifact_store.save_many
snapshot_collector.collect
artifact_validator.check_refs
citation_checker.check_claims
review_checker.check_report
```

后续补充：

```text
web_search
web_scrape
docs_fetch
news_search
github_search
source_verify
mcp_tool_discovery
mcp_tool_execute
```

当前只是：

```text
MCP-style tool boundary
```

也就是“像 MCP 一样有工具边界”，但还不是真正的 MCP server/client。

最终要支持真正 MCP 集成：工具发现、schema 映射、权限检查、工具执行和 ToolCall trace。

### 3.2 Orchestration Layer：编排层

职责：

- 管理 DAG / TaskBoard。
- 管理 `AgentRuntime`。
- 管理需要 LLM 的 ReAct Agent Loop。
- 管理动态任务插入。
- 管理反馈循环。
- 管理 Ratchet 机制和失败恢复。

当前 M1-M6 已有：

```text
fixed DAG workflow
DAGNode
AgentRuntime
AgentRun
pipeline_summary
```

后续补充：

```text
OrchestratorAgent
TaskBoard
TaskRecord
Dynamic DAG
ReAct Agent Loop
RatchetMechanism
RetryPolicy
FeedbackLoopController
```

最终形态是：

```text
外层：Dynamic DAG / TaskBoard
内层：部分 Agent 使用 ReAct Loop
```

不要把 DAG 和 ReAct 混为一谈：

```text
DAG / TaskBoard 负责“任务怎么流转”
ReAct Agent Loop 负责“某个 Agent 在节点内部如何思考、调工具、观察、继续”
```

### 3.3 Context Layer：上下文层

职责：

- 为不同 Agent 构造不同上下文。
- 管理 System Context、Task Context、Working Context。
- 控制上下文预算。
- 做渐进式压缩。
- 从 `ArtifactStore` 按需选择上下文。
- 压缩文本但保留 `source_id`、`evidence_id`、`claim_id`、`citation_status`。

最终采用三层上下文：

```text
System Context：
Agent 角色、工具描述、输出 schema、禁止无证据结论等固定约束。

Task Context：
当前任务、竞品、行业、focus areas、当前 DAGNode、前置任务摘要。

Working Context：
当前 Agent 需要的 evidence、ProductCard、Claim、CitationCheck、ToolCall 摘要。
```

后续需要新增：

```text
ContextBuilder
ContextCompressor
TokenBudgetEstimator
PriorityRanker
ContextBundle
```

压缩策略：

```text
Level 1：移除 raw HTML / raw JSON / 工具原始输出，只保留摘要。
Level 2：压缩旧 ToolCall / 旧 AgentRun / 旧 working turns。
Level 3：按 competitor / dimension / claim 选择 top-k evidence。
Level 4：激进摘要，只保留关键发现、决策、数据点和证据 ID。
```

硬规则：

```text
压缩可以压缩文本，但不能丢 source_id / evidence_id / claim_id / citation_status。
```

### 3.4 Governance Layer：治理层

职责：

- 管理 Token 预算。
- 管理工具权限。
- 管理 Agent 权限边界。
- 管理审计日志。
- 管理质量阈值。
- 管理人工介入条件。

当前 M1-M6 已有：

```text
Evaluation Harness
CitationCheck
ReviewFeedback
ReviewIssue
ToolCall trace
AgentRun trace
```

后续补充：

```text
TokenBudgetManager
PermissionGuard
AuditLogger
QualityGate
HumanInterventionPolicy
MaxReviewRounds
MaxRetryBudget
```

质量闸门原则：

```text
CitationAgent 和 ReviewerAgent 是质量闸门。
如果 claim 没有 evidence，或者 report 引用了 unsupported claim，系统不能直接 finalize report。
```

### 3.5 Infrastructure Layer：基础设施层

职责：

- 提供可观测性。
- 提供 trace。
- 提供 metrics。
- 提供 API。
- 提供前端展示。
- 后续支持 OpenTelemetry / SSE / 日志 / 监控。

当前 M1-M6 已有：

```text
DAGNode
AgentRun
ToolCall
pipeline_summary
eval_summary
check_workflow_trace.py
check_run_artifacts.py
harness/run_eval.py
```

后续补充：

```text
trace_id
span_id
parent_span_id
FastAPI read-only API
SSE event stream
frontend trace panel
OpenTelemetry-compatible trace export
structured logs
metrics dashboard
```

## 4. Agent 角色定义

### 4.1 OrchestratorAgent

职责：

- 解析用户竞品分析需求。
- 识别行业、竞品、分析维度、输出格式。
- 创建初始 DAG / TaskBoard。
- 维护任务状态。
- 根据 Analyst / Writer / Reviewer 的反馈动态创建新任务。
- 控制反馈循环轮数。
- 最终汇总状态。

`OrchestratorAgent` 是协调者，不应该自己直接写没有证据支撑的最终分析结论。

### 4.2 CollectorAgent

职责：

- 根据采集任务执行 Web Search / Web Scrape / Docs Fetch / News Search 等。
- 生成 `SourceDocument`。
- 从网页、文档、新闻、官网中抽取 `SourceEvidence`。
- 所有证据必须保留 `source_id`、`url`、`title`、`source_type`、`accessed_at`、`snippet`。
- 不允许把原始网页全文直接传给后续 Agent 作为主要输入。
- 后续 Agent 应主要消费 `SourceEvidence`。

### 4.3 ExtractorAgent

职责：

- 从 `SourceEvidence` 中聚合产品画像。
- 输出 `ProductCard`。
- `ProductCard` 中的核心字段必须挂 `evidence_ids`。
- 不允许生成没有证据支撑的产品特征、优势、劣势。

### 4.4 AnalystAgent

职责：

- 基于 `ProductCard` 和 `SourceEvidence` 进行多维分析。
- 生成 `AnalysisClaim`。
- 支持 SWOT、功能对比、市场定位、定价/商业模式、生态接入、风险限制、产品机会分析。
- 每条 `AnalysisClaim` 必须绑定 `evidence_ids`。
- 如果证据不足，应创建 `supplement_collection` 或 `supplement_analysis` 任务，而不是硬写结论。

### 4.5 WriterAgent

职责：

- 基于 `ProductCard`、`AnalysisClaim`、`CitationCheck` 生成 `CompetitiveReport`。
- 报告关键观点必须引用 `claim_id`。
- 不允许新增没有 `claim_id` / `evidence_ids` 支撑的强结论。
- 如果发现章节缺材料，应创建补充采集或补充分析任务。

### 4.6 CitationAgent

职责：

- 检查 `claim.evidence_ids` 是否存在。
- 检查 evidence 是否能追溯到 `SourceDocument`。
- 判断 `citation_status`：

```text
supported
weak
missing_evidence
invalid_evidence
```

- 标记 weak / invalid / missing citation。
- 为 `ReviewerAgent` 提供可解释的 citation issue。

### 4.7 ReviewerAgent

职责：

- 审查报告结构完整性。
- 审查是否覆盖用户要求的分析维度。
- 审查是否存在 unsupported claim。
- 审查 weak citation 是否被合理标注。
- 输出 `ReviewFeedback` / `ReviewIssue`。
- 如果不通过，触发 `revise_report` / `supplement_collection` / `supplement_analysis` 任务。
- 支持最多 N 轮反馈循环，避免无限返工。

## 5. TaskBoard / TaskRecord 设计目标

最终系统需要支持 `TaskBoard`，而不是只支持固定 pipeline。

建议 `TaskRecord` 字段：

```text
task_id
parent_task_id
task_type
target_agent_role
status
priority
depends_on
blocked_by
input_refs
output_refs
reason
created_by_agent_run_id
claimed_by_agent
created_at
updated_at
error
metadata
```

任务状态建议：

```text
pending
ready
claimed
running
blocked
completed
failed
skipped
requires_human
```

任务类型建议：

```text
initial_planning
collect_sources
supplement_collection
extract_product_card
analyze_dimension
supplement_analysis
check_citations
write_report
revise_report
review_report
finalize_report
```

规则：

- `OrchestratorAgent` 可以创建任务。
- `AnalystAgent` / `WriterAgent` / `ReviewerAgent` 可以提出任务请求。
- `TaskBoard` 负责维护状态。
- DAG 负责维护依赖。
- Agent 可以根据 role 和 ready 状态认领任务。
- 第一阶段先做系统调度。
- 后续再做真正自主认领。

## 6. 动态 DAG 与反馈循环

最终 workflow 必须支持动态扩展 DAG，也必须支持有上限的反馈循环。

### 6.1 Analyst -> Collector

当 `AnalystAgent` 发现证据不足：

```text
AnalystAgent
    -> create supplement_collection task
    -> CollectorAgent
    -> new SourceEvidence
    -> AnalystAgent re-analyze
```

例子：

- 缺少某竞品定价证据。
- 缺少部署成本证据。
- 缺少 SDK / API 文档证据。
- 缺少安全合规证据。

### 6.2 Writer -> Analyst / Collector

当 `WriterAgent` 写报告时发现某章节缺少支撑：

```text
WriterAgent
    -> request supplement claim / supplement evidence
    -> AnalystAgent / CollectorAgent
    -> WriterAgent revise
```

例子：

- 战略建议缺少 evidence。
- 市场定位结论缺少 claim。
- 报告某章节没有足够材料。

### 6.3 Reviewer / Citation -> Writer / Analyst / Collector

当 `ReviewerAgent` 或 `CitationAgent` 发现问题：

```text
Reviewer / Citation
    -> ReviewIssue
    -> create revise_report / supplement_collection / supplement_analysis
    -> rerun affected nodes
    -> review again
```

必须支持最大反馈轮数，例如：

```text
max_review_rounds = 3
```

避免无限循环。

## 7. Hierarchical Error Recovery Strategy：分级错误恢复策略

最终系统不能“报错就崩”，而要先识别错误类型，再选择恢复路径。

错误恢复拆成两层：

```text
LLM / Agent Loop 层错误恢复
DAG / TaskBoard 层错误恢复
```

核心原则：

```text
错误先分类，恢复再执行，失败最后才暴露。
```

### 7.1 LLM / Agent Loop 层错误恢复

最小状态结构：

```text
recovery_state = {
    "continuation_attempts": 0,
    "compact_attempts": 0,
    "transport_attempts": 0,
}
```

恢复决策类型：

```text
continue
compact
backoff
fail
```

#### 输出被截断

典型情况：

```text
stop_reason == max_tokens
```

恢复策略：

```text
continue recovery
```

追加续写提示：

```text
Output limit hit. Continue directly from where you stopped. Do not restart or repeat.
```

预算：

```text
continuation_attempts <= 3
```

#### 上下文太长

典型情况：

```text
prompt too long
context length exceeded
```

恢复策略：

```text
compact recovery
```

做法：

```text
调用 ContextCompressor
压缩旧 working context
保留任务目标、关键决定、下一步计划、source_id / evidence_id / claim_id
再重试
```

预算：

```text
compact_attempts <= 2
```

#### 临时请求失败

典型情况：

```text
timeout
rate limit
connection error
service unavailable
transient API error
```

恢复策略：

```text
backoff retry
```

做法：

```text
指数退避 + jitter
最多重试若干次
```

预算：

```text
transport_attempts <= 3
```

### 7.2 DAG / TaskBoard 层错误恢复

#### Level 1：自动重试

适用：

```text
网络超时
API 限流
临时工具失败
```

策略：

```text
retry with exponential backoff
```

#### Level 2：降级执行

适用：

```text
某个工具不可用
某个 LLM 调用失败
某个网页抓取失败
```

例子：

```text
web_scrape 失败 -> 降级使用 web_search 摘要
LLM Extractor 失败 -> fallback 到 rule-based extractor
LLM Writer 失败 -> fallback 到 template writer
```

#### Level 3：任务跳过

适用：

```text
非关键分析维度失败
某个可选竞品来源缺失
某个非核心数据源不可用
```

策略：

```text
mark task as skipped
在 ReviewFeedback / final report 中注明信息缺口
```

#### Level 4：人工介入

适用：

```text
核心任务多次失败
citation 冲突严重
关键结论无法验证
review 多轮不通过
```

策略：

```text
status = requires_human
暂停自动 finalize
输出需要人工判断的 issue
```

## 8. 错误恢复和 Ratchet 的关系

错误恢复负责：

```text
失败后怎么继续
```

Ratchet 负责：

```text
防止系统反复做无效动作、重复调用同一工具、回退到已完成状态
```

二者结合后：

```text
重复搜索同一个 query 超过阈值 -> Ratchet block
工具临时失败 -> ErrorRecovery retry
证据不足 -> 创建 supplement_collection task
多轮仍失败 -> requires_human
```

Ratchet 不是 retry。它是一个防止系统退步和重复无效操作的机制。

## 9. MCP Tool Integration Roadmap

当前项目已有：

```text
ToolRegistry
ToolCall
MCP-style tool boundary
```

但还没有真正 MCP。

最终系统需要支持：

```text
MCP Client
MCP Server Connection
tools/list discovery
tools/call execution
JSON Schema mapping
permission control
ToolCall trace
```

需要支持的传输方式：

```text
stdio
HTTP
```

MCP 工具接入流程：

```text
1. MCPConnector 连接外部 MCP server
2. 调用 tools/list 发现工具
3. 将 MCP tool schema 转成内部 ToolDefinition
4. 注册到 ToolRegistry
5. Agent 根据角色权限获取可用工具
6. ToolRegistry.call 执行工具
7. 结果统一保存 ToolCall
8. 必要时将结果转成 SourceDocument / SourceEvidence
```

后续可接入：

```text
web_search MCP
browser MCP
github MCP
filesystem MCP
document reader MCP
database MCP
news/search MCP
```

硬规则：

```text
MCP 工具返回的原始结果不能直接进入 WriterAgent。
必须经过 SourceDocument / SourceEvidence 或其他结构化 artifact，再进入后续分析链路。
```

## 10. Ratchet and Quality Gate

Ratchet 机制用于防止 Agent 退化：

```text
重复调用同一个工具
反复搜索同一个 query
已完成分析后又回退重做
在两个方案之间来回摇摆
基于错误中间结果继续扩散
```

核心机制：

```text
checkpoint
locked state
repeat action detection
regression detection
progress validation
```

建议后续实现：

```text
RatchetCheckpoint
RatchetDecision
RatchetMechanism
```

在本项目中，Ratchet 可以用于：

1. `CollectorAgent` 防止重复搜索同一 query。
2. `AnalystAgent` 防止重复生成同一类 unsupported claim。
3. `WriterAgent` 防止反复重写已经通过 review 的章节。
4. `ReviewerAgent` 防止无限要求修改。
5. `OrchestratorAgent` 防止动态 DAG 无限扩张。

Quality Gate 用于控制每个阶段是否能进入下一阶段：

```text
SourceEvidence 数量不足 -> 不能进入完整分析
AnalysisClaim 没有 evidence_ids -> 不能进入 report
CitationCheck 有 invalid_evidence -> 不能 finalize
ReviewFeedback approved=false -> 不能输出 final report
```

当前已有的 `CitationCheck` / `ReviewFeedback` / `Evaluation Harness` 可以视为 Quality Gate 雏形，后续需要升级成显式 Ratchet + Gate 机制。

## 11. 最终架构完整描述

完整最终架构：

```text
User Request
    ↓
OrchestratorAgent
    ↓
TaskBoard / Dynamic DAG
    ↓
AgentRuntime
    ↓
Specialist Agents
    ↓
ToolRegistry / MCP Tools
    ↓
ArtifactStore
    ↓
SourceDocument / SourceEvidence
    ↓
ProductCard / AnalysisClaim
    ↓
CitationCheck / ReviewFeedback
    ↓
CompetitiveReport
    ↓
Evaluation Harness
    ↓
FastAPI / SSE / Frontend Trace Panel
```

对应五层 Harness：

```text
Tool Layer：
ToolRegistry + MCP + internal tools

Orchestration Layer：
DAG + TaskBoard + AgentRuntime + ReAct + Ratchet

Context Layer：
ContextBuilder + ContextCompressor + Artifact-aware context

Governance Layer：
Permission + Token Budget + Audit + Quality Gate + Recovery Policy

Infrastructure Layer：
ArtifactStore + Trace + Metrics + FastAPI + SSE + Frontend
```

## 12. 当前 M1-M6 和最终形态的关系

当前 M1-M6 不是废弃版本，而是最终系统的底座。

当前阶段可以定义为：

```text
Static Evidence-first Agent Workflow
```

当前已经完成：

```text
SourceDocument / SourceEvidence
ProductCard / AnalysisClaim
CitationCheck / ReviewFeedback
ArtifactStore
fixed DAG workflow
AgentRun / ToolCall trace
AgentRuntime
ToolRegistry
Evaluation Harness
```

最终阶段定义为：

```text
Dynamic TaskBoard-driven Multi-Agent Competitive Intelligence System
```

当前还缺：

```text
OrchestratorAgent 真正拆任务
TaskBoard / TaskRecord
动态 DAG 插入新任务
Agent 自主认领任务
WebCollector 自动搜索
LLM ReAct Agent Loop
ContextBuilder / ContextCompressor
反馈循环自动返工
多轮 review 限制
FastAPI / 前端展示
MCP tool integration
Hierarchical error recovery
Ratchet / Quality Gate
```

## 13. 后续里程碑路线：M7-M16

### M7：FastAPI Read-only Artifact API

目标：

- 暴露已有 artifacts。
- 支持前端读取 summary、report、claims、evidence、trace、eval。
- 不做复杂前端，不接 LLM。

建议接口：

```text
GET /api/tasks/{task_id}/summary
GET /api/tasks/{task_id}/sources
GET /api/tasks/{task_id}/evidence
GET /api/tasks/{task_id}/product-cards
GET /api/tasks/{task_id}/claims
GET /api/tasks/{task_id}/citation-checks
GET /api/tasks/{task_id}/report
GET /api/tasks/{task_id}/review
GET /api/tasks/{task_id}/trace
GET /api/tasks/{task_id}/eval
```

### M8：LLMClient + LLM Agent

目标：

- 新增统一 `LLMClient`。
- 让 `ExtractorAgent` / `AnalystAgent` / `WriterAgent` 支持 LLM 模式。
- 保留 rule fallback。
- LLM 输出必须通过 Pydantic schema 校验。
- 不允许丢 `evidence_ids` / `claim_ids`。

### M9：WebCollector / Search / Scraper

目标：

- 替换人工 sources/evidence。
- `CollectorAgent` 自动搜索和抓网页。
- 输出 `SourceDocument` / `SourceEvidence`。
- 后续 workflow 保持不变。

### M10：ContextBuilder / ContextCompressor

目标：

- 引入三层上下文：

```text
System Context
Task Context
Working Context
```

- 从 `ArtifactStore` 按需构造 Agent 上下文。
- 压缩 raw HTML、工具原始输出、旧 trace、重复 evidence。
- 永远保留 `source_id` / `evidence_id` / `claim_id`。

### M11：TaskBoard + TaskRecord

目标：

- 支持任务板。
- 支持 ready / blocked / running / completed / failed。
- 支持 `supplement_collection` / `revise_report` 等任务。
- 初期由 `OrchestratorAgent` 调度，不做完全自主认领。

### M12：Dynamic DAG + Feedback Loop

目标：

- 支持运行时插入新 `DAGNode`。
- `AnalystAgent` / `WriterAgent` / `ReviewerAgent` 可以触发补采、补分析、修订报告。
- 支持最多 3 轮 review loop。
- 支持 failed / skipped / requires_human 状态。

### M13：Hierarchical Error Recovery + Ratchet

实现：

```text
continue recovery
compact recovery
backoff retry
fallback
skip
requires_human
Ratchet checkpoint
Quality Gate
```

### M14：MCP Integration

实现：

```text
MCPConnector
MCP tools/list
MCP tools/call
MCP schema mapping
MCP tool registration
```

### M15：Autonomous Agent Claiming

目标：

- 空闲 Agent 可以扫描 `TaskBoard`。
- 根据自身 role 认领 ready task。
- 执行后更新 `TaskRecord`。
- `OrchestratorAgent` 负责全局监督和冲突处理。
- 防止无限循环和重复任务。

### M16：SSE + Frontend Trace Panel

目标：

- 展示 DAG。
- 展示 AgentRun。
- 展示 ToolCall。
- 展示 ReviewIssue。
- 展示 CitationCheck。
- 展示 Eval metrics。
- 支持实时事件流。

## 14. 必须坚持的长期设计原则

1. 不让 `WriterAgent` 直接基于网页原文自由写报告。
2. 所有关键结论必须先形成 `AnalysisClaim`。
3. 每条 `AnalysisClaim` 必须绑定 `evidence_ids`。
4. 每条 `SourceEvidence` 必须能追溯到 `SourceDocument`。
5. 报告中关键观点必须引用 `claim_id`。
6. `CitationAgent` 和 `ReviewerAgent` 是质量闸门。
7. `ArtifactStore` 是跨 Agent 传递中间产物的基础。
8. `ToolRegistry` 是工具边界，不等于 `ArtifactStore`。
9. `TaskBoard` 是动态协作入口。
10. DAG 是任务依赖关系。
11. `AgentRuntime` 是 Agent 执行外壳。
12. `Evaluation Harness` 是质量回归检查。
13. `ContextCompressor` 压缩文本，但不能丢 `source_id` / `evidence_id` / `claim_id`。
14. MCP 工具输出必须先转成结构化 artifact。
15. LLM 输出必须通过 Pydantic schema 校验。
16. 反馈循环必须有最大轮数。
17. 错误恢复必须有 retry budget，不能无限循环。
18. 后续接 LLM、WebCollector、MCP、前端时，不能破坏 evidence-first 主链路。
19. 所有新功能必须能映射到五层 Harness 架构之一。
20. 报告读者界面不直接展示内部 `claim_id` / `evidence_id`；通过 ReportStatement（报告论点）在交互式证据面板中完成追溯，内部编号继续保留在审计数据层。
21. 用户自然语言需求必须先形成可编辑的 `AnalysisTaskDraft`，不得直接启动完整工作流。
22. Intent Agent（意图智能体）只识别任务边界，不承担竞品事实分析、检索或报告写作。
23. 用户确认任务与启动分析必须是两个独立动作；确认后的任务保持 `execution_started=false`。

## 14.1 Step6D 当前推进点

已完成 Step6D.1 + Step6D.2：

```text
自然语言需求输入
Intent Agent（意图智能体）真实 DeepSeek 结构化解析
AnalysisTaskDraft（分析任务草稿）
确定性必要字段校验
前端编辑、追问与动态标题预览
确认并保存 pending AnalysisTask（待执行分析任务）
```

Step6D.3 已完成：

```text
Dataset Compatibility Gate（资料兼容闸门）
Execution Plan Preview（执行计划预览）
用户显式执行授权与 TaskBoard（任务板）入队
```

资料兼容闸门会阻止把当前在线教育人工快照用于其他行业或缺失竞品的任务；确认草稿和授权入队都不会同步运行 Analyst / Writer（分析 / 写作智能体）。

Step6D.4 已完成：

```text
Execution Runner（后台执行器）领取已授权 TaskBoard
复用用户确认的 AnalysisTask，不退回固定演示任务
Mock / DeepSeek 两种显式执行模式
execution_runs / execution_events 持久化
SSE（服务器发送事件）实时进度与刷新续接
完成、失败、中断和重复启动边界
```

下一步进入资料获取阶段：先建设 WebCollector（网页采集器）与 Source Intake（来源入口），把新资料规范化为 SourceDocument / SourceEvidence，再考虑 RAG（检索增强生成）、Embedding（向量嵌入）与向量数据库。

Step6E.1 已完成：

```text
Research Planner（研究规划智能体）
ResearchPlan → KIQ → InformationNeed → ResearchTask
按竞品与维度动态拆分任务并发布 TaskBoard
查询提示、来源偏好、停止条件与最大研究预算
Mock 规划，零真实模型调用、零网络访问
```

Step6E.2 再让 WebCollector（网页采集器）领取 `waiting_for_collector` 任务。

Step6E.2A + Step6E.2B 已完成：Seed URL 安全采集、可见正文提取、采集审计、Collector 领取任务和 TaskBoard 状态回写。真实历史 URL 验证为 5/9 可直接读取；下一小步增加 Browser Fallback（浏览器渲染回退），处理 JavaScript 与反爬页面。

Step6E.2 Browser + Search 已完成：普通 HTTP 正文过短时进入一次有超时预算的 Browser Fallback（浏览器渲染回退）；没有 Seed URL 时可通过可插拔 SearchProvider（搜索供应商）发现候选网址，当前智谱为主、博查备用。`search_attempts / web_search_results / web_pages / collection_attempts` 均为可审计 artifacts（产物）。真实历史 URL 成功率为 7/9；FlowIn 的两个页面仍因严格浏览器超时失败。

Step6E.3 已完成：SearchProvider（搜索供应商）增加智谱正式适配器，并保留博查备用；Collector 成功后动态发布 `extract_source_evidence`，Extractor（抽取智能体）输出带原文字符位置和 URL 的 SourceEvidence。真实腾讯会议试验产生 13 条可逐字回放证据，营销文案为 0，真实 LLM 调用为 0。

Step6E.4 已完成：Extractor 完成后动态发布 `evaluate_evidence_coverage`；Analyst 更新 ProductCard / EvidenceCoverage，并依据 ResearchPlan 的完整竞品 × 维度范围识别 missing、partial、weak 与 conflicting。未关闭缺口会生成 ResearchGap 和下一轮 `supplement_collection`，Collector 可直接领取；`collection_round`、`max_collection_rounds` 与 `max_total_sources` 共同限制循环。重复刷新不会跳过未执行任务，也不会产生超预算轮次。该步骤不调用 LLM。

Step6E.5 已完成：ResearchLoopRunner 通过单工作线程自动调度 Collector -> Extractor -> Analyst coverage，并把 `ResearchLoopRun / ResearchLoopEvent` 落盘，通过 SSE 展示实时进度。覆盖充分时停止；来源、采集轮次或动作安全预算耗尽时进入 `requires_human`；无可执行任务时保留明确停止原因。循环复用现有 Agent 与 TaskBoard 边界，不让网页绕过 SourceDocument / SourceEvidence，也不调用 LLM。

Step6F 已完成：覆盖充分的研究任务可由用户显式确认后执行一次真实 DeepSeek Domain Analyst。Analyst 读取 Step6E 的结构化来源、证据、产品卡片、研究计划和覆盖结果，输出 `CompetitiveAnalysisPortfolioV2 / AnalysisClaim`；CitationAgent 随后确定性检查引用。真实腾讯会议试验得到 13 条带有效 evidence_id 的结论，13 条 CitationCheck 均为 `supported`。该路径不调用 Writer、不重新采集网页，并在已有完成产物时阻断重复 LLM 调用。

Step6F 之后的推荐小步是 Writer Integration（写作接入）：只读取通过 CitationCheck 的 AnalysisClaim 生成 CompetitiveReport，并继续要求用户明确确认额外 1 次真实模型调用。Reviewer 与正式报告批准仍应保持独立质量闸门。

Step6G 已开始并完成固定 URL 抽取基线：单对象需求可以确认，主要竞品先从本地来源目录发现；Live 任务会重新采集已知 URL，而不是直接把旧人工 evidence 当成新结果。同一网页可被多个意图／维度复用。Extractor 使用 ResearchTask 的 objective 和 query_hints 做意图筛选，并保持逐字位置校验。当前 7 个可访问网页中仍存在的 5 条人工证据全部找回；两种不同意图的证据 Jaccard 重合率为 1.25%。资料覆盖不完整时允许 Analyst 输出阶段性结论和 ResearchGap，不再等待所有维度齐全。

## 15. 后续开发前必须回答的问题

每次新增能力前，先回答：

1. 这个功能属于五层 Harness 的哪一层？
2. 它读取哪个 schema？
3. 它写入哪个 schema？
4. 它会生成哪个 artifact 文件？
5. 哪个 Agent 负责这个行为？
6. 哪个 validation 或 eval metric 能证明它工作正常？
7. 它是否保留 `SourceDocument -> SourceEvidence -> ProductCard -> AnalysisClaim -> CitationCheck -> CompetitiveReport -> ReviewFeedback` 主链路？

如果这些问题答不清楚，就不应该开始实现。
