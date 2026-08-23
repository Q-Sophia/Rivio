# Step6E.4：证据覆盖更新与有限补采循环

## 目标

把新增网页证据接回证据优先主链路，并允许 Analyst（分析智能体）在预算内发布补采任务：

```text
Collector -> SourceDocument -> Extractor -> SourceEvidence
-> Analyst -> ProductCard + EvidenceCoverage + ResearchGap
-> Collector（仅在仍有缺口且预算未耗尽时）
```

## 关键规则

- 覆盖矩阵读取 ResearchPlan / AnalysisTask 的完整竞品与必需维度；零证据维度也必须显示 `missing`。
- 两条及以上可靠直接证据为 `sufficient`；单条为 `partial`；低置信度来源为 `weak`；冲突资料为 `conflicting`。
- `missing / partial / weak / conflicting` 生成 ResearchGap，并按竞品和维度发布定向 `supplement_collection`。
- ResearchTask 保存 `collection_round`、`parent_research_task_id` 和 `research_gap_id`。
- 最新一轮仍在等待采集或等待抽取时，不提前发布下一轮。
- 达到 `max_collection_rounds` 或 `max_total_sources` 后停止，保留未关闭缺口供人工判断。
- 全程不调用 LLM（大模型），不允许网页正文绕过 SourceEvidence 进入分析或报告。

## 前端与 API（接口）

前端“新建分析 → 研究计划”区域增加“更新覆盖并判断补采”按钮，显示覆盖格数、缺口数和当前轮次。

```text
POST /api/analysis-tasks/{task_id}/coverage/run-once
```

## 验证

```powershell
python check_step6e3_web_evidence_extractor.py
python check_step6e4_gap_tasks.py
python check_step6e4_update.py
```

验证覆盖：Extractor 动态发布 Analyst 任务、必需缺失维度识别、ProductCard 更新、补采任务发布、重复刷新去重、最大轮次停止、零网络与零真实 LLM 调用。
