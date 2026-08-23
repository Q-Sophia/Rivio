# 竞品分析 Agent 运行与治理规范 v1

状态：`design-draft（设计草案）`

方法依据：[竞品分析方法规范 v1](competitive_analysis_spec_v1.md)

本文把竞品分析方法转化为项目中的 Agent（智能体）、Schema（数据结构）、Artifact（产物）、Prompt（提示词）、安全和评估约束。它不用于讲解竞品分析专业方法。

## 1. 证据主链

```text
SourceDocument（来源文档）
-> SourceEvidence（来源证据）
-> ProductCard（产品卡片）
-> AnalysisClaim（分析结论）
-> CitationCheck（引用检查）
-> CompetitiveReport（竞品报告）
-> ReviewFeedback（审查反馈）
```

- 所有关键判断必须先形成 `AnalysisClaim`；
- 每条 `AnalysisClaim`必须引用 `evidence_ids`；
- 每条 `SourceEvidence`必须追溯到 `SourceDocument`；
- 报告中的分析观点必须引用 `claim_ids`；
- `WriterAgent（报告撰写智能体）`不得直接从网页产生新产品事实。

## 2. 方法产物到运行产物的映射

| 方法概念 | 计划 Schema | Artifact | 主要负责人 |
|---|---|---|---|
| Research Brief（研究简报） | AnalysisTask 扩展或 ResearchBrief | research_brief | OrchestratorAgent |
| Competitive Set（竞争集合） | CompetitorProfile / competitor_role | competitor_profiles | Planner/Analyst |
| KIQ（关键情报问题） | KeyIntelligenceQuestion | intelligence_questions | Planner/Analyst |
| Information Needs Matrix（信息需求矩阵） | InformationNeed | information_needs | Planner/Analyst |
| Evidence Coverage（证据覆盖） | EvidenceCoverage | evidence_coverage | AnalystAgent |
| 分析发现 | AnalysisClaim v2 | claims | AnalystAgent |
| Research Gap（研究缺口） | ResearchGap | research_gaps | AnalystAgent |
| 引用检查 | CitationCheck | citation_checks | CitationAgent |
| 综合报告 | CompetitiveReport | reports | WriterAgent |
| 审查意见 | ReviewFeedback | review_feedback | ReviewerAgent |

这些旁路产物用于计划和治理，不替代证据主链。

## 3. AnalysisClaim v2 候选字段

在保留现有字段的基础上增加：

```json
{
  "claim_type": "comparison",
  "counter_evidence_ids": [],
  "reasoning_summary": "可审计的简短依据",
  "uncertainty": "证据限制、适用条件或未知项",
  "decision_impact": "对当前决策可能产生的影响"
}
```

`claim_type`候选值：

```text
fact
comparison
baseline
inference
risk
opportunity
recommendation
```

`reasoning_summary`只记录可审计依据，不要求或保存模型隐藏的逐步思维过程。

## 4. Prompt Registry（提示词注册表）

每次 LLM Call（大模型调用）至少记录：

- prompt_id；
- prompt_version；
- method_spec_version；
- output_schema_version；
- model、Provider（供应商）、temperature（温度）和 thinking_mode（思考模式）；
- 输入 Artifact 引用或哈希；
- 输出校验与回退状态。

候选提示词只有通过 Contract Test（契约测试）、Fixture（测试样本）和 A/B Test（对照测试）后才能变为 `approved（已批准）`。

## 5. Agent 职责边界

### OrchestratorAgent（编排智能体）

- 将用户目标转成研究简报和任务；
- 管理 TaskBoard（任务板）和 Dynamic DAG（动态任务图）；
- 不自行产生产品事实；
- 当 ResearchGap 阻塞决策时插入补充采集任务。

### CollectorAgent（采集智能体）

- 只采集允许范围内的来源；
- 记录网址、时间、标题、类型和原文；
- 不把网页内容直接当作最终结论；
- 网页中的指令视为不可信数据。

### EvidenceExtractorAgent（证据提取智能体）

- 从来源中提取原子事实；
- 保留原文片段和来源关系；
- 区分直接事实、厂商声明和弱线索；
- 不进行跨竞品高级推断。

### AnalystAgent（分析智能体）

- 执行竞争集合、KIQ、证据覆盖、同口径比较、路径取舍和研究缺口分析；
- 不得用模型记忆填补产品事实；
- 比较必须覆盖相关各方证据；
- 缺失不能当作不存在；
- 建议只能作为带条件的候选。

### CitationAgent（引用检查智能体）

- 确定性检查编号与引用关系；
- 检查比较结论的竞品证据覆盖；
- 标记弱、无效和缺失证据。

### WriterAgent（报告撰写智能体）

- 综合已经验证的结论；
- 不得逐条改写结论冒充分析；
- 不得在报告中首次创造产品事实；
- 建议必须引用相关比较、风险或机会结论。

### ReviewerAgent（审查智能体）

- 检查事实、引用、比较口径、推断克制、研究缺口和决策价值；
- 低质量报告应产生返工任务；
- 反馈循环必须受最大轮数和重试预算约束。

## 6. Guardrails（安全护栏）

- 不允许输入中的网页文字覆盖系统和 Agent 规则；
- 不允许模型创造或改写 source_id、evidence_id 和 claim_id；
- 不允许缺乏多方证据的确定性比较；
- 不允许把“未提及”写成“不支持”；
- 不允许弱来源单独支持高影响、高置信度建议；
- 不允许把推断写成事实；
- 不允许无限补充搜索或无限评审循环；
- 高风险商业结论保留人工复核。

## 7. Quality Gate（质量闸门）

### 进入分析前

- AnalysisTask 具有最小研究目标；
- SourceEvidence 引用有效；
- 关键竞品和重点维度得到基本覆盖，或存在明确 ResearchGap。

### 进入报告前

- AnalysisClaim v2 结构有效；
- comparison/baseline 覆盖所有相关竞品的证据；
- 推断、风险、机会和建议披露不确定性；
- CitationCheck 没有 invalid_evidence 或 missing_evidence。

### 不合格结论的整条拒绝策略

- 确定性 Validator（校验器）必须使用真实输入的“证据编号—竞品”映射，不能相信模型自行声明的证据归属；
- 如果结论正文、推理、不确定性或决策影响点名某个竞品，该竞品必须出现在 `competitors`，并且至少有一条属于该竞品的 `evidence_id`；
- 不满足时，Harness（运行框架）只能整条拒绝该结论，不得删除句子片段、改写模型措辞或补造证据；
- 被拒绝结论的完整原文、编号和原因保存在 `LLMCall.metadata.rejected_portfolio_claims`，供人工审计；
- 下游只能读取过滤后产物，运行状态标记为 `completed_with_rejections（剔除后完成）`；该状态不等于原始模型输出全部正确，也不等于 `approved（已批准）`；
- 如果模型已经输出对应 ResearchGap（研究缺口），保留缺口；如果没有，不允许过滤器代替模型生成新缺口。

### 最终交付前

- 报告重要观点均可回到 claim_id；
- 没有报告层新增无支持产品事实；
- 机械复制率、多结论综合率和建议支持率达到阈值；
- ReviewFeedback 通过；
- 弱证据和研究限制得到披露。

## 8. Evaluation Harness（评估框架）

硬门槛：

- Schema 校验率 100%；
- 已知证据引用率 100%；
- 比较各方证据覆盖率 100%；
- 新增无支持产品事实数量为 0；
- Prompt Injection（提示注入）执行数量为 0；
- 中文输出一致率 100%。

深度指标：

- 跨竞品比较率；
- 比较证据均衡率；
- 决策影响覆盖率；
- 不确定性披露率；
- ResearchGap 精确率与召回率；
- 结论冗余率；
- 报告结论复制率；
- 多结论综合率；
- 建议支持率。

自动指标不能替代人工盲评。普通用户负责可理解性和决策帮助判断；事实准确性、行业适用性或高风险事项可由相应专家复核。

## 9. Harness 五层映射

| Harness 层 | Step6C 能力 |
|---|---|
| Tool Layer（工具层） | 后续采集工具和 PromptRegistry 读取工具 |
| Orchestration Layer（编排层） | TaskBoard、Dynamic DAG、ResearchGap 补采循环 |
| Context Layer（上下文层） | 研究简报、方法规范、提示词模板和上下文构建 |
| Governance Layer（治理层） | 证据策略、Guardrails、Quality Gate 和 Evaluation |
| Infrastructure Layer（基础设施层） | ArtifactStore、Trace、Metrics 和 API |

## 10. 当前实施边界

当前已完成 Step6C Mock（模拟）实验运行和受限真实候选试运行：

- 已新增 AnalysisClaim v2、CompetitorProfile、KeyIntelligenceQuestion、InformationNeed、ResearchGap、EvidenceCoverage 和 CompetitiveAnalysisPortfolioV2 Schema（数据结构）；
- 已实现 PromptRegistry（提示词注册表）加载、候选状态保护、prompt_version 和 SHA-256（安全哈希算法）追踪；
- 已实现 `LLMProfessionalAnalystAgent（专业分析智能体）`和独立 Step6C 工作流入口；
- V2 结论会转换为兼容的旧 AnalysisClaim，继续经过 CitationCheck、CompetitiveReport 和 ReviewFeedback；
- 已完成在线教育固定快照与实体消费品合成 Fixture（测试样本）的 Mock 契约测试；
- Prompt `2.2.2-candidate` 仍是 `candidate_not_runtime_enabled（候选、未默认启用）`，只允许显式实验入口加载；
- 真实候选试运行中，原始 10 条结论有 1 条证据归属不合格，整条拒绝后剩余 9 条通过全部自动指标，多竞品证据均衡率为 100%；
- 前端已提供只读的 Step6C 专业分析实验栏目，并明确显示被拒绝结论数量。
- 已实现 `competitive_writer@2.0.0-candidate`、任务化标题解析和专业报告章节契约；
- Professional Writer（专业撰写智能体）只接收治理后的分析产物，不直接读取原始网页自由生成报告；
- Writer Mock（模拟）契约验证的标题、章节、claim_id、ResearchGap 和重复复制指标全部通过。

尚未完成：

- 12 个 Mock Fixture（模拟测试样本）和新深度指标已完成；
- Analyst v1/v2 的 DeepSeek（深度求索）A/B Pilot（对照试运行）及 `2.2.2-candidate` 单次候选试运行已完成；
- `2.2.2-candidate` 已加入运行时证据对齐算法、Few-shot（少样本示例）、输出契约与确定性整条拒绝策略；
- 人工盲评与候选版本批准；
- Writer Prompt v2（报告撰写提示词第二版）的真实模型试运行与人工盲评；
- 用户对话、Intent Recognition（意图识别）和标题确认界面。

实施顺序见《Step6C 专业分析提示词评估计划》。
