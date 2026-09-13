# RIVIO-EVAL-R1-PHASE0：评测可行性审计

> 审计日期：2026-09-12
>
> 审计对象：当前工作区（基准提交 `cf86fc51871cf802c40f184db3fc26b4afc1c6ad`，工作区已有未提交改动）
>
> 阶段边界：只做代码与历史 ArtifactStore 的只读审计；未运行正式 A/B，未生成实验指标，未修改生产逻辑。

## 0. 执行摘要

EVAL-R1 可行，但必须把“实验开关”和“冻结输入”放在独立的 eval-only 层，不能直接把生产 `ResearchLoopRunner` 当成完整 E2 baseline，也不能用修改生产预算的方式模拟 E3 关闭补采。

核心结论如下：

- **E2 推荐 baseline**：复用现存的固定单轮组件链 `CollectorQueueService.run_once -> ExtractorQueueService.run_once`，仅处理冻结的首轮 `ResearchTask`，不进入 Coverage 发布和补采调度。它代表 Research Agent Loop 之前的固定研究路径；生产 `ResearchLoopRunner` 本身会继续执行 Coverage/Gap/Supplement，直接拿整条 runner 做 E2 会引入补采混杂。
- **E3 推荐开关**：在 eval-only orchestrator 中以“首轮完成后的状态快照”为分叉点，`supplement_enabled=false` 同时阻断 deterministic gap bridge 和 mission supervisor 两个补采任务生产者；`true` 调用现有生产补采服务。两臂共享完全相同的首轮 Research Agent 前缀，不能通过把 agent budget 置零或改 `max_collection_rounds` 来实现关闭。
- **推荐历史任务**：正文第 5 节列出的 10 个任务，覆盖旧固定循环、新 Research Agent、单对象/多对象、无 gap 控制样本、gap-heavy 压力样本和结构化输出失败样本。
- **可完全 replay**：Coverage/Gap/Budget/Traceability 统计、E3 历史 provenance ablation、固定 collector/extractor 的冻结网页 replay、保留了 raw output 的 JSON/schema replay、离线 fault injection。
- **必须 live 的部分**：如果要评价 Research Agent 的实际策略能力，则 agent action selection 的 LLM 必须 live；如果评价 mission supervisor 的语义补采决策或 Analyst 实际生成可靠性，相应 LLM 也必须 live。主 E2/E3 并不要求 Live Search；只有专门评价实时检索召回与网页时效性时才应 live。
- **最小新增物**：独立的 case manifest、冻结 fixture/cassette manifest、fail-closed replay adapter、E2/E3 eval runner、artifact-only metrics、fault-injection fixtures；均可放在 `backend/eval_r1/` 与独立 evaluation data 目录，不改生产模块。

## 1. 审计范围与方法

### 1.1 审计过的主要路径

- API 与统一 pipeline：`backend/app/api/main.py`、`backend/app/harness/pipeline.py`
- 固定研究循环：`backend/app/execution/research_loop.py`
- Collector/Extractor：`backend/app/collection/service.py`、`backend/app/extraction/service.py`
- Research Agent：`backend/app/execution/research_agent.py`、`backend/app/execution/research_agent_coordinator.py`
- Coverage/Gap/Supplement：`backend/app/intake/step6e4.py`、`backend/app/execution/research_mission.py`
- Analyst 与结构化输出：`backend/app/execution/research_analysis.py`、`backend/app/agents/llm_snapshot.py`、`backend/app/llm/client.py`、`backend/app/llm/structured_output.py`
- 搜索与知乎 MCP：`backend/app/tools/search_provider.py`、`backend/app/tools/search_transport.py`、`backend/app/tools/zhihu_mcp.py`
- ArtifactStore 与现有 eval/check：`backend/app/harness/artifacts.py`、`backend/build_eval_task_inventory.py`、`backend/run_structured_output_eval.py`、`backend/run_traceability_eval.py`、`backend/harness/metrics.py`
- 历史运行：`backend/app/data/runs/*`；另检查了顶层 `artifacts/`，其中主要是两个 professional analyst smoke export，不作为完整历史任务池。

### 1.2 历史库存扫描结果

使用仓库现有 `backend/build_eval_task_inventory.py` 对 `backend/app/data/runs` 做只读扫描，输出写入系统临时目录而非仓库。共发现 92 个 task 目录，92 个可扫描；按现有脚本启发式分类为 complete 13、partial 5、failed 58、unknown 16。该分类反映 pipeline/status 字段，不等同于“是否含可用评测证据”：若干被标记 failed 的任务仍拥有完整研究和报告 artifacts，因此本审计按字段完整度、路径代表性和已知缺陷重新筛选。

全量库存的粗略范围为：

| Artifact | 最小值 | 中位数 | 最大值 |
|---|---:|---:|---:|
| ResearchTask | 0 | 10 | 45 |
| Source | 0 | 9 | 40 |
| Evidence | 0 | 10 | 351 |
| Claim | 0 | 5 | 21 |
| ReportStatement | 0 | 0 | 45 |

现有 `ArtifactStore` 默认根目录在 `backend/app/data/runs`（`backend/app/harness/artifacts.py:93-100`），采用按 task 分目录、按 artifact type JSON 文件持久化和原子写入。以下所有历史数量都来自现有 artifacts；它们只是可行性和选样依据，不是正式 A/B 指标。

## 2. E2 Research Agent A/B

### 2.1 是否还存在 Research Agent Loop 之前的单轮/固定研究路径

**存在，而且组件级入口仍可调用。** 当前仓库同时保留了三类入口：

1. 手动固定阶段 API：
   - `POST /api/analysis-tasks/{task_id}/collector/run-once`（`backend/app/api/main.py:869-880`）
   - `POST /api/analysis-tasks/{task_id}/extractor/run-once`（`backend/app/api/main.py:882-894`）
   - `POST /api/analysis-tasks/{task_id}/coverage/run-once`（`backend/app/api/main.py:896-908`）
2. 旧固定 loop API：`POST /api/analysis-tasks/{task_id}/research-loop`（`backend/app/api/main.py:924-937`），进入 `ResearchLoopRunner`。
3. 当前统一 pipeline：`ResearchPipelineHarness` 在 researching 阶段进入 `ResearchAgentCoordinator`（`backend/app/harness/pipeline.py:627-649,782-821`）；API 为 `POST /api/analysis-tasks/{task_id}/pipeline/run`（`backend/app/api/main.py:1252-1284`）。另保留单任务 Research Agent API（`backend/app/api/main.py:1397-1425`）和 coordinator API（`backend/app/api/main.py:1020-1063`）。

旧固定调用链为：

```text
manual endpoint / ResearchLoopRunner
  -> CollectorQueueService.run_once
     -> 固定 query hints / seed URL
     -> SearchProvider 或 SearchToolTransport
     -> fetch / WebPageContent / SourceDocument
  -> ExtractorQueueService.run_once
     -> WebEvidenceExtractor（确定性 quote extraction）
     -> SourceEvidence
  -> Step6E4QueueService.run_once
     -> Coverage / Gap
     -> 可能发布下一轮 supplement ResearchTask
```

`ResearchLoopRunner` 的固定工厂与调度顺序见 `backend/app/execution/research_loop.py:37-61,261-337`；Collector 单次 claim、搜索、按 `max_sources_per_task` 采集并发布 extractor task 的实现见 `backend/app/collection/service.py:681-886`，Research Agent 自有单 query 入口见同文件 `:657-679`。Extractor 仍是确定性抽取，不依赖 LLM。

### 2.2 能否直接作为 baseline

结论分两层：

- **组件链可以直接复用。** `CollectorQueueService` 和 `ExtractorQueueService` 是合适的历史固定研究 baseline。
- **完整 `ResearchLoopRunner` 不应直接作为 E2 treatment。** 它固定串联 Coverage，并可继续创建 supplement task；这样 E2 比较的将不只是“固定研究 vs Research Agent”，还混入了补采轮数和调度策略差异。

因此推荐在 eval-only runner 中构建“固定单轮 baseline”：

1. 从只读 fixture 克隆完全相同的 `AnalysisTask`、`ResearchPlan`、`InformationNeed` 和**首轮** `ResearchTask`。
2. 以稳定排序逐个运行 `CollectorQueueService.run_once` 与 `ExtractorQueueService.run_once`。
3. 只选择 `collection_round == 1` 且无 `parent_research_task_id`、无 `research_gap_id` 的初始任务。
4. 研究 treatment 内不调用 Coverage queue，也不创建补采任务。
5. 两臂结束后用同一套纯函数做统一 post-hoc 评分：`build_evidence_coverage`（`backend/app/intake/step6e4.py:36-120`）与 `build_research_gaps`（同文件 `:253` 起）。

这个 wrapper 只组合现有服务，不需要改生产分支、API 或环境变量。它也比从 git 历史复制旧实现更可靠，因为 artifact schema、搜索 adapter 和 extractor 都与当前仓库兼容。

### 2.3 当前 Research Agent 调用链

```text
pipeline/run
  -> ResearchPipelineHarness._run_research
  -> ResearchAgentCoordinator.submit/_execute
  -> ResearchEvidenceAgentService.run_once
  -> ResearchEvidenceAgent.run
     -> LLM ResearchAgentDecider
     -> ProductionResearchTools
        -> SEARCH / FETCH / READ / SUBMIT_EVIDENCE
  -> compatibility projection / coverage refresh
  -> mission supervisor
  -> 下一批 bounded ResearchTask（如需要）
```

关键位置：

- per-task service 可注入 `decider` 和 `tools`：`backend/app/execution/research_agent.py:1942-2120`，参数见 `:1966-1967`。
- action loop 与预算检查：`backend/app/execution/research_agent.py:1539-1683`；action 执行：`:1685-1895`。
- coordinator 工厂、提交、执行：`backend/app/execution/research_agent_coordinator.py:132-167,269-369,499-687`。
- per-task 预算派生：`backend/app/execution/research_agent_coordinator.py:717-749`；当前上限包括 12 steps、至少 4 searches、每任务 sources 限额和 failures 限额，最终仍受 plan/global remaining budget 约束。

### 2.4 如何保证 A/B 公平

不能只保证“调用了同一个 provider 名称”；Research Agent 会自适应地产生不同 query，顺序可变的 mock 队列也会制造偏差。正式 E2 应强制以下不变量：

| 控制面 | 做法 |
|---|---|
| ResearchTask | 不重新运行 Planner；从同一 fixture 克隆完全相同的首轮任务 JSON，保留 ID、query hints、seed URLs、round、framework metadata |
| InformationNeed | 克隆同一 `research_information_needs.json`，启动前比较规范化 JSON SHA-256 |
| Search Provider | 两臂使用同一版本的冻结结果语料/cassette，但各自获得独立无状态实例；cassette miss 必须 fail closed，禁止悄悄落到 live |
| Web 内容 | 使用冻结 `web_pages.json`/content hash；不重新抓取实时页面 |
| 预算 | 使用共同的 eval budget contract；对 search/fetch/read/submit 的允许上限一致，由 wrapper 计数。允许策略选择少用预算，但不能给某一臂更高上限 |
| 检索配置 | 固定 provider、domain filter、top-k、ranker/embedding 版本、chunk 参数、source-RAG 参数、时间边界与 locale，并记录配置 hash |
| 执行环境 | 每臂独立临时 ArtifactStore；固定任务顺序、随机种子、eval clock/ID policy；不共享会被消费的 provider 队列 |
| 评分 | 同一 artifact-only scorer；只从完成后的 Evidence/Source/Coverage/Gap/预算事件计算，不让各 treatment 自报分数 |
| 补采 | E2 主实验两臂均 `supplement_enabled=false`，避免把 E3 效应混入 E2 |

“相同预算”应解释为**相同可用上限和计费规则**，而不是强迫两臂产生相同的调用次数；调用效率本身可以是结果。固定 baseline 的固定 hints 与 agent 的自适应 query 不可能逐 query 配对，因此推荐使用“task-specific 冻结语料 + 确定性排名”，而不是仅按历史 query 字符串做顺序型 stub。

### 2.5 E2 可行性判断

- 旧固定路径存在：**是**。
- 可作为 baseline：**组件级可以；完整旧 loop 不可直接作为干净 baseline**。
- 完全离线运行 fixed baseline：**可以**，前提是为选定任务冻结搜索结果和网页内容。
- 完全离线评价真实 Research Agent 策略：**不可以**；若回放历史 action，只验证 runner/metrics，不再评价 agent 的决策能力。
- 推荐主实验：网络冻结、Research Agent LLM live、baseline 无 LLM、相同初始输入/预算/语料；报告 LLM 模型与采样参数，并做重复运行。

## 3. E3 Bounded Supplement A/B

### 3.1 当前补采逻辑的真实入口

当前并非只有一个补采入口，而是有两个 ResearchTask 生产者，且 coordinator 会在每轮协调它们：

1. **确定性 Coverage/Gap bridge**
   - `ResearchAgentBoundedRefreshService.refresh`：`backend/app/intake/step6e4.py:763-821`
   - 内部 `_refresh_bounded_gap_state` 根据 plan budget、coverage 与 gaps 构造/发布 bounded supplement task：同文件 `:485-624`
   - 兼容旧循环的 `Step6E4RefreshService.refresh`：同文件 `:631-675`
2. **Research Mission Supervisor**
   - coordinator `_supervise_missions`：`backend/app/execution/research_agent_coordinator.py:977-1187`
   - `materialize_research_unit` 创建带下一 `collection_round`、parent、gap 与 `metadata.source=r2_mission_supervisor` 的 ResearchTask：`backend/app/execution/research_mission.py:481-623`，关键 provenance 在 `:609-617`

coordinator 在一轮 worker 完成后调用 bounded refresh（`backend/app/execution/research_agent_coordinator.py:608-617`），随后更新 mission coverage 并进入 supervisor。任务执行前还有 semantic supplement dedup（同文件 `:1436-1474`）。因此 E3 的 off 开关必须覆盖**两个任务生产者及其后续执行**。

### 3.2 最小 eval 配置是否可实现 `supplement_enabled=false / true`

**可以，但当前没有一个可直接复用的生产布尔开关。** 推荐只在 eval-only orchestrator 定义：

```yaml
supplement_enabled: false | true
```

语义必须是：

- 两臂先从同一 fixture 运行完全相同的首轮 Research Agent。
- 首轮结束，先持久化 coverage/gaps，并冻结一个 prefix snapshot。
- `false`：不调用/不消费 deterministic bridge 和 mission supervisor 创建的后续 ResearchTask；保留 gap 作为“需要但被实验关闭”的可观测状态。
- `true`：调用现有 `ResearchAgentBoundedRefreshService.refresh` 和现有 mission supervisor，然后仅按现有 budget/round/stop rules 执行其产生的后续 ResearchTask。

最好先生成一次首轮状态，再复制成两个独立 ArtifactStore 分支。这样无需依赖“两次 live LLM 恰好给出相同首轮输出”，并能用文件 hash 证明两臂共享同一个前缀。

### 3.3 如何保证关闭补采时 Research Agent 完全不变

以下方式会改变 Research Agent，本审计不推荐：把 per-task/global budget 设为零、删除 InformationNeed、改变 prompt/tool registry、把 `max_collection_rounds` 改成 1、或在 agent loop 内新增条件分支。

正确隔离边界是 agent 首轮之外的 orchestrator gate：

```text
相同 frozen prefix
  ├─ A/off：统一评分，停止
  └─ B/on ：现有 gap bridge + mission supervisor -> 后续任务 -> 统一评分
```

需在 manifest 中验证首轮前缀完全一致：首轮 `research_agent_runs`、`research_agent_actions`、`research_agent_observations`、`search_attempts`、`source_documents`、`source_chunks`、`source_evidence` 分别做规范化 hash。A/B 之间唯一允许的 treatment 差异是补采 gate 之后新增的 task/action/source/evidence。

### 3.4 可直接统计的 artifact

| 类别 | Artifact / 字段 | 可做的直接统计 |
|---|---|---|
| Coverage | `evidence_coverage.json`；`EvidenceCoverage.status/source_ids/evidence_ids/limitations`（`backend/app/schemas.py:1469-1477`） | sufficient/partial/insufficient 分布、need 覆盖率、每 need 来源数/证据数 |
| Gap | `research_gaps.json`；`ResearchGap`（`backend/app/schemas.py:1665-1688`） | gap 数、impact 分布、resolved/unresolved、与 need/competitor/dimension 的映射 |
| Supplement | `research_tasks.json` 的 `collection_round`、`parent_research_task_id`、`research_gap_id`、framework metadata（`backend/app/schemas.py:640-685`） | 首轮/补采任务数、轮深、parent/gap provenance、重复任务率 |
| Plan budget | `ResearchPlan.budget`（`backend/app/schemas.py:1144-1157`）与 `ResearchBudget`（`:562-567`） | 计划搜索/来源/轮数上限 |
| Agent budget | `ResearchAgentBudget`（`backend/app/schemas.py:689-693`）和 run 的 budget/counts（`:758-779`） | searches、sources、steps、failures 的用量和越界检查 |
| 执行 | coordinator runs/events、`research_batch_results`、mission states/worker results/decisions | round/batch 数、stop reason、worker 成败、supervisor 决策 |
| 搜索/采集 | search attempts/results、source candidates/selection、collection attempts、web pages/chunks/retrieval | 检索调用、候选到入选转换、失败类型、补采新增唯一来源 |
| 下游 | Evidence、Claim、ReportStatement、citations/traceability | 补采证据增量、最终 claim/report 可追溯性变化 |

正式 E3 指标应由上述 artifacts 计算，例如 coverage 状态变化、关闭的高影响 gap、补采新增的 verified Evidence/unique Source、预算消耗和失败率。它们是建议的指标来源，不是本阶段产生的指标值。现有 `backend/harness/metrics.py`、`backend/run_traceability_eval.py` 和 `backend/harness/step6c_metrics.py` 可复用基础统计，但仍需一个按 round/provenance 配对的 E2/E3 metrics 模块。

## 4. Structured Output Reliability

### 4.1 当前 Analyst staged generation

当前 Research-to-Report 主路径从 `ResearchAnalysisService.run_once` 进入，构造 `LLMProfessionalAnalystAgent(preserve_research_artifacts=True)`，按实际 assessment 分片数检查 LLM 调用 artifact 数量，并确认 upstream Coverage/Gap 未被 Analyst 改写。

在 `backend/app/agents/llm_snapshot.py` 中：

- 路由到 staged path：`:324-395`
- Stage A `brief_profiles`：`:520-540`
- Stage B `framework_assessment`：小 scope 保持单次生成；估算输出单元超过阈值时按 Framework dimension 切成有界子阶段，逐片使用同一个 `AnalystAssessmentStage` Schema 验证，再由 Python 覆盖校验、去重、排序并组装成完整 stage；最终仍只持久化一个可复用的 `analysis_assessments`
- Stage C `claims`：`:634-656`
- Python deterministic assembly：`:658-710`，metadata 标记 `assembly=python_deterministic_v1`、`llm_stage_count=3`
- portfolio/evidence ref validation：`:711-723`
- artifacts 持久化：`:725-767`

因此当前主路径并不是让 LLM 一次性输出整份最终报告；LLM 输出 brief、一个或多个 assessment 子阶段以及 claims，最终 assessment 和 portfolio 都由 Python 确定性拼装。分片不改变既有最终 Schema，也不在 Analyst 层静默修正 competitor scope。

### 4.2 Pydantic validation 与 retry

验证分两层：

1. 每个 stage 由对应 Pydantic model 构造/验证：`AnalystBriefProfilesStage`、`AnalystAssessmentStage`、`AnalystClaimsStage`；最终构造 `CompetitiveAnalysisPortfolioV2` 并做引用一致性验证。
2. 通用 structured client 在 `backend/app/llm/client.py` 执行解析、schema validation、diagnostics 和一次结构化 retry；validation failure 的唯一 retry 会收到截断后的精确错误反馈，但不会放宽原 Schema 或引用约束。

当前 retry 语义：

- JSON parse/schema validation failure：由 `LLMClient.generate_structured` 做一次 structured retry。
- `finish_reason=length`/输出截断：不走内层 schema retry，而由 Analyst stage wrapper 捕获 `LLMOutputTruncatedError`，以更严格输入子集重试一次（`backend/app/agents/llm_snapshot.py:769-817`）。
- 外层仅对发生 `finish_reason=length` 的子阶段重试一次；structured client 自身仍保留既有的一次 JSON/schema 修复重试。服务层不再使用固定 2–6 上界，而按 `2 + assessment_shard_count` 个逻辑调用 artifact 动态核验（复用已有 assessment 时不计 assessment 子阶段）。

### 4.3 是否保留 single-shot/legacy 路径

**仍保留。** 同一 `LLMProfessionalAnalystAgent` 在 `preserve_research_artifacts=False` 时进入旧 single-shot `CompetitiveAnalysisPortfolioV2` 路径（`backend/app/agents/llm_snapshot.py:396-470`），并存在更早的 `LLMAnalystAgent`（同文件 `:251` 起）。它们主要服务 Step6C/snapshot/兼容流程，不是当前 Step6F Research-to-Report 主路径。

`backend/run_structured_output_eval.py` 已明确把 `CompetitiveAnalysisPortfolioV2` 标为旧 one-shot，把三个 stage schema 分组统计（`:13-19,112-117`）。它也明确提醒：没有同任务、同条件的配对运行，只能做历史描述，不能做 staged 优于 one-shot 的因果结论（约 `:310-316,408`）。正式评测可复用其扫描器和分类逻辑，但不能把现有历史成功率直接当 A/B。

### 4.4 可用于 replay 的历史失败 fixture

| Task / fixture | 失败类型 | raw output 可用性 | Replay 价值 |
|---|---|---|---|
| `task_user_fbf259723e8b` | 旧 one-shot Analyst，`finish_reason=length`，约 38,614 chars，以 `{` 开始但没有闭合 | `raw_output` 未保留，仅有空对象和诊断 | 可 replay 失败分类/trace，不可精确重放 parser 对截断文本的行为 |
| `task_user_4143cb765d5e` | staged `AnalystAssessmentStage` 初次与 structured retry 均未满足 completion criteria | raw output 与 retry diagnostics 已保留 | 强 schema/completion replay fixture |
| `task_user_3815fdbfb3f9` | staged brief profiles 返回未知 competitor，触发 schema/semantic validation | raw output 已保留 | 强引用域/competitor validation replay fixture |
| `snapshot_step6b2_deepseek_v4_real_clean/snapshot_online_education_demo` | 较旧 snapshot writer，CompetitiveReport 引用了不存在的 claim id | 输出/诊断存在 | 可做跨引用 validation fixture，但不是当前 Analyst stage |

此外 `backend/check_kp_r2_llm_structured_output_reliability.py` 已有离线 synthetic provider，覆盖 malformed JSON、schema retry、retry exhaustion、HTTP 402 不重试等行为。它适合回归测试，但不是历史真实分布，正式报告中应与历史 replay 分开。

## 5. Failure Isolation

### 5.1 Web Search 主链路与知乎 MCP 的隔离

`ProductionResearchTools` 初始化时始终注册主 Web collector；只有存在 `ZHIHU_API_KEY` 时才注册知乎 client（`backend/app/execution/research_agent.py:531-574`）。SEARCH action 的实际顺序为：

1. 先调用主 Web search（同文件 `:599-609`）。主链路异常会使该 SEARCH action 失败，不会伪装成“知乎成功所以主链路正常”。
2. 再调用知乎补充来源（`:615-653`）。知乎调用被独立 `try/except` 包裹；错误写入 `supplemental_errors`，已取得的 Web 结果仍返回。
3. 最后按 URL 去重合并（`:705-718`）。

provider 边界在 `backend/app/tools/search_provider.py`：`SearchProvider` protocol（`:25-35`）和 Tavily/Zhipu/Bocha 构建（`:239-267`）。该构建器明确拒绝把 `zhihu` 设为 `SEARCH_PROVIDER`，因为知乎不是 Web 主链路。transport 抽象 `SearchToolTransport` 在 `backend/app/tools/search_transport.py:47-62`，有 native/MCP 实现。知乎 client 的 timeout/retry 和解析分别在 `backend/app/tools/zhihu_mcp.py:156-278,385-592`。

现有 `backend/check_multi_source_search.py:134-181` 已用 failing Zhihu method 验证“Web 结果保留、错误进入 supplemental_errors”；`backend/check_search_provider_tavily.py:24-48` 使用 `httpx.MockTransport`；`backend/check_mcp_r1_production_integration.py` 也有 fake provider/transport/MCP server。说明当前依赖边界足以支持离线 fault injection。

### 5.2 无生产配置污染的 fault injection 方案

在 `backend/eval_r1/faults.py` 提供 protocol-compatible fake，不修改环境变量、不访问真实网络、只写独立临时 ArtifactStore：

| 目标 | Connection failure | Timeout | Invalid response |
|---|---|---|---|
| Web Search | fake `SearchProvider`/`SearchToolTransport` 抛 `httpx.ConnectError` | 抛 `httpx.ReadTimeout` 或 `TimeoutError` | 返回错误 shape、缺字段、非列表或 transport `is_error=True` |
| 知乎 MCP | 注入 fake adapter/client 的 invoke/search 抛连接异常 | 抛 timeout，并验证 retry 上限 | 返回 malformed JSON、缺 `structured_content`、MCP `is_error=True` |

每个 case 必须断言 artifact 级结果，而不只断言异常类型：

- 主 Web failure：对应 search/tool observation 标记 failed，记录 attempt/error，不能生成伪造候选或自动切到真实网络。
- 知乎 failure：Web results 数量和内容不变，`supplemental_errors` 有结构化记录，整个 SEARCH action 不因补充源失败而丢失主链路结果。
- timeout：验证 bounded retry 与总调用次数，不做真实 sleep；使用 fake clock/立即抛错。
- invalid response：验证被分类为 provider/protocol/schema failure，而不是空结果成功。

若当前 `ProductionResearchTools` 没有公开的知乎 client 构造参数，eval-only 代码可采用小型 factory/subclass 组合或复用现有 check 的 `__new__` 注入方式；不应为 Phase 0 改生产构造器，更不应临时写入真实 `.env`。

## 6. 历史评测任务候选

### 6.1 推荐的 10 个任务

“Coverage/Gap”和“Claim/Report”列为 `数量/数量`；ResearchTask 列同时给出 `总数（首轮+补采）`。competitor/dimension 取该任务全部 `research_tasks.json` 中的去重并集，因此可能比最初 `AnalysisTask.competitors` 更宽。`适合 E2/E3` 是可行性判断，不代表历史数据已构成因果 A/B。

| task_id | user query / research objective | competitor | dimension | ResearchTask | Evidence | Coverage/Gap | Claim/Report | 适合 E2 | 适合 E3 |
|---|---|---|---|---:|---:|---:|---:|---|---|
| `task_user_644a69bb061f` | 了解 ClassIn 的竞争格局，识别其主要竞品并分析竞争态势 | 腾讯云实时互动 / TRTC 教育方案 / BigBlueButton / ClassIn | 产品定位、产品能力、定价与成本、生态与集成、other | 21（12+9） | 343 | 15/6 | 14/1 | 是：旧固定循环完整样本 | 是：有 9 个 supplement task |
| `task_user_0564c40bec88` | 了解小红书、抖音和快手三个产品的竞争态势，为后续决策提供参考 | 小红书 / 抖音 / 快手 | 产品定位、产品能力、定价与成本、生态与集成、other | 21（12+9） | 267 | 15/5 | 18/1 | 是：旧固定循环、多对象 | 是：搜索与补采较完整 |
| `task_user_232a12475144` | 了解腾讯云实时互动的产品能力、市场定位及竞争格局，为后续决策提供参考 | 腾讯云实时互动 / BigBlueButton / ClassIn | 产品定位、产品能力、定价与成本、生态与集成、other | 15（12+3） | 107 | 15/8 | 17/1 | 是：seed URL/无 search-result 的对照样本 | 是：有 gap 与补采，但需标注 seed-only |
| `task_user_78e88143e47e` | 了解微信和 QQ 两个产品的整体情况，并进行横向比较 | 微信 / QQ | 产品定位、产品能力、定价与成本、生态与集成、other | 14（8+6） | 266 | 10/3 | 17/1 | 是：旧固定循环、双对象；status 有失败记录 | 是：有 6 个补采任务 |
| `task_user_829e249408ef` | 了解 Trae 产品的整体情况，包括其功能、特点、定位等，为后续可能的分析或决策提供基础信息 | Trae | 产品定位、产品能力、定价与成本、生态与集成、other | 7（4+3） | 173 | 5/1 | 18/1 | 是：旧固定循环、单对象；status 有失败记录 | 是：小型补采样本 |
| `task_user_bc7a0f16ccad` | 对比分析 Trae 与 Cursor 两款产品的竞争态势，以辅助理解其相对定位与差异 | Cursor / Trae | 产品定位、产品能力、定价与成本、生态与集成，以及 ecosystem/feature/positioning 混合维度 | 12（8+4） | 19 | 8/3 | 17/1 | 是：新 Research Agent 完整 trace | 是：coordinator/mission/batch artifacts 完整 |
| `task_user_4143cb765d5e` | 比较 Trae 和 Cursor 两款产品的竞争态势，以辅助产品决策 | Cursor / Trae | customer、ecosystem、feature、other、positioning、pricing | 19（12+7） | 20 | 12/25 | 15/1 | 是：新 agent + Analyst failure 压力样本 | 是：gap-heavy；同时适合 structured replay |
| `task_user_6cf70eb3acdd` | 了解豆包和 DeepSeek 的竞争态势，为后续决策提供参考 | 豆包 / 豆包、DeepSeek / DeepSeek | customer、ecosystem、feature、positioning、pricing | 33（15+18） | 17 | 15/27 | 17/1 | 有条件：新 agent 压力样本；competitor 中有复合脏值 | 是：18 个补采任务、gap-heavy |
| `task_user_fe0fa065d8bc` | 对豆包和千问进行调研并开展竞品分析，以了解两者的产品情况与竞争关系 | 豆包 / 千问 | customer、ecosystem、feature、positioning、pricing | 28（10+18） | 15 | 10/13 | 17/1 | 是：新 agent、失败/预算压力样本 | 是：18 个补采任务 |
| `task_user_3109d14df6c9` | 了解 Trae 的核心产品能力 | Trae | 核心产品能力 | 1（1+0） | 29 | 1/0 | 18/1 | 是：最小单任务/无 gap 控制样本 | 仅作 negative control，不适合估计补采收益 |

### 6.2 选样覆盖与注意事项

上述 10 个候选合计含 171 个 ResearchTask、194 个 Source、1,433 个 SearchResult、194 个 WebPage、805 个 SourceChunk、1,256 个 Evidence、106 个 Coverage、91 个 Gap、168 个 Claim 和 10 份 Report。这里只用于说明数据覆盖；不代表质量分或 A/B 结果。

- 前 5 个和最小控制任务主要代表旧固定研究路径；后 4 个主要代表新 Research Agent/coordinator 路径。
- 9/10 有 normalized search results；`task_user_232a12475144` 主要依赖 seed URL，可作为不同采集入口的控制样本。
- 10/10 有 WebPage；只有 6/10 明确保留 SourceChunk，所以“所有任务都可原样 chunk replay”不成立。
- `task_user_78e88143e47e`、`task_user_829e249408ef` 的整体 status 含失败，但研究、coverage、claim/report artifacts 较完整；适合 robustness case，不应混入“全成功主集”而不分层。
- `task_user_6cf70eb3acdd` 的 competitor 存在 `豆包、DeepSeek` 复合脏值；这是有价值的数据质量压力样本，但必须在正式 case manifest 中标记，不能静默规范化。
- `task_user_3109d14df6c9` 没有 gap 和 supplement，适合验证 off/on 在无补采需求时应得到相同结果，而不是用来估计平均补采增益。

正式 EVAL-R1 建议把这 10 个任务分层报告，而不是只算一个总体均值：旧路径 5 个、新 agent 4 个、无 gap 控制 1 个；再单列 dirty/status-failed robustness strata。

## 7. Replay 可行性

### 7.1 历史 SearchResult、SourceDocument、SourceChunk 是否足够

字段层面的结论：

- `web_search_results.json` 是 normalized SearchResult（schema 见 `backend/app/schemas.py:1047-1062`），足以回放**历史已经发出过的 query 的结果列表**，但通常不含 provider 原始 HTTP payload/headers，也不能回答新 agent 临时生成、历史从未出现过的 query。
- `source_documents.json` 的 `SourceDocument` 主要保存 URL/title/excerpt 等元数据（`backend/app/schemas.py:1348-1357`），**单独不足以重放全文读取**。
- `web_pages.json` 的 `WebPageContent` 保存抓取正文与 hash（`backend/app/schemas.py:936-948`），与 SourceDocument 组合后可冻结 fetch/read。
- `source_chunks.json` 的 `SourceChunk` 保存 chunk text、offset/provenance（`backend/app/schemas.py:951-964`），存在时可直接冻结 retrieval 输入；缺失时可从 frozen WebPage 重新 chunk，但必须冻结 chunker 版本、参数和 ID/hash 规则。

因此，选定任务的历史数据足以建立一个实用 replay corpus，但不能简单声称三个文件在所有任务上都齐全。正式 fixture builder 应逐任务校验：URL/content hash、document-to-page 链接、chunk offsets、evidence-to-chunk/source 引用，并在 manifest 中列出缺失项。

### 7.2 推荐的 replay 层级

1. **Exact query cassette**：按规范化 `(provider, task_id, query, domain_filter, limit, config_hash)` 返回历史 `web_search_results`。适合 fixed baseline 与历史 action replay。
2. **Frozen task corpus search**：将某任务的全部历史结果和页面作为封闭 corpus，对任意 agent query 用冻结 ranker 确定性排序。适合 E2 的 adaptive query，避免 cassette miss 不公平地只惩罚 agent。
3. **Frozen page reader**：仅按 URL/content hash 返回历史 WebPage/SourceDocument；未命中立即失败，不访问 live。
4. **Frozen chunk/retrieval**：优先返回历史 chunks；缺失时用锁定版本的 chunker 离线重建，并在结果中标记 `reconstructed=true`，不要把重建 chunk 与原始 chunk 混为同一层证据。

所有 replay adapter 必须 fail closed。若需要 fallback live，必须作为单独实验模式与结果分组，不能在主 A/B 中自动发生。

### 7.3 哪些可完全 replay

- Coverage/Gap 的重新计算和按 initial/supplement provenance 的 E3 retrospective ablation。
- ResearchTask、Evidence、Source、预算、失败、traceability、claim/report 引用等 artifact-only 指标。
- 旧固定 collector/extractor 路径，只要该任务所需 query/page 已冻结；确定性 extractor 不需要 LLM。
- 既有 Research Agent action trace 的 runner/工具协议 replay；但只验证执行器和指标，不评价 agent 是否会做出这些 action。
- 有 raw output 的结构化 parser/schema/completion replay，例如 `task_user_4143cb765d5e` 与 `task_user_3815fdbfb3f9`。
- connection failure/timeout/invalid response 的 Web/知乎隔离测试。

### 7.4 哪些必须 live

- **E2 Research Agent 策略能力**：action selection 的 Research Agent LLM 必须 live。Search 和 page fetch 可以、也推荐保持 replay。
- **E3 mission supervisor 的语义补采决策**：若正式实验包含当前 supervisor，而不是只回放已有后续任务，则 supervisor LLM 必须 live。
- **Structured Output 生成可靠性**：如果要比较真实生成成功率、重试率和 token/latency，Analyst LLM 必须 live；只 replay raw output 只能验证 parser/validator。
- **实时检索召回/时效性实验**：Search Provider 与网页抓取必须 live，但它应从主 E2/E3 拆出，避免网络变化成为 treatment confound。

### 7.5 最大程度避免实时网络污染

- 生成 immutable fixture manifest，记录每个 JSON/page/chunk 的 SHA-256、schema/version、来源 task、抓取时间和配置 hash。
- 两臂从同一只读 snapshot 克隆到独立 store；禁止在原历史 task 目录中写运行产物。
- 默认关闭网络，并让 cassette/corpus miss 直接失败；运行记录必须包含 `network_mode=replay|live`。
- 固定结果排序、domain filter、top-k、ranker/embedding、chunker、source-RAG、locale、时间边界、random seed 和 eval clock。
- live LLM 使用相同模型版本、prompt hash、temperature/top-p 和 token limits；随机化/交错执行 A/B，并做多次重复，避免时间顺序偏差。
- 记录每次 provider request id、usage、finish reason、retry diagnostics；不要只保留最终 JSON。
- 任何 live Search 实验单独成组，并同时保存原始 provider response 和抓取页面，以便后续 replay。

## 8. 正式 EVAL-R1 的最小新增文件

建议新增以下 eval-only 文件；名称可调整，但职责不应合并进生产模块：

```text
backend/eval_r1/
  cases.yaml                 # 10 个 case、分层标签、允许的 artifacts、异常/脏数据声明
  replay.py                  # ReplaySearchProvider/Transport、FrozenPageReader、fail-closed 校验
  research_ab.py             # E2 fixed-single-round vs agent；E3 prefix fork + supplement gate
  metrics.py                 # artifact-only、按 task 配对、按 round/provenance 统计
  faults.py                  # Web/知乎 connection/timeout/invalid-response fakes
  run.py                     # 只面向独立 evaluation store 的 CLI；禁止指向生产 runs 原目录

backend/app/data/evaluations/eval_r1/
  manifest.json              # fixture/config/schema/hash 清单
  tasks/<task_id>/...        # 从历史 runs 复制出的只读最小输入与 replay cassette/corpus
```

可以复用而不新增：`backend/build_eval_task_inventory.py`、`backend/run_structured_output_eval.py`、`backend/run_traceability_eval.py`、`backend/harness/metrics.py` 及已有 check 中的 fake provider 模式。正式实现时至少还要增加 eval-only 单元/集成测试；为了保持“最小文件”，可先放入 `backend/tests/eval_r1/test_replay_and_switches.py`，覆盖：fixture hash、cassette miss fail-closed、E3 共享前缀、off 不产出补采任务、知乎失败不污染 Web、主 Web 失败不伪装成功。

Phase 0 不建议新增或修改：生产 API 参数、生产 `ResearchPlan` schema、生产环境变量、`ResearchAgent` prompt、`ResearchAgentCoordinator` stop rules、Collector/Extractor 业务逻辑。

## 9. 最终建议

### E2 推荐 baseline

采用 eval-only 的**固定单轮 Collector + deterministic Extractor**：复用 `CollectorQueueService.run_once` 与 `ExtractorQueueService.run_once`，只消费冻结的首轮 ResearchTask，跳过 Coverage queue 和全部 supplement 调度；结束后由共同 scorer 计算 Coverage/Gap。不要直接用完整 `ResearchLoopRunner` 作为 E2 baseline。

### E3 推荐开关方式

在首轮 Research Agent 完成后的 eval orchestrator 边界设置 `supplement_enabled`。先生成并冻结共同首轮 prefix；off 分支停止，on 分支才调用现有 bounded refresh 和 mission supervisor。开关必须同时覆盖两个补采任务生产者，且不能改变 Research Agent 本身的输入、工具、prompt、预算或首轮执行。

### 推荐任务

正式主候选为：

1. `task_user_644a69bb061f`
2. `task_user_0564c40bec88`
3. `task_user_232a12475144`
4. `task_user_78e88143e47e`
5. `task_user_829e249408ef`
6. `task_user_bc7a0f16ccad`
7. `task_user_4143cb765d5e`
8. `task_user_6cf70eb3acdd`
9. `task_user_fe0fa065d8bc`
10. `task_user_3109d14df6c9`

其中第 10 个是无 gap negative control；第 8 个是 competitor 脏值 robustness case；第 4、5 个应按 status-failed robustness case 分层，不与 clean cases 静默汇总。

### Replay / Live 边界

- **完全 replay**：固定 baseline、E3 retrospective ablation、Coverage/Gap/Budget/Traceability 指标、保留 raw output 的 parser/schema replay、Web/知乎 fault isolation。
- **LLM live、网络 replay**：推荐的正式 E2 agent arm；包含 mission supervisor 的正式 E3；Analyst 真实结构化生成可靠性。
- **必须 Live Search**：只有专门测试当前网页召回、provider 可用性或内容时效性时；它不应成为主 E2/E3 的默认条件。

本审计确认 EVAL-R1 的工程基础已存在。缺口主要不是生产能力，而是 eval-only 的严格 treatment 边界、冻结语料、配对 runner、provenance-aware metrics 和 fixture 完整性校验。
