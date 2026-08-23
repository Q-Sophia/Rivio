# Step6F Research Analysis（真实研究分析）

## 目标

Step6F 把 Step6E 形成的真实研究产物交给已有 Domain Analyst（领域分析智能体），生成经过结构校验、证据绑定和引用检查的分析结论。它不重新采集网页，也不调用 Writer（写作智能体）。

```text
ResearchPlan + SourceDocument + SourceEvidence + ProductCard
-> DeepSeek Analyst
-> CompetitiveAnalysisPortfolioV2 + AnalysisClaim
-> CitationAgent
-> CitationCheck
```

## 安全与费用边界

- 只支持显式 `deepseek` 模式，并要求 `acknowledge_real_llm_call=true`。
- 每次首次执行只允许 1 次真实 Analyst LLM（大模型）调用。
- 已存在 `step6f_research_analysis` 完成产物时拒绝再次执行，避免重复计费。
- 不触发 Writer，不生成正式 CompetitiveReport（竞品报告）。
- 不触发 Collector / SearchProvider，不增加网页搜索费用。
- Analyst 输出必须通过 Pydantic schema（数据结构）校验；每条结论的 `evidence_ids` 必须来自当前任务已有证据。
- Step6E 的确定性 `evidence_coverage / research_gaps` 原样保留；LLM 派生结果另存为 `analysis_evidence_coverage / analysis_research_gaps`。

## API（接口）

```text
GET  /api/analysis-tasks/{task_id}/research-analysis
POST /api/analysis-tasks/{task_id}/research-analysis
```

POST 请求示例：

```json
{
  "mode": "deepseek",
  "acknowledge_real_llm_call": true
}
```

## 前端入口

在“新建分析”的 Research Planning（研究规划）区域，研究计划达到 `ready_for_analysis` 后显示：

```text
调用 DeepSeek 生成分析结论（1 次）
```

点击后会再次弹出费用确认。页面刷新只读取现有结果，不会重复调用模型。

## 真实验收结果（2026-08-21）

任务：`task_step6e3_zhipu_tencent_meeting_pilot`

- DeepSeek Analyst 真实调用：1 次
- 模型记录：`deepseek-v4-flash`
- `claims_v2`：13 条
- CitationCheck：13 条，全部 `supported`
- 所有结论均绑定当前任务的有效 `evidence_ids`
- Step6E 确定性覆盖结果保持不变
- 第二次执行在调用前被阻断
- Writer 调用：0 次
- 本步骤新增网络采集／搜索：0 次

## 下一步

下一步可把这些已校验结论交给 Writer 生成正式报告。该动作应继续使用独立按钮和明确确认，因为会产生额外 1 次真实模型调用。
