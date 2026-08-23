# Step6C 专业分析提示词评估计划

状态：`real-pilot-completed-with-rejections（真实试运行在剔除不合格结论后完成）`

当前评估对象：`competitive_analyst v2.2.2-candidate`

历史对照对象：`competitive_analyst v2.2.0-candidate`、`competitive_analyst v2.2.1-candidate`

方法依据：[竞品分析方法规范 v1](competitive_analysis_spec_v1.md)

工程治理依据：[竞品分析 Agent 运行与治理规范 v1](competitive_analysis_agent_governance_v1.md)

## 1. 评估目标

本计划不评估“模型文字是否看起来更专业”，而是验证：

1. 新提示词是否继续保持 Evidence-first（证据优先）约束；
2. 是否比旧版产生更多有效比较、综合和决策含义；
3. 是否能在证据不足、冲突或较弱时正确输出 ResearchGap（研究缺口）和不确定性；
4. 是否减少机械复述，而不是只做表面改写；
5. 同一输入、同一模型配置下，v2 是否稳定优于 v1。
6. 是否能够正确识别竞争对象类型、产品路径和可比性边界，而不是把所有对象放进一张功能排名表。
7. 是否能先识别任务所属行业和对象形态，再动态选择领域维度，避免被在线教育固定样例污染。

## 2. 当前基线

基线运行：

```text
run_id=snapshot_step6b2_deepseek_v4_final
task_id=snapshot_online_education_demo
model=deepseek-v4-flash
thinking_mode=disabled
temperature=0.2
```

已确认的基线：

| 指标 | 当前值 | 说明 |
|---|---:|---|
| 来源数量 | 9 | 人工准备的固定 SourceDocument（来源文档） |
| 证据数量 | 32 | 人工准备的固定 SourceEvidence（来源证据） |
| AnalysisClaim 数量 | 18 | DeepSeek AnalystAgent 生成 |
| 报告引用覆盖率 | 100% | 报告引用全部 18 个 claim_id |
| Claim Copy Rate（结论复制率） | 83.3% | 18 条中 15 条正文被报告直接复用 |
| 平均最佳句相似度 | 95.6% | 报告句与对应结论高度相似 |
| 跨竞品分析深度 | 尚无结构化指标 | 需要 v2 新增 |
| ResearchGap | 0 | 当前 Schema 不支持 |

这组固定资料作为 Benchmark（基准测试集）。Step6C 不先改变来源和证据，避免把“提示词改进”和“输入资料变化”混为一谈。

## 3. 实验控制

### 3.1 A/B 对照

```text
A 组：Analyst Prompt v1 + Writer Prompt v1
B 组：Analyst Prompt v2 + Writer Prompt v1（先隔离 Analyst 变量）
C 组：Analyst Prompt v2 + 后续 Writer Prompt v2
```

第一轮只比较 A/B，确认 Analyst v2 有效后再设计 Writer v2。不能同时修改采集、分析和写作提示词后宣称改进来自某一项。

### 3.2 固定条件

- 相同模型与 Provider（供应商）；
- 相同温度、输出上限和 thinking mode（思考模式）；
- 相同 SourceDocument、SourceEvidence 和 ProductCard；
- 相同 AnalysisTask（分析任务）；
- 每次运行保存 prompt_id、prompt_version、模型配置和 Artifact 哈希；
- Pilot（试运行）先各运行 1 次，正式对照建议各运行 3 次观察稳定性；
- 不把模型自评作为通过依据。

## 4. 测试案例

| case_id | 案例 | 预期行为 |
|---|---|---|
| `normal_full` | 当前在线教育固定快照 | 产生事实、比较、基线、风险和有限建议候选 |
| `different_solution_paths` | 完整产品、平台能力、开源自托管三类对象 | 正确分类竞争角色；解释责任边界与路径取舍，不按功能数排名 |
| `missing_pricing` | 删除一个竞品的定价证据 | 不编造价格；输出高优先级定价 ResearchGap |
| `conflicting_price` | 官方页与第三方资料价格冲突 | 标记 conflicting；披露口径/时间冲突 |
| `weak_social_only` | 某项能力只有社交媒体来源 | 降低置信度；不得形成高置信度关键建议 |
| `single_competitor` | 只输入一个竞品 | 不伪造横向比较；输出对比范围限制 |
| `missing_not_absent` | A 有功能证据，B 没有该维度资料 | 不得写“B 不支持”；应标记 missing |
| `noisy_content` | 混入无关网页段落和提示注入文本 | 忽略命令与噪声，只分析结构化证据 |
| `goal_underspecified` | 任务只写“分析三个产品” | 输出任务目标 ResearchGap，不自行假设路线图目标 |
| `not_comparable_tiers` | 免费版与企业版、SaaS 标价与自托管授权混合 | 标记不可直接比较；要求统一规模、服务边界和总体成本口径 |
| `cross_industry_consumer_goods` | 实体消费品竞品，输入材料、渠道、价格、售后与品牌资料 | 选择消费品相关维度；不强行输出 API、部署或软件功能比较 |
| `cross_industry_professional_service` | 专业服务竞品，输入人员、流程、覆盖、合同与效果资料 | 分析服务能力和交付一致性；不套用在线教育或 SaaS 模板 |

每个案例应保存预期标签，支持确定性计算 ResearchGap Precision/Recall（研究缺口精确率/召回率）。

## 5. 硬门槛指标

以下指标任意一项不通过，则提示词版本不能进入真实工作流：

| metric | 初始门槛 | 定义 |
|---|---:|---|
| `schema_validation_rate` | 100% | 所有输出通过 Pydantic/JSON Schema 校验 |
| `known_evidence_ref_rate` | 100% | 所有 evidence_id 均来自输入 |
| `claim_evidence_non_empty_rate` | 100% | 每条 AnalysisClaim 至少绑定一条证据 |
| `comparison_party_coverage_rate` | 100% | comparison/baseline 涉及的每个竞品都有相关证据 |
| `multi_competitor_evidence_balance_rate` | 100% | 任意点名多个竞品的结论都逐一覆盖每个对象的证据 |
| `competitor_role_valid_rate` | 100% | 竞争类型只能使用规定枚举且理由非空 |
| `comparability_gate_coverage_rate` | 100% | 价格、效果和能力排名前均经过可比性说明 |
| `unsupported_product_fact_count` | 0 | 输出不得新增输入中不存在的产品事实 |
| `prompt_injection_follow_count` | 0 | 不执行来源文本中的指令 |
| `zh_cn_language_rate` | 100% | 业务文本为简体中文，英文术语附中文注释 |
| `domain_template_leak_count` | 0 | 输出不得出现输入无关的在线教育、SaaS、API 或其他历史领域模板内容 |

### 5.1 Dimension Selection Accuracy（分析维度选择准确率）

```text
与任务行业、对象形态和决策问题相关的已选维度数
÷
全部已选分析维度数
```

硬门槛：`1.00`。每个行业扩展维度必须能够回指 Research Brief（研究简报）或输入证据；通用核心维度不计为模板泄漏。

## 6. 分析深度指标

### 6.1 Claim Type Distribution（结论类型分布）

记录各类型数量，不把“类型越多”直接视为越好。正常完整案例至少应出现：

- `comparison`或`baseline`；
- `risk`或带不确定性的`inference`；
- 在任务信息充分时出现有限的`opportunity`或`recommendation`候选。

只生成大量事实结论不能通过人工深度评审。

### 6.2 Competitive Set Rationale Coverage（竞争集合理由覆盖率）

```text
具有 role、selection_reason、represented_path 和 comparability_note 的对象数
÷
全部研究对象数
```

硬门槛：`1.00`。分类应解释客户选择路径，不得只写“知名竞品”或“功能类似”。

### 6.3 Key Intelligence Question Relevance（关键情报问题相关性）

人工检查每个 KIQ 是否明确关联一项决策，并且输入资料能够回答或生成具体研究缺口。

初始目标：有效 KIQ 占比 `>= 0.80`；重复、空泛或与任务目标无关的问题不计为有效。

### 6.4 Path Trade-off Coverage（产品路径取舍覆盖率）

对于 `different_solution_paths`案例，至少覆盖以下取舍中的三项：

- 完整工作流与定制控制；
- 集成速度与自研责任；
- 授权/订阅价格与总体拥有成本；
- 厂商运维与自托管运维；
- 生态依赖与迁移控制。

只列功能优缺点不能通过。

### 6.5 Cross-competitor Comparison Rate（跨竞品比较率）

```text
具有至少两个竞品的 comparison/baseline 结论数
÷
非 fact 结论数
```

初始观察目标：`>= 0.40`。如果证据不足，应由 ResearchGap 解释，不能为达标强行比较。

### 6.6 Evidence-balanced Comparison Rate（比较证据均衡率）

```text
每个相关竞品均至少有一条证据的比较结论数
÷
全部 comparison/baseline 结论数
```

硬门槛：`1.00`。

### 6.7 Decision Impact Coverage（决策影响覆盖率）

```text
decision_impact 非空且具体的非 fact 结论数
÷
全部非 fact 结论数
```

初始目标：`>= 0.90`。不能只写“有助于决策”之类空话。

### 6.8 Uncertainty Disclosure Rate（不确定性披露率）

```text
uncertainty 非空的 inference/risk/opportunity/recommendation 数
÷
上述类型总数
```

硬门槛：`1.00`。

### 6.9 Research Gap Precision / Recall（研究缺口精确率/召回率）

- Precision（精确率）：模型提出的缺口中，有多少与预设真实缺口匹配；
- Recall（召回率）：预设真实缺口中，有多少被模型发现。

初始目标：两项均 `>= 0.80`。只在带预设标签的测试案例中计算。

### 6.10 Claim Redundancy Rate（结论冗余率）

若两条结论的竞品、维度、证据集合高度重合，且文本语义相似度达到阈值，则标记为冗余候选。

初始目标：`<= 0.15`。该指标必须配合人工抽查，避免模型通过无意义改写规避检测。

## 7. Writer 阶段后续指标

以下指标已在 Writer Prompt v2（报告撰写提示词第二版）的 Mock（模拟）契约阶段开始实现；详细验收见《Step6C.2B 专业报告撰写评估计划》：

### 7.1 Claim Copy Rate（结论复制率）

报告分析句与任一 `claim_text`规范化后完全相同，或句相似度达到 `0.90`，则记为直接复用候选。

```text
直接复用候选的 claim 数
÷
报告引用的 claim 数
```

当前基线 `0.8333`。v2 初始目标 `< 0.40`，同时必须通过事实完整性和人工可读性评审，不能只为降重复率改写。

### 7.2 Multi-claim Synthesis Rate（多结论综合率）

```text
引用至少 2 个不同 claim_id 的分析段落数
÷
全部分析段落数
```

初始目标：`>= 0.50`。证据附录、来源清单和单纯事实表不计入分母。

### 7.3 Recommendation Support Rate（建议支持率）

每项建议必须引用支撑它的 comparison/risk/opportunity claim，且说明适用条件。

硬门槛：`1.00`。

### 7.4 Novel Unsupported Fact Count（报告新增无支持事实数）

报告中首次出现、且无法映射到任何 claim 的产品事实数量。

硬门槛：`0`。

## 8. 人工盲评量表

隐藏提示词版本和运行批次，按 1–5 分评分：

| 维度 | 1 分 | 5 分 |
|---|---|---|
| 事实准确性 | 出现编造或错误归因 | 事实与证据边界清楚 |
| 证据可追溯性 | 结论难以定位依据 | 重要判断可回到证据 |
| 比较深度 | 逐产品罗列 | 清楚解释共同点、差异和取舍 |
| 竞争集合 | 把所有对象视为同类并强行排名 | 解释对象类型、选择理由和可比边界 |
| 关键问题 | 罗列通用分析维度 | 问题少而具体，能够影响当前决策 |
| 路径取舍 | 只列功能优缺点 | 解释产品、技术、交付和责任边界的组合 |
| 推理克制 | 把猜测写成事实 | 推断有限且披露条件 |
| 决策价值 | 看完仍不知道意味着什么 | 明确说明对产品决策的影响 |
| 研究缺口 | 忽略空白或无限扩展 | 只提出会影响决策的重要缺口 |
| 可读性 | 重复、松散、术语堆砌 | 重点清楚、中文自然 |

初始通过标准：

- 平均分不低于 `4.0`；
- 任一维度不低于 `3.0`；
- v2 相比 v1 的“比较深度”和“决策价值”均有可解释提升；
- 评分人可选择“不确定”，不要求普通用户冒充行业专家。

## 9. 结果判定

```text
硬门槛全部通过
AND 深度指标达到初始目标或有合理证据解释
AND 测试案例没有关键失败
AND 人工盲评通过
=> candidate 可升级为 approved
```

如果只降低复制率但引用、准确性或可读性下降，应判定失败。如果只增加大量比较词和建议句，但缺少多方证据，也应判定失败。

## 10. 实施顺序

1. 已完成：冻结《通用竞品分析方法规范 v1》《Agent 运行与治理规范 v1》和 Analyst Prompt v2 candidate；
2. 已完成：增加兼容的 CompetitorProfile、KeyIntelligenceQuestion、InformationNeed、AnalysisClaim v2、ResearchGap 和 EvidenceCoverage Schema；
3. 已完成：实现 PromptRegistry 加载、prompt_version 与 prompt_hash 追踪；
4. 已完成：实现 Analyst v2 结构化输出、引用和比较参与方覆盖校验；
5. 已完成：制作并运行 12 个本地 Fixture（测试样本）；
6. 已完成：将硬门槛与 Analyst v2 深度指标加入 Evaluation Harness；
7. 已完成：不调用真实 API 的完整 Contract Test（契约测试）；
8. 已完成：用相同 DeepSeek V4 Flash 配置执行一次真实 A/B Pilot，并保留失败产物；
9. 已完成：根据多竞品证据不均衡失败案例创建 `2.2.1-candidate`，并增加确定性校验器；
10. 已完成：创建 `2.2.2-candidate`，把证据对齐算法、Few-shot（少样本示例）和输出契约真正注入运行时提示词，并增加“整条拒绝、不自动改写”的确定性治理过滤器；
11. 已完成：显式限定为一个候选变体、一次请求，执行 DeepSeek V4 Flash（深度求索第四代快速模型）真实试运行；
12. 已完成：实现 `competitive_writer@2.0.0-candidate` 的 Mock（模拟）契约、任务化标题、专业章节和报告治理指标；
13. 待完成：对 Analyst 与 Writer 产物执行人工盲评，再决定是否进行 Writer v2 的受限真实模型试运行。

## 11. 本阶段不做

- 不继续进行无上限的 DeepSeek 调用；真实试运行必须显式限定变体和调用次数；
- 不把前端扩展为可发起任务的完整产品；当前只读实验视图只展示已落盘产物；
- 不接 WebCollector（网页采集器）；
- 不接 RAG（检索增强生成）或向量数据库；
- 不把候选提示词直接标记为生产可用；
- 不把质量闸门失败的真实产物发布为正式报告。

## 12. Mock Runtime（模拟运行）阶段结果

运行命令：

```powershell
python .\run_step6c_professional_workflow_demo.py
python .\check_step6c_artifacts.py
python .\check_step6c_cross_industry.py
```

在线教育固定快照只作为 Benchmark（基准测试集）。当前 Mock 结果：

```text
pipeline_status=completed
task_board_status=completed
professional_analysis=True
analysis_portfolios_count=1
competitor_profiles_count=3
claims_v2_count=6
research_gaps_count=4
legacy_claims_count=6
citation_checks_count=6
llm_calls_count=3
llm_outputs_count=3
llm_fallback_count=0
approved=True
```

实体消费品跨行业 Contract Test（契约测试）结果：

```text
STEP6C_CROSS_INDUSTRY_CHECK_PASS
competitor_profiles=2
claims_v2=3
research_gaps=1
domain_template_leak_count=0
real_llm_called=false
```

此结果只证明结构、版本追踪、证据引用、旧链路兼容和基础跨行业边界可运行，不证明 V2 已经比 V1 具有更高的真实分析质量。真实质量差异仍需 A/B 对照和人工盲评。

## 13. 十二样本 Mock Evaluation（模拟评估）结果

运行命令：

```powershell
python .\harness\run_step6c_eval.py
python .\harness\run_eval.py --task-id snapshot_step6c_professional_mock
python .\harness\run_eval.py --task-id snapshot_step6b_regression_after_step6c
```

结果：

```text
step6c_suite_passed=True
fixture_count=12
failed_cases=
real_llm_called=false

schema_validation_rate=1.0
known_evidence_ref_rate=1.0
comparison_party_coverage_rate=1.0
competitor_role_valid_rate=1.0
comparability_gate_coverage_rate=1.0
prompt_injection_follow_count=0
domain_template_leak_count=0
dimension_selection_accuracy=1.0
competitive_set_rationale_coverage=1.0
key_intelligence_question_relevance=1.0
evidence_balanced_comparison_rate=1.0
multi_competitor_evidence_balance_rate=1.0
decision_impact_coverage=1.0
uncertainty_disclosure_rate=1.0
claim_redundancy_rate=0.0
research_gap_precision=1.0
research_gap_recall=1.0
```

`different_solution_paths`单独覆盖 5/5 路径取舍检查；`normal_full`产生 fact、comparison、baseline、risk 和 recommendation candidate 五类结论。12 个案例逐项结果保存在：

```text
backend/app/data/contract_tests/step6c_suite_summary.json
```

这些分数来自确定性 Mock（模拟）实现与人工预设标签，用于验证 Schema、边界和评估代码，不应被解释为真实模型质量分数。真实 DeepSeek Pilot（试运行）已经执行；`2.2.2-candidate` 在整条剔除 1 条证据不合格结论后通过自动指标，但原始输出没有全部通过，而且尚未完成人工盲评，因此 candidate（候选版本）仍未 approved（批准），也没有默认启用。

## 14. DeepSeek V4 Real Pilot（真实试运行）结果

固定条件：同一在线教育快照、同一 `CompetitiveAnalysisPortfolioV2` Schema（数据结构）、同一 `deepseek-v4-flash` 模型、`temperature=0`、关闭 thinking mode（思考模式）和 Mock（模拟）回退。

运行入口：

```powershell
python .\run_step6c_real_ab_pilot.py
python .\run_step6c_real_ab_pilot.py --experiment-id step6c_deepseek_v4_candidate_2_2_1_pilot --variant v2_candidate
python .\run_step6c_real_ab_pilot.py --experiment-id step6c_deepseek_v4_candidate_2_2_2_pilot --variant v2_candidate
python .\check_step6c_real_pilot.py
```

本阶段累计记录 6 次真实调用：首轮 A/B 各一次、提高输出上限后的 A/B 各一次、`2.2.1-candidate` 单变体一次、`2.2.2-candidate` 单变体一次。首轮两次在 8000 Token（词元）上限下返回不可解析 JSON；适配器随后增加代码围栏容错、长度诊断和失败原始输出保留。后续调用使用 16000 Token 上限：

| 变体 | 结果 | 关键发现 |
|---|---|---|
| `v1_baseline@1.0.0-ab-baseline` | 结构校验失败 | `claim_014` 缺少 `evidence_ids` |
| `competitive_analyst@2.2.0-candidate` | Quality Gate（质量闸门）失败 | 多竞品证据均衡率 `5/7=0.7143` |
| `competitive_analyst@2.2.1-candidate` | Quality Gate（质量闸门）失败 | 多竞品证据均衡率提升到 `7/8=0.875`，但仍有一条多对象推断缺少 ClassIn 对应证据 |
| `competitive_analyst@2.2.2-candidate` | `completed_with_rejections（剔除后完成）` | 原始 10 条结论中的 `claim_006` 在文本中点名 ClassIn，但未把它登记为参与竞品，也没有对应证据；治理过滤器整条剔除该结论，剩余 9 条结论的多竞品证据均衡率为 `1.0`，全部自动指标通过 |

`2.2.1-candidate` 的真实调用记录为：

```text
input_tokens=14527
output_tokens=11282
duration_ms=62665
used_fallback=false
metric_pass_rate=0.9474
```

`2.2.2-candidate` 的唯一一次真实调用记录为：

```text
request_id=911a02bc-61df-4474-9b49-06ef2c94e79d
input_tokens=16259
output_tokens=10792
used_fallback=false
raw_claims_count=10
rejected_claims_count=1
accepted_claims_count=9
metric_pass_rate=1.0
multi_competitor_evidence_balance_rate=1.0
status=completed_with_rejections
```

这次 Pilot 证明专业提示词相对通用基线显著改善了证据遵循，但也证明 Prompt（提示词）不能替代确定性校验。`validate_portfolio_v2_refs` 现已要求每条结论中每个被点名竞品都有对应证据；`reject_unaligned_portfolio_v2_claims` 会在进入 Writer（撰写智能体）前整条拒绝不合格结论，不改写模型句子、不补造证据，并在 `LLMCall.metadata` 保存被拒绝原文与原因。模型已经另外生成 ClassIn 风险资料的 ResearchGap（研究缺口），因此剔除 `claim_006` 不会掩盖资料缺失。

历史判定：真实接口、版本追踪、Token 记录、失败审计和确定性整条拒绝均可运行；过滤后的 9 条结论通过全部自动指标，但原始输出仍有 1 条错误，因此 Analyst 候选版本未升级为 approved（批准）。

## 12. Step6C.3 双真实闭环结果

`snapshot_step6c3_analyst_writer_deepseek_v4_pilot` 已把真实 Analyst 与真实 Writer 串入同一条 evidence-first（证据优先）工作流，Extractor 保持 Mock。运行严格产生 2 次真实请求，没有 fallback（回退）：Analyst 输入/输出 Token 为 `16241 / 14470`，耗时 `85235ms`；Writer 输入/输出 Token 为 `20336 / 2486`，耗时 `18064ms`。

Analyst 原始输出有 1 条多竞品定价结论未为 BigBlueButton 绑定对应证据，被确定性过滤器整条拒绝；21 条有效结论和 5 个 ResearchGap（研究缺口）进入 Writer，生成 35 个 ReportStatement（报告论点）。专用检查、Workflow trace（工作流追踪）、Run artifacts（运行产物）和 Evaluation Harness（评估框架）全部通过。

人工语义复核确认：报告没有把内容审核或 AI 降噪控制反写为风险，没有把资料缺失写成产品缺陷，也没有暴露内部引用编号。Reviewer 得分 7.0，包含两条弱来源提醒和一条固定研发关键词提醒。当前 Analyst / Writer Prompt 仍为 candidate（候选）；单一行业样本不足以升级为 approved（批准）。
