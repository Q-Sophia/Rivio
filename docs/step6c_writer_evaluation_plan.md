# Step6C.2B 专业报告撰写评估计划

状态：`bounded-real-pilot-passed（受限真实试运行已通过）`

当前评估对象：`competitive_writer@2.1.1-candidate`

Step6C.2C（步骤 6C.2C）补充三项阻断指标：

- `writer_report_statement_mapping_rate`：报告正文中带内部引用的可见行必须全部生成 ReportStatement（报告论点）；
- `writer_report_statement_evidence_valid_rate`：报告论点引用的 evidence_ids（证据编号）必须全部存在；
- `writer_claim_statement_evidence_coverage`：绑定 AnalysisClaim（分析结论）的报告论点不得缺少证据。

这三项指标只验证追溯结构，不评价文风是否专业；专业文风仍需要真实 Writer LLM（写作大模型）、Prompt（提示词）试验和人工评分共同验证。

## 1. 目标

WriterAgent（报告撰写智能体）必须把已经治理的专业分析产物组织成面向当前任务的报告，而不是把平台名称或固定行业模板当成报告主题。

核心要求：

1. 报告标题来自 `AnalysisTask（分析任务）`，优先级为 `preferred_title -> report_subject -> industry -> competitors`；
2. 当前在线教育演示任务应生成《在线教育实时互动与虚拟教室解决方案竞品分析报告》；
3. Writer 只消费 BriefAssessment（简报评估）、CompetitorProfile（竞品画像）、EvidenceCoverage（证据覆盖）、ComparabilityNote（可比性说明）、AnalysisClaimV2（第二版分析结论）、CitationCheck（引用检查）和 ResearchGap（研究缺口），不直接读取原始网页写报告；
4. 关键判断必须引用 `[claim_id]`，研究缺口必须保留 `[gap_id]`；
5. 报告必须综合结论、解释决策影响并披露限制，不能逐条重复复制结论；
6. “资料未提及”不能改写成“不具备”，建议必须带适用条件和验证动作。
7. Writer 模型输入不得包含 `source_ids / evidence_ids（来源/证据内部编号字段）`，不得根据编号名称猜测语义；
8. 审核、风控、降噪等控制能力不得反向写成风险，除非治理后的语义字段明确陈述具体风险。

## 2. 报告结构

```text
执行摘要
研究目标与决策背景
竞品与解决路径
核心维度对比
成本、交付与采用条件
风险、限制与不确定性
后续研究缺口
决策建议
结论引用索引
```

## 3. 自动指标

```text
step6c_writer_title_task_specific = true
step6c_writer_required_section_coverage = 1.0
step6c_writer_claim_reference_coverage = 1.0
step6c_writer_research_gap_disclosure_rate = 1.0
step6c_writer_repeated_claim_copy_rate = 0.0
step6c_writer_reader_claim_trace_coverage = 1.0
step6c_writer_reader_gap_trace_coverage = 1.0
step6c_writer_audit_phrase_count = 0
step6c_writer_coursework_phrase_count = 0
step6c_writer_reader_body_char_count >= 1200
```

以上指标均为 Blocking Metric（阻断指标）。任一失败时，Writer Prompt（报告提示词）不能升级为 approved（已批准）。

## 4. 当前验证

在 `backend` 目录运行：

```powershell
python .\run_step6c_professional_workflow_demo.py
python .\check_step6c_writer.py
python .\harness\run_eval.py --task-id snapshot_step6c_professional_mock
python .\run_step6c_writer_real_pilot.py
python .\check_step6c_writer_real_pilot.py
python .\harness\run_eval.py --task-id snapshot_step6c2d_writer_deepseek_v4_pilot_v2
```

当前结果：Mock regression（模拟回归）与一次合格的真实 Writer 受限运行均完成。真实运行只调用 1 次 `deepseek-v4-flash`，上游 2 次调用为 Mock（模拟），无 fallback（回退）；标题来源为 `report_subject`，Writer 不读取原始 `sources/evidence`，也不接收来源/证据内部编号字段，全部 Writer 指标通过。

第一次真实运行的自动指标曾通过，但人工复核发现“内容审核、AI 降噪能力”被反向写成风险。该结果仅保留为审计历史，未作为最终验收版本。Prompt `2.1.1-candidate` 增加语义方向约束，并在 Writer 输入层移除内部证据编号；第二次运行经自动检查和人工语义复核后通过。

## 5. 当前边界

- Mock LLM（模拟大模型）仍用于稳定回归；Step6C.2D 已完成一次受限 DeepSeek Writer（深度求索写作智能体）验收；
- Prompt 仍为 candidate（候选），一次真实样本不足以升级为 approved（正式批准）；
- 当前没有用户输入框，在线教育演示任务由 `AnalysisTask.report_subject` 提供规范化主题；
- 后续接入用户对话后，Intent Recognition（意图识别）负责生成 `report_subject`，用户确认或修改后的标题写入 `preferred_title`；
- 当前尚未进行多任务、多行业人工盲评，因此不会把候选 Prompt 升级为 approved（批准）。
