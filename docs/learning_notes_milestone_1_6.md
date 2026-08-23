# Milestone 1-6 Learning Notes

这份文档用于学习复盘当前 `competitive-intel-agents` 项目。目标不是背代码，而是理解这个系统为什么这样拆、每一步产物怎么流动、后面接 LLM / FastAPI / RAG / 前端时应该接在哪里。

当前默认 run 目录是：

```text
backend/app/data/runs/snapshot_online_education_demo/
```

当前已跑通的 summary 是：

```text
sources=9
evidence=32
product_cards=3
claims=5
citation_checks=5
reports=1
review_feedback=1
dag_nodes=6
agent_runs=6
tool_calls=19
supported=4
weak=1
approved=True
review_score=9.0
pipeline_status=completed
eval_passed=True
```

## 1. 当前项目总览

当前项目不是普通 LLM 报告生成器。普通报告生成器通常是“用户给一个需求，模型直接写一篇报告”。这种方式的问题是：报告看起来完整，但很难回答“这句话来自哪里？”、“哪个来源支撑这个结论？”、“哪个 Agent 在哪一步生成了它？”。

当前项目是一个 evidence-first multi-agent workflow。它先把来源和证据结构化，再从证据生成产品卡、分析结论、引用检查、报告和审查反馈。报告不是凭空生成的，而是由一串可检查 artifact 推出来的。

当前仍然是规则版，不接 LLM。这里的 `CollectorAgent / ExtractorAgent / AnalystAgent / CitationAgent / WriterAgent / ReviewerAgent` 都已经有 Agent 角色，但执行逻辑还是确定性的 Python 规则。这样做的好处是先把证据链、trace、validation、evaluation 打稳定，后面替换 LLM 时不会把系统边界弄乱。

当前已经有四个基础设施层：

- `AgentRuntime`：负责一个 AgentRun 的生命周期，包括开始时间、结束时间、耗时、错误、输出摘要。
- `ToolRegistry`：负责注册内部工具，并且每次调用工具都自动记录 `ToolCall`。
- `ArtifactStore`：负责把每一步产物按 `task_id/artifact_type.json` 保存下来。
- `Evaluation Harness`：负责把 artifact 和 trace 的质量检查抽象成指标，并输出 `eval_summary.json`。

当前主证据链：

```text
SourceDocument
-> SourceEvidence
-> ProductCard
-> AnalysisClaim
-> CitationCheck
-> CompetitiveReport
-> ReviewFeedback
```

当前运行链路：

```text
run_snapshot_agent_workflow_demo.py
-> DAGNode
-> AgentRun
-> ToolCall
-> ArtifactStore
-> Eval Harness
```

可以把它理解成两条线：

- 业务数据线：来源、证据、产品卡、结论、引用检查、报告、审查。
- 运行观测线：哪个节点执行了、哪个 Agent 执行了、调用了什么工具、是否成功。

## 2. Milestone 1 到 Milestone 6 分别做了什么

### Milestone 1：Snapshot 数据入口

Milestone 1 解决的是“数据从哪里进来，并且怎么保证进入系统的数据是结构化的”。

输入是本地 snapshot 文件：

```text
backend/app/data/snapshots/online_education/sources.json
backend/app/data/snapshots/online_education/evidence.json
```

输出是 run 目录中的：

```text
sources.json
evidence.json
```

对应文件：

- `backend/app/collectors.py`
- `backend/check_snapshot.py`
- `backend/run_collect_demo.py`
- `backend/app/harness/artifacts.py`
- `backend/app/schemas.py`

这一步的核心是 `LocalSnapshotCollector` 读取本地 snapshot，然后把数据校验成：

- `SourceDocument`
- `SourceEvidence`

它解决的问题是：后面的分析不直接读散乱 markdown，也不直接让 LLM 自由发挥，而是先把来源和证据变成稳定的 schema 对象。

当前生成的 artifact：

```text
sources.json    # 9 条 SourceDocument
evidence.json   # 32 条 SourceEvidence
```

### Milestone 2：ProductCard / Claim / CitationCheck

Milestone 2 解决的是“从原始证据变成可分析的结构化业务结论”。

`ProductCard` 是竞品产品卡。它按 competitor 聚合 evidence，形成一个产品视角的摘要，例如定位、目标用户、价格、核心功能、优势、劣势。当前由 `backend/build_product_cards_demo.py` 的规则函数生成，Milestone 6 后由 `ExtractorAgent` 调用同一套规则逻辑。

`AnalysisClaim` 是分析结论。它不是普通自然语言句子，而是一条有维度、有竞品范围、有证据绑定的 claim。当前 5 条 claim 覆盖：

```text
positioning
feature
pricing
ecosystem
risk
```

`CitationCheck` 是引用检查结果。它检查每条 claim 的 `evidence_ids` 是否存在，证据能否关联到 source，以及证据质量是否足够强。

每条 claim 必须绑定 `evidence_ids`，原因很简单：没有证据绑定的 claim 就无法被检查，也无法进入可靠报告。这个项目的核心不是“写得像分析报告”，而是“每条关键结论能回到证据”。

Citation 状态含义：

- `supported`：claim 的 evidence 都存在，evidence.source_id 都能关联到 SourceDocument，且没有明显低置信度/低可靠性问题。
- `weak`：证据链存在，但 evidence confidence 或 source reliability 偏低。当前有 1 条 weak。
- `invalid_evidence`：claim 引用了不存在的 evidence，或者 evidence 引用了不存在的 source。
- `missing_evidence`：claim 没有绑定任何 evidence_ids。
- `unsupported`：schema 预留状态，表示证据无法支撑 claim。当前规则版 checker 主要使用 supported、weak、invalid_evidence、missing_evidence。

对应文件：

- `backend/build_product_cards_demo.py`
- `backend/build_claims_demo.py`
- `backend/run_citation_check_demo.py`
- `backend/app/agents/snapshot.py`
- `backend/app/schemas.py`

生成 artifact：

```text
product_cards.json
claims.json
citation_checks.json
```

### Milestone 3：Report / ReviewFeedback

Milestone 3 解决的是“把结构化 claim 组织成报告，并对报告做审查反馈”。

`CompetitiveReport` 是最终报告对象。它不只是 markdown，还保存：

- `title`
- `markdown`
- `claim_ids`
- `created_by_agent_run_id`
- `sections`

`report.claim_ids` 很重要，因为它说明这份报告用了哪些 claim。后面的 validation 会检查：

- `report.claim_ids` 都能在 `claims.json` 找到。
- `report.markdown` 里包含每个 claim id，例如 `[cl_online_education_feature_baseline]`。

`ReviewFeedback` 是审查结果。它保存：

- 是否 approved
- overall_score
- issues
- suggestions

`ReviewIssue` 是具体问题项。比如 weak citation 会进入 medium severity issue。当前 `run_review_demo.py` 会读取 citation_checks，如果某个 claim 的 citation status 是 `weak`，就生成一个中等严重度 issue，说明这条 claim 有弱引用支持。

对应文件：

- `backend/build_report_demo.py`
- `backend/run_review_demo.py`
- `backend/app/agents/snapshot.py`
- `backend/app/schemas.py`

生成 artifact：

```text
reports.json
review_feedback.json
```

### Milestone 4：可复现本地 Pipeline + Artifact Validation

Milestone 4 解决的是“不要一段一段手动跑，要能一条命令复现整个链路，并且有独立验证脚本”。

`run_snapshot_pipeline_demo.py` 做的是顺序执行旧版本地 pipeline：

```text
collect snapshot
build product_cards
build claims
citation check
build report
review
```

每一步失败就停止，最后输出 summary。

`check_run_artifacts.py` 不生成业务产物，只做 artifact validation。它检查：

- required artifacts 是否存在。
- `evidence.source_id` 是否能关联到 `sources.id`。
- `product_cards.source_ids / evidence_ids` 是否真实存在。
- `claims.evidence_ids` 是否真实存在。
- `citation_checks.claim_id` 是否能关联到 claim。
- `report.claim_ids` 是否能关联到 claims。
- `report.markdown` 是否包含所有 claim id。
- `review_feedback` 是否解释 weak / missing / invalid citation。

这一步可以叫 validation harness，因为它已经不是单个业务函数，而是独立于生成逻辑之外的质量闸门。生成逻辑说“我写好了”，validation harness 说“我来检查引用链是否真的完整”。

对应文件：

- `backend/run_snapshot_pipeline_demo.py`
- `backend/check_run_artifacts.py`
- `docs/runbook_snapshot_pipeline.md`

### Milestone 5：DAGNode / AgentRun / ToolCall Trace

Milestone 5 解决的是“pipeline 能跑，但我们还想知道每一步是谁执行的、什么时候执行的、用了什么输入、输出了什么、有没有失败”。

`DAGNode` 解决 workflow 拓扑和节点状态问题。它回答：

- 当前 pipeline 有哪几个节点？
- 节点顺序是什么？
- 每个节点依赖谁？
- 节点状态是 pending / running / completed / failed？
- 节点输入和输出 artifact 是什么？

`AgentRun` 解决 Agent 执行记录问题。它回答：

- 哪个 Agent role 执行了这个节点？
- 什么时候开始和结束？
- 耗时多久？
- 输入摘要是什么？
- 输出摘要是什么？
- 失败时错误是什么？

`ToolCall` 解决工具调用追踪问题。它回答：

- Agent 执行过程中调用了哪个内部工具？
- 输入摘要是什么？
- 输出摘要是什么？
- 工具是否成功？

有了这三类 trace，系统就从“能跑的 pipeline”变成了“可观测的 workflow”。如果将来前端展示 Agent 协作过程，核心数据就来自：

```text
dag_nodes.json
agent_runs.json
tool_calls.json
```

对应文件：

- `backend/run_snapshot_agent_workflow_demo.py`
- `backend/app/workflow/dag.py`
- `backend/app/workflow/trace.py`
- `backend/app/workflow/snapshot_pipeline.py`
- `backend/check_workflow_trace.py`

生成 artifact：

```text
dag_nodes.json
agent_runs.json
tool_calls.json
pipeline_summary.json
```

### Milestone 6：AgentRuntime / ToolRegistry / Evaluation Harness

Milestone 6 解决的是“把当前规则版 Agent 工作流包装成更标准的 lightweight harness，而不是在 workflow 函数里散落记录 trace”。

`BaseAgent` 是所有 Agent 的业务接口。它定义：

```text
name
role
input_artifacts
output_artifacts
execute(context)
```

Agent 只处理业务逻辑，不负责计时、保存 AgentRun、捕获错误。这借鉴了 `competitive-analysis-agent-main` 里 BaseAgent 的角色结构，但删掉了 LLM client、shared memory、governance 等当前 V1 不需要的部分。

`AgentRuntime` 是 Agent 的运行外壳。它负责：

- 创建 AgentRun。
- 记录 started_at / completed_at / duration_ms。
- 捕获 error。
- 保存 AgentRun artifact。
- 调用 `agent.execute(context)`。
- 把 output_refs 写回 DAGNode。

这借鉴了 `competitive-analysis-agent-main/backend/harness/runtime.py` 的 runtime 生命周期思想，也借鉴了 `learn-claude-code-main` 的“模型/业务逻辑和 harness loop 分开”的思想。

`ToolRegistry` 是内部工具注册表。它负责：

- 注册工具名到 handler。
- 统一调用工具。
- 自动记录 ToolCall。

当前注册的内部工具：

```text
artifact_store.load_many
artifact_store.save_many
snapshot_collector.collect
artifact_validator.check_refs
citation_checker.check_claims
review_checker.check_report
```

这借鉴了 `competitive-analysis-agent-main/backend/harness/capability.py` 和 `learn-claude-code-main/s02_tool_use` 的 tool dispatch 思想，但当前不是完整 MCP server，只是 MCP-style tool boundary。

`Evaluation Harness` 是独立评估层。它把 `check_run_artifacts.py` 和 `check_workflow_trace.py` 里的核心检查抽象成 metrics，并输出：

```text
eval_summary.json
```

当前指标包括：

```text
artifact_completeness
evidence_ref_valid_rate
product_card_ref_valid_rate
claim_evidence_valid_rate
report_claim_coverage
citation_supported_rate
weak_citation_count
dag_completed_rate
agent_success_rate
tool_call_success_rate
review_approved
review_score
```

当前仍然没有接 LLM 的原因：

1. 主证据链需要先稳定，否则 LLM 引入后很难判断错误来自模型还是工程链路。
2. 引用和 artifact validation 是系统可信度的基础，应该先确定性跑通。
3. 后续接 LLM 时可以只替换某个 Agent 的内部逻辑，而不用重写 DAG、ArtifactStore、ToolRegistry、Evaluation Harness。

对应文件：

- `backend/app/agents/base.py`
- `backend/app/agents/runtime.py`
- `backend/app/agents/snapshot.py`
- `backend/app/tools/registry.py`
- `backend/harness/metrics.py`
- `backend/harness/run_eval.py`
- `backend/harness/cases.yaml`
- `docs/reference_adaptation.md`

## 3. 每个核心 schema 的作用

### AnalysisTask

表示一次分析任务。它包含 query、competitors、industry、focus_areas、mode、status 等信息。

- 谁生成：`check_snapshot.py` 中的 `build_snapshot_task()`。
- 谁消费：collector、workflow、AgentContext。
- 保存在哪：当前主要作为运行上下文使用，暂未单独保存成 artifact。

### SourceDocument

表示一个来源文档，比如官网页面、文档页、价格页、第三方资料。

- 谁生成：`LocalSnapshotCollector` / `CollectorAgent`。
- 谁消费：`SourceEvidence` 引用它；`CitationAgent` 检查 evidence.source_id；`WriterAgent` 渲染引用列表。
- 保存在哪：`sources.json`。

### SourceEvidence

表示从来源中抽取的一条证据。它有 `source_id`、competitor、dimension、snippet、normalized_fact、confidence。

- 谁生成：`LocalSnapshotCollector` / `CollectorAgent`。
- 谁消费：`ExtractorAgent` 生成 ProductCard；`AnalystAgent` 生成 Claim；`CitationAgent` 做引用检查。
- 保存在哪：`evidence.json`。

### ProductCard

表示一个竞品的产品卡，把同一 competitor 的 evidence 聚合成产品视角。

- 谁生成：`ExtractorAgent`，内部调用 `build_product_cards_demo.py` 的规则函数。
- 谁消费：`AnalystAgent` 生成 claims；`WriterAgent` 写报告；`ReviewerAgent` 检查至少 3 个 ProductCard。
- 保存在哪：`product_cards.json`。

### AnalysisClaim

表示一条结构化分析结论。每条 claim 必须有 `evidence_ids`。

- 谁生成：`AnalystAgent`，内部调用 `build_claims_demo.py`。
- 谁消费：`CitationAgent` 检查 evidence；`WriterAgent` 写报告；`ReviewerAgent` 做审查。
- 保存在哪：`claims.json`。

### CitationCheck

表示对一条 claim 的引用检查结果。

- 谁生成：`CitationAgent`，内部调用 `run_citation_check_demo.py` 的 citation checker。
- 谁消费：`WriterAgent` 写报告中的引用状态；`ReviewerAgent` 把 weak/missing/invalid 转成 issue；Evaluation Harness 计算 supported rate。
- 保存在哪：`citation_checks.json`。

### CompetitiveReport

表示最终竞品分析报告。它包含 markdown 和 claim_ids。

- 谁生成：`WriterAgent`，内部调用 `build_report_demo.py`。
- 谁消费：`ReviewerAgent` 审查；FastAPI/前端后续展示。
- 保存在哪：`reports.json`。

### ReviewFeedback

表示审查反馈，包括 approved、overall_score、issues、suggestions。

- 谁生成：`ReviewerAgent`，内部调用 `run_review_demo.py`。
- 谁消费：Evaluation Harness；后续前端显示质量结论和问题列表。
- 保存在哪：`review_feedback.json`。

### ReviewIssue

表示一个具体审查问题，比如 weak citation、缺少 claim、报告为空。

- 谁生成：`ReviewerAgent` 在构建 ReviewFeedback 时生成。
- 谁消费：ReviewFeedback 包含它；后续前端可按 severity 展示。
- 保存在哪：作为 `review_feedback.json` 内部字段保存。

### DAGNode

表示 workflow 中一个节点，比如 `build_claims`。

- 谁生成：`DAGExecutor` 根据固定 StepSpec 生成。
- 谁消费：`AgentRuntime` 和 trace checker；后续前端 DAG 展示。
- 保存在哪：`dag_nodes.json`。

### AgentRun

表示某个 Agent 对某个 DAGNode 的一次执行。

- 谁生成：`AgentRuntime`。
- 谁消费：`ToolRegistry` 通过 agent_run_id 关联 ToolCall；trace checker；后续前端 timeline。
- 保存在哪：`agent_runs.json`。

### ToolCall

表示一次内部工具调用。

- 谁生成：`ToolRegistry.call()` 自动生成。
- 谁消费：trace checker；Evaluation Harness；后续前端展开 AgentRun 详情。
- 保存在哪：`tool_calls.json`。

## 4. 当前 artifacts 解释

### sources.json

从本地 snapshot 的 `sources.json` 来，由 `CollectorAgent` 通过 `snapshot_collector.collect` 读取并保存。

用途是保存所有来源文档。后续 FastAPI 可以提供 source 列表接口；前端可以在 claim 或报告引用处展示来源标题、URL、source_type、reliability_score。

### evidence.json

从本地 snapshot 的 `evidence.json` 来，由 `CollectorAgent` 保存。

用途是保存可被引用的证据事实。后续前端可以在 claim 详情里展示 evidence snippet、normalized_fact、confidence。

### product_cards.json

由 `ExtractorAgent` 从 sources/evidence 生成。

用途是按竞品形成产品视角，帮助用户快速理解 ClassIn、腾讯云实时互动 / TRTC 教育方案、BigBlueButton 的路线。后续前端可以展示成竞品卡片或对比表。

### claims.json

由 `AnalystAgent` 从 product_cards/evidence 生成，后续又被 `CitationAgent` 更新 citation_status。

用途是保存报告中的关键分析结论。后续前端可以展示 claim 列表、维度筛选、证据绑定情况。

### citation_checks.json

由 `CitationAgent` 从 sources/evidence/claims 生成。

用途是保存 claim-level citation verification。后续前端可以给每条 claim 标记 supported / weak / invalid / missing_evidence。

### reports.json

由 `WriterAgent` 从 product_cards/claims/citation_checks/sources/evidence 生成。

用途是保存最终 Markdown 报告。后续 FastAPI 可以返回报告详情；前端可以渲染 Markdown，并把 `[cl_xxx]` 做成可点击引用。

### review_feedback.json

由 `ReviewerAgent` 从 reports/claims/citation_checks/product_cards 生成。

用途是保存审查结论和质量问题。后续前端可以展示 approved、score、issues、suggestions。

### dag_nodes.json

由 `DAGExecutor` 生成。

用途是保存 workflow 拓扑和每个节点状态。后续前端可以画 DAG：collector -> extractor -> analyst -> citation -> writer -> reviewer。

### agent_runs.json

由 `AgentRuntime` 生成。

用途是保存每个 Agent 的执行记录。后续前端可以做 Agent timeline，展示每个角色什么时候开始、什么时候结束、输出摘要是什么。

### tool_calls.json

由 `ToolRegistry` 自动生成。

用途是保存内部工具调用 trace。后续前端可以在每个 AgentRun 下展开工具调用，例如读取了哪些 artifact、保存了哪些 artifact、跑了哪些 checker。

### pipeline_summary.json

由 `run_snapshot_agent_workflow_demo.py` 最后保存。

用途是保存 pipeline 总结，包括各 artifact 数量、supported/weak 数量、approved、review_score、pipeline_status。后续前端可以作为任务概览卡片。

### eval_summary.json

由 `harness/run_eval.py` 生成。

用途是保存 evaluation harness 的指标结果。后续可以作为 CI 或后端健康检查指标，也可以在前端展示“证据链完整率、Agent 成功率、工具调用成功率”。

## 5. 当前规则版逻辑在哪里

当前仍然是规则版的地方：

- ProductCard 生成：`backend/build_product_cards_demo.py`，按 competitor 和 evidence dimension 聚合。
- Claim 生成：`backend/build_claims_demo.py`，固定 5 条 claim specs。
- CitationCheck：`backend/run_citation_check_demo.py`，规则检查 evidence/source 引用和可靠性。
- Report 生成：`backend/build_report_demo.py`，固定报告结构和 claim 引用格式。
- ReviewFeedback：`backend/run_review_demo.py`，规则检查 markdown、claim_ids、weak citation、关键词、ProductCard 数量。
- Eval metrics：`backend/harness/metrics.py`，规则计算各种通过率和分数。

后续适合替换为 LLM 的 Agent：

- `ExtractorAgent`：后续可用 LLM 从 evidence 中抽取更丰富的产品卡字段。
- `AnalystAgent`：后续可用 LLM 生成更有洞察的 claim，但必须输出 schema，并绑定 evidence_ids。
- `WriterAgent`：后续可用 LLM 写更自然的报告，但必须保留 claim id 标注。

暂时建议保持规则版的 Agent：

- `CollectorAgent`：本地 snapshot 和后续 WebCollector 都应该以确定性数据采集和 schema 校验为主。
- `CitationAgent`：引用检查是质量闸门，应该尽量规则化或至少规则 + LLM 双重校验。
- `ReviewerAgent`：审查可以加入 LLM，但基础红线检查必须保留规则版。
- `Evaluation Harness`：评估指标应保持确定性，不能依赖 LLM 自己评价自己。

## 6. 当前项目和普通 RAG 问答系统有什么区别

普通 RAG 通常是：

```text
query -> retrieve chunks -> answer
```

用户问一个问题，系统从向量库里找 top-k chunks，然后让 LLM 生成回答。

我们当前系统是：

```text
sources/evidence -> ProductCard -> Claim -> CitationCheck -> Report -> ReviewFeedback
```

区别在于：

- 普通 RAG 的核心产物通常是 answer。
- 当前系统的核心产物是一组可追踪 artifact。
- 普通 RAG 关注“找哪些 chunk 回答这个问题”。
- 当前系统关注“证据如何一步步变成报告结论，并被引用检查和审查反馈验证”。

所以 RAG 是后续能力，不是当前主线。后续如果引入 embedding / Chroma / top-k，它应该服务于 evidence retrieval，也就是帮助 Collector 或 Extractor 找到更相关的证据，而不是绕过 ProductCard、Claim、CitationCheck 直接生成答案。

## 7. 当前项目和 competitive-analysis-agent-main 的区别

`competitive-analysis-agent-main` 更像一个竞品分析产品 demo。它已经有 FastAPI、前端 trace 展示、LLM Agent、Web 搜索、SSE、报告展示等产品形态。

当前项目更强调 evidence-first artifact workflow。我们没有直接让 Agent 上网搜索并写报告，而是先做：

```text
schema -> artifact -> validation -> trace -> evaluation
```

我们借鉴了它的：

- 竞品分析产品形态。
- Agent 角色划分。
- Harness 分层思想。
- ToolRegistry 思路。
- DAG / trace / API 展示方向。

但没有直接复刻它的 schema 和流程。当前项目的 schema 更围绕 evidence chain 设计，例如 `SourceEvidence -> AnalysisClaim -> CitationCheck` 是非常明确的主链路。

## 8. 当前项目和 learn-claude-code-main 的关系

`learn-claude-code-main` 是 harness / agent runtime 机制参考。它不专注竞品分析，而是解释一个 Agent 产品外围需要哪些工程机制：

- agent loop
- tool use
- task system
- trace / audit
- harness evaluation
- team protocol
- MCP-style tool boundary

我们当前借鉴的是轻量机制：

- Agent 和 runtime 分离。
- 工具定义和工具 handler 分离。
- DAG / task dependency 的思路。
- ToolCall trace。
- Evaluation Harness。

当前没有引入完整 MCP、team protocol、长期记忆、background task、worktree isolation、自主队友协作系统。原因是 V1 的重点是 evidence-first snapshot workflow，不是做一个完整 Claude Code 复刻版。

## 9. 面试时我应该怎么讲这个项目

### 1 分钟版

这是一个 evidence-first 的多 Agent 竞品分析 workflow。它不是直接让 LLM 写报告，而是先把来源材料校验成 `SourceDocument` 和 `SourceEvidence`，再通过多个 Agent 生成 `ProductCard`、`AnalysisClaim`、`CitationCheck`、`CompetitiveReport` 和 `ReviewFeedback`。所有数据都用 Pydantic schema 校验，并通过 `ArtifactStore` 保存成可复现的 JSON artifact。系统还有 `DAGNode / AgentRun / ToolCall` 三层 trace，能看到每一步由哪个 Agent 执行、输入输出是什么、调用了什么工具。最后用 Evaluation Harness 检查证据引用、报告 claim 覆盖率、Agent 成功率和工具调用成功率。当前是规则版，后续可以逐步接入 LLM、FastAPI、RAG 和 WebCollector。

### 3 分钟版

这个项目是一个面向产品研发场景的多 Agent 竞品分析系统。我没有把它做成普通的“输入需求，LLM 直接生成报告”，而是设计成 evidence-first artifact workflow。

第一层是数据和证据。系统先从本地 Snapshot 读取来源和证据，把它们校验成 Pydantic schema：`SourceDocument` 表示来源文档，`SourceEvidence` 表示从来源中抽取出的证据事实。当前 run 里有 9 条 source 和 32 条 evidence。

第二层是分析产物。ExtractorAgent 把 evidence 聚合成 3 个 ProductCard；AnalystAgent 生成 5 条 AnalysisClaim，每条 claim 都必须绑定 evidence_ids；CitationAgent 对每条 claim 做 citation check，判断是 supported、weak、invalid 还是 missing evidence；WriterAgent 生成 CompetitiveReport，并在报告关键结论后标注 claim id；ReviewerAgent 生成 ReviewFeedback 和 ReviewIssue，比如 weak citation 会进入 medium severity issue。

第三层是 workflow 可观测性。系统有固定 DAG：collect_sources -> build_product_cards -> build_claims -> check_citations -> build_report -> review_report。每个节点保存成 DAGNode，每个 Agent 执行保存成 AgentRun，每次内部工具调用保存成 ToolCall。这样后续前端不仅能展示报告，还能展示 Agent 协作过程和工具调用轨迹。

第四层是工程 harness。Milestone 6 引入了 BaseAgent、AgentRuntime、ToolRegistry 和 Evaluation Harness。BaseAgent 只关心业务逻辑；AgentRuntime 负责计时、错误捕获和 AgentRun；ToolRegistry 统一内部工具边界并自动记录 ToolCall；Evaluation Harness 输出 eval_summary.json，检查 artifact 完整度、证据引用有效率、claim 覆盖率、DAG 完成率、Agent 成功率、工具成功率和 review 分数。

当前系统仍然是规则版，没有接 LLM、FastAPI、前端、爬虫或 RAG。这样做是为了先把证据链和可观测链打牢。后续可以把 ExtractorAgent、AnalystAgent、WriterAgent 替换为 LLM，把 CollectorAgent 扩展成 WebCollector，把 evidence retrieval 接入 embedding / Chroma，把 artifacts 暴露给 FastAPI 和前端展示。

## 10. 我应该重点阅读哪些文件

1. `backend/app/schemas.py`

   先看所有 Pydantic schema。重点理解业务链 `SourceDocument -> SourceEvidence -> ProductCard -> AnalysisClaim -> CitationCheck -> CompetitiveReport -> ReviewFeedback`，以及 trace 链 `DAGNode -> AgentRun -> ToolCall`。

2. `backend/app/harness/artifacts.py`

   看 `ArtifactStore.save_many()` 和 `load_many()`。重点理解为什么所有产物都按 `task_id/artifact_type.json` 保存，这就是可复现和可检查的基础。

3. `backend/app/collectors.py`

   看 `LocalSnapshotCollector` 如何从本地 snapshot 读取 sources/evidence，并校验成 schema。它是当前数据入口。

4. `backend/app/agents/base.py`

   看 `BaseAgent` 的轻量接口。重点理解 Agent 只写业务逻辑，不负责 trace、计时、保存 AgentRun。

5. `backend/app/agents/runtime.py`

   看 `AgentRuntime.run()`。重点理解 AgentRun 是如何创建、更新、保存的，以及错误如何被捕获。

6. `backend/app/tools/registry.py`

   看 `ToolRegistry.register()` 和 `ToolRegistry.call()`。重点理解内部工具如何被统一调用，以及 ToolCall 如何自动记录。

7. `backend/app/workflow/dag.py`

   看 `DAGExecutor`。重点理解固定 DAG 如何顺序执行，节点失败时如何停止，DAGNode 如何保存状态。

8. `backend/app/workflow/snapshot_pipeline.py`

   这是 Milestone 6 的核心编排文件。重点看 `build_snapshot_tool_registry()` 注册了哪些工具，`build_snapshot_workflow_steps()` 如何把 6 个 Agent 接到固定 DAG。

9. `backend/harness/metrics.py`

   看每个 metric 如何从 artifacts 计算出来。重点理解 evaluation harness 不是生成报告，而是检查系统质量。

10. `backend/harness/run_eval.py`

    看 evaluation harness 的入口。重点理解它如何读取当前 run artifacts、调用 metrics、写出 `eval_summary.json`。

建议阅读顺序：

```text
schemas.py
-> artifacts.py
-> collectors.py
-> build_product_cards_demo.py / build_claims_demo.py / run_citation_check_demo.py
-> build_report_demo.py / run_review_demo.py
-> app/agents/base.py
-> app/agents/runtime.py
-> app/tools/registry.py
-> app/workflow/dag.py
-> app/workflow/snapshot_pipeline.py
-> harness/metrics.py
```

如果你能顺着这个顺序讲清楚“一个 evidence 如何最终支撑报告中的一个 claim”，这个项目就真正理解了。
