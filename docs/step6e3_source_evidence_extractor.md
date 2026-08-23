# Step6E.3：网页正文到可引用证据

## 目标

把 Collector（采集智能体）保存的 `WebPageContent（网页正文）` 转换为可进入证据优先主链路的 `SourceEvidence（来源证据）`：

```text
SearchResult -> SourceDocument -> WebPageContent
-> SourceEvidence -> ProductCard -> AnalysisClaim
```

## 任务流

Collector 完成一个研究任务后，会在 TaskBoard（任务板）动态发布：

```text
task_type=extract_source_evidence
target_agent_role=extractor
status=ready
```

Extractor（抽取智能体）领取任务后读取目标 `source_ids`，输出：

```text
evidence.json
evidence_extraction_attempts.json
agent_runs.json
dag_nodes.json
```

抽取成功后不会直接进入报告，而是继续向 TaskBoard 发布 `evaluate_evidence_coverage`，交给 Step6E.4 的 Analyst（分析智能体）更新 ProductCard / EvidenceCoverage 并判断有限轮次补采。

## 证据约束

每条新证据必须包含：

- 有效的 `source_id`，可以关联 `SourceDocument.url`；
- 原始 `snippet`，不得补写网页中不存在的事实；
- `source_text_start / source_text_end`，能够在 WebPageContent 中逐字回放；
- `web_page_id / content_hash / research_task_id`；
- `quote_verified=true`；
- 明确的 `extraction_method`。

第一版使用 `deterministic_quote_extractor_v1`（确定性引文抽取器），不调用 LLM（大模型）。`normalized_fact` 只允许清理空白符，不做事实改写。营销、订阅、未来承诺和问句会被过滤。后续可以加入 LLM 结构化抽取，但仍必须通过逐字引文和 Pydantic schema（数据结构）校验。

## API（接口）

```text
POST /api/analysis-tasks/{task_id}/extractor/run-once
GET  /api/tasks/{task_id}/evidence-extraction-attempts
GET  /api/tasks/{task_id}/evidence
```

## 验证

```powershell
python check_step6e3_web_evidence_extractor.py
python run_step6e3_zhipu_tencent_meeting.py
```

Mock（模拟）回归结果：PASS；覆盖来源引用、字符位置回放、URL 保留、营销文案过滤、抽取审计和 TaskBoard 状态回写。

真实智谱试验 `task_step6e3_zhipu_tencent_meeting_pilot`：

- SearchProvider（搜索供应商）：`zhipu`；
- 搜索结果：3 条，均限定为 `meeting.tencent.com`；
- 成功采集：2 个腾讯会议官方页面；
- 失败页面：1 个官方帮助页返回 404，已保留失败审计；
- 新增 SourceEvidence：13 条；
- 可逐字回放：13/13；
- 营销或订阅文案：0 条；
- 真实 LLM 调用：0。
