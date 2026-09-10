# Professional Analyst 真实 DeepSeek Smoke Test

日期：2026-09-08。只验证 Analyst 语义评估，生产逻辑未修改。

> 后续边界修复：本报告记录的是修复前的历史 smoke 输入。该次请求曾把
> `SourceDocument.content_excerpt` 一并送入 Assessment，暴露出 Analyst
> 越过 Verified Evidence 边界的问题。当前实现已移除 `sources` 与
> `evidence_coverage` 输入，并在 LLMClient 入口加入 fail-closed 校验；所有新的
> Analyst 模型阶段均不再接收 SourceDocument 或原文，Assessment 请求只允许
> AnalysisTask、Framework、assessment scope、ResearchTask 和 Verified
> SourceEvidence。下文中的 Source 输入统计仅用于
> 保留历史审计事实，不代表修复后的模型输入。

## Evidence-only 修复后复测

使用同一历史任务再次真实调用 `deepseek-v4-flash`，隔离任务标识为
`professional_analyst_deepseek_evidence_only_20260908`。没有调用搜索、Fetch
或自动补采。

- Brief 请求：没有 `sources`、`content_excerpt` 或 `raw_content`。
- Assessment 请求：artifact 仅为 `analysis_task`、`framework_definition`、
  `assessment_scope`、`research_tasks`、`evidence`。
- Claims 请求：没有 `sources`、`content_excerpt` 或 `raw_content`。
- 三次真实调用共输入 30,170 tokens、输出 9,178 tokens；均一次返回，
  `finish_reason=stop`。
- Assessment 成功持久化为 `PARTIAL`，coverage score 为 `0.2778`，包含
  6 个维度评估、3 条 insight 和 4 个 ResearchGap。

真实复测确认无原文后，最终代码又把可选 `ProductCard` 从 Analyst 模型输入中
移除；离线 Provider 边界回归确认当前 Brief 仅接收 `analysis_task/evidence`，
Claims 仅额外接收本轮生成的 `competitor_profiles`。因此保存的真实 request_01/03
仍可看到当时的 ProductCard，而当前运行代码已经比该审计样本更严格。

四个 Gap 分别指向 Cursor 定价合同/地域信息、两款产品的真实客户体验、
两款产品的版本与部署边界，以及 Trae 国内外定价口径。它们都有对象、缺失事实、
影响、建议查询和停止条件，能够作为后续补采输入。客户体验 Gap 为 critical 且
阻塞决策，优先级合理；功能边界 Gap 同时包含版本、部署和适用场景，后续自动补采时
宜拆成更小任务。

人工复核仍发现一个语义问题：模型写成“官方页面未提供”时，实际只能证明“当前
Evidence 未覆盖”，不能证明原页面不存在该信息；另外“无公开价格时记录询价边界”
是条件式 completion criterion，在已有公开价格时不应机械标为 unmet。这些属于后续
Analyst 语义评测项，不影响本次原文隔离已生效的结论。

完整三阶段 Agent 最终仍在 Portfolio 组装时因 smoke 夹具缺少上游 KIQ 失败；
Assessment 已在失败前成功保存。这与第一次 smoke 的夹具问题一致，不是模型调用或
Evidence-only 边界故障。复测审计文件位于
`artifacts/professional_analyst_deepseek_evidence_only_20260908/`。

## 结果与测试边界

真实 `Framework + ResearchTask + Evidence → Analyst assessment → AnalysisAssessment + ResearchGap` 已跑通。
模型返回 6 个维度评估、3 条 insight、3 个 ResearchGap。Python 校验与物化得到 `PARTIAL`、coverage_score `0.3333`。
这表示本次选定 scope 的 required facts 覆盖比例，不是模型准确率或完整竞品研究完成率。

必须单独说明完整 Agent 的结果：现有 Professional Analyst 的 Brief、Assessment、Claims 三阶段均真实调用成功，均一次返回且通过结构化校验；但最终 Portfolio 组装失败，原因是本次 smoke 输入漏复制上游 KIQ（`CompetitiveAnalysisPortfolioV2.key_intelligence_questions 为空`）。这是测试夹具准备问题，不能把本次运行宣称为完整报告流水线成功。

Assessment 在 Claims 之前已经由生产 Agent 持久化。随后使用已保存的真实 Assessment 响应，经原有 `materialize_analysis_assessment` 再校验、导出独立 `research_gaps.json`；与已保存 Assessment 内的 Gap 逐项一致，没有修改模型输出、放宽校验或额外调用模型。原始失败记录保留在 `smoke_agent_result.json`、`smoke_summary.json`，最终定界结果在 `assessment_smoke_result.json`。

测试过程中只出现本地 ArtifactStore 读写与引用检查工具调用，没有 Tavily、知乎、Fetch 或 Harness 自动补采调用。历史任务目录和生产源码哈希核对未改变。

## 实际输入

来源任务：`task_user_58adfe02725c`，2026-09-06 的历史真实研究材料。

原始决策问题：了解 Trae 与 Cursor 两款产品的竞争态势，为后续决策提供参考。

- Framework：Registry 加载 `competitive_intelligence@1.0.0`，业务定义未改。
- Framework hash：`3b8514ebbb7a302e306ca745d360fba041b60a87db8b25c9e0e928716662ed29`。
- 研究对象：Trae、Cursor。
- Scope：产品能力、商业与定价策略、客户与使用体验，共 6 个竞品/维度组合。
- Evidence：从原 Research Agent `verified_evidence_ids` allowlist 选取 9 条；功能 4 条、定价 5 条、客户体验 0 条。
- Source：7 个历史 SourceDocument，包括 TRAE 企业页、TRAE CN 计费文档、TRAE 国际定价页、Cursor 官网/文档/定价页与一篇第三方定价文章。
- 4 个历史 ResearchTask 的测试副本固定到 Framework v2；另构造 2 个客户体验评估任务，仅用于声明待评估 scope，从未调度采集。
- 原 Evidence ID、quote、normalized_fact、Source 内容及元数据保留，task_id 改为隔离 smoke ID。没有提供历史 Analyst 判断或 ResearchGap。
- 用户原问题没有具体的采购对象、地域和业务决策，因此影响级别应保留不确定性。

真实 Assessment 请求携带：`analysis_task`、`framework_definition`、`assessment_scope`、`research_tasks`、`sources`、`evidence`、`evidence_coverage`。

完整 Framework 含六维，实际 scope 只评估上述三维。传入生产 Prompt 原文，没有 smoke 专用诱导或预设正确答案。实际请求的 system 为 829 字符，user 为 81,351 字符；其中 Source 的 `content_excerpt` 共 34,641 字符。

输入例子（以下是历史输入内容，不代表本次重新核实后的当前产品事实）：

- Trae 功能 Evidence 描述 AI IDE 中的生成、调试、Review、测试和文档查询；另有 TraeWork Code/Work 双模式。
- Cursor 功能 Evidence 描述理解代码库、规划构建、修复缺陷和审查更改；另有并行智能体能力。
- Trae 定价包含 CN 人民币套餐、国际美元套餐及用量体系。
- Cursor 定价包括官方 Pro `$20 / mo` 摘录和第三方套餐表。
- 没有任何 `dimension=customer` 的已验证 Evidence，官网展示的用户见证并未作为该维度 Evidence 提交。

## 真实调用记录

端点为项目已配置的 `https://api.deepseek.com/v1/chat/completions`，请求与响应 model 均为 `deepseek-v4-flash`，temperature 0.2、max_tokens 8000、thinking disabled、JSON object 模式。

| 阶段 | 输入 tokens | 输出 tokens | 响应时间 | finish_reason |
| --- | ---: | ---: | ---: | --- |
| Brief/Profile | 23,674 | 1,432 | 11.51 秒 | stop |
| Framework Assessment | 29,095 | 3,030 | 16.78 秒 | stop |
| Claims | 24,678 | 5,278 | 29.33 秒 | stop |
| 合计 | 77,447 | 9,740 | Agent 总计 57.81 秒 | 无重试 |

Assessment 请求 ID：`9027e21d-c1a8-414d-b6af-4ba9e471c7ca`。
token 统计来自响应 usage；原始 `smoke_summary.json` 的 token 总数为 0 是测试脚本最初读错 LLMCall 层级，已在独立 `assessment_smoke_result.json` 按真实响应修正，原始文件保留。

## 结构化 Assessment

```json
{
  "id": "assessment_40ec8c5bf6248247a37d",
  "framework_id": "competitive_intelligence",
  "framework_version": "1.0.0",
  "overall_status": "PARTIAL",
  "coverage_score": 0.3333,
  "dimension_assessment_count": 6,
  "insight_count": 3,
  "research_gap_count": 3
}
```

上面是字段摘要；原始语义 JSON、完整持久化 Assessment 与 Gap 见后面的 Artifact 链接。`overall_status` 和 score 由 Python 依据模型的 fact coverage 计算。

| 竞品 | 维度 | 状态 | 覆盖 | 核心缺失 |
| --- | --- | --- | ---: | --- |
| Cursor | 商业策略 | PARTIAL | 2/3 | 价格版本、地域、生效时间 |
| Trae | 商业策略 | PARTIAL | 2/3 | 价格版本、地域、生效时间 |
| Cursor | 产品能力 | PARTIAL | 1/3 | 版本/套餐/部署限制、场景与交付边界 |
| Trae | 产品能力 | PARTIAL | 1/3 | 版本/套餐/部署限制、场景与交付边界 |
| Cursor | 客户体验 | MISSING | 0/3 | 真实体验、角色/场景/版本、正负及冲突反馈 |
| Trae | 客户体验 | MISSING | 0/3 | 真实体验、角色/场景/版本、正负及冲突反馈 |

共覆盖 6/18 个 required facts，两个客户维度没有被官网介绍填成已覆盖。

## 实际 ResearchGap 与人工审阅

### Gap 1：定价口径

- 类型 `missing_fact`，impact `high`，blocks_decision `false`，对象为 Cursor 与 Trae。
- missing_information：`Cursor 与 Trae 定价适用的地域版本（如国际版/国内版）与生效时间（如价格页面版本日期）`。
- suggested_queries：`Cursor pricing 地域 版本 生效时间`、`Trae 定价 地域 版本 生效时间`。
- preferred_source_types：`official_site`、`docs`。
- stop_condition：`获得官方价格页面中明确标注地域与生效日期的证据。`

人工评价：有明确对象与缺失字段，补采方向基本合理；日期/适用市场关系确实没有完全验证。但模型把“部分已知”写成“全部缺失”：现有 Trae Evidence 已含 `TRAE CN` 和人民币价格，来源又区分 `.cn` 与 `.ai`，模型在自己的 reasoning 中也承认国际版/国内版。更准确的补采目标应是确认所选市场/套餐的适用性及价格观察时间，而不是重复发现已经识别的地域版本。

搜索 query 偏模板化；stop_condition 要求官网必须标出“生效日期”，可能无法满足，未来需要接受官方变更记录、明确观测日期或公开资料不足的退出条件。可作为人工补采草案，不宜直接作为硬停止规则。

### Gap 2：能力限制与交付边界

- 类型 `missing_fact`，impact `medium`，blocks_decision `false`，对象为 Cursor 与 Trae。
- missing_information：`Cursor 与 Trae 产品能力的版本号、套餐限制、部署方式（本地/云端）及功能适用场景与交付边界`。
- suggested_queries：`Cursor 版本 部署 限制`、`Trae 版本 部署 限制`。
- preferred_source_types：`docs`、`official_site`。
- stop_condition：`获得官方文档中关于版本、套餐与部署限制的明确说明。`

人工评价：能识别“官方说有功能”不足以支撑完整能力比较，方向合理。但一个 Gap 合并两款产品、多种形态、三个以上缺失字段，粒度偏大；Trae 的 TraeCode/TraeWork 与 Cursor 的本地/云端能力应明确比较对象后分别收集。

此外，已有 pricing Evidence 含产品适用范围、云端任务并行上限等能力限制。生产校验要求 assessment 引用同维度 Evidence，可能让可跨维度复用的信息表现为缺口。这属于当前边界的限制，不应直接归咎于模型漏读。后续补采前应先检查现有 Evidence 可否经受控关联满足该事实。

### Gap 3：真实客户体验

- 类型 `missing_fact`，impact `critical`，blocks_decision `true`，对象为 Cursor 与 Trae。
- missing_information：`Cursor 与 Trae 的真实用户反馈、使用体验描述、用户角色与场景、正面负面反馈`。
- suggested_queries：`Cursor 使用体验 用户评价`、`Trae 使用体验 用户评价`。
- preferred_source_types：`social`、`blog`、`report`。
- stop_condition：`获得至少一条包含具体体验事实的可追溯证据。`

人工评价：这条缺口真实且必要。模型没有把营销页、用户见证、功能介绍外推为已验证满意度，也没有把缺少资料解释为产品差。

但 `critical` 的理由不足：用户只要求了解竞争态势，没有明确把体验评估作为某项决策的一票否决条件。可以认为“无法判断体验”，但不能直接推导为阻塞整个研究目标。合并两个竞品后要求“至少一条”也不足以覆盖两者、用户角色/版本与正负反馈；未来任务需要分别记录采样对象、场景、版本、时间与观点分布，并允许“未找到某类反馈”而不强制凑齐正负观点。

## 语义质量结论

基础通过：真实模型能按 Registry scope 给出结构化覆盖判断与可理解的 Gap，required facts 与 Evidence ID 没有越界，3 个 Gap 可用于人工设计后续补采。

尚不适合直接自动调度的原因：

1. 部分已知信息被过度标成缺失，尤其是 Trae 地域口径。
2. 决策影响级别缺少与用户具体目标的充分联系。
3. Gap 跨产品、多字段合并，query 与 stop_condition 的粒度不匹配。
4. completion criteria 的条件语义处理不够好：例如已有公开价格时仍把“无公开价格时记录询价边界”标为 unmet。
5. 实际输入包含大量 Source 原文，模型在 assessment reasoning 中引用了未进入所选 Evidence 的信息：Cursor 学生优惠关闭日期（来自 Source 中的 `June 25, 2026`）及 Trae 的 SaaS/VPC（来自 Source 原文）。这些不是凭空捏造，但说明当前“仅依赖 Verified Evidence”的语义边界还不严格；ID 校验不能自动发现这种情况。
6. 9 条 Evidence 的 Assessment 请求已有 29,095 input tokens，原因包括完整 Framework、Source 原文及重复证据字段。单次 smoke 暴露了上下文开销，但不足以推导平均成本或质量指标。

本次未针对这些观察修改生产代码、Prompt 或 Framework，也未重新生成输出以追求更好结果。

## 可复查 Artifact

目录：`artifacts/professional_analyst_deepseek_smoke_20260908/professional_analyst_deepseek_smoke_20260908/`。

- `input_summary.json`：任务来源与输入构造说明。
- `request_02.json`：真实 Assessment HTTP JSON body，包含完整提示、schema 和实际输入；没有 Authorization header。
- `response_02.json`：真实服务响应、model、request ID、usage 与原始 JSON 文本。
- `assessment_model_output.json`：从真实响应提取的原始结构化语义输出。
- `analysis_assessments.json`：生产物化后的完整 Assessment。
- `research_gaps.json`：从同一真实输出经原有物化函数导出的完整 Gap。
- `assessment_smoke_result.json`：定界后的结果与修正 token 统计。
- `smoke_agent_result.json`：包含完整 Agent 最终 Portfolio 失败信息。
- `llm_calls.json`、`llm_outputs.json`、`tool_calls.json`：调用审计。

新增的独立 smoke 脚本：`backend/run_professional_analyst_real_smoke.py`。此次真实执行只进行一次（3 个 HTTP 请求）；`--export-recorded-only` 不调用模型。没有 Git commit。
